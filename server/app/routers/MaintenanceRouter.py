
import asyncio
import json
import os
import signal
import sys
import threading
from collections.abc import AsyncGenerator
from typing import Annotated, Literal, TextIO, cast

import anyio
import psutil
from fastapi import APIRouter, Depends, Path, Request, status
from fastapi.exceptions import HTTPException
from fastapi.responses import Response
from fastapi.security import OAuth2PasswordBearer
from sse_starlette.sse import EventSourceResponse

from app import logging, schemas
from app.config import Config
from app.constants import (
    KONOMITV_ACCESS_LOG_PATH,
    KONOMITV_SERVER_LOG_PATH,
    RESTART_REQUIRED_LOCK_PATH,
    THUMBNAILS_DIR,
)
from app.metadata.CMSectionsDetector import CMSectionsDetector
from app.metadata.RecordedScanTask import RecordedScanTask
from app.metadata.ThumbnailGenerator import ThumbnailGenerator
from app.models.Channel import Channel
from app.models.Program import Program
from app.models.RecordedProgram import RecordedProgram
from app.models.RecordedVideo import RecordedVideo
from app.models.User import User
from app.routers.UsersRouter import GetCurrentAdminUser, GetCurrentUser
from app.utils.DriveIOLimiter import DriveIOLimiter
from app.utils.ProcessLimiter import ProcessLimiter


# ルーター
router = APIRouter(
    tags = ['Maintenance'],
    prefix = '/api/maintenance',
)

# 録画フォルダの一括スキャン・バックグラウンド解析タスクの asyncio.Task インスタンス
batch_scan_task: asyncio.Task[None] | None = None
background_analysis_task: asyncio.Task[None] | None = None


async def GetCurrentAdminUserOrLocal(
    request: Request,
    token: Annotated[str | None, Depends(OAuth2PasswordBearer(tokenUrl='users/token', auto_error=False))],
) -> User | None:
    """
    現在管理者ユーザーでログインしているか、http://127.0.0.77:7010 からのアクセスであるかを確認する
    KonomiTV の Windows サービスからサーバーをシャットダウンするために必要
    """

    # HTTP リクエストの Host ヘッダーが 127.0.0.77:7010 である場合、Windows サービスプロセスからのアクセスと見なす
    ## 通常アクセス時の Host ヘッダーは 192-168-1-11.local.konomi.tv:7000 のような形式になる
    valid_host = f'127.0.0.77:{Config().server.port + 10}'
    if request.headers.get('host', '').strip() == valid_host:
        return None

    # それ以外である場合、管理者ユーザーでログインしているかを確認する
    if token is None:
        logging.error('[MaintenanceRouter][GetCurrentAdminUserOrLocal] Not authenticated.')
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = 'Not authenticated',
            headers = {'WWW-Authenticate': 'Bearer'},
        )
    return await GetCurrentAdminUser(await GetCurrentUser(token))


@router.get(
    '/logs/{log_type}',
    summary = 'サーバーログストリーミング API',
    response_class = Response,
    responses = {
        status.HTTP_200_OK: {
            'description': 'サーバーログまたはアクセスログが随時配信されるイベントストリーム。',
            'content': {'text/event-stream': {}},
        }
    }
)
async def LogStreamAPI(
    log_type: Annotated[Literal['server', 'access'], Path(description='ログの種類。server: サーバーログ、access: アクセスログ')],
    current_user: Annotated[User, Depends(GetCurrentAdminUser)],
):
    """
    サーバーログまたはアクセスログを Server-Sent Events で随時配信する。

    イベントには、
    - 初回にログファイルの先頭から現在の最新行までのすべての行を送信する **initial_log_update**
    - リアルタイムに追加されたログを送信する **log_update**
    の2種類がある。

    初回接続時にはログファイルの先頭から現在の最新行までのすべての行が initial_log_update イベントで一括送信され、<br>
    その後ログに更新があれば log_update イベントで1行ずつ送信される。

    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていて、かつ管理者アカウントでないとアクセスできない。
    """

    # ログファイルのパスを決定
    log_path = KONOMITV_SERVER_LOG_PATH if log_type == 'server' else KONOMITV_ACCESS_LOG_PATH

    # ログファイルが存在しない場合はエラー
    if not log_path.exists():
        logging.error(f'[MaintenanceRouter][LogStreamAPI] Log file not found: {log_path}')
        raise HTTPException(
            status_code = status.HTTP_404_NOT_FOUND,
            detail = f'Log file not found: {log_path}',
        )

    def OpenLogFile() -> TextIO:
        """
        ログファイルを開く

        ログファイルは基本 UTF-8 だが、稀に外部プロセス由来の文字化けや別エンコーディングが混入し、
        UTF-8 としてデコードできないバイト列が含まれることがある。
        その場合でもログストリームの配信を継続できるよう、errors='replace' でデコード不能なバイトは
        置換文字 (U+FFFD) に置き換えて読み取る。

        Returns:
            TextIO: 開いたログファイルのファイルオブジェクト
        """

        return open(log_path, encoding='utf-8', errors='replace')

    # ログの変更を監視し、変更があればログ行をイベントストリームとして出力する
    ## 同期ジェネレーターとして実装すると sse-starlette が iterate_in_threadpool() でラップするが、
    ## 下記の通り新しいログ行が書き込まれるまで次の要素を yield しないため、接続1本につき anyio のスレッドプール
    ## (既定で 40 スレッド) を無期限に1つ占有してしまう。ログ画面を開いたままの端末が増えると、同じスレッドプールを
    ## 使う他の同期エンドポイントが待たされるため、非同期ジェネレーターとして実装した上でファイル I/O のみスレッドへ逃がす
    async def generator() -> AsyncGenerator[dict[str, str], None]:
        """イベントストリームを出力する非同期ジェネレーター"""

        log_file = await asyncio.to_thread(OpenLogFile)
        try:
            def ReadAllLines() -> tuple[list[str], int]:
                """ログファイルを先頭から最後まで読み込み、読み込んだ行と読み込み後のファイル位置を返す"""
                all_lines = [line.rstrip('\n') for line in log_file.readlines() if line.strip()]  # 空行は除外
                return all_lines, log_file.tell()

            def ReadNewLines(position: int) -> tuple[list[str], int]:
                """前回の読み込み位置以降に追記された行と、読み込み後のファイル位置を返す"""
                # ファイルが更新されたかチェック
                log_file.seek(0, os.SEEK_END)
                if log_file.tell() <= position:
                    return [], position
                # ファイルが更新された場合、前回の位置に戻って新しい行を読み込む
                log_file.seek(position)
                new_lines: list[str] = []
                for new_line in log_file:
                    new_line = new_line.rstrip('\n')
                    if new_line:  # 空行は送信しない
                        new_lines.append(new_line)
                return new_lines, log_file.tell()

            # 初回接続時に全ての行を送信
            all_lines, current_position = await asyncio.to_thread(ReadAllLines)
            yield {
                'event': 'initial_log_update',
                'data': json.dumps(all_lines, ensure_ascii=False),
            }

            # 継続的に新しい行を監視
            while True:
                new_lines, current_position = await asyncio.to_thread(ReadNewLines, current_position)
                for line in new_lines:
                    yield {
                        'event': 'log_update',
                        'data': json.dumps(line, ensure_ascii=False),
                    }

                # 少し待機
                await asyncio.sleep(0.5)
        finally:
            # クライアント切断時にジェネレーターが閉じられた場合も、ファイルディスクリプタを確実に解放する
            ## 非同期ジェネレーターの終了時は GeneratorExit が送出されるため、ここで await するとイベントループへ制御が戻り
            ## 「async generator ignored GeneratorExit」となってしまう。読み取り専用で開いたファイルの close() は
            ## ディスクへの書き戻しを伴わず即座に完了するため、同期のまま閉じる
            log_file.close()

    # EventSourceResponse でイベントストリームを配信する
    return EventSourceResponse(generator())


@router.post(
    '/update-database',
    summary = 'データベース更新 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def UpdateDatabaseAPI():
    """
    データベースに保存されている、チャンネル情報・番組情報・Twitter アカウント情報などの外部 API に依存するデータをすべて更新する。<br>
    即座に外部 API からのデータ更新を反映させたい場合に利用する。<br>
    このメンテナンス機能は管理者ユーザーでなくてもアクセスできる。
    """

    await Channel.update()
    await Channel.updateJikkyoStatus()
    await Program.update(multiprocess=True)


@router.post(
    '/run-batch-scan',
    summary = '録画フォルダ一括スキャン API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def BatchScanAPI():
    """
    録画フォルダ内の全 TS ファイルをスキャンし、メタデータを解析して DB に永続化する。<br>
    追加・変更があったファイルのみメタデータを解析し、DB に永続化する。<br>
    存在しない録画ファイルに対応するレコードを一括削除する。<br>
    このメンテナンス機能は管理者ユーザーでなくてもアクセスできる。
    """

    global batch_scan_task

    async def BatchScan():
        global batch_scan_task
        logging.info('Manual batch scan of recording folders has started.')

        # 一括スキャンを実行
        await RecordedScanTask().runBatchScan()

        # 一括スキャンが完了した
        logging.info('Manual batch scan of recording folders has finished.')
        batch_scan_task = None  # 再度新しいタスクを作成できるように None にする

    # タスクが実行中でない場合、新しくタスクを作成して実行
    ## asyncio.create_task() で実行することで、API への HTTP コネクションが切断されてもタスクが継続される
    if batch_scan_task is None:
        batch_scan_task = asyncio.create_task(BatchScan())
    else:
        logging.warning('[MaintenanceRouter][BatchScanAPI] Batch scan of recording folders is already running.')
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail = 'Batch scan of recording folders is already running',
        )


@router.post(
    '/scan-file',
    summary = '録画ファイル手動スキャン API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def ManualScanFileAPI(
    request: schemas.ManualScanRequest,
    current_user: Annotated[User, Depends(GetCurrentAdminUser)],
):
    """
    指定されたパスの録画ファイルを手動でスキャンし、メタデータを解析して DB に永続化する。<br>
    force_update=True で既存レコードの強制更新を行う。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていて、かつ管理者アカウントでないとアクセスできない。
    """

    # RecordedScanTask のインスタンスを取得し、指定されたファイルをスキャン
    scan_task = RecordedScanTask()
    await scan_task.scanSingleFile(request.path, force_update=True)


@router.post(
    '/run-background-analysis',
    summary = 'バックグラウンド解析タスク手動実行 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def BackgroundAnalysisAPI():
    """
    CM 区間情報が未解析の録画ファイルに対して CM 区間情報を解析し、<br>
    サムネイルが未生成の録画ファイルに対してサムネイルを生成する。<br>
    このメンテナンス機能は管理者ユーザーでなくてもアクセスできる。
    """

    global background_analysis_task

    async def BackgroundAnalysis():
        global background_analysis_task
        logging.info('Manual background analysis has started.')

        # CM 区間情報やサムネイルが未生成の録画ファイルを取得
        ## 再生開始位置はオンデマンドで解決できるため、重い key_frames は取得しない
        video_rows = await RecordedVideo.filter(status='Recorded').values(
            'id',
            'recorded_program_id',
            'file_path',
            'file_hash',
            'duration',
            'container_format',
            'cm_sections',
        )

        # 手動処理も自動解析・サムネイル再生成と同じ枠を使い、同時起動で負荷が増えないようにする。
        for video_row in video_rows:
            file_path = anyio.Path(video_row['file_path'])
            try:
                if not await file_path.is_file():
                    logging.warning(f'{file_path}: File not found. Skipping...')
                    continue

                async with ProcessLimiter.getSemaphore('RecordedScanTask', max_concurrency=1):
                    async with await DriveIOLimiter.getSemaphore(file_path):
                        # CM 区間情報が未解析の場合だけ検出する。
                        ## [] は「正常に解析したが CM 区間がなかった」ことを表す。
                        container_format = cast(Literal['MPEG-TS', 'MPEG-4', 'MMT/TLV'], video_row['container_format'])
                        if (
                            video_row['cm_sections'] is None and
                            CMSectionsDetector.shouldAnalyze(
                                container_format,
                                Config().video.enable_mmt_tlv_cm_analysis,
                            )
                        ):
                            db_recorded_program = await RecordedProgram.all() \
                                .select_related('recorded_video') \
                                .get_or_none(id=video_row['recorded_program_id'])
                            await CMSectionsDetector(
                                file_path = file_path,
                                duration_sec = video_row['duration'],
                                container_format = container_format,
                                service_id = db_recorded_program.service_id if db_recorded_program is not None else None,
                            ).detectAndSave()

                        # どちらかのサムネイルが未生成の場合は再生成する。
                        thumbnail_tile_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{video_row["file_hash"]}_tile.webp'
                        thumbnail_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{video_row["file_hash"]}.webp'
                        if (not await thumbnail_tile_path.is_file()) or (not await thumbnail_path.is_file()):
                            db_recorded_program = await RecordedProgram.all() \
                                .select_related('recorded_video') \
                                .select_related('channel') \
                                .get_or_none(id=video_row['recorded_program_id'])
                            if db_recorded_program is not None:
                                recorded_program = schemas.RecordedProgram.model_validate(db_recorded_program, from_attributes=True)
                                await ThumbnailGenerator.fromRecordedProgram(recorded_program).generateAndSave()

            except Exception as ex:
                logging.error(f'{file_path}: Error in background analysis:', exc_info=ex)
                continue

        # すべての録画ファイルのバックグラウンド解析が完了した
        logging.info('Manual background analysis has finished processing all recorded files.')
        background_analysis_task = None  # 再度新しいタスクを作成できるように None にする

    # タスクが実行中でない場合、新しくタスクを作成して実行
    ## asyncio.create_task() で実行することで、API への HTTP コネクションが切断されてもタスクが継続される
    if background_analysis_task is None:
        background_analysis_task = asyncio.create_task(BackgroundAnalysis())
    else:
        logging.warning('[MaintenanceRouter][BackgroundAnalysisAPI] Background analysis task is already running.')
        raise HTTPException(
            status_code = status.HTTP_429_TOO_MANY_REQUESTS,
            detail = 'Background analysis task is already running',
        )


@router.post(
    '/restart',
    summary = 'サーバー再起動 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
def ServerRestartAPI(
    current_user: Annotated[User | None, Depends(GetCurrentAdminUserOrLocal)],
):
    """
    KonomiTV サーバーを再起動する。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていて、かつ管理者アカウントでないとアクセスできない。
    """

    def Restart():

        # シグナルの送信対象の PID
        ## --reload フラグが付与されている場合のみ、Reloader の起動元である親プロセスの PID を利用する
        target_process = psutil.Process(os.getpid())
        if '--reload' in sys.argv:
            parent_process = target_process.parent()
            if parent_process is not None:
                target_process = parent_process

        # 現在の Uvicorn サーバーを終了する
        if sys.platform == 'win32':
            target_process.send_signal(signal.CTRL_C_EVENT)
        else:
            target_process.send_signal(signal.SIGINT)

        # Uvicorn 終了後に再起動が必要であることを示すロックファイルを作成する
        # Uvicorn 終了後、KonomiTV.py でロックファイルの存在が確認され、もし存在していればサーバー再起動が行われる
        RESTART_REQUIRED_LOCK_PATH.touch(exist_ok=True)

    # バックグラウンドでサーバー再起動を開始
    threading.Thread(target=Restart).start()


@router.post(
    '/shutdown',
    summary = 'サーバー終了 API',
    status_code = status.HTTP_204_NO_CONTENT,
)
def ServerShutdownAPI(
    current_user: Annotated[User | None, Depends(GetCurrentAdminUserOrLocal)],
):
    """
    KonomiTV サーバーを終了する。<br>
    なお、PM2 環境 / Docker 環境ではサーバー終了後に自動的にプロセスが再起動されるため、事実上 /api/maintenance/restart と等価。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていて、かつ管理者アカウントでないとアクセスできない。
    """

    def Shutdown():

        # シグナルの送信対象の PID
        ## --reload フラグが付与されている場合のみ、Reloader の起動元である親プロセスの PID を利用する
        target_process = psutil.Process(os.getpid())
        if '--reload' in sys.argv:
            parent_process = target_process.parent()
            if parent_process is not None:
                target_process = parent_process

        # 現在の Uvicorn サーバーを終了する
        if sys.platform == 'win32':
            target_process.send_signal(signal.CTRL_C_EVENT)
        else:
            target_process.send_signal(signal.SIGINT)

    # バックグラウンドでサーバー終了を開始
    threading.Thread(target=Shutdown).start()


@router.post(
    '/test-notification',
    summary = '通知設定テスト API',
    status_code = status.HTTP_204_NO_CONTENT,
)
async def TestNotificationAPI():
    """
    最新の録画ファイル1件でテスト通知を送信する。<br>
    通知設定が正しく機能しているか確認するために使用。<br>
    JWT エンコードされたアクセストークンがリクエストの Authorization: Bearer に設定されていて、かつ管理者アカウントでないとアクセスできない。
    """

    from app.utils.NotificationService import NotificationManager

    # 最新の録画を取得
    db_recorded_program = await RecordedProgram.all() \
        .select_related('recorded_video') \
        .select_related('channel') \
        .order_by('-id').first()

    if db_recorded_program is None:
        raise HTTPException(
            status_code = status.HTTP_404_NOT_FOUND,
            detail = 'No recorded programs found for testing',
        )

    # 通知サービスが設定されているかチェック
    config = Config()
    if len(config.notifications.services) == 0:
        raise HTTPException(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail = 'No notification services are configured',
        )

    # 有効な通知サービスがあるかチェック
    enabled_services = [svc for svc in config.notifications.services if svc.enabled]
    if len(enabled_services) == 0:
        raise HTTPException(
            status_code = status.HTTP_400_BAD_REQUEST,
            detail = 'No notification services are enabled',
        )

    # RecordedProgram モデルを schemas.RecordedProgram に変換
    recorded_program = schemas.RecordedProgram.model_validate(db_recorded_program, from_attributes=True)

    # テスト通知を送信
    notification_manager = NotificationManager(config.notifications.services)
    try:
        await notification_manager.send_test(recorded_program)
    except Exception as ex:
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = f'Failed to send test notification: {ex!s}',
        )
