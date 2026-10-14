import httpx
from fastapi import HTTPException, status

from app import logging, schemas
from app.constants import API_REQUEST_HEADERS, HTTPX_CLIENT
from app.models.BangumiEpisodeCompletion import BangumiEpisodeCompletion
from app.models.RecordedProgram import RecordedProgram
from app.models.Series import Series
from app.models.User import User
from app.utils.BangumiClient import BangumiClient


async def UpdateBangumiEpisodeCollection(access_token: str, subject_id: int, episode_id: int) -> None:
    """
    Bangumi の対象エピソードを「看過」に更新する。

    Args:
        access_token (str): Bangumi 個人アクセストークン。
        subject_id (int): Bangumi 条目 ID。
        episode_id (int): Bangumi エピソード ID。

    Returns:
        None: Bangumi 上でエピソードの更新が完了した場合。

    Raises:
        HTTPException: Bangumi API への接続、認証、または更新に失敗した場合。
    """

    headers = {**API_REQUEST_HEADERS, 'Authorization': f'Bearer {access_token}'}
    try:
        async with HTTPX_CLIENT() as httpx_client:
            # エピソードを「看過」にする。同じ値への PUT なのでリトライしても結果は変わらない
            episode_response = await httpx_client.put(
                url = f'{BangumiClient.API_BASE_URL}/users/-/collections/-/episodes/{episode_id}',
                headers = headers,
                json = {'type': 2},
            )

            # Bangumi は未収藏条目のエピソードを更新できないため、初回だけ「在看」を作成して再送する
            if episode_response.status_code == status.HTTP_400_BAD_REQUEST:
                collection_status_response = await httpx_client.get(
                    url = f'{BangumiClient.API_BASE_URL}/users/-/collections/{subject_id}/episodes',
                    headers = headers,
                )
                # 未収藏の場合だけ「在看」を作成し、既存の想看・看過・擱置・拋棄は上書きしない
                if collection_status_response.status_code == status.HTTP_404_NOT_FOUND:
                    collection_response = await httpx_client.post(
                        url = f'{BangumiClient.API_BASE_URL}/users/-/collections/{subject_id}',
                        headers = headers,
                        json = {'type': 3},
                    )
                    collection_response.raise_for_status()
                else:
                    collection_status_response.raise_for_status()
                episode_response = await httpx_client.put(
                    url = f'{BangumiClient.API_BASE_URL}/users/-/collections/-/episodes/{episode_id}',
                    headers = headers,
                    json = {'type': 2},
                )
            episode_response.raise_for_status()
    except (httpx.NetworkError, httpx.TimeoutException) as ex:
        logging.error('[BangumiPlaybackSync][UpdateBangumiEpisodeCollection] Failed to connect to Bangumi API.')
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = 'Failed to connect to Bangumi API',
        ) from ex
    except httpx.HTTPStatusError as ex:
        if ex.response.status_code in [status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN]:
            logging.warning('[BangumiPlaybackSync][UpdateBangumiEpisodeCollection] Bangumi access token is invalid or forbidden.')
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Bangumi access token is invalid or does not have write:collection permission',
            ) from ex
        logging.error(
            f'[BangumiPlaybackSync][UpdateBangumiEpisodeCollection] Bangumi API returned HTTP {ex.response.status_code}.',
        )
        raise HTTPException(
            status_code = status.HTTP_502_BAD_GATEWAY,
            detail = f'Failed to update Bangumi episode collection (HTTP Error {ex.response.status_code})',
        ) from ex



async def SyncBangumiPlaybackProgress(
    video_id: int, progress_request: schemas.BangumiPlaybackProgressRequest, current_user: User,
) -> schemas.BangumiPlaybackProgressResponse:
    """
    現在の再生位置を所有するユーザーの Bangumi 視聴状態へ反映する。

    Args:
        video_id (int): 今回再生中の録画 ID。
        progress_request (schemas.BangumiPlaybackProgressRequest): 現在位置と解決済み時間。
        current_user (User): 認証済みの所有ユーザー。

    Returns:
        schemas.BangumiPlaybackProgressResponse: 対象話の同期結果。
    """

    if current_user.bangumi_user_id is None or current_user.bangumi_access_token is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Bangumi account is not linked',
        )

    recorded_program = await RecordedProgram.get_or_none(id=video_id).prefetch_related('recorded_video', 'channel')
    if recorded_program is None:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified video_id was not found',
        )

    # 完了判定をクライアントへ分散せず、今回の現在位置が属する話だけを選ぶ。
    episode_number, is_range, is_completed = BangumiClient.selectPlaybackEpisode(
        recorded_program, progress_request.playback_position, progress_request.duration,
    )
    if episode_number is None:
        return schemas.BangumiPlaybackProgressResponse(status='NotEligible')
    if not is_completed or recorded_program.recorded_video.status != 'Recorded':
        return schemas.BangumiPlaybackProgressResponse(status='Pending')

    # 保存済み ID を先に再検証し、旧章の完了記録で正しい章の更新が抑止されないようにする。
    ## タイトル検索やコレクション全件同期はせず、既知の主条目と続編関係だけを参照する。
    access_token = current_user.decryptBangumiAccessToken()
    series = await Series.get_or_none(id=recorded_program.series_id) if recorded_program.series_id is not None else None
    subject_id, episode_id = recorded_program.bangumi_subject_id, recorded_program.bangumi_episode_id
    if series is not None:
        try:
            if is_range:
                resolved = await BangumiClient.resolveEpisodeNumber(series, episode_number, access_token)
                subject_id, episode_id = resolved if resolved is not None else (None, None)
            else:
                await BangumiClient.resolveRecordedEpisode(recorded_program, series, access_token)
                subject_id, episode_id = recorded_program.bangumi_subject_id, recorded_program.bangumi_episode_id
        except (httpx.HTTPError, ValueError) as ex:
            logging.warning('[BangumiPlaybackSync][SyncBangumiPlaybackProgress] Episode validation failed.', exc_info=ex)
            return schemas.BangumiPlaybackProgressResponse(status='Pending')

    if is_range and series is None:
        return schemas.BangumiPlaybackProgressResponse(status='Pending')

    # 過去の同期済み記録があれば、重播や複数タブからの重複更新を避ける
    if episode_id is not None:
        is_completed = await BangumiEpisodeCompletion.filter(
            user_id = current_user.id,
            bangumi_episode_id = episode_id,
        ).exists()
        if is_completed:
            return schemas.BangumiPlaybackProgressResponse(status='AlreadyCompleted')

    # 一意に解決できない章は外部更新せず、次回の進捗送信で再検証する。
    if subject_id is None or episode_id is None:
        logging.info(
            f'[BangumiPlaybackSync][SyncBangumiPlaybackProgress] Bangumi episode is not mapped. [video_id: {video_id}]',
        )
        return schemas.BangumiPlaybackProgressResponse(status='Pending')

    # 外部更新に成功した後で完了記録を作り、失敗を成功として扱わない
    await UpdateBangumiEpisodeCollection(
        access_token,
        subject_id,
        episode_id,
    )
    await BangumiEpisodeCompletion.get_or_create(
        user_id = current_user.id,
        bangumi_episode_id = episode_id,
        defaults = {'source_recorded_program_id': recorded_program.id},
    )
    logging.info(
        f'[BangumiPlaybackSync][SyncBangumiPlaybackProgress] Completed Bangumi episode. '
        f'[konomitv_user_id: {current_user.id}, video_id: {video_id}, '
        f'bangumi_episode_id: {episode_id}]',
    )
    return schemas.BangumiPlaybackProgressResponse(status='Completed')



async def SyncHistoryPlaybackProgress(
    current_user: User, playback_progress: schemas.HistoryPlaybackProgress | None,
) -> None:
    """
    履歴同期に明示された現在の再生位置だけを処理し、履歴保存とは障害を分離する。

    Args:
        current_user (User): 履歴を保存した認証済みユーザー。
        playback_progress (schemas.HistoryPlaybackProgress | None): 現在の再生報告。履歴一括移行では None。

    Returns:
        None: 未連携や外部更新失敗でも履歴保存の成功を保持する。
    """

    if playback_progress is None or current_user.bangumi_user_id is None or current_user.bangumi_access_token is None:
        return
    try:
        await SyncBangumiPlaybackProgress(playback_progress.video_id, playback_progress, current_user)
    except (httpx.HTTPError, HTTPException, ValueError) as ex:
        logging.warning(
            f'[BangumiPlaybackSync] Failed to sync current playback. [video_id: {playback_progress.video_id}]',
            exc_info = ex,
        )
