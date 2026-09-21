
import asyncio
import copy
import time
from collections.abc import AsyncGenerator
from typing import Annotated

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.requests import Request
from fastapi.responses import Response, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from starlette.types import Receive

from app import logging, schemas
from app.constants import API_REQUEST_HEADERS
from app.models.Channel import Channel
from app.streams.LiveStream import LiveStream, LiveStreamStatus
from app.streams.StreamEncodingOptions import (
    LiveStreamQualityWithOptions,
    SplitLiveQualityAndEncodingOptions,
)
from app.utils import GetBackendForReceiving, GetMirakurunAPIEndpointURL
from app.utils.JellyfinClient import JellyfinClient, JellyfinError


# ルーター
router = APIRouter(
    tags = ['Streams'],
    prefix = '/api/streams/live',
)


async def ValidateChannelID(display_channel_id: Annotated[str, Path(description='チャンネル ID 。ex: gr011')]) -> str:
    """ チャンネル ID のバリデーション """

    # チャンネル ID が存在するか確認
    if await Channel.filter(display_channel_id=display_channel_id).get_or_none() is None:
        logging.error(f'[LiveStreamsRouter][ValidateChannelID] Specified display_channel_id was not found. [display_channel_id: {display_channel_id}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified display_channel_id was not found',
        )

    return display_channel_id


async def ValidateQuality(
    display_channel_id: Annotated[str, Path(description='チャンネル ID 。ex: gr011')],
    quality: Annotated[str, Path(description='映像の品質。ex: original, 1080p')],
) -> LiveStreamQualityWithOptions:
    """ 映像の品質のバリデーション """

    # ラジオチャンネルには MPEG-2 映像がなく、mpeg2toh264 では再生できないため受け付けない
    if quality == 'original':
        channel = await Channel.filter(display_channel_id=display_channel_id).get_or_none()
        if channel is not None and channel.is_radiochannel is True:
            logging.error(f'[LiveStreamsRouter][ValidateQuality] Original quality is not available for radio channels. [display_channel_id: {display_channel_id}]')
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Original quality is not available for radio channels',
            )

    # 指定されたエンコード済み品質が存在するか確認
    ## 品質の指定に -10bit や -24fps が付いていれば分解する
    ## ライブストリームでは BS4K Raw MMTS 専用の raw-mmts も受け付ける
    stream_quality = SplitLiveQualityAndEncodingOptions(quality)
    if stream_quality is None:
        logging.error(f'[LiveStreamsRouter][ValidateQuality] Specified quality was not found. [quality: {quality}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified quality was not found',
        )

    return stream_quality


def BuildDirectRawMMTSLiveStreamStatus() -> schemas.LiveStreamStatus:
    """
    Raw MMTS 直通配信用の軽量ライブストリームステータスを生成する。

    Args:
        なし

    Returns:
        schemas.LiveStreamStatus: Raw MMTS 直通配信用の疑似ライブストリームステータス
    """

    now = time.time()
    return schemas.LiveStreamStatus(
        status = 'ONAir',
        detail = 'Raw MMTS passthrough is ONAir.',
        started_at = now,
        updated_at = now,
        client_count = 0,
    )


async def OpenMirakurunRawMMTSStream(display_channel_id: str) -> tuple[aiohttp.ClientSession, aiohttp.ClientResponse]:
    """
    Mirakurun の BS4K Raw MMTS ストリームを decode=0 で直接開く。

    Args:
        display_channel_id (str): 視聴対象のチャンネル ID

    Returns:
        tuple[aiohttp.ClientSession, aiohttp.ClientResponse]: Mirakurun への HTTP セッションとレスポンス
    """

    # Raw MMTS 直通は Mirakurun / mirakc の Service Stream API が前提
    if GetBackendForReceiving() != 'Mirakurun':
        logging.error(
            '[LiveStreamsRouter][OpenMirakurunRawMMTSStream] '
            'Raw MMTS is only available with Mirakurun / mirakc receiving backend.'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Raw MMTS is only available with Mirakurun / mirakc receiving backend',
        )

    # チャンネル情報を取得
    channel = await Channel.filter(display_channel_id=display_channel_id).get_or_none()
    if channel is None:
        logging.error(
            f'[LiveStreamsRouter][OpenMirakurunRawMMTSStream] Specified display_channel_id was not found. '
            f'[display_channel_id: {display_channel_id}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified display_channel_id was not found',
        )

    # Raw MMTS は BS4K チャンネル専用
    if channel.type != 'BS4K':
        logging.error(
            f'[LiveStreamsRouter][OpenMirakurunRawMMTSStream] Raw MMTS is only available for BS4K channels. '
            f'[display_channel_id: {display_channel_id}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Raw MMTS is only available for BS4K channels',
        )

    # Mirakurun 形式のサービス ID
    # NID と SID を 5 桁でゼロ埋めした上で int に変換する
    mirakurun_service_id = int(str(channel.network_id).zfill(5) + str(channel.service_id).zfill(5))

    # Mirakurun の Service Stream API へ HTTP リクエストを開始
    session = aiohttp.ClientSession()
    try:
        response = await session.get(
            url = GetMirakurunAPIEndpointURL(f'/api/services/{mirakurun_service_id}/stream?decode=0'),
            headers = {**API_REQUEST_HEADERS, 'X-Mirakurun-Priority': '0'},
            timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_connect=15),
        )
    except (TimeoutError, aiohttp.ClientConnectorError) as ex:
        await session.close()
        logging.error(
            f'[LiveStreamsRouter][OpenMirakurunRawMMTSStream] Failed to connect to Mirakurun / mirakc. '
            f'[display_channel_id: {display_channel_id}]',
            exc_info = ex,
        )
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = 'Failed to connect to Mirakurun / mirakc',
        ) from ex

    # Mirakurun の Service Stream API からエラーが返された場合
    if response.status != 200:
        response.close()
        await session.close()
        logging.error(
            f'[LiveStreamsRouter][OpenMirakurunRawMMTSStream] Mirakurun / mirakc returned HTTP {response.status}. '
            f'[display_channel_id: {display_channel_id}]'
        )
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = f'Mirakurun / mirakc returned HTTP {response.status}',
        )

    return session, response


async def GenerateMirakurunRawMMTSStream(
    request: Request,
    session: aiohttp.ClientSession,
    response: aiohttp.ClientResponse,
) -> AsyncGenerator[bytes, None]:
    """
    Mirakurun から受け取った Raw MMTS を、そのまま StreamingResponse へ流す。

    Args:
        request (Request): FastAPI のリクエスト
        session (aiohttp.ClientSession): Mirakurun への HTTP セッション
        response (aiohttp.ClientResponse): Mirakurun からの HTTP レスポンス

    Yields:
        bytes: Raw MMTS のチャンク
    """

    try:
        while True:
            # リクエストがキャンセル（切断）されている場合は Mirakurun との接続も閉じる
            if await request.is_disconnected():
                logging.debug('[LiveStreamsRouter][GenerateMirakurunRawMMTSStream] Request is disconnected.')
                break

            try:
                chunk = await response.content.read(512 * 1024)
            except (aiohttp.ClientError, TimeoutError):
                logging.warning(
                    '[LiveStreamsRouter][GenerateMirakurunRawMMTSStream] '
                    'Mirakurun / mirakc stream was interrupted.'
                )
                break

            # 空のデータが返ってきたら、Mirakurun とのストリーミング接続が終了したものと判断する
            if len(chunk) == 0:
                break

            yield chunk

    finally:
        response.close()
        await session.close()


@router.get(
    '',
    summary = 'ライブストリーム一覧 API',
    response_description = 'ステータスごとに分類された、すべてのライブストリームの状態。',
    response_model = schemas.LiveStreamStatuses,
)
async def LiveStreamsAPI():
    """
    すべてのライブストリームの状態を Offline・Standby・ONAir・Idling・Restart の各ステータスごとに取得する。
    """

    # 返却するデータ
    # 逆順になっているのは、デバッグ時に全体の大半を占める Offline なストリームが邪魔なため
    result: dict[str, dict[str, LiveStreamStatus]] = {
        'Restart': {},
        'Idling' : {},
        'ONAir'  : {},
        'Standby': {},
        'Offline': {},
    }

    # すべてのストリームごとに
    for live_stream in LiveStream.getAllLiveStreams():
        live_stream_status = live_stream.getStatus()
        result[live_stream_status.status][live_stream.live_stream_id] = live_stream_status

    # すべてのライブストリームの状態を返す
    return result


@router.post('/sessions', response_model=schemas.LiveStreamSession)
async def CreateLiveStreamSessionAPI(request: schemas.LiveStreamSessionRequest):
    """外部ライブ入力向けの共通再生セッションを作成する。"""
    if request.channel_id.startswith('jellyfin-') is False:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail='This channel does not use a live stream session')
    try:
        await JellyfinClient.collect_expired_playbacks()
        session = await JellyfinClient.open_playback(request.channel_id.removeprefix('jellyfin-'))
        return schemas.LiveStreamSession(
            id=session.session_id,
            stream_url=f'/api/streams/live/sessions/{session.session_id}/playlist',
            stream_type=session.stream_type,
        )
    except JellyfinError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.delete('/sessions/{session_id}', status_code=status.HTTP_204_NO_CONTENT)
async def CloseLiveStreamSessionAPI(session_id: Annotated[str, Path(min_length=16, max_length=64)]):
    """共通ライブ再生セッションを閉じ、上流の LiveStream も解放する。"""
    await JellyfinClient.close_playback(session_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get('/sessions/{session_id}/playlist')
async def LiveStreamSessionPlaylistAPI(session_id: Annotated[str, Path(min_length=16, max_length=64)]):
    """HLS プレイリストを同一オリジンのリソース URL へ変換する。"""
    try:
        session = JellyfinClient.get_playback_session(session_id)
        if session.stream_type == 'mpegts':
            resource_id = JellyfinClient.register_resource(session, session.upstream_url)
            stream, media_type = await JellyfinClient.stream_resource(session, resource_id)
            return StreamingResponse(
                stream,
                media_type=media_type,
                headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'},
            )
        content, media_type = await JellyfinClient.fetch_resource(session)
        if session.stream_type == 'hls':
            content = JellyfinClient.rewrite_playlist(session, content, session.upstream_url)
            media_type = 'application/vnd.apple.mpegurl'
        return Response(content=content, media_type=media_type, headers={'Cache-Control': 'no-store'})
    except JellyfinError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.get('/sessions/{session_id}/resource/{resource_id}')
async def LiveStreamSessionResourceAPI(
    session_id: Annotated[str, Path(min_length=16, max_length=64)],
    resource_id: Annotated[str, Path(min_length=16, max_length=64)],
):
    """HLS の子プレイリスト・鍵・分片を代理し、TS はストリーミングで中継する。"""
    try:
        session = JellyfinClient.get_playback_session(session_id)
        upstream_url = session.resource_urls.get(resource_id, '')
        if '.m3u8' not in upstream_url.lower():
            stream, media_type = await JellyfinClient.stream_resource(session, resource_id)
            return StreamingResponse(stream, media_type=media_type, headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
        content, media_type = await JellyfinClient.fetch_resource(session, resource_id)
        content = JellyfinClient.rewrite_playlist(session, content, upstream_url)
        return Response(content=content, media_type='application/vnd.apple.mpegurl', headers={'Cache-Control': 'no-store'})
    except JellyfinError as error:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(error)) from error


@router.get(
    '/{display_channel_id}/{quality}',
    summary = 'ライブストリーム API',
    response_description = 'ライブストリームの状態。',
    response_model = schemas.LiveStreamStatus,
)
async def LiveStreamAPI(
    display_channel_id: Annotated[str, Depends(ValidateChannelID)],
    stream_quality: Annotated[LiveStreamQualityWithOptions, Depends(ValidateQuality)],
):
    """
    ライブストリームの状態を取得する。<br>
    ライブストリーム イベント API にて配信されるイベントと同一のデータだが、一回限りの取得である点が異なる。
    """

    # Raw MMTS は直通配信のため、LiveStream の再利用・ステータス管理には載せない
    if stream_quality.quality == 'raw-mmts':
        return BuildDirectRawMMTSLiveStreamStatus()

    # 品質とオプション指定に対応する LiveStream を取得する
    # ステータスを取得したいだけなので、接続はしない
    live_stream = LiveStream(display_channel_id, stream_quality.quality, stream_quality.encoding_options)

    # 取得してきた値をそのまま返す
    return live_stream.getStatus()


@router.get(
    '/{display_channel_id}/{quality}/events',
    summary = 'ライブストリーム イベント API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'ライブストリームのイベントが随時配信されるイベントストリーム。',
            'content': {'text/event-stream': {}},
        }
    }
)
async def LiveStreamEventAPI(
    request: Request,
    display_channel_id: Annotated[str, Depends(ValidateChannelID)],
    stream_quality: Annotated[LiveStreamQualityWithOptions, Depends(ValidateQuality)],
):
    """
    ライブストリームのイベントを Server-Sent Events で随時配信する。

    イベントには、
    - 初回接続時に現在のステータスを示す **initial_update**
    - ステータスの更新を示す **status_update**
    - ステータス詳細の更新を示す **detail_update**
    - クライアント数の更新を示す **clients_update**
    の4種類がある。

    どのイベントでも配信される JSON 構造は同じ。<br>
    ステータスが Offline になった、あるいは既にそうなっている時は、status_update イベントが配信された後に接続を終了する。
    """

    # Raw MMTS は直通配信のため、LiveStream の再利用・ステータス管理には載せない
    # ただしクライアント側はライブ状態監視にこの SSE を使うため、軽量な ONAir ステータスだけ返す
    if stream_quality.quality == 'raw-mmts':
        async def raw_mmts_generator():
            raw_mmts_status = BuildDirectRawMMTSLiveStreamStatus()
            yield {
                'event': 'initial_update',
                'data': raw_mmts_status.model_dump_json(),
            }

            while await request.is_disconnected() is False:
                await asyncio.sleep(5)

        return EventSourceResponse(raw_mmts_generator())

    # 品質とオプション指定に対応する LiveStream を取得する
    # ステータスを取得したいだけなので、接続はしない
    live_stream = LiveStream(display_channel_id, stream_quality.quality, stream_quality.encoding_options)

    # ステータスの変更を監視し、変更があればステータスをイベントストリームとして出力する
    async def generator():
        """イベントストリームを出力するジェネレーター"""

        # 初期値
        previous_status = live_stream.getStatus()

        # 取得できたクライアント数はあくまで同じチャンネル+同じ画質で視聴中のクライアントをカウントしたものなので、
        # 同じチャンネル+すべての画質で視聴中のクライアント数を別途取得して上書きする
        previous_status.client_count = LiveStream.getViewerCount(display_channel_id)

        # 初回接続時に必ず現在のステータスを返す
        yield {
            'event': 'initial_update',  # initial_update イベントを設定
            'data': previous_status.model_dump_json(),
        }

        while True:

            # 現在のライブストリームのステータスを取得
            status = live_stream.getStatus()

            # 取得できたクライアント数はあくまで同じチャンネル+同じ画質で視聴中のクライアントをカウントしたものなので、
            # 同じチャンネル+すべての画質で視聴中のクライアント数を別途取得して上書きする
            status.client_count = LiveStream.getViewerCount(display_channel_id)

            # 以前の結果と異なっている場合のみレスポンスを返す
            if previous_status != status:

                # ステータスが以前と異なる
                if previous_status.status != status.status:
                    yield {
                        'event': 'status_update',  # status_update イベントを設定
                        'data': status.model_dump_json(),
                    }
                # 詳細が以前と異なる
                elif previous_status.detail != status.detail:
                    yield {
                        'event': 'detail_update',  # detail_update イベントを設定
                        'data': status.model_dump_json(),
                    }
                # クライアント数が以前と異なる
                elif previous_status.client_count != status.client_count:
                    yield {
                        'event': 'clients_update',  # clients_update イベントを設定
                        'data': status.model_dump_json(),
                    }

                # 取得結果を保存
                previous_status = copy.copy(status)

            # 一応スリープを入れておく
            await asyncio.sleep(0.05)

    # EventSourceResponse でイベントストリームを配信する
    return EventSourceResponse(generator())


# ***** ライブ PSI/SI アーカイブデータストリーミング API *****


@router.get(
    '/{display_channel_id}/{quality}/psi-archived-data',
    summary = 'ライブ PSI/SI アーカイブデータストリーミング API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'ライブ PSI/SI アーカイブデータストリーム。',
            'content': {'application/octet-stream': {}},
        }
    }
)
async def LivePSIArchivedDataAPI(
    request: Request,
    display_channel_id: Annotated[str, Depends(ValidateChannelID)],
    stream_quality: Annotated[LiveStreamQualityWithOptions, Depends(ValidateQuality)],
):
    """
    ライブ PSI/SI アーカイブデータストリームを配信する。

    何らかの理由でライブストリームが終了しない限り、継続的にレスポンスが出力される（ストリーミング）。
    """

    # 品質とオプション指定に対応する LiveStream を取得する
    # PSI/SI アーカイブデータを取得したいだけなので、接続はしない
    live_stream = LiveStream(display_channel_id, stream_quality.quality, stream_quality.encoding_options)

    # LivePSIDataArchiver がまだ初期化されていない場合は、起動するまで最大10秒待つ
    ## LivePSIDataArchiver は LiveEncodingTask が起動次第自動的に初期化されるので、ここでは待つだけ
    for _ in range(20):
        if live_stream.psi_data_archiver is not None:
            break
        await asyncio.sleep(0.5)

    # 10秒待っても起動しなかった場合はエラー
    if live_stream.psi_data_archiver is None:
        logging.error(f'{live_stream.log_prefix} PSI/SI Data Archiver is not running.')
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = 'PSI/SI Data Archiver is not running',
        )

    # StreamingResponse で読み取ったストリームデータをストリーミングする
    # LivePSIDataArchiver.getPSIArchivedData() は AsyncGenerator なので、そのまま渡せる
    response = StreamingResponse(live_stream.psi_data_archiver.getPSIArchivedData(request), media_type='application/octet-stream')

    # HTTP リクエストがキャンセルされたときに psisiarc を終了できるよう、StreamingResponse のインスタンスにモンキーパッチを当てる
    # モンキーパッチしている理由は LiveMPEGTSStreamAPI と同じ
    # ref: https://github.com/encode/starlette/pull/839
    async def listen_for_disconnect_monkeypatch(receive: Receive) -> None:
        try:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    # 上のループで HTTP リクエストの切断を検知できるようにしばらく待つ
                    await asyncio.sleep(5)
                    break
        except asyncio.CancelledError:
            pass
    response.listen_for_disconnect = listen_for_disconnect_monkeypatch

    return response


# ***** MPEG-TS ストリーミング API *****


@router.get(
    '/{display_channel_id}/{quality}/mpegts',
    summary = 'ライブ MPEG-TS ストリーム API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'ライブ MPEG-TS ストリーム。',
            'content': {'video/mp2t': {}},
        }
    }
)
async def LiveMPEGTSStreamAPI(
    request: Request,
    display_channel_id: Annotated[str, Depends(ValidateChannelID)],
    stream_quality: Annotated[LiveStreamQualityWithOptions, Depends(ValidateQuality)],
):
    """
    ライブ MPEG-TS ストリームを配信する。

    同じチャンネル ID 、同じ画質のライブストリームが Offline 状態のときは、新たにエンコードタスクを立ち上げて、
    ONAir 状態になるのを待機してからストリームデータを配信する。<br>
    同じチャンネル ID 、同じ画質のライブストリームが ONAir や Idling 状態のときは、新たにエンコードタスクを立ち上げることなく、他のクライアントとストリームデータを共有して配信する。

    何らかの理由でライブストリームが終了しない限り、継続的にレスポンスが出力される（ストリーミング）。
    """

    # Raw MMTS は BS4K/BS8K の TLV/MMT を変換せずブラウザへ渡す専用経路。
    # 通常の LiveStream Queue / LiveEncodingTask の共有管線を通すと Python/ASGI の per-chunk オーバーヘッドが増えるため、
    # Mirakurun の decode=0 ストリームをこのリクエスト専用に直接反代する。
    if stream_quality.quality == 'raw-mmts':
        session, response_upstream = await OpenMirakurunRawMMTSStream(display_channel_id)
        return StreamingResponse(
            GenerateMirakurunRawMMTSStream(request, session, response_upstream),
            media_type = 'video/mp2t',
            headers = {
                'Cache-Control': 'no-store',
                'X-Accel-Buffering': 'no',
            },
        )

    # 品質とオプション指定に対応する LiveStream に接続し、ライブストリームクライアントを取得する
    ## 接続時に Offline だった場合は自動的にエンコードタスクが起動される
    live_stream = LiveStream(display_channel_id, stream_quality.quality, stream_quality.encoding_options)
    live_stream_client = await live_stream.connect('mpegts')

    # ライブストリームを出力するジェネレーター
    async def generator():
        while True:

            # リクエストがキャンセル（切断）されている場合
            ## エンコードに失敗とかしない限り基本エンドレスで配信されるので、
            ## チャンネル変えたりやタブの再読み込みで必然的にリクエストがキャンセルされる
            if await request.is_disconnected():

                # ライブストリームへの接続を切断し、ループを終了する
                logging.debug(f'{live_stream.log_prefix} Request is disconnected.')
                live_stream.disconnect(live_stream_client)
                break

            if live_stream.getStatus().status != 'Offline':

                # クライアントが持つ Queue から読み取ったストリームデータ
                stream_data: bytes | None = await live_stream_client.readStreamData()

                # 読み取ったストリームデータを yield で随時出力する
                if stream_data is not None:
                    yield stream_data

                # stream_data に None が入った場合はエンコードタスクが終了し、接続が切断されたものとみなす
                else:

                    # ライブストリームへの接続を切断し、ループを終了する
                    logging.debug(f'{live_stream.log_prefix} Encode task is finished.')
                    live_stream.disconnect(live_stream_client)  # 必要ないとは思うけど念のため
                    break

            # ライブストリームが Offline になった場合もエンコードタスクが終了し、接続が切断されたものとみなす
            else:

                # ライブストリームへの接続を切断し、ループを終了する
                logging.debug(f'{live_stream.log_prefix} LiveStream is currently Offline.')
                live_stream.disconnect(live_stream_client)  # 必要ないとは思うけど念のため
                break

    # StreamingResponse で読み取ったストリームデータをストリーミングする
    response = StreamingResponse(generator(), media_type='video/mp2t')

    # HTTP リクエストがキャンセルされたときに自前でライブストリームの接続を切断できるよう、StreamingResponse のインスタンスにモンキーパッチを当てる
    ## Starlette の StreamingResponse は stream_response() と listen_for_disconnect() を TaskGroup で並行実行し、
    ## listen_for_disconnect() が完了すると cancel_scope.cancel() で stream_response() (ジェネレーター) を強制終了する
    ## デフォルトの listen_for_disconnect() は http.disconnect を受け取ると即座に完了するため、
    ## ジェネレーターが強制終了されて disconnect() が呼ばれず、client_count が減少しない問題があった
    ## これを避けるため listen_for_disconnect() を書き換え、http.disconnect の受信時点で即座に LiveStream.disconnect() を呼び出す
    ## LiveStream.disconnect() は二重呼び出しに安全なので、ジェネレーター側で重複して呼ばれても問題ない
    # ref: https://github.com/encode/starlette/pull/839
    async def listen_for_disconnect_monkeypatch(receive: Receive) -> None:
        try:
            while True:
                message = await receive()
                if message['type'] == 'http.disconnect':
                    # HTTP リクエストの切断を検知したら即座にライブストリームへの接続を切断する
                    ## こうすることで client_count が即座に減少し、チューナー再利用の判定が高速化される
                    logging.debug(f'{live_stream.log_prefix} Request is disconnected.')
                    live_stream.disconnect(live_stream_client)
                    break
        except asyncio.CancelledError:
            pass
    response.listen_for_disconnect = listen_for_disconnect_monkeypatch

    return response
