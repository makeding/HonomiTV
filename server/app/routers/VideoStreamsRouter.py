
import asyncio
import json
import pathlib
import struct
import uuid
from typing import Annotated, Literal
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import Response, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from starlette.background import BackgroundTask

from app import logging
from app.constants import QUALITY
from app.metadata.RecordedScanTask import (
    RecordedFileMetadataNotStableError,
    RecordedFileMetadataRefreshError,
    RecordedScanTask,
)
from app.models.RecordedProgram import RecordedProgram
from app.schemas import OfflineVideoStreamMetadata
from app.streams.StreamEncodingOptions import (
    SplitQualityAndEncodingOptions,
    StreamQualityWithOptions,
)
from app.streams.VideoSourceTimeline import VideoSourceTimelineResolver
from app.streams.VideoStream import VideoStream
from app.utils.DisconnectAwareFileResponse import DisconnectAwareFileResponse


# ルーター
router = APIRouter(
    tags = ['Streams'],
    prefix = '/api/streams/video',
)

# オフライン保存はエンコーダーを長時間占有するため、通常視聴分の余裕を残して同時実行数を制限する
OFFLINE_VIDEO_STREAM_SEMAPHORE = asyncio.Semaphore(3)


async def ValidateVideoID(video_id: Annotated[int, Path(description='録画番組の ID 。')]) -> RecordedProgram:
    """ 録画番組 ID のバリデーション """

    # 指定された video_id が存在するか確認
    recorded_program = await RecordedProgram.filter(id=video_id).get_or_none() \
        .select_related('recorded_video') \
        .select_related('channel')
    # 録画ファイルが見つからなくなった (Missing の) 録画番組は再生できないので、存在しない録画番組と同じく扱う
    if recorded_program is None or recorded_program.recorded_video.status == 'Missing':
        logging.error(f'[VideoStreamsRouter][ValidateVideoID] Specified video_id was not found. [video_id: {video_id}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified video_id was not found',
        )

    # 詳細 API を経由せずストリームへ直接アクセスした場合も、古いコーデック情報でセッションを生成しないよう同期する。
    ## 同じファイルの並行リクエストは RecordedScanTask 側で 1 個の再解析タスクへ合流する。
    try:
        is_refreshed = await RecordedScanTask().refreshRecordedFileMetadataIfNeeded(recorded_program.recorded_video)
    except RecordedFileMetadataNotStableError as ex:
        logging.warning(
            f'[VideoStreamsRouter][ValidateVideoID] Recorded file is still being updated. [video_id: {video_id}]'
        )
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = 'Recorded video file is still being updated. Please retry shortly.',
            headers = {'Retry-After': str(RecordedScanTask.RECORDING_COMPLETE_SECONDS)},
        ) from ex
    except RecordedFileMetadataRefreshError as ex:
        logging.error(
            f'[VideoStreamsRouter][ValidateVideoID] Failed to refresh recorded file metadata. [video_id: {video_id}]',
            exc_info = ex,
        )
        raise HTTPException(
            status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
            detail = 'Failed to refresh recorded video metadata. Please retry shortly.',
            headers = {'Retry-After': '5'},
        ) from ex

    # 再解析後の ORM インスタンスを返し、新しく生成される VideoStream が必ず最新の技術情報を保持するようにする。
    if is_refreshed is True:
        refreshed_recorded_program = await RecordedProgram.filter(id=video_id).get_or_none() \
            .select_related('recorded_video') \
            .select_related('channel')
        if refreshed_recorded_program is None:
            raise HTTPException(
                status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail = 'Specified video_id was not found after metadata refresh',
            )
        recorded_program = refreshed_recorded_program

    return recorded_program


async def ValidateQuality(quality: Annotated[str, Path(description='映像の品質。ex: 1080p')]) -> StreamQualityWithOptions:
    """ 映像の品質のバリデーション """

    # 指定された品質が存在するか確認
    ## 品質の指定に -10bit や -24fps が付いていれば分解する
    stream_quality = SplitQualityAndEncodingOptions(quality)
    if stream_quality is None:
        logging.error(f'[VideoStreamsRouter][ValidateQuality] Specified quality was not found. [quality: {quality}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified quality was not found',
        )

    # 指定された画質が "original" の場合、HLS プレイリストではオリジナル画質で配信できないのでエラーにする
    if stream_quality.quality == 'original':
        logging.error(f'[VideoStreamsRouter][ValidateQuality] Original quality is not available for HLS playlist. [quality: {quality}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Original quality is not available for HLS playlist',
        )

    return stream_quality


def ValidateVideoCopyQuality(recorded_program: RecordedProgram, stream_quality: StreamQualityWithOptions) -> None:
    """
    再エンコードなしの HLS 再多重化を利用できる録画か検証する

    Args:
        recorded_program (RecordedProgram): 配信対象の録画番組
        stream_quality (StreamQualityWithOptions): リクエストされた録画ストリーミング品質

    Returns:
        None
    """

    # 通常の再エンコード品質では追加の制約を設けない
    if stream_quality.quality != 'copy':
        return

    recorded_video = recorded_program.recorded_video
    is_copy_compatible = recorded_video.status == 'Recorded' and (
        recorded_video.container_format == 'MMT/TLV' or (
            recorded_video.container_format == 'MPEG-TS' and
            recorded_video.video_codec in ['H.264', 'H.265'] and
            recorded_video.video_scan_type == 'Progressive' and
            recorded_video.has_video_stream_changes is False
        )
    )
    if is_copy_compatible is False:
        logging.error(
            f'[VideoStreamsRouter][ValidateVideoCopyQuality] Specified video is not compatible with stream copy. '
            f'[video_id: {recorded_program.id}, status: {recorded_video.status}, '
            f'container_format: {recorded_video.container_format}, video_codec: {recorded_video.video_codec}, '
            f'video_scan_type: {recorded_video.video_scan_type}, '
            f'has_video_stream_changes: {recorded_video.has_video_stream_changes}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified video is not compatible with stream copy',
        )


@router.get(
    '/{video_id}/raw-mmts/mpegts',
    summary = '録画番組 Raw MMTS ストリーム API',
    response_class = DisconnectAwareFileResponse,
    responses = {
        status.HTTP_200_OK: {
            'description': '録画ファイルに保存されている MMT/TLV データ。',
            'content': {'video/mp2t': {}},
        },
        status.HTTP_422_UNPROCESSABLE_ENTITY: {
            'description': '指定された録画番組は Raw MMTS 直通配信に対応していない。',
        },
    },
)
async def VideoRawMMTSStreamAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateVideoID)],
):
    """
    MMT/TLV 形式で保存されている録画ファイルを、変換せずにそのまま配信する。<br>
    ブラウザ側では mpegts.js の MMTS demuxer がこのストリームを直接解析する。
    """

    # Raw MMTS 直通配信は MMT/TLV の録画ファイル専用の経路
    # MPEG-TS / MPEG-4 は既存の HLS エンコード経路で扱う
    if recorded_program.recorded_video.container_format != 'MMT/TLV':
        logging.error(
            f'[VideoStreamsRouter][VideoRawMMTSStreamAPI] Specified video_id is not an MMT/TLV file. '
            f'[video_id: {recorded_program.id}, container_format: {recorded_program.recorded_video.container_format}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified video is not an MMT/TLV file',
        )

    # 録画ファイルの存在を確認する
    file_path = pathlib.Path(recorded_program.recorded_video.file_path)
    if file_path.is_file() is False:
        logging.error(
            f'[VideoStreamsRouter][VideoRawMMTSStreamAPI] Recorded video file was not found. '
            f'[video_id: {recorded_program.id}, file_path: {file_path}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Recorded video file was not found',
        )

    return DisconnectAwareFileResponse(
        path = file_path,
        media_type = 'video/mp2t',
        headers = {
            'Cache-Control': 'no-store',
        },
    )


@router.get(
    '/{video_id}/{quality}/playlist',
    summary = '録画番組 HLS M3U8 プレイリスト API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': '録画番組の HLS M3U8 プレイリスト。',
            'content': {'application/vnd.apple.mpegurl': {}},
        }
    }
)
async def VideoHLSPlaylistAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateVideoID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
    cache_key: Annotated[str | None, Query(description='キャッシュ制御用のキー。')] = None,
    is_recording: Annotated[bool, Query(alias='recording', description='録画追いかけ再生中かどうか。')] = False,
    playlist_type: Annotated[
        Literal['master', 'primary-audio', 'secondary-audio'],
        Query(alias='type', description='プレイリストの種類。'),
    ] = 'primary-audio',
):
    """
    指定された画質に対応する、録画番組のストリーミング用 HLS M3U8 プレイリストを返す。<br>
    この M3U8 プレイリストは仮想的なもので、すべてのセグメントデータがエンコード済みとは限らない。セグメントはリクエストされ次第随時生成される。
    """

    # MPEG-TS パススルー品質では、元映像をそのまま配信できる録画だけを受け付ける
    ValidateVideoCopyQuality(recorded_program, stream_quality)

    # 品質とオプション指定に対応する録画視聴セッションを作成または取得
    assert stream_quality.quality != 'original'
    video_stream = VideoStream(
        session_id,
        recorded_program,
        stream_quality.quality,
        stream_quality.encoding_options,
        is_new_session_allowed = True,
    )

    # 中断した MMT/TLV 録画をそのまま連結すると libaribtlv の demux 状態が壊れるため、
    ## HLS（オリジナル）の各入力区間を別々に再多重化する仮想時間軸を構成する。
    ## 原始 TLV のダウンロード API はこの経路を通らず、従来どおり単一ファイルだけを返す。
    if (
        stream_quality.quality == 'copy' and
        recorded_program.recorded_video.container_format == 'MMT/TLV'
    ):
        source_recorded_programs = [
            source_recorded_program
            for source_recorded_program in await VideoSourceTimelineResolver.findCandidates(recorded_program)
            if source_recorded_program.recorded_video.container_format == 'MMT/TLV'
        ]
        if recorded_program.is_partially_recorded is True or len(source_recorded_programs) > 1:
            source_timeline = VideoSourceTimelineResolver.buildFromRecordedPrograms(
                recorded_program,
                source_recorded_programs,
            )
            video_stream.configureSourceTimeline(source_timeline, source_recorded_programs)

    # master playlist はエンコード済み TS に常に主/副音声を含める経路だけに公開する。
    # copy 品質も同じ TS 出力を経由し、tsreadex または FFmpeg の無音 AAC 入力で副音声を必ず補完する。
    if playlist_type == 'master':
        quality = stream_quality.quality
        # copy は入力ビットレートを固定値から導けないため、BS4K も上回る安全側の帯域上限を宣言する。
        if quality == 'copy':
            bandwidth = 50_000_000
        else:
            quality_setting = QUALITY[quality]
            video_bitrate = int(quality_setting.video_bitrate_max.removesuffix('K')) * 1000
            audio_bitrate = int(quality_setting.audio_bitrate.removesuffix('K')) * 1000
            bandwidth = round((video_bitrate + audio_bitrate * 2) * 1.1)
        # 子 playlist も親と同じ追いかけ再生の許可・キャッシュ世代で開かなければ、master URL 経由で録画中視聴が切れる。
        playlist_query = {'session_id': session_id}
        if cache_key is not None:
            playlist_query['cache_key'] = cache_key
        if is_recording is True:
            playlist_query['recording'] = '1'
        playlist_uri = f'playlist?{urlencode(playlist_query)}'
        virtual_playlist = '#EXTM3U\n#EXT-X-VERSION:6\n'
        virtual_playlist += '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="主音声",DEFAULT=YES,AUTOSELECT=YES,LANGUAGE="jpn"\n'
        virtual_playlist += f'#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="副音声",DEFAULT=NO,AUTOSELECT=YES,LANGUAGE="jpn",URI="{playlist_uri}&type=secondary-audio"\n'
        virtual_playlist += f'#EXT-X-STREAM-INF:BANDWIDTH={bandwidth},AUDIO="audio"\n'
        virtual_playlist += f'{playlist_uri}&type=primary-audio\n'
    else:
        audio = 'secondary' if playlist_type == 'secondary-audio' else 'primary'
        virtual_playlist = await video_stream.getVirtualPlaylist(cache_key, audio)
    return Response(
        content = virtual_playlist,
        media_type = 'application/vnd.apple.mpegurl',
        headers = {
            'Cache-Control': 'max-age=0',
        },
    )


@router.get(
    '/{video_id}/{quality}/segment',
    summary = '録画番組 HLS セグメント API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'HLS セグメントとして分割された MPEG-TS データ。',
            'content': {'video/mp2t': {}},
        }
    }
)
async def VideoHLSSegmentAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateVideoID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
    sequence: Annotated[int, Query(description='HLS セグメントの 0 スタートのシーケンス番号。')],
    cache_key: Annotated[str | None, Query(description='キャッシュ制御用のキー。')],
    audio: Annotated[Literal['primary', 'secondary'], Query(description='音声トラック。')] = 'primary',
):
    """
    指定された画質に対応する、録画番組のストリーミング用 HLS セグメントを返す。<br>
    呼び出された時点でエンコードされていない場合は既存のエンコードタスクが終了され、<br>
    sequence の HLS セグメントが含まれる範囲から新たにエンコードタスクが開始される。
    """

    # MPEG-TS パススルー品質では、元映像をそのまま配信できる録画だけを受け付ける
    ValidateVideoCopyQuality(recorded_program, stream_quality)

    # 品質とオプション指定に対応する録画視聴セッションを取得
    assert stream_quality.quality != 'original'
    video_stream = VideoStream(session_id, recorded_program, stream_quality.quality, stream_quality.encoding_options)

    # セグメントを取得（キャッシュキーはブラウザキャッシュ避けのための ID なので特に使わない）
    try:
        segment_data = await video_stream.getSegment(sequence, audio)
    except HTTPException:
        # 録画追いかけ再生の待機、gap、上限超過など、既存の HTTP 契約はそのまま返す。
        raise
    except ValueError as ex:
        logging.error(
            f'{video_stream.log_prefix} Failed to get segment. [sequence: {sequence}, audio: {audio}]',
            exc_info=ex,
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f'Failed to get segment. [audio: {audio}, reason: {ex}]',
        ) from ex
    except RuntimeError as ex:
        logging.error(
            f'{video_stream.log_prefix} Failed to generate segment. [sequence: {sequence}, audio: {audio}]',
            exc_info=ex,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f'Failed to generate segment. [audio: {audio}, reason: {ex}]',
        ) from ex
    if segment_data is None:
        logging.error(
            f'{video_stream.log_prefix} Specified sequence segment was not found. '
            f'[sequence: {sequence}]'
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified sequence segment was not found',
        )

    # 取得した MPEG-TS データを返す
    return Response(
        content = segment_data,
        media_type = 'video/mp2t',
        headers = {
            # キャッシュ有効期間を3時間に設定
            'Cache-Control': 'max-age=10800',
        },
    )


@router.get(
    '/{video_id}/{quality}/buffer',
    summary = '録画番組 HLS バッファ範囲 API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': '録画番組の HLS バッファ範囲が随時配信されるイベントストリーム。',
            'content': {'text/event-stream': {}},
        }
    }
)
async def VideoHLSBufferAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateVideoID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
):
    """
    録画番組の HLS バッファ範囲を Server-Sent Events で随時配信する。

    イベントには、
    - バッファ範囲の更新を示す **buffer_range_update**
    の1種類がある。

    どのイベントでも配信される JSON 構造は同じ。<br>
    エンコードタスクが終了した場合は、接続を終了する。
    """

    # MPEG-TS パススルー品質では、元映像をそのまま配信できる録画だけを受け付ける
    ValidateVideoCopyQuality(recorded_program, stream_quality)

    # 品質とオプション指定に対応する録画視聴セッションを取得
    assert stream_quality.quality != 'original'
    video_stream = VideoStream(session_id, recorded_program, stream_quality.quality, stream_quality.encoding_options)

    # バッファ範囲の変更を監視し、変更があればバッファ範囲をイベントストリームとして出力する
    async def generator():
        """イベントストリームを出力するジェネレーター"""

        # 初期値
        previous_buffer_range = video_stream.getBufferRange()

        # 初回接続時に必ず現在のバッファ範囲を返す
        yield {
            'event': 'buffer_range_update',  # buffer_range_update イベントを設定
            'data': json.dumps({
                'begin': previous_buffer_range[0],
                'end': previous_buffer_range[1],
            }),
        }

        while True:

            # 現在のバッファ範囲を取得
            buffer_range = video_stream.getBufferRange()

            # 以前の結果と異なっている場合のみレスポンスを返す
            if previous_buffer_range != buffer_range:
                logging.info(f'{video_stream.log_prefix} Buffer range updated. [begin: {buffer_range[0]}, end: {buffer_range[1]}]')
                yield {
                    'event': 'buffer_range_update',  # buffer_range_update イベントを設定
                    'data': json.dumps({
                        'begin': buffer_range[0],
                        'end': buffer_range[1],
                    }),
                }

                # 取得結果を保存
                previous_buffer_range = buffer_range

            # ビジーにならないように、0.1秒ごとにチェックする
            await asyncio.sleep(0.1)

    # EventSourceResponse でイベントストリームを配信する
    return EventSourceResponse(generator())


@router.put(
    '/{video_id}/{quality}/keep-alive',
    summary = '録画番組 HLS Keep-Alive API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def VideoHLSKeepAliveAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateVideoID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    session_id: Annotated[str, Query(description='セッション ID（クライアント側で適宜生成したランダム値を指定する）。')],
):
    """
    録画番組のストリーミング用 HLS セグメントの生成を継続するための API 。<br>
    ストリーミングセッションを維持するために、この API は録画番組の視聴を続けている間、定期的に呼び出さなければならない。<br>
    この API が定期的に呼び出されなくなった場合、一定時間後にストリーミング用 HLS セグメントの生成が停止され、メモリ上のデータが破棄される。
    """

    # MPEG-TS パススルー品質では、元映像をそのまま配信できる録画だけを受け付ける
    ValidateVideoCopyQuality(recorded_program, stream_quality)

    # 品質とオプション指定に対応する録画視聴セッションを取得
    assert stream_quality.quality != 'original'
    video_stream = VideoStream(session_id, recorded_program, stream_quality.quality, stream_quality.encoding_options)

    # セッションのアクティブ状態を維持する
    video_stream.keepAlive()


@router.get(
    '/{video_id}/{quality}/offline-stream',
    summary = '録画番組オフライン保存ストリーム API',
    response_class = StreamingResponse,
    responses = {
        status.HTTP_200_OK: {
            'description': 'オフライン保存用のメタデータと HLS セグメントを格納したバイナリストリーム。',
            'content': {'application/octet-stream': {}},
        },
        status.HTTP_409_CONFLICT: {
            'description': '録画中のためオフライン保存を開始できない。',
        },
    },
)
async def VideoOfflineStreamAPI(
    recorded_program: Annotated[RecordedProgram, Depends(ValidateVideoID)],
    stream_quality: Annotated[StreamQualityWithOptions, Depends(ValidateQuality)],
    quality: Annotated[str, Path(description='映像の品質。ex: 720p-hevc-10bit-24fps')],
) -> StreamingResponse:
    """
    録画番組を指定画質で先頭から順にエンコードし、オフライン保存用の単一ストリームとして返す。<br>
    応答は `KTVODLP` のヘッダーから始まる独自バイナリ形式で、長さ付き JSON メタデータ、長さ付き MPEG-TS セグメント、終端レコードの順に格納される。
    """

    # 追いかけ再生中のファイルは終端とハッシュが変化するため、録画完了後だけ保存を許可する
    if recorded_program.recorded_video.status == 'Recording':
        logging.error(f'[VideoOfflineStreamAPI] Recording video cannot be saved for offline playback. [video_id: {recorded_program.id}]')
        raise HTTPException(
            status_code = status.HTTP_409_CONFLICT,
            detail = 'Recording video cannot be saved for offline playback',
        )

    # copy 品質はコンテナを HLS 向け MPEG-TS へ詰め替えるだけなので、対応する録画形式かを通常再生と同じ条件で検証する
    ValidateVideoCopyQuality(recorded_program, stream_quality)

    # 待機中のリクエストは HTTP 応答を開始せず、クライアント側で Waiting と表示できる状態を維持する
    await OFFLINE_VIDEO_STREAM_SEMAPHORE.acquire()
    video_stream: VideoStream | None = None
    try:
        # 通常再生とは独立したセッションを作り、仮想プレイリスト生成によって全セグメント情報を初期化する
        session_id = f'offline-{uuid.uuid4().hex}'
        assert stream_quality.quality != 'original'
        video_stream = VideoStream(
            session_id,
            recorded_program,
            stream_quality.quality,
            stream_quality.encoding_options,
            is_new_session_allowed = True,
        )
        await video_stream.getVirtualPlaylist()

        # Pydantic を通して、クライアントへ渡す JSON の型とフィールドを固定する
        metadata = OfflineVideoStreamMetadata(
            video_id = recorded_program.id,
            file_hash = recorded_program.recorded_video.file_hash,
            quality = quality,
            duration_seconds = recorded_program.recorded_video.duration,
        ).model_dump_json().encode('utf-8')
    except Exception:
        # StreamingResponse を返す前の失敗ではジェネレーターの finally が動かないため、ここで実行枠を返す
        if video_stream is not None:
            await video_stream.destroy()
        OFFLINE_VIDEO_STREAM_SEMAPHORE.release()
        raise

    is_stream_cleaned_up = False

    async def CleanupOfflineVideoStream() -> None:
        """オフライン保存用ストリームと同時実行枠を解放する。"""
        nonlocal is_stream_cleaned_up
        # 応答本文の finally と StreamingResponse の終了処理から重複して呼ばれるため、最初の1回だけ解放する
        if is_stream_cleaned_up is True:
            return
        is_stream_cleaned_up = True
        await video_stream.destroy()
        OFFLINE_VIDEO_STREAM_SEMAPHORE.release()

    async def GenerateOfflineVideoStream():
        """オフライン保存用のバイナリストリームを生成する。"""

        # 長い1セグメントのエンコード中も10秒のセッションタイムアウトを迎えないよう、視聴画面と同じ周期で維持する
        async def KeepVideoStreamAlive() -> None:
            """ダウンロード中の録画視聴セッションを維持する。"""
            while True:
                await asyncio.sleep(5)
                video_stream.keepAlive()

        keep_alive_task = asyncio.create_task(KeepVideoStreamAlive())
        try:
            # 固定マジック値とメタデータ長により、任意のネットワーク分割位置から同じ規則で復元できる
            yield b'KTVODLP\n'
            yield struct.pack('>I', len(metadata))
            yield metadata

            # VideoStream が連続生成したセグメントを順番どおり読み、各データを独立して CacheStorage へ格納できる単位にする
            for segment in video_stream.segments:
                segment_data = await video_stream.getSegment(segment.sequence_index)
                if segment_data is None or len(segment_data) == 0:
                    logging.error(f'[VideoOfflineStreamAPI] Offline segment generation failed. [sequence: {segment.sequence_index}]')
                    raise RuntimeError(f'Offline segment generation failed. [sequence: {segment.sequence_index}]')
                duration_milliseconds = max(1, round(segment.duration_seconds * 1000))
                yield struct.pack('>III', segment.sequence_index, duration_milliseconds, len(segment_data))
                yield segment_data

            # 終端レコードには件数を格納し、通信切断による末尾欠落をクライアント側で検出できるようにする
            yield struct.pack('>II', 0xffffffff, len(video_stream.segments))
        finally:
            # 応答完了・切断・例外のすべてで維持タスクとエンコーダーを終了し、次の保存へ実行枠を返す
            keep_alive_task.cancel()
            await asyncio.gather(keep_alive_task, return_exceptions=True)
            await CleanupOfflineVideoStream()

    return StreamingResponse(
        GenerateOfflineVideoStream(),
        media_type = 'application/octet-stream',
        headers = {
            'Cache-Control': 'no-store',
            'X-Content-Type-Options': 'nosniff',
        },
        # クライアントが応答開始直後に切断し、ジェネレーター本体が一度も実行されない場合も必ず解放する
        background = BackgroundTask(CleanupOfflineVideoStream),
    )
