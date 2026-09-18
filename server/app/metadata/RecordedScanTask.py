
from __future__ import annotations

import asyncio
import concurrent.futures
import pathlib
import weakref
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import ClassVar, Literal, cast

import anyio
from fastapi import HTTPException, status
from tortoise import transactions
from tortoise.exceptions import IntegrityError
from watchfiles import Change, awatch

from app import logging, schemas
from app.config import Config
from app.constants import JST, THUMBNAILS_DIR
from app.metadata.CMSectionsDetector import CMSectionsDetector
from app.metadata.MetadataAnalyzer import MetadataAnalyzer
from app.metadata.SeriesIndexer import SeriesIndexer
from app.metadata.ThumbnailGenerator import ThumbnailGenerator
from app.models.Channel import Channel
from app.models.RecordedProgram import RecordedProgram
from app.models.RecordedVideo import RecordedVideo
from app.streams.VideoSegmentPlanner import VideoSegmentPlanner
from app.utils import (
    LimitWorkerProcessResourcePriority,
    ShutdownProcessPoolExecutor,
    SubmitToProcessPoolExecutor,
)
from app.utils.DriveIOLimiter import DriveIOLimiter
from app.utils.NotificationService import NotificationManager
from app.utils.ProcessLimiter import ProcessLimiter
from app.utils.RecordingStatusProvider import (
    ActiveRecordingFilePaths,
    GetActiveRecordingFilePaths,
    GetEPGStationRecentRecordedFilePaths,
    IsActiveRecordingFilePath,
)
from app.utils.TSInformation import TSInformation


@dataclass(slots=True)
class FileRecordingInfo:
    """
    - last_modified: ファイルの最終更新日時
    - last_checked: ファイルの最終チェック日時
    - file_size: ファイルのサイズ
    - mtime_continuous_start_at: ファイルの最終更新日時が継続的に更新されている場合の継続更新の開始日時
    """
    last_modified: datetime
    last_checked: datetime
    file_size: int
    mtime_continuous_start_at: datetime | None


@dataclass(slots=True)
class RecordedVideoSummary:
    """
    RecordedScanTask.runBatchScan() 内でのメモリ使用量を抑えるため、RecordedVideo のうち必要最低限の情報のみを保持する軽量データ構造
    slots=True を指定し、メモリ使用量を抑える
    """

    id: int
    file_path: str
    created_at: datetime
    recorded_program_id: int
    status: Literal['Recording', 'Recorded', 'AnalysisFailed']
    file_created_at: datetime
    file_modified_at: datetime
    file_size: int
    file_hash: str
    duration: float


class RecordedFileMetadataNotStableError(Exception):
    """録画ファイルが更新中で、安全にメタデータを再解析できないことを表す例外。"""


class RecordedFileMetadataRefreshError(Exception):
    """録画ファイルのメタデータ再解析が完了しなかったことを表す例外。"""


class RecordedScanTask:
    """
    録画フォルダの監視とメタデータの DB への同期を行うタスク
    Mirakurun 構成では録画フォルダの一括スキャンと変更監視を常駐させる
    EDCB / EPGStation 構成ではローカル監視を行わず、録画バックエンドが返すファイルだけを同期する
    """

    # シングルトンインスタンス
    __instance: ClassVar[RecordedScanTask | None] = None

    # スキャン対象の拡張子
    SCAN_TARGET_EXTENSIONS: ClassVar[list[str]] = ['.ts', '.m2t', '.m2ts', '.mts', '.mp4', '.mmts']

    # 録画中ファイルの更新イベントを間引く間隔 (ログ出力用) (秒)
    UPDATE_THROTTLE_SECONDS: ClassVar[int] = 30

    # 録画完了と判断するまでの無更新時間 (秒)
    RECORDING_COMPLETE_SECONDS: ClassVar[int] = 15

    # 録画中と判断する最大の経過時間 (秒)
    RECORDING_MAX_AGE_SECONDS: ClassVar[int] = 300  # 5分

    # 録画バックエンドから取得した録画中ファイルパスのキャッシュ有効時間 (秒)
    ACTIVE_RECORDING_PATHS_CACHE_SECONDS: ClassVar[int] = 5

    # 録画バックエンドが把握している録画状態を同期する間隔 (秒)
    ACTIVE_RECORDING_SYNC_INTERVAL_SECONDS: ClassVar[int] = 5

    # EPGStation が把握している直近の録画済み一覧を同期する間隔 (秒)
    EPGSTATION_RECENT_RECORDED_SYNC_INTERVAL_SECONDS: ClassVar[int] = 60

    # 録画中ファイルの最小データ長 (秒)
    MINIMUM_RECORDING_SECONDS: ClassVar[int] = 60

    # 継続更新を録画中と判断する最小時間 (秒)
    CONTINUOUS_UPDATE_THRESHOLD_SECONDS: ClassVar[int] = 60

    # 継続更新を強制的に完了とする時間 (秒)
    CONTINUOUS_UPDATE_MAX_SECONDS: ClassVar[int] = 86400  # 24時間

    # 転码検出時の時長許容差異 (秒)
    # 転码時にエンコーダーの処理により時長が微小に変化する可能性を考慮
    TRANSCODE_DURATION_TOLERANCE: ClassVar[float] = 3.0

    # 既知のハッシュ衝突が発生しうる file_hash の集合
    KNOWN_COLLISION_FILE_HASHES: ClassVar[set[str]] = {
        'd1dd210d6b1312cb342b56d02bd5e651',
    }


    def __new__(cls) -> RecordedScanTask:
        """
        シングルトンインスタンスを作成または取得する
        既にインスタンスが存在する場合はそれを返し、存在しない場合は新規作成する

        Returns:
            RecordedScanTask: シングルトンインスタンス
        """

        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
        return cls.__instance


    def __init__(self) -> None:
        """
        録画フォルダの監視タスクを初期化する
        """

        # 初期化済みの場合は何もしない
        if hasattr(self, '_initialized') and self._initialized:
            return

        # 設定を読み込む
        self.config = Config()
        self.recorded_folders = [anyio.Path(folder) for folder in self.config.video.recorded_folders]

        # 録画中ファイルの状態管理
        self._recording_files: dict[anyio.Path, FileRecordingInfo] = {}
        # 録画バックエンドが把握している録画中ファイルパスの短時間キャッシュ
        ## ファイル変更イベントや一括スキャンで同じ情報を何度も参照するため、バックエンドへの問い合わせを数秒単位でまとめる。
        self._active_recording_paths_cache: ActiveRecordingFilePaths | None = None
        self._active_recording_paths_cache_updated_at: datetime | None = None
        self._active_recording_paths_lock = asyncio.Lock()
        # 録画バックエンド同期のログを状態変化時だけ出すため、直前の状態を保持する。
        ## 参照箇所: __syncActiveRecordingFiles()
        ## 前提条件: ファイル実体ではなく、比較用のパス文字列だけを保持する。
        self._active_recording_paths_log_signature: tuple[bool, tuple[str, ...]] | None = None
        # タスクの状態管理
        self._is_running = False
        self._task: asyncio.Task[None] | None = None
        # EDCB / EPGStation の API だけを定期的に確認する同期タスクを保持する。
        ## 参照箇所: startBackendRecordingSync(), stopBackendRecordingSync()
        ## 前提条件: このタスクは録画フォルダの全件スキャンや変更監視を一切起動しない。
        self._is_backend_recording_sync_running = False
        self._backend_recording_sync_task: asyncio.Task[None] | None = None

        # 録画フォルダ以下の一括スキャンを実行中かどうか
        self._is_batch_scan_running = False

        # バックグラウンドタスクの状態管理
        self._background_tasks: dict[anyio.Path, asyncio.Task[None]] = {}

        # 再生開始前のファイルメタデータ再解析タスクをファイルパスごとに共有する。
        ## 参照箇所: refreshRecordedFileMetadataIfNeeded()
        ## 前提条件: 同じ録画への複数リクエストが重なっても、重い解析処理は常に 1 回だけ実行する。
        self._metadata_refresh_tasks: dict[anyio.Path, asyncio.Task[None]] = {}
        # _metadata_refresh_tasks 辞書自体へのアクセスを保護するためのロック
        self._metadata_refresh_tasks_lock = asyncio.Lock()

        # ファイルパスごとのロックを弱参照で管理し、処理完了後に参照されなくなったロックを自動的に解放する。
        ## 待機中のタスクはロックへの強参照を保持するため、処理中に別ロックが生成されることはない。
        self._file_locks: weakref.WeakValueDictionary[anyio.Path, asyncio.Lock] = weakref.WeakValueDictionary()
        # _file_locks 辞書自体へのアクセスを保護するためのロック
        self._file_locks_dict_lock = asyncio.Lock()
        # 録画専用チャンネルの枝番計算と保存を直列化するためのロック
        self._recording_only_channels_lock = asyncio.Lock()

        # シンボリックリンクのマッピングを管理する辞書
        self._symlink_path_map: dict[str, str] = {}
        # _symlink_path_map 辞書自体へのアクセスを保護するためのロック
        self._symlink_path_map_lock = asyncio.Lock()

        # 初回バッチスキャン中かどうかのフラグ（通知抑制用）
        self._is_initial_scan = True

        # 初期化済みフラグをセット
        self._initialized = True


    async def __getActiveRecordingFilePaths(self) -> ActiveRecordingFilePaths:
        """
        録画バックエンドが把握している録画中ファイルパス一覧を取得する。

        Returns:
            ActiveRecordingFilePaths: 録画中ファイルパス一覧
        """

        # ファイル変更イベントや一括スキャンでは短時間に同じ問い合わせが連続するため、
        # バックエンドへの問い合わせ結果を数秒間だけ共有する。
        async with self._active_recording_paths_lock:
            now = datetime.now(tz=JST)
            if (self._active_recording_paths_cache is not None and
                self._active_recording_paths_cache_updated_at is not None and
                (now - self._active_recording_paths_cache_updated_at).total_seconds() < self.ACTIVE_RECORDING_PATHS_CACHE_SECONDS):
                return self._active_recording_paths_cache

            self._active_recording_paths_cache = await GetActiveRecordingFilePaths(self.config)
            self._active_recording_paths_cache_updated_at = now
            return self._active_recording_paths_cache


    async def __syncActiveRecordingFiles(self) -> None:
        """
        録画バックエンドが把握している録画中ファイルだけを DB と同期する。

        Returns:
            None
        """

        active_recording_file_paths = await self.__getActiveRecordingFilePaths()
        if active_recording_file_paths.is_reliable is False:
            return

        # API の接続状態と候補数を確認できるよう、状態が変わったときだけ要約を出す。
        current_signature = (
            active_recording_file_paths.is_reliable,
            tuple(sorted(active_recording_file_paths.paths)),
        )
        if current_signature != self._active_recording_paths_log_signature:
            self._active_recording_paths_log_signature = current_signature
            logging.info(
                f'Active recording paths from {active_recording_file_paths.backend}: '
                f'{len(active_recording_file_paths.paths)} candidate(s).'
            )

        # バックエンドが返した明示的なパス候補だけを確認し、録画フォルダ全体は走査しない。
        processed_paths: set[str] = set()
        for active_path in sorted(active_recording_file_paths.paths):
            file_path = anyio.Path(active_path)
            if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
                continue
            if str(file_path) in processed_paths:
                continue
            if await self.isFileExists(file_path) is False:
                continue
            processed_paths.add(str(file_path))

            # 録画中と判定されたファイルは状態管理へ登録してから、通常の単一ファイル処理へ渡す。
            stat = await file_path.stat()
            file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=JST)
            self._recording_files[file_path] = FileRecordingInfo(
                last_modified = file_modified_at,
                last_checked = datetime.now(tz=JST),
                file_size = stat.st_size,
                mtime_continuous_start_at = file_modified_at,
            )
            await self.processRecordedFile(file_path)

        # バックエンドの録画中一覧から外れた DB レコードは、録画完了として再解析する。
        recording_video_rows = await RecordedVideo.filter(status='Recording').values('file_path', 'recorded_program_id')
        for row in recording_video_rows:
            file_path_str = row['file_path']
            if IsActiveRecordingFilePath(file_path_str, active_recording_file_paths.paths) is True:
                continue
            file_path = anyio.Path(file_path_str)
            self._recording_files.pop(file_path, None)
            if await self.isFileExists(file_path) is True:
                await self.processRecordedFile(file_path, force_update=True)
            else:
                await RecordedProgram.filter(id=row['recorded_program_id']).delete()
                logging.info(
                    f'{file_path}: Deleted stale Recording record for non-existent file. '
                    f'[recorded_program_id: {row["recorded_program_id"]}]'
                )


    async def __syncEPGStationRecentRecordedFiles(self) -> None:
        """
        EPGStation が把握している直近の録画済みファイルだけを DB と同期する。

        Returns:
            None
        """

        if self.config.general.backend != 'EPGStation':
            return
        recent_recorded_file_paths = await GetEPGStationRecentRecordedFilePaths(self.config)
        if recent_recorded_file_paths.is_reliable is False:
            return

        processed_paths: set[str] = set()
        saved_count = 0
        for recorded_path in sorted(recent_recorded_file_paths.paths):
            file_path = anyio.Path(recorded_path)
            if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
                continue
            if str(file_path) in processed_paths:
                continue
            if await self.isFileExists(file_path) is False:
                continue
            processed_paths.add(str(file_path))

            # API が返した直近録画だけを通常の単一ファイル処理へ渡し、ローカル全件スキャンは行わない。
            await self.processRecordedFile(file_path)
            if await RecordedVideo.filter(file_path=str(file_path)).exists() is True:
                saved_count += 1

        logging.info(
            f'Recent recorded paths from EPGStation: '
            f'{len(processed_paths)} local candidate(s), {saved_count} saved, total: {recent_recorded_file_paths.total}.'
        )


    async def __syncBackendRecordingLoop(self) -> None:
        """
        録画バックエンドの録画中・録画完了状態を定期的に同期する。

        Returns:
            None
        """

        next_recent_recorded_sync_at = datetime.now(tz=JST)
        while self._is_backend_recording_sync_running:
            try:
                await self.__syncActiveRecordingFiles()
                now = datetime.now(tz=JST)
                if now >= next_recent_recorded_sync_at:
                    await self.__syncEPGStationRecentRecordedFiles()
                    next_recent_recorded_sync_at = now + timedelta(
                        seconds = self.EPGSTATION_RECENT_RECORDED_SYNC_INTERVAL_SECONDS,
                    )
            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logging.error('Error in backend recording sync:', exc_info=ex)

            await asyncio.sleep(self.ACTIVE_RECORDING_SYNC_INTERVAL_SECONDS)


    async def startBackendRecordingSync(self) -> None:
        """
        EDCB / EPGStation の API を使った録画同期だけを開始する。

        Returns:
            None
        """

        if self._is_backend_recording_sync_running:
            return
        self._is_backend_recording_sync_running = True
        self._backend_recording_sync_task = asyncio.create_task(self.__syncBackendRecordingLoop())


    async def stopBackendRecordingSync(self) -> None:
        """
        EDCB / EPGStation の API を使った録画同期を停止する。

        Returns:
            None
        """

        if self._is_backend_recording_sync_running is False:
            return
        self._is_backend_recording_sync_running = False
        if self._backend_recording_sync_task is not None:
            self._backend_recording_sync_task.cancel()
            try:
                await self._backend_recording_sync_task
            except asyncio.CancelledError:
                pass
            self._backend_recording_sync_task = None


    async def start(self) -> None:
        """
        録画フォルダの監視タスクを開始する
        Mirakurun 構成のサーバー起動時だけ app.py から呼ばれ、サーバーの起動中は常時稼働し続ける
        """

        # 既に実行中の場合は何もしない
        if self._is_running:
            return
        self._is_running = True

        # バックグラウンドタスクとして実行
        self._task = asyncio.create_task(self.run())


    async def stop(self) -> None:
        """
        録画フォルダの監視タスクを停止する
        監視タスクが起動済みの場合に、サーバー終了時の app.py から呼ばれる
        """

        # 録画バックエンド同期はローカル監視と独立して起動できるため、先に必ず停止する。
        await self.stopBackendRecordingSync()

        # 既に停止中の場合は何もしない
        if not self._is_running:
            return

        # 実行中タスクを停止
        self._is_running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None


    async def run(self) -> None:
        """
        録画フォルダ以下の一括スキャンと DB への同期と録画フォルダ以下のファイルシステム変更の監視を開始し、
        変更があれば随時メタデータを解析後、DB に永続化する
        Mirakurun 構成では start() 経由でサーバー起動時に呼ばれ、サーバーの起動中は常時稼働し続ける
        """

        try:
            # runBatchScan() が完了しなくても新しく録画されたファイルの監視を開始するため、同時に実行する
            await asyncio.gather(
                # サーバー起動時の一括スキャン・同期を実行
                self.runBatchScan(),
                # 録画フォルダの監視を開始
                self.watchRecordedFolders(),
            )
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logging.error('Error in RecordedScanTask:', exc_info=ex)
        finally:
            self._is_running = False


    async def runBatchScan(self) -> None:
        """
        録画フォルダ以下の一括スキャンと DB への同期を実行する
        - 録画フォルダ内の全 TS ファイルをスキャン
        - 追加・変更があったファイルのみメタデータを解析し、DB に永続化
        - 存在しない録画ファイルに対応するレコードを一括削除
        """

        # 既に一括スキャンを実行中の場合は HTTPException を発生させる
        # API から手動で一括スキャンを実行した際に重複して実行されないようにするためのバリデーション
        if self._is_batch_scan_running:
            raise HTTPException(
                status_code = status.HTTP_429_TOO_MANY_REQUESTS,
                detail = 'Batch scan of recording folders is already running',
            )

        logging.info('Batch scan of recording folders has been started.')
        self._is_batch_scan_running = True

        # 現在登録されている全ての RecordedVideo レコードの情報をキャッシュ
        ## すべての情報をキャッシュすると key_frames フィールドのデータ量が大きすぎてメモリとディスク I/O を大量に食うため、
        ## 必要最低限の情報のみをキャッシュする
        logging.info('Gathering all recorded video records...')
        all_video_rows = await RecordedVideo.all().values(
            'id',
            'file_path',
            'created_at',
            'recorded_program_id',
            'status',
            'file_created_at',
            'file_modified_at',
            'file_size',
            'file_hash',
            'duration',
        )
        videos_by_path: dict[str, list[RecordedVideoSummary]] = {}
        videos_to_keep: list[RecordedVideoSummary] = []  # 保持するレコードのリスト
        for index, row in enumerate(all_video_rows, start=1):
            recorded_video_summary = RecordedVideoSummary(
                id = row['id'],
                file_path = row['file_path'],
                created_at = row['created_at'],
                recorded_program_id = row['recorded_program_id'],
                status = row['status'],
                file_created_at = row['file_created_at'],
                file_modified_at = row['file_modified_at'],
                file_size = row['file_size'],
                file_hash = row['file_hash'],
                duration = row['duration'],
            )
            if recorded_video_summary.file_path not in videos_by_path:
                videos_by_path[recorded_video_summary.file_path] = []
            videos_by_path[recorded_video_summary.file_path].append(recorded_video_summary)
            if index % 100 == 0:
                # 起動時にイベントループが他のタスクを処理できるよう定期的に制御を返す
                await asyncio.sleep(0)

        # 同一ファイルパスに対応するレコードが複数存在する場合、最新のものを保持して残りを削除する
        ## 重複削除処理をトランザクション配下で実行
        logging.info('Checking for duplicate recorded video records...')
        duplicates_found = False
        total_deleted_count = 0
        async with transactions.in_transaction():
            for index, (file_path, videos) in enumerate(videos_by_path.items(), start=1):
                if len(videos) > 1:
                    duplicates_found = True
                    logging.warning(f'{file_path}: Found {len(videos)} duplicate records. Keeping the latest one.')
                    # created_at でソートして最新のレコードを特定
                    videos.sort(key=lambda v: v.created_at, reverse=True)
                    latest_video = videos[0]
                    videos_to_keep.append(latest_video)  # 最新のものを保持リストに追加
                    # 最新以外のレコードを削除
                    for video_to_delete in videos[1:]:
                        try:
                            # RecordedProgram を削除 (CASCADE により RecordedVideo も削除される)
                            await RecordedProgram.filter(id=video_to_delete.recorded_program_id).delete()
                            logging.info(
                                f'{file_path}: Deleted duplicate record. [deleted recorded_program_id: {video_to_delete.recorded_program_id}] '
                                f'[kept recorded_program_id: {latest_video.recorded_program_id}]'
                            )
                        except Exception as ex_del:
                            logging.error(
                                f'{file_path}: Failed to delete duplicate record. [deleted recorded_program_id: {video_to_delete.recorded_program_id}]',
                                exc_info=ex_del,
                            )
                    # 削除対象のレコード数をカウント
                    deleted_count = len(videos) - 1  # -1 は最新のレコードを除いた数
                    total_deleted_count += deleted_count
                else:
                    # 重複がない場合も保持リストに追加
                    videos_to_keep.append(videos[0])
                if index % 50 == 0:
                    # 重複チェックがイベントループを占有し続けないよう適宜制御を返す
                    await asyncio.sleep(0)
        if duplicates_found:
            logging.info(f'Duplicate record cleanup finished. Total {total_deleted_count} duplicate records were deleted.')
        else:
            logging.info('No duplicate records found.')

        # 旧 key_frames が残っている録画は、再生開始位置キャッシュへ変換して DB サイズを抑える
        await self.__migrateKeyFramesToSegmentMap()

        # 現在登録されている全ての RecordedVideo レコードをキャッシュ
        ## 重複削除処理で保持すると判断されたレコードのみを使う
        existing_db_recorded_videos: dict[anyio.Path, RecordedVideoSummary] = {}
        for video in videos_to_keep:
            # データベース中のパスをそのまま使用（シンボリックリンクを解決しない）
            existing_db_recorded_videos[anyio.Path(video.file_path)] = video

        # スキャン対象から除外するフォルダ
        # 空文字列は全パスにマッチしてしまうため除外する
        exclude_scan_paths = [
            self.__normalizePathForPrefixMatch(pattern)
            for pattern in self.config.video.exclude_scan_paths
            if type(pattern) is str and pattern.strip() != ''
        ]

        # 各録画フォルダをスキャン
        logging.info('Scanning recorded folders...')
        processed_paths: set[str] = set()
        for folder in self.recorded_folders:
            async for file_path in folder.rglob('*'):
                try:
                    # Mac の metadata ファイルをスキップ
                    if file_path.name.startswith('._'):
                        continue
                    # 除外パターンのチェック（シンボリックリンク解決前）
                    original_path_str = str(file_path)
                    original_path_for_match = self.__normalizePathForPrefixMatch(original_path_str)
                    if any(original_path_for_match.startswith(pattern) for pattern in exclude_scan_paths) is True:
                        continue
                    # シンボリックリンクを含むパスは実体に解決して処理する
                    canonical_path = await self.resolveRecordedPath(file_path)
                    canonical_path_str = str(canonical_path)
                    # 除外パターンのチェック（シンボリックリンク解決後）
                    # 空文字列は全パスにマッチしてしまうため除外する
                    canonical_path_for_match = self.__normalizePathForPrefixMatch(canonical_path_str)
                    if any(canonical_path_for_match.startswith(pattern) for pattern in exclude_scan_paths) is True:
                        continue
                    # シンボリックリンクのマッピングを更新する
                    await self.__updateSymlinkMapping(original_path_str, canonical_path_str)
                    if await canonical_path.is_dir():
                        continue
                    # 対象拡張子のファイル以外をスキップ
                    if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
                        continue
                    # 録画ファイルが確実に存在することを確認する
                    ## 環境次第では、稀に glob で取得したファイルが既に存在しなくなっているケースがある
                    if not await self.isFileExists(file_path):
                        continue
                    # スキャン時に検出したパスをそのまま使用（シンボリックリンクを解決しない）
                    file_path_str = str(file_path)
                    if file_path_str in processed_paths:
                        continue
                    processed_paths.add(file_path_str)

                    # 見つかったファイルを処理
                    await self.processRecordedFile(
                        file_path = file_path,
                        original_path = None,  # シンボリックリンクを解決しないため original_path は不要
                        existing_db_recorded_videos = existing_db_recorded_videos,
                    )
                except Exception as ex:
                    logging.error(f'{file_path}: Failed to process recorded file:', exc_info=ex)

        # 存在しない録画ファイルに対応するレコードを一括削除
        ## トランザクション配下に入れることでパフォーマンスが向上する
        logging.info('Deleting records for non-existent files...')
        async with transactions.in_transaction():
            for index, (file_path, existing_recorded_video_summary) in enumerate(existing_db_recorded_videos.items(), start=1):
                # ファイルの存在確認を非同期に行う
                if not await self.isFileExists(file_path):
                    # RecordedVideo の親テーブルである RecordedProgram を削除すると、
                    # CASCADE 制約により RecordedVideo も同時に削除される (Channel は親テーブルにあたるため削除されない)
                    await RecordedProgram.filter(id=existing_recorded_video_summary.recorded_program_id).delete()
                    logging.info(f'{file_path}: Deleted record for non-existent file.')
                if index % 50 == 0:
                    # 既存レコードの走査がイベントループを占有し続けないよう適宜制御を返す
                    await asyncio.sleep(0)

        # DB に存在する全ての RecordedVideo レコードのハッシュを取得
        logging.info('Gathering all recorded video hashes...')
        db_recorded_video_hashes = set(
            cast(list[str], await RecordedVideo.all().values_list('file_hash', flat=True))
        )

        # サムネイルフォルダ内の全ファイルをスキャンし、不要なサムネイルファイルを削除
        logging.info('Deleting orphaned thumbnail files...')
        thumbnails_dir = anyio.Path(str(THUMBNAILS_DIR))
        if await thumbnails_dir.is_dir():
            async for thumbnail_path in thumbnails_dir.glob('*'):
                try:
                    # .git から始まるファイルは無視
                    if thumbnail_path.name.startswith('.git'):
                        continue
                    # ディレクトリは無視
                    if await thumbnail_path.is_dir():
                        continue

                    # ファイル名からハッシュを抽出
                    ## ファイル名は "{hash}.webp" または "{hash}_tile.webp" の形式
                    file_name = thumbnail_path.stem
                    if file_name.endswith('_tile'):
                        file_hash = file_name[:-5]  # "_tile" を除去
                    else:
                        file_hash = file_name

                    # DB に存在しないハッシュのファイルを削除
                    if file_hash not in db_recorded_video_hashes:
                        await thumbnail_path.unlink()
                        logging.info(f'{thumbnail_path.name}: Deleted orphaned thumbnail file.')
                except Exception as ex:
                    logging.error(f'{thumbnail_path}: Error deleting orphaned thumbnail file:', exc_info=ex)

        # かつてのバグで RecordedVideo.file_hash が衝突している録画ファイルのメタデータを再解析する
        ## トランザクション配下に入れることでパフォーマンスが向上する
        ## メモリ使用量を抑えるため、key_frames などの大きなフィールドは取得せず、必要最低限のフィールドのみを取得する
        ## ref: https://github.com/tsukumijima/KonomiTV/commit/92e8630f41b6440ebd10defa5fdde1489ac7376a
        async with transactions.in_transaction():
            collision_video_rows = await RecordedVideo.filter(
                file_hash__in=list(self.KNOWN_COLLISION_FILE_HASHES),
            ).values(
                'status',
                'file_path',
                'file_hash',
            )
            processed_collision_paths: set[str] = set()
            if len(collision_video_rows) > 0:
                logging.info(f'Found {len(collision_video_rows)} videos affected by known hash collisions. Reanalyzing...')
                for collision_video_row in collision_video_rows:
                    file_path_str = collision_video_row['file_path']
                    # 既に処理済みのファイルはスキップ
                    if file_path_str in processed_collision_paths:
                        continue
                    # 録画中のファイルは今後の解析に任せる
                    if collision_video_row['status'] == 'Recording':
                        continue
                    file_path = anyio.Path(file_path_str)
                    # ファイルが存在しない場合はスキップ
                    if not await self.isFileExists(file_path):
                        continue
                    try:
                        # メタデータ再解析を実行
                        logging.info(f'{file_path}: Reanalyzing due to known hash collision ({collision_video_row["file_hash"]}).')
                        await self.processRecordedFile(
                            file_path = file_path,
                            force_update = True,
                        )
                        # 処理済みファイルに追加
                        processed_collision_paths.add(file_path_str)
                        logging.info(f'{file_path}: Reanalysis completed.')
                    except Exception as ex:
                        logging.error(f'{file_path}: Failed to reanalyze known hash collision file:', exc_info=ex)

        # メタデータ解析に失敗した録画ファイルの数をログ出力
        analysis_failed_count = await RecordedVideo.filter(status='AnalysisFailed').count()
        if analysis_failed_count > 0:
            logging.warning(
                f'Batch scan completed with files in AnalysisFailed status. '
                f'count: {analysis_failed_count}. '
                f'Re-run metadata analysis after checking source files.',
            )

        # 変更のない既存録画も含め、確定的に解析できる番組を Series ページへ反映する
        await SeriesIndexer.rebuild()
        logging.info('Batch scan of recording folders has been completed.')
        self._is_batch_scan_running = False
        # 初回バッチスキャン完了後は通知を有効にする
        self._is_initial_scan = False


    async def scanSingleFile(self, file_path_str: str, force_update: bool = True) -> None:
        """
        指定されたファイルパスの録画ファイルを手動でスキャンする

        Args:
            file_path_str (str): スキャン対象のファイルの絶対パス
            force_update (bool): 既存レコードの強制更新フラグ

        Raises:
            HTTPException: ファイルが存在しない、または対象外の拡張子の場合
        """

        file_path = anyio.Path(file_path_str)

        # ファイルの存在確認
        if not await self.isFileExists(file_path):
            raise HTTPException(
                status_code = status.HTTP_404_NOT_FOUND,
                detail = f'File not found: {file_path_str}',
            )

        # 対象拡張子のチェック
        if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
            raise HTTPException(
                status_code = status.HTTP_400_BAD_REQUEST,
                detail = f'Unsupported file extension: {file_path.suffix}. Supported extensions: {", ".join(self.SCAN_TARGET_EXTENSIONS)}',
            )

        # Mac の metadata ファイルをチェック
        if file_path.name.startswith('._'):
            raise HTTPException(
                status_code = status.HTTP_400_BAD_REQUEST,
                detail = f'macOS metadata files are not supported: {file_path.name}',
            )

        logging.info(f'Manual scan started for file: {file_path_str}')

        # ファイルを処理 (force_update=True で強制的に再解析)
        await self.processRecordedFile(file_path, None, None, force_update)

        logging.info(f'Manual scan completed for file: {file_path_str}')


    async def refreshRecordedFileMetadataIfNeeded(self, recorded_video: RecordedVideo) -> bool:
        """
        録画済みファイルの実体が DB 上のファイル情報から変化している場合、安全にメタデータを再解析する。

        Args:
            recorded_video (RecordedVideo): 再生または詳細取得の対象となる録画ファイル情報。

        Returns:
            bool: ファイル情報に差分があり、共有再解析タスクの完了を待った場合は True。

        Raises:
            RecordedFileMetadataNotStableError: ファイルがまだ外部プロセスから更新されている可能性がある場合。
            RecordedFileMetadataRefreshError: ファイルの確認またはメタデータ再解析が完了しなかった場合。
        """

        # 録画中ファイルは意図的にサイズが増え続けるため、追いかけ再生経路では差分検出を行わない。
        if recorded_video.status == 'Recording':
            return False

        file_path = anyio.Path(recorded_video.file_path)
        try:
            stat = await file_path.stat()
        except (FileNotFoundError, OSError) as ex:
            raise RecordedFileMetadataRefreshError(f'Failed to stat recorded file: {file_path}') from ex

        file_created_at = datetime.fromtimestamp(stat.st_ctime, tz=JST)
        file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=JST)
        file_metadata_changed = (
            recorded_video.file_created_at != file_created_at or
            recorded_video.file_modified_at != file_modified_at or
            recorded_video.file_size != stat.st_size
        )
        if file_metadata_changed is False:
            return False

        # 更新直後のファイルを強制解析すると、外部トランスコードの一時出力を完成品として保存しかねない。
        ## 最終更新から録画完了判定と同じ時間が経過するまでは、呼び出し元へ再試行可能なエラーとして返す。
        if (datetime.now(tz=JST) - file_modified_at).total_seconds() < self.RECORDING_COMPLETE_SECONDS:
            raise RecordedFileMetadataNotStableError(f'Recorded file is still being updated: {file_path}')

        # 詳細取得と HLS の複数リクエストが同時に来ても、ファイルパスごとに再解析タスクを 1 個だけ生成する。
        async with self._metadata_refresh_tasks_lock:
            refresh_task = self._metadata_refresh_tasks.get(file_path)
            if refresh_task is None:
                refresh_task = asyncio.create_task(self.__refreshRecordedFileMetadata(file_path))
                self._metadata_refresh_tasks[file_path] = refresh_task
                refresh_task.add_done_callback(
                    lambda completed_task: self.__handleMetadataRefreshTaskDone(file_path, completed_task)
                )

        try:
            # クライアント切断で共有タスクまでキャンセルされないよう shield() し、他の待機リクエストを完走させる。
            await asyncio.shield(refresh_task)
        except (RecordedFileMetadataNotStableError, RecordedFileMetadataRefreshError):
            raise
        except Exception as ex:
            raise RecordedFileMetadataRefreshError(f'Failed to refresh recorded file metadata: {file_path}') from ex
        finally:
            # 最後に完了を観測したリクエストが、同じタスクだけを管理辞書から取り除く。
            async with self._metadata_refresh_tasks_lock:
                if self._metadata_refresh_tasks.get(file_path) is refresh_task and refresh_task.done():
                    self._metadata_refresh_tasks.pop(file_path, None)

        # processRecordedFile() は解析失敗をログへ記録して戻るため、DB のファイル情報が実体と一致したことを明示的に検証する。
        refreshed_recorded_video = await RecordedVideo.get_or_none(file_path=str(file_path)).only(
            'file_created_at',
            'file_modified_at',
            'file_size',
        )
        if (
            refreshed_recorded_video is None or
            refreshed_recorded_video.file_created_at != file_created_at or
            refreshed_recorded_video.file_modified_at != file_modified_at or
            refreshed_recorded_video.file_size != stat.st_size
        ):
            raise RecordedFileMetadataRefreshError(f'Failed to refresh recorded file metadata: {file_path}')

        return True


    def __handleMetadataRefreshTaskDone(
        self,
        file_path: anyio.Path,
        completed_task: asyncio.Task[None],
    ) -> None:
        """
        共有メタデータ再解析タスクの完了後クリーンアップを予約する。

        Args:
            file_path (anyio.Path): 完了した再解析タスクに対応する録画ファイルのパス。
            completed_task (asyncio.Task[None]): 完了した共有再解析タスク。

        Returns:
            None
        """

        # 全リクエストが切断された場合も例外を回収し、完了済みタスクを管理辞書へ残さない。
        if completed_task.cancelled() is False:
            completed_task.exception()
        # done callback はイベントループ上で同期的に実行され、この辞書を更新する他のクリティカルセクションも await を含まない。
        ## そのため完了タスクの同一性を確認して直接削除しても、別タスクの登録と途中で競合することはない。
        if self._metadata_refresh_tasks.get(file_path) is completed_task:
            self._metadata_refresh_tasks.pop(file_path, None)


    async def __refreshRecordedFileMetadata(self, file_path: anyio.Path) -> None:
        """
        共有タスク内で録画ファイルのメタデータを再解析する。

        Args:
            file_path (anyio.Path): 再解析する録画ファイルのパス。

        Returns:
            None

        Raises:
            RecordedFileMetadataNotStableError: ファイルが再解析開始前に更新された場合。
        """

        # タスク生成待ちの間にファイルや DB が更新された可能性があるため、重い解析の直前にもう一度照合する。
        stat = await file_path.stat()
        file_created_at = datetime.fromtimestamp(stat.st_ctime, tz=JST)
        file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=JST)
        existing_recorded_video = await RecordedVideo.get_or_none(file_path=str(file_path)).only(
            'file_created_at',
            'file_modified_at',
            'file_size',
        )
        if (
            existing_recorded_video is not None and
            existing_recorded_video.file_created_at == file_created_at and
            existing_recorded_video.file_modified_at == file_modified_at and
            existing_recorded_video.file_size == stat.st_size
        ):
            return

        if (datetime.now(tz=JST) - file_modified_at).total_seconds() < self.RECORDING_COMPLETE_SECONDS:
            raise RecordedFileMetadataNotStableError(f'Recorded file changed before metadata refresh: {file_path}')

        # バックエンドまたはファイル監視が録画中と認識している場合は、古い mtime だけを根拠に完成品とみなさない。
        active_recording_file_paths = await self.__getActiveRecordingFilePaths()
        if (
            IsActiveRecordingFilePath(str(file_path), active_recording_file_paths.paths) is True or
            file_path in self._recording_files
        ):
            raise RecordedFileMetadataNotStableError(f'Recorded file is active before metadata refresh: {file_path}')

        logging.info(
            f'{file_path}: File metadata changed outside HonomiTV. '
            f'Refreshing recorded video metadata before playback.'
        )
        await self.processRecordedFile(
            file_path = file_path,
            force_update = True,
            files_only = True,
        )


    async def processRecordedFile(
        self,
        file_path: anyio.Path,
        original_path: anyio.Path | None = None,
        existing_db_recorded_videos: dict[anyio.Path, RecordedVideoSummary] | None = None,
        force_update: bool = False,
        selected_service_id: int | None = None,
        wait_background_analysis: bool = False,
        files_only: bool = False,
    ) -> None:
        """
        指定された録画ファイルのメタデータを解析し、DB に永続化する
        既に当該ファイルの情報が DB に登録されており、ファイル内容に変更がない場合は何も行われない

        Args:
            file_path (anyio.Path): 処理対象のファイルパス
            original_path (anyio.Path | None): シンボリックリンクなどで取得した元のファイルパス
            existing_db_recorded_videos (dict[anyio.Path, RecordedVideoSummary] | None): 既に DB に永続化されている録画ファイルパスと RecordedVideo のサマリーデータのマッピング
                (ファイル変更イベントから呼ばれた場合、watchfiles 初期化時に取得した全レコードと今で状態が一致しているとは限らないため、None が入る)
            force_update (bool): 既に DB に登録されている録画ファイルのメタデータを強制的に再解析するかどうか (デフォルト: False)
            selected_service_id (int | None): 指定するサービスID (複数チャンネル選択用) (デフォルト: None)
            wait_background_analysis (bool): バックグラウンド解析が完了するまで待つかどうか (デフォルト: False)
            files_only (bool): ファイル情報のみを再解析し、CM 区間検出・サムネイル生成・キーフレーム解析をスキップするかどうか (デフォルト: False)
        """

        # ファイルパスに対応するロックを取得または作成
        # シンボリックリンクを解決せず、スキャン時に検出したパスをそのまま使用
        file_path_str = str(file_path)
        async with self._file_locks_dict_lock:
            file_lock = self._file_locks.get(file_path)
            if file_lock is None:
                # WeakValueDictionary へ一時オブジェクトを直接代入すると、次の参照取得前に回収される可能性がある。
                ## 局所変数で強参照を保持した状態で登録し、async with へ入るまでロックの生存を保証する。
                file_lock = asyncio.Lock()
                self._file_locks[file_path] = file_lock

        # 同一ファイルパスへの DB レコード操作を排他制御する
        async with file_lock:
            try:
                # 万が一この時点でファイルが存在しない場合はスキップ
                # ファイル変更イベント発火後に即座にファイルが削除される可能性も考慮
                if not await self.isFileExists(file_path):
                    logging.warning(f'{file_path}: File does not exist after acquiring lock! ignored.')
                    return

                # ファイルの状態をチェック
                stat = await file_path.stat()
                now = datetime.now(tz=JST)
                file_size = stat.st_size
                file_created_at = datetime.fromtimestamp(stat.st_ctime, tz=JST)
                file_modified_at = datetime.fromtimestamp(stat.st_mtime, tz=JST)

                # 全く録画できていない0バイトのファイルをスキップ
                if file_size == 0:
                    logging.warning(f'{file_path}: File size is 0. ignored.')
                    return

                # 録画バックエンドが現在録画中のファイルパス一覧を返せる場合は、それを Recording 判定の主情報源にする。
                # EPGStation / EDCB が正常に応答している間は、ファイル更新時刻だけによる推測よりもこの結果を優先する。
                active_recording_file_paths = await self.__getActiveRecordingFilePaths()
                is_backend_recording = (
                    active_recording_file_paths.is_reliable is True and
                    IsActiveRecordingFilePath(file_path_str, active_recording_file_paths.paths) is True
                )

                # 同じファイルパスの既存レコードのサマリーがあれば取り出す
                if existing_db_recorded_videos is not None:
                    existing_recorded_video_summary = existing_db_recorded_videos.pop(file_path, None)
                else:
                    existing_recorded_video_summary = None

                # この時点でサマリーがない場合、DB に同一ファイルパスのレコードがないか最小限のカラムで取得する
                ## ファイル変更イベントから呼ばれた場合は existing_db_recorded_videos は None となるが、
                ## DB には同一ファイルパスのレコードが存在する可能性がある
                if existing_recorded_video_summary is None:
                    summary_rows = await RecordedVideo.filter(
                        file_path=file_path_str
                    ).values(
                        'id',
                        'file_path',
                        'created_at',
                        'recorded_program_id',
                        'status',
                        'file_created_at',
                        'file_modified_at',
                        'file_size',
                        'file_hash',
                        'duration',
                    )
                    if len(summary_rows) > 0:
                        row = summary_rows[0]
                        existing_recorded_video_summary = RecordedVideoSummary(
                            id = row['id'],
                            file_path = row['file_path'],  # データベース中の元のパスを保持
                            created_at = row['created_at'],
                            recorded_program_id = row['recorded_program_id'],
                            status = row['status'],
                            file_created_at = row['file_created_at'],
                            file_modified_at = row['file_modified_at'],
                            file_size = row['file_size'],
                            file_hash = row['file_hash'],
                            duration = row['duration'],
                        )

                # 同じファイルパスの既存レコードがあり、ファイルの基本情報（作成日時、更新日時、サイズ）が前回と一致した場合、
                # ファイル内容は変更されておらず、レコード内容は更新不要と判断してスキップ
                ## こうすることで、録画済みファイルに対しては HDD への I/O 負荷が高いハッシュ算出やメタデータ解析処理を省略できる
                ## 万が一前回実行時からファイルサイズや最終更新日時の変更を伴わずに録画が完了した場合に状態を適切に反映できるよう、録画中はスキップしない
                if (force_update is False and
                    existing_recorded_video_summary is not None and
                    existing_recorded_video_summary.status == 'Recorded'):
                    if (existing_recorded_video_summary.file_created_at == file_created_at and
                        existing_recorded_video_summary.file_modified_at == file_modified_at and
                        existing_recorded_video_summary.file_size == file_size and
                        is_backend_recording is False):
                        # logging.debug(f'{file_path}: File metadata unchanged, skipping...')
                        return

                # 現在録画中とマークされているファイルの処理
                is_file_system_recording = file_path in self._recording_files
                is_recording = is_backend_recording or (
                    active_recording_file_paths.is_reliable is False and
                    is_file_system_recording is True
                )
                if is_recording:
                    # 既に DB に登録済みで録画中の場合は再解析しない
                    if (force_update is False and
                        existing_recorded_video_summary is not None and
                        existing_recorded_video_summary.status == 'Recording'):
                        return

                if active_recording_file_paths.is_reliable is False and is_file_system_recording:
                    # まだ DB に登録されていない＆ファイルサイズが前回から変化していない場合
                    recording_info = self._recording_files[file_path]
                    last_size = recording_info.file_size
                    mtime_continuous_start_at = recording_info.mtime_continuous_start_at
                    if file_size == last_size:
                        # 最終更新日時の継続更新中でない場合はスキップ
                        if mtime_continuous_start_at is None:
                            logging.warning(f'{file_path}: File is not recording. ignored.')
                            return
                        # 最終更新日時の継続更新が1分未満の場合もスキップ
                        continuous_duration = (now - mtime_continuous_start_at).total_seconds()
                        if continuous_duration < self.CONTINUOUS_UPDATE_THRESHOLD_SECONDS:
                            return
                        # 最終更新日時の継続更新が24時間を超えた場合は何かがおかしい可能性が高いため打ち切る
                        if continuous_duration >= self.CONTINUOUS_UPDATE_MAX_SECONDS:
                            logging.warning(f'{file_path}: Continuous mtime updates for {continuous_duration:.1f} seconds. (> {self.CONTINUOUS_UPDATE_MAX_SECONDS}s) ignored.')
                            return
                        # ここまで到達した時点で（ファイルサイズこそ変化していないが）最終更新日時の推移から1分以上ファイル内容の更新が続いているとみなし、
                        # 後続の処理でメタデータを解析し、解析に成功次第 DB に録画中として登録する
                        # 録画開始前にファイルアロケーションを行う録画予約ソフトでは、録画中も表面上ファイルサイズが変化しない問題への対処
                        pass

                # ProcessPoolExecutor を使い、別プロセス上でメタデータを解析
                ## メタデータ解析処理は実装上同期 I/O で実装されており、また CPU-bound な処理のため、別プロセスで実行している
                ## コンテキストマネージャーはキャンセル時にも子プロセス終了を同期的に待つため、イベントループ上では使わない
                ## 正常完了時は明示的に待ってクリーンアップし、リクエスト切断時だけ待機なしで解放処理へ進める
                ## メタデータ解析は一括スキャン・ファイル監視・録画完了チェック・API 経由の単体スキャンの4経路から呼ばれ、
                ## それぞれが独立したループのため同時に複数の解析プロセスが fork され得る。ProcessLimiter で同時実行数を CPU コア数の 50% に制限する
                ## なお __runBackgroundAnalysis() とは意図的に別のキーを使っている。同じキーを共有すると、
                ## 数十秒〜数分掛かるサムネイル生成タスクがセマフォを占有し続け、一括スキャンによる DB への録画番組の反映が
                ## サムネイル生成の速度に律速されてしまうため
                analyzer = MetadataAnalyzer(pathlib.Path(str(file_path)), selected_service_id)  # anyio.Path -> pathlib.Path に変換
                async with ProcessLimiter.getSemaphore('RecordedScanTask.MetadataAnalyzer'):
                    executor = concurrent.futures.ProcessPoolExecutor(
                        max_workers = 1,
                        # 子プロセス側で CPU / ディスク I/O 優先度を下げ、ライブ視聴や録画の処理を妨げないようにする
                        initializer = LimitWorkerProcessResourcePriority,
                    )
                    should_wait_executor = True
                    try:
                        recorded_program = await SubmitToProcessPoolExecutor(executor, analyzer.analyze)
                    except asyncio.CancelledError:
                        should_wait_executor = False
                        await ShutdownProcessPoolExecutor(executor, is_cancelled=True)
                        raise
                    except Exception as ex:
                        logging.error(f'{file_path}: Error analyzing metadata:', exc_info=ex)
                        # メタデータ解析中に例外が発生した場合も、この時点ですでに DB にエントリが存在している場合は、UI から判別できるようステータスを更新する
                        if existing_recorded_video_summary is not None:
                            await RecordedVideo.filter(id=existing_recorded_video_summary.id).update(status='AnalysisFailed')
                            existing_recorded_video_summary.status = 'AnalysisFailed'
                        self._recording_files.pop(file_path, None)  # もし録画中扱いであればここで削除
                        return
                    finally:
                        if should_wait_executor is True:
                            await ShutdownProcessPoolExecutor(executor, is_cancelled=False)
                if recorded_program is None:
                    logging.error(f'{file_path}: Failed to analyze metadata.')
                    # メタデータ解析に失敗したがこの時点ですでに DB にエントリが存在している場合は、UI から判別できるようステータスを更新する
                    ## 本来メタデータ解析に失敗した録画ファイルは DB には登録されないが、「録画中は問題なく解析できていたが、録画完了後に解析できなくなった」
                    ## といったシチュエーションも稀に考えられなくもないため、そうした場合の保険として実装した
                    if existing_recorded_video_summary is not None:
                        await RecordedVideo.filter(id=existing_recorded_video_summary.id).update(status='AnalysisFailed')
                        existing_recorded_video_summary.status = 'AnalysisFailed'
                    self._recording_files.pop(file_path, None)  # もし録画中扱いであればここで削除
                    return

                # 60秒未満のファイルは録画失敗または切り抜きとみなしてスキップ
                # 録画中だがまだ60秒に満たない場合、今後のファイル変更イベント発火時に60秒を超えていれば録画中ファイルとして処理される
                if recorded_program.recorded_video.duration < self.MINIMUM_RECORDING_SECONDS:
                    logging.debug(f'{file_path}: This file is too short. (duration {recorded_program.recorded_video.duration:.1f}s < {self.MINIMUM_RECORDING_SECONDS}s) Skipped.')
                    return

                # 前回の DB 取得からメタデータ解析までの間に他のタスクがレコードを作成/更新している可能性があるため、
                # メタデータ解析後に再度ファイルパスに対応するレコードを取得する
                existing_db_recorded_video_after_analyze = await RecordedVideo.get_or_none(
                    file_path=file_path_str
                ).select_related('recorded_program', 'recorded_program__channel')

                # 同じファイルパスの既存レコードがあり、先ほど計算した最新のハッシュと変わっていない場合は、レコード内容は更新不要と判断してスキップ
                ## 万が一前回実行時からファイルサイズや最終更新日時の変更を伴わずに録画が完了した場合に状態を適切に反映できるよう、録画中はスキップしない
                if (force_update is False and
                    existing_db_recorded_video_after_analyze is not None and
                    existing_db_recorded_video_after_analyze.status == 'Recorded' and
                    existing_db_recorded_video_after_analyze.file_hash == recorded_program.recorded_video.file_hash):
                    return

                # 同一ファイルパスで既存レコードがあり、ハッシュが変化している場合、
                # 時長差異をチェックして転码された同一動画かどうかを判定する
                is_transcoded_same_video = False
                preserve_analysis_metadata = False
                if existing_db_recorded_video_after_analyze is not None:
                    # ファイルパスは同じ（これは既に前提条件として満たされている）
                    # ハッシュが変化している（上の if 文でスキップされていない = ハッシュ変化）
                    old_file_hash = existing_db_recorded_video_after_analyze.file_hash
                    new_file_hash = recorded_program.recorded_video.file_hash
                    hash_changed = (old_file_hash != new_file_hash)

                    # files_only モードでハッシュが変化した場合、既存のサムネイルファイルを新しいハッシュ名にリネーム
                    # さもなければサムネイルが見つからなくなる（orphaned として削除される可能性もある）
                    if files_only and hash_changed:
                        old_thumbnail_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{old_file_hash}.webp'
                        new_thumbnail_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{new_file_hash}.webp'
                        old_thumbnail_tile_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{old_file_hash}_tile.webp'
                        new_thumbnail_tile_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{new_file_hash}_tile.webp'
                        old_thumbnail_jpeg_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{old_file_hash}.jpg'
                        new_thumbnail_jpeg_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{new_file_hash}.jpg'
                        old_thumbnail_tile_jpeg_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{old_file_hash}_tile.jpg'
                        new_thumbnail_tile_jpeg_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{new_file_hash}_tile.jpg'

                        # 既存のサムネイルファイルをリネーム
                        thumbnail_pairs = [
                            (old_thumbnail_path, new_thumbnail_path),
                            (old_thumbnail_tile_path, new_thumbnail_tile_path),
                            (old_thumbnail_jpeg_path, new_thumbnail_jpeg_path),
                            (old_thumbnail_tile_jpeg_path, new_thumbnail_tile_jpeg_path),
                        ]
                        for old_thumb, new_thumb in thumbnail_pairs:
                            try:
                                if await old_thumb.is_file():
                                    await old_thumb.rename(new_thumb)
                                    logging.info(f'{old_thumb.name} → {new_thumb.name}: Renamed thumbnail to match new hash (files_only mode).')
                            except Exception as ex:
                                logging.error(f'{old_thumb}: Error renaming thumbnail:', exc_info=ex)

                    # 時長差異をチェック
                    old_duration = existing_db_recorded_video_after_analyze.duration
                    new_duration = recorded_program.recorded_video.duration
                    duration_diff = abs(old_duration - new_duration)

                    # 時長差異が許容範囲内（3秒以内）であれば、転码された同一動画と判定
                    # 3秒という閾値は、転码時の微小な時間差を許容しつつ、全く別の動画との誤判定を防ぐバランス
                    if duration_diff <= self.TRANSCODE_DURATION_TOLERANCE:
                        is_transcoded_same_video = True
                        # files_only モードでは再生成しない CM 区間だけを、同一内容と判断できる場合に限って保持する。
                        ## キーフレームとセグメントマップはファイル内部の位置に依存するため、後段で常に破棄する。
                        preserve_analysis_metadata = files_only
                        logging.info(
                            f'{file_path}: Detected transcoded video '
                            f'(duration diff: {duration_diff:.2f}s, '
                            f'old: {old_duration:.1f}s → new: {new_duration:.1f}s). '
                            f'Preserving program metadata.'
                        )
                    else:
                        # 時長差異が大きい場合は転码ではなく、別の動画と判断
                        logging.info(
                            f'{file_path}: Duration changed significantly '
                            f'(diff: {duration_diff:.2f}s, '
                            f'old: {old_duration:.1f}s → new: {new_duration:.1f}s). '
                            f'Treating as different video.'
                        )

                # 強制再解析時は番組情報の更新を優先する
                if force_update is True and is_transcoded_same_video is True:
                    logging.info(f'{file_path}: Force update enabled, overriding transcoded update mode.')
                    is_transcoded_same_video = False

                # 録画中のファイルとして処理
                ## 他ドライブからファイルコピー中のファイルも、実際の録画処理より高速に書き込まれるだけで随時書き込まれることに変わりはないので、
                ## 録画中として判断されることがある（その場合、ファイルコピーが完了した段階で「録画完了」扱いとなる）
                ## force_update (手動スキャン) の場合は _recording_files の状態を無視し、ファイルの更新時刻のみで判定
                if (
                    (not force_update and is_recording) or
                    (
                        not force_update and
                        active_recording_file_paths.is_reliable is False and
                        (now - file_modified_at).total_seconds() < self.RECORDING_COMPLETE_SECONDS
                    )
                ):
                    # status を Recording に設定
                    recorded_program.recorded_video.status = 'Recording'
                    # 状態を更新
                    self._recording_files[file_path] = FileRecordingInfo(
                        last_modified = file_modified_at,
                        last_checked = now,
                        file_size = file_size,
                        mtime_continuous_start_at = file_modified_at,  # 初回は必ず mtime_continuous_start_at を設定
                    )
                    logging.debug(f'{file_path}: This file is recording or copying. (duration {recorded_program.recorded_video.duration:.1f}s >= {self.MINIMUM_RECORDING_SECONDS}s)')
                else:
                    # status を Recorded に設定
                    # MetadataAnalyzer 側で既に Recorded に設定されているが、念のため
                    recorded_program.recorded_video.status = 'Recorded'
                    # 手動スキャンの場合、_recording_files から削除して録画完了状態をクリアする
                    # 録画バックエンドから信頼できる「現在録画中ではない」結果が得られた場合も、ローカル推測状態をクリアする。
                    if force_update or active_recording_file_paths.is_reliable is True:
                        self._recording_files.pop(file_path, None)

                # __saveRecordedMetadataToDB() は既存の RecordedVideo ORM インスタンスをそのまま更新する。
                ## そのため保存後に existing_db_recorded_video_after_analyze.status を参照すると、
                ## Recording → Recorded の遷移前ステータスが失われ、録画完了通知の判定に失敗する。
                previous_recorded_video_status: Literal['Recording', 'Recorded', 'AnalysisFailed'] | None = (
                    existing_db_recorded_video_after_analyze.status
                    if existing_db_recorded_video_after_analyze is not None
                    else None
                )

                # DB に永続化
                # メタデータ解析後の最新のデータベース情報を使う
                # ファイルパスはスキャン時に検出したパスをそのまま使用（シンボリックリンクを解決しない）
                # recorded_program.recorded_video.file_path は既に正しい値が設定されている
                # files_only モードではファイル情報のみ更新し、番組情報は既存レコードから保持する
                await self.__saveRecordedMetadataToDB(
                    recorded_program,
                    existing_db_recorded_video_after_analyze,
                    is_transcoded_same_video,
                    files_only,
                    preserve_analysis_metadata,
                )
                logging.info(f'{file_path}: {"Updated" if existing_db_recorded_video_after_analyze else "Saved"} metadata to DB. (status: {recorded_program.recorded_video.status})')

                # DB への永続化が完了したら、録画完了後のバックグラウンド解析タスクを開始
                ## "Recording" 状態の録画ファイルはまだ録画が完了していないので、サムネイル生成などの解析タスクは実行しない
                ## DB 保存に失敗した状態で開始すると、RecordedVideo が存在しないままサムネイル生成だけが進んでしまうため、この処理は永続化後に実行する必要がある
                if recorded_program.recorded_video.status == 'Recorded':
                    # files_only が True の場合は、CM 区間検出・サムネイル生成・キーフレーム解析をスキップ
                    if not files_only and file_path not in self._background_tasks:
                        # 通知が必要かどうかを判定（新規ファイルまたは Recording → Recorded の状態変化）
                        should_notify = (
                            len(self.config.notifications.services) > 0 and
                            (
                                existing_db_recorded_video_after_analyze is None or
                                previous_recorded_video_status == 'Recording'
                            )
                        )
                        task = asyncio.create_task(self.__runBackgroundAnalysis(recorded_program, should_notify))
                        self._background_tasks[file_path] = task

                # wait_background_analysis が True かつ files_only が False の場合のみ、バックグラウンド解析タスクが完了するまで待つ
                # 録画番組メタデータ再解析 API では、API レスポンスの返却をもってメタデータ再解析が完全に完了したことをユーザーに伝える必要があるため
                # files_only が True の場合は、バックグラウンド解析タスクが作成されないため、待つ必要もない
                if wait_background_analysis is True and not files_only and file_path in self._background_tasks:
                    await self._background_tasks[file_path]

            except Exception as ex:
                logging.error(f'{file_path}: Error processing file inside lock:', exc_info=ex)


    @staticmethod
    async def isFileExists(file_path: anyio.Path) -> bool:
        """
        ファイルが存在し、通常のファイルであるかを安全にチェックする。
        PermissionError やその他のファイルアクセスエラーが発生した場合は False を返す。

        Args:
            file_path (anyio.Path): チェックするファイルパス

        Returns:
            bool: ファイルが存在し、通常のファイルであるかどうか
        """
        try:
            return await file_path.is_file()
        except PermissionError:
            logging.warning(f'{file_path}: Permission denied when checking file.')
            return False
        except FileNotFoundError:
            return False
        except OSError as e:
            logging.warning(f'{file_path}: OSError during is_file() check:', exc_info=e)
            return False


    @staticmethod
    async def resolveRecordedPath(file_path: anyio.Path) -> anyio.Path:
        """
        シンボリックリンクを解決して実体のパスを取得する。解決に失敗した場合は元のパスを返す。

        Args:
            file_path (anyio.Path): ファイルパス

        Returns:
            anyio.Path: シンボリックの参照先である実体のパス
        """
        try:
            return await file_path.resolve()
        except (OSError, RuntimeError) as ex:
            logging.warning(f'{file_path}: Failed to resolve symlink. Using original path:', exc_info=ex)
            return file_path


    @staticmethod
    def __normalizePathForPrefixMatch(path_str: str) -> str:
        """
        パス区切り文字を統一し、前方一致の比較を安定させる。

        Args:
            path_str (str): 正規化対象のパス文字列

        Returns:
            str: パス区切り文字を / に統一した文字列
        """

        # Windows ではパス区切り文字として / と \\ の両方が使えるため、比較前に / に統一する
        return path_str.replace('\\', '/')


    async def __updateSymlinkMapping(self, original_path_str: str | None, canonical_path_str: str) -> None:
        """
        シンボリックリンクの元パスと実体パスのマッピングを管理する。

        Args:
            original_path_str (str | None): シンボリックリンクの元パス
            canonical_path_str (str): シンボリックリンクの実体パス
        """
        if original_path_str is None:
            return
        async with self._symlink_path_map_lock:
            if original_path_str == canonical_path_str:
                self._symlink_path_map.pop(original_path_str, None)
            else:
                self._symlink_path_map[original_path_str] = canonical_path_str


    @staticmethod
    def __populateChannelModelFromSchema(db_channel: Channel, channel_schema: schemas.Channel) -> None:
        """
        Pydantic スキーマから Channel モデルへ属性を転写する

        Args:
            db_channel (Channel): 値を設定する DB モデル
            channel_schema (schemas.Channel): 転写元のチャンネル情報
        """

        # 録画メタデータ解析結果のチャンネル情報を、そのまま DB モデルへ反映する
        db_channel.id = channel_schema.id
        db_channel.display_channel_id = channel_schema.display_channel_id
        db_channel.network_id = channel_schema.network_id
        db_channel.service_id = channel_schema.service_id
        db_channel.transport_stream_id = channel_schema.transport_stream_id
        db_channel.remocon_id = channel_schema.remocon_id
        db_channel.channel_number = channel_schema.channel_number
        db_channel.type = channel_schema.type
        db_channel.name = channel_schema.name
        db_channel.jikkyo_force = channel_schema.jikkyo_force
        db_channel.is_subchannel = channel_schema.is_subchannel
        db_channel.is_radiochannel = channel_schema.is_radiochannel
        db_channel.is_watchable = channel_schema.is_watchable


    async def __saveRecordedMetadataToDB(
        self,
        recorded_program: schemas.RecordedProgram,
        existing_db_recorded_video: RecordedVideo | None,
        is_transcoded_update: bool = False,
        preserve_program_metadata: bool = False,
        preserve_analysis_metadata: bool = False,
    ) -> None:
        """
        録画ファイルのメタデータ解析結果を DB に保存する
        既存レコードがある場合は更新し、ない場合は新規作成する
        録画専用の地デジチャンネルは、保存直前にメインプロセス側で枝番を再計算する
        並行保存時の枝番衝突を避けるため、録画専用の地デジチャンネル作成だけは直列化する

        Args:
            recorded_program (schemas.RecordedProgram): 保存する録画番組情報
            existing_db_recorded_video (RecordedVideo | None): 既に DB に永続化されている録画ファイルの RecordedVideo レコード
            is_transcoded_update (bool): 転码更新モードかどうか（デフォルト: False）
            preserve_program_metadata (bool): 番組情報を既存レコードから保持するかどうか（デフォルト: False）
            preserve_analysis_metadata (bool): ファイル位置に依存しない既存解析情報を保持するかどうか（デフォルト: False）
        """

        # トランザクション配下に入れることでパフォーマンスが向上する
        async with transactions.in_transaction():

            # Channel の保存（まだ当該チャンネルが DB に存在しない場合のみ）
            db_channel = None
            if recorded_program.channel is not None:
                db_channel = await Channel.get_or_none(id=recorded_program.channel.id)
                if db_channel is None:
                    # 録画専用の地デジチャンネルは、地方違いの TS ファイルを並行解析すると枝番衝突が発生しうる
                    ## そのため、ここで保存直前に最新の DB 状態を見て枝番を再計算し、保存処理自体も直列化する
                    if recorded_program.channel.type == 'GR' and recorded_program.channel.is_watchable is False:
                        async with self._recording_only_channels_lock:
                            db_channel = await Channel.get_or_none(id=recorded_program.channel.id)
                            if db_channel is None:
                                # 同一プロセス内では lock で競合を防げているが、
                                ## 別プロセスや想定外の外部介入で display_channel_id が衝突した場合に備えて 1 回だけ再試行する
                                for retry_count in range(2):
                                    recalculated_channel_number = await TSInformation.calculateChannelNumber(
                                        recorded_program.channel.type,
                                        recorded_program.channel.network_id,
                                        recorded_program.channel.service_id,
                                        recorded_program.channel.remocon_id,
                                    )
                                    recorded_program.channel.channel_number = recalculated_channel_number
                                    recorded_program.channel.display_channel_id = (
                                        recorded_program.channel.type.lower() + recalculated_channel_number
                                    )

                                    db_channel = Channel()
                                    self.__populateChannelModelFromSchema(db_channel, recorded_program.channel)
                                    try:
                                        await db_channel.save()
                                        # 初回の INSERT で競合した場合のみ、リトライで解消されたことをログに残す
                                        if retry_count > 0:
                                            logging.info(
                                                f'{recorded_program.recorded_video.file_path}: '
                                                f'Recorded-only channel save recovered after retry. '
                                                f'retry_count: {retry_count}, '
                                                f'display_channel_id: {db_channel.display_channel_id}'
                                            )
                                        break
                                    except IntegrityError as ex:
                                        if retry_count == 0:
                                            logging.warning(
                                                f'{recorded_program.recorded_video.file_path}: '
                                                f'Retrying recording-only channel save due to display_channel_id conflict.'
                                            )
                                            continue
                                        raise ex
                    else:
                        db_channel = Channel()
                        self.__populateChannelModelFromSchema(db_channel, recorded_program.channel)
                        await db_channel.save()
                elif (
                    db_channel.transport_stream_id is None and
                    recorded_program.channel.transport_stream_id is not None
                ):
                    # 既存チャンネルに TSID がない場合だけ、録画メタデータから得た値で補完する
                    ## Mirakurun のチャンネル情報には TSID が含まれないが、NID/SID/TSID の組は放送運用上ほぼ不変なので、
                    ## 既知の TSID を失わず保持しておくことで MP4 再生時の psisimux 引数にも利用できる
                    db_channel.transport_stream_id = recorded_program.channel.transport_stream_id
                    await db_channel.save(update_fields=['transport_stream_id'])

            # RecordedProgram の保存または更新
            if existing_db_recorded_video is not None:
                db_recorded_program = existing_db_recorded_video.recorded_program
            else:
                db_recorded_program = RecordedProgram()

            # RecordedProgram の属性を設定 (id, created_at, updated_at は自動生成のため指定しない)
            if preserve_program_metadata is True and existing_db_recorded_video is not None:
                # 解析結果が不完全な場合は既存の番組情報を保持する
                logging.debug(f'{recorded_program.recorded_video.file_path}: Preserving existing program metadata.')
            elif is_transcoded_update:
                # 転码更新モード：番組情報を保持し、技術的なフィールドのみ更新
                # 転码時に変化する可能性のあるフィールドのみ更新
                db_recorded_program.recording_start_margin = recorded_program.recording_start_margin
                db_recorded_program.recording_end_margin = recorded_program.recording_end_margin
                db_recorded_program.is_partially_recorded = recorded_program.is_partially_recorded
                db_recorded_program.duration = recorded_program.duration  # 時長は微小に変化する可能性

                # 以下のフィールドは保持（更新しない）：
                # - channel, network_id, service_id, event_id
                # - series_id, series_broadcast_period_id
                # - title, series_title, episode_number, subtitle
                # - description, detail
                # - start_time, end_time
                # - is_free, genres
                # - primary_audio_type, primary_audio_language
                # - secondary_audio_type, secondary_audio_language

                logging.debug(f'{recorded_program.recorded_video.file_path}: Transcoded update mode - preserving program metadata.')
            else:
                # 通常更新モード：すべてのフィールドを更新（既存の動作）
                db_recorded_program.recording_start_margin = recorded_program.recording_start_margin
                db_recorded_program.recording_end_margin = recorded_program.recording_end_margin
                db_recorded_program.is_partially_recorded = recorded_program.is_partially_recorded
                db_recorded_program.channel = db_channel  # type: ignore
                db_recorded_program.network_id = recorded_program.network_id
                db_recorded_program.service_id = recorded_program.service_id
                db_recorded_program.event_id = recorded_program.event_id
                db_recorded_program.series_id = recorded_program.series_id
                db_recorded_program.series_broadcast_period_id = recorded_program.series_broadcast_period_id
                db_recorded_program.title = recorded_program.title
                db_recorded_program.series_title = recorded_program.series_title
                db_recorded_program.episode_number = recorded_program.episode_number
                db_recorded_program.subtitle = recorded_program.subtitle
                db_recorded_program.description = recorded_program.description
                db_recorded_program.detail = recorded_program.detail
                db_recorded_program.start_time = recorded_program.start_time
                db_recorded_program.end_time = recorded_program.end_time
                db_recorded_program.duration = recorded_program.duration
                db_recorded_program.is_free = recorded_program.is_free
                db_recorded_program.genres = recorded_program.genres
                db_recorded_program.primary_audio_type = recorded_program.primary_audio_type
                db_recorded_program.primary_audio_language = recorded_program.primary_audio_language
                db_recorded_program.secondary_audio_type = recorded_program.secondary_audio_type
                db_recorded_program.secondary_audio_language = recorded_program.secondary_audio_language
            await db_recorded_program.save()

            # 通常解析で得た番組タイトルから Series を確定できる場合は、録画保存と同じ処理内で関連付ける
            if preserve_program_metadata is False and is_transcoded_update is False:
                await SeriesIndexer.linkRecordedProgram(db_recorded_program)

            # RecordedVideo の保存または更新
            if existing_db_recorded_video is not None:
                db_recorded_video = existing_db_recorded_video
            else:
                db_recorded_video = RecordedVideo()

            # RecordedVideo の属性を設定 (id, created_at, updated_at は自動生成のため指定しない)
            db_recorded_video.recorded_program = db_recorded_program
            db_recorded_video.status = recorded_program.recorded_video.status
            db_recorded_video.file_path = str(recorded_program.recorded_video.file_path)
            db_recorded_video.file_hash = recorded_program.recorded_video.file_hash
            db_recorded_video.file_size = recorded_program.recorded_video.file_size
            db_recorded_video.file_created_at = recorded_program.recorded_video.file_created_at
            db_recorded_video.file_modified_at = recorded_program.recorded_video.file_modified_at
            db_recorded_video.recording_start_time = recorded_program.recorded_video.recording_start_time
            db_recorded_video.recording_end_time = recorded_program.recorded_video.recording_end_time
            db_recorded_video.duration = recorded_program.recorded_video.duration
            db_recorded_video.container_format = recorded_program.recorded_video.container_format
            db_recorded_video.video_codec = recorded_program.recorded_video.video_codec
            db_recorded_video.video_codec_profile = recorded_program.recorded_video.video_codec_profile
            db_recorded_video.video_scan_type = recorded_program.recorded_video.video_scan_type
            db_recorded_video.video_frame_rate = recorded_program.recorded_video.video_frame_rate
            db_recorded_video.video_resolution_width = recorded_program.recorded_video.video_resolution_width
            db_recorded_video.video_resolution_height = recorded_program.recorded_video.video_resolution_height
            db_recorded_video.has_video_stream_changes = recorded_program.recorded_video.has_video_stream_changes
            db_recorded_video.primary_audio_codec = recorded_program.recorded_video.primary_audio_codec
            db_recorded_video.primary_audio_channel = recorded_program.recorded_video.primary_audio_channel
            db_recorded_video.primary_audio_sampling_rate = recorded_program.recorded_video.primary_audio_sampling_rate
            db_recorded_video.secondary_audio_codec = recorded_program.recorded_video.secondary_audio_codec
            db_recorded_video.secondary_audio_channel = recorded_program.recorded_video.secondary_audio_channel
            db_recorded_video.secondary_audio_sampling_rate = recorded_program.recorded_video.secondary_audio_sampling_rate
            # ファイル本体を再解析した場合、以前の再生開始位置キャッシュは別ファイル由来の可能性がある
            ## 新規録画と同じ空状態へ戻し、次回再生時に現在のファイルからオンデマンドで解決する
            db_recorded_video.key_frames = []
            db_recorded_video.segment_map = []
            # files_only で同じ内容のトランスコードと確認できた場合は、再生成しない既存 CM 区間を保持する。
            ## それ以外は未解析を表す None に戻し、通常のバックグラウンド解析で再生成できる状態にする。
            if preserve_analysis_metadata is False:
                db_recorded_video.cm_sections = None
            await db_recorded_video.save()



    async def _sendNewFileNotification(self, recorded_program: schemas.RecordedProgram) -> None:
        """
        新規録画ファイルの通知を送信する

        Args:
            recorded_program: 録画番組情報
        """
        try:
            notification_manager = NotificationManager(self.config.notifications.services)

            # データベースから保存済みのRecordedProgramを取得（正しいIDを持つ）
            db_recorded_video = await RecordedVideo.get_or_none(
                file_path=recorded_program.recorded_video.file_path
            ).select_related('recorded_program', 'recorded_program__channel')

            if db_recorded_video and db_recorded_video.recorded_program:
                # 元のrecorded_programオブジェクトをコピーしてIDを更新
                notification_recorded_program = recorded_program.model_copy()
                notification_recorded_program.id = db_recorded_video.recorded_program.id
            else:
                # データベースから取得できない場合は元のオブジェクトを使用
                notification_recorded_program = recorded_program

            # サムネイルパスを取得
            thumbnail_path = anyio.Path(str(THUMBNAILS_DIR)) / f'{recorded_program.recorded_video.file_hash}.webp'

            await notification_manager.send_new_recording(
                recorded_program=notification_recorded_program,
                thumbnail_path=thumbnail_path if await thumbnail_path.exists() else None
            )
        except Exception as e:
            logging.warning(f'通知送信に失敗しました: {e}')


    async def __runBackgroundAnalysis(self, recorded_program: schemas.RecordedProgram, should_notify: bool = False) -> None:
        """
        録画完了後のバックグラウンド解析タスク
        - サムネイル生成
        - CM区間検出
        など、時間のかかる処理を非同期に同時実行する

        Args:
            recorded_program (schemas.RecordedProgram): 解析対象の録画番組情報
            should_notify (bool): 解析完了後に通知を送信するかどうか
        """

        # 録画ファイルのパスを anyio.Path に変換
        file_path = anyio.Path(recorded_program.recorded_video.file_path)

        try:
            logging.info(f'{file_path}: Starting background analysis task...')
            # ProcessLimiter で稼働中のバックグラウンドタスクの同時実行数を CPU コア数の 50% に制限
            async with ProcessLimiter.getSemaphore('RecordedScanTask'):
                # DriveIOLimiter で同一 HDD に対してのバックグラウンドタスクの同時実行数を原則1セッションに制限
                async with DriveIOLimiter.getSemaphore(file_path):
                    # サムネイル生成と CM 区間検出を並列実行する。
                    background_tasks = [
                        ThumbnailGenerator.fromRecordedProgram(recorded_program).generateAndSave(),
                    ]
                    # MMT/TLV の CM 解析は非常に高負荷なので、明示的に有効化された環境だけで実行する。
                    if CMSectionsDetector.shouldAnalyze(
                        recorded_program.recorded_video.container_format,
                        self.config.video.enable_mmt_tlv_cm_analysis,
                    ):
                        background_tasks.append(CMSectionsDetector(
                            file_path,
                            recorded_program.recorded_video.duration,
                            recorded_program.recorded_video.container_format,
                            recorded_program.service_id,
                        ).detectAndSave())
                    else:
                        logging.info(f'{file_path}: Skipping MMT/TLV CM analysis because it is disabled.')
                    await asyncio.gather(*background_tasks)
            logging.info(f'{file_path}: Background analysis task completed.')

            # バックグラウンド解析完了後に通知を送信（サムネイル生成完了後）
            if should_notify:
                try:
                    await self._sendNewFileNotification(recorded_program)
                except Exception as notification_ex:
                    logging.warning(f'通知送信に失敗しました: {notification_ex}')

        except Exception as ex:
            logging.error(f'{file_path}: Error in background analysis task:', exc_info=ex)
        finally:
            # 完了したタスクを管理対象から削除
            self._background_tasks.pop(file_path, None)


    async def __migrateKeyFramesToSegmentMap(self) -> None:
        """
        旧 key_frames を再生開始位置キャッシュへ移行する

        このメソッドは runBatchScan() から呼び出され、以下の処理を行う:
        - TS コンテナは key_frames から segment_map を生成して保存
        - MPEG-4 コンテナは moov の同期サンプル表を再生時に読むため key_frames だけ破棄
        - 変換後の key_frames は空配列へ戻し、巨大な JSON が残り続けないようにする
        """

        logging.info('Starting keyframe to segment map migration...')

        migrated_count = 0
        repaired_count = 0
        skipped_count = 0
        last_seen_id = 0
        next_progress_log_count = 500

        while True:
            # key_frames は ORM 取得時に list へ復元されるため、Python 側で空配列かどうかを判定する
            ## DB 側で巨大 JSON の文字列比較を走らせず、ID 順に少量ずつ読み出して移行する
            video_rows = await RecordedVideo.filter(
                status = 'Recorded',
                id__gt = last_seen_id,
            ).order_by('id').limit(50).values(
                'id',
                'file_path',
                'duration',
                'container_format',
                'video_frame_rate',
                'key_frames',
                'segment_map',
            )
            if len(video_rows) == 0:
                break

            for video_row in video_rows:
                last_seen_id = video_row['id']

                try:
                    segment_map = video_row['segment_map']
                    if not isinstance(segment_map, list):
                        segment_map = []

                    is_broken_segment_map = False
                    # 旧変換ロジックで同じ入力位置が連続保存された MPEG-TS は、再生時に同じ映像を繰り返す
                    ## key_frames が既に空でも検出できるよう、移行対象判定より先に segment_map を確認する
                    if (
                        video_row['container_format'] == 'MPEG-TS' and
                        len(segment_map) > 0 and
                        VideoSegmentPlanner.isSegmentMapProbablyBroken(cast(list[schemas.SegmentMapEntry], segment_map)) is True
                    ):
                        is_broken_segment_map = True

                    key_frames = video_row['key_frames']
                    if not isinstance(key_frames, list) or len(key_frames) == 0:
                        # 壊れた既存キャッシュだけを空に戻し、通常の未キャッシュ状態としてオンデマンド探索へ戻す
                        ## key_frames が空の録画は旧データから再変換できないため、誤った値を温存しない
                        if is_broken_segment_map is True:
                            await RecordedVideo.filter(id=video_row['id']).update(segment_map = [])
                            repaired_count += 1
                            logging.warning(
                                f'{video_row["file_path"]}: Broken segment map was cleared. '
                                f'[video_id: {video_row["id"]}]'
                            )
                        continue

                    # TS コンテナは既存 key_frames をオンデマンド探索と同じ規則のキャッシュへ変換できる
                    if video_row['container_format'] == 'MPEG-TS':
                        if len(segment_map) == 0 or is_broken_segment_map is True:
                            video_frame_rate = video_row['video_frame_rate']
                            # 旧 DB に壊れたフレームレートが混じっている場合、セグメント長を復元できないため移行対象から外す
                            if (
                                isinstance(video_frame_rate, bool) is True or
                                isinstance(video_frame_rate, int | float) is False
                            ):
                                skipped_count += 1
                                logging.warning(
                                    f'{video_row["file_path"]}: Invalid video frame rate. '
                                    f'[video_id: {video_row["id"]}, video_frame_rate: {video_frame_rate}]'
                                )
                                continue
                            # 0 以下のフレームレートは segment_map の時刻計算で除算できないため移行対象から外す
                            if video_frame_rate <= 0:
                                skipped_count += 1
                                logging.warning(
                                    f'{video_row["file_path"]}: Invalid video frame rate. '
                                    f'[video_id: {video_row["id"]}, video_frame_rate: {video_frame_rate}]'
                                )
                                continue
                            segment_map = VideoSegmentPlanner.convertKeyFramesToSegmentMap(
                                key_frames = key_frames,
                                video_frame_rate = float(video_frame_rate),
                                duration_seconds = video_row['duration'],
                            )

                        await RecordedVideo.filter(id=video_row['id']).update(
                            segment_map = segment_map,
                            key_frames = [],
                        )
                        migrated_count += 1
                    # MP4 は moov から同期サンプル DTS を短時間で復元できるため、巨大な旧キャッシュだけ破棄する
                    else:
                        await RecordedVideo.filter(id=video_row['id']).update(key_frames = [])
                        migrated_count += 1
                except Exception as ex:
                    skipped_count += 1
                    logging.error(f'{video_row["file_path"]}: Failed to migrate keyframes to segment map:', exc_info=ex)

            # 大量の録画を持つ環境では起動直後に沈黙すると不安になるため、500件ごとに進捗をログへ出す
            processed_count = migrated_count + repaired_count + skipped_count
            if processed_count >= next_progress_log_count:
                logging.info(
                    f'Keyframe to segment map migration progress. '
                    f'[processed: {processed_count}, migrated: {migrated_count}, repaired: {repaired_count}, '
                    f'skipped: {skipped_count}]'
                )
                next_progress_log_count += 500

            # 移行処理がイベントループを占有し続けないよう適宜制御を返す
            await asyncio.sleep(0)

        logging.info(
            f'Keyframe to segment map migration completed. '
            f'[migrated: {migrated_count}, repaired: {repaired_count}, skipped: {skipped_count}]'
        )


    async def watchRecordedFolders(self) -> None:
        """
        録画フォルダ以下のファイルシステム変更の監視を開始し、変更があれば随時メタデータを解析後、DB に永続化する
        """

        logging.info('Starting file system watch of recording folders.')

        # 監視対象のディレクトリを設定
        watch_paths = [str(path) for path in self.recorded_folders]

        # スキャン対象から除外するフォルダ
        # 空文字列は全パスにマッチしてしまうため除外する
        exclude_scan_paths = [
            self.__normalizePathForPrefixMatch(pattern)
            for pattern in self.config.video.exclude_scan_paths
            if type(pattern) is str and pattern.strip() != ''
        ]

        # 録画完了チェック用のタスク
        completion_check_task = asyncio.create_task(self.__checkRecordingCompletion())

        try:
            # watchfiles によるファイル監視
            async for changes in awatch(*watch_paths, recursive=True):
                if not self._is_running:
                    break

                # 変更があったファイルごとに処理
                for change_type, file_path_str in changes:
                    if not self._is_running:
                        break

                    file_path = anyio.Path(file_path_str)
                    # Mac の metadata ファイルをスキップ
                    if file_path.name.startswith('._'):
                        continue
                    # 除外パターンのチェック（シンボリックリンク解決前）
                    # 空文字列は全パスにマッチしてしまうため除外する
                    original_path_str = str(file_path)
                    original_path_for_match = self.__normalizePathForPrefixMatch(original_path_str)
                    if any(original_path_for_match.startswith(pattern) for pattern in exclude_scan_paths) is True:
                        continue
                    # シンボリックリンクを含むパスは実体に解決して処理する
                    canonical_path = await self.resolveRecordedPath(file_path)
                    # 除外パターンのチェック（シンボリックリンク解決後）
                    # 空文字列は全パスにマッチしてしまうため除外する
                    canonical_path_str = str(canonical_path)
                    canonical_path_for_match = self.__normalizePathForPrefixMatch(canonical_path_str)
                    if any(canonical_path_for_match.startswith(pattern) for pattern in exclude_scan_paths) is True:
                        continue
                    if await canonical_path.is_dir():
                        continue
                    # 対象拡張子のファイル以外は無視
                    if file_path.suffix.lower() not in self.SCAN_TARGET_EXTENSIONS:
                        continue

                    try:
                        # 追加 or 変更イベント
                        if change_type == Change.added or change_type == Change.modified:
                            await self.__handleFileChange(file_path)
                        # 削除イベント
                        elif change_type == Change.deleted:
                            await self.__handleFileDeletion(file_path)
                    except Exception as ex:
                        logging.error(f'{file_path}: Error handling file change:', exc_info=ex)

        except asyncio.CancelledError:
            raise
        except Exception as ex:
            logging.error('Error in file system watch of recording folders:', exc_info=ex)
        finally:
            completion_check_task.cancel()
            try:
                await completion_check_task
            except asyncio.CancelledError:
                pass
            logging.info('File system watch of recording folders has been stopped.')


    async def __handleFileChange(self, file_path: anyio.Path) -> None:
        """
        ファイル追加・変更イベントを受け取り、適切な頻度で __processFile() を呼び出す
        - 録画中ファイルの状態管理
        - メタデータ解析のスロットリング
        - 最終更新日時の継続更新検出による録画中判定

        Args:
            file_path (anyio.Path): ファイルパス（シンボリックリンクを解決しない）
        """

        try:
            # ファイルの状態をチェック
            stat = await file_path.stat()
            last_modified = datetime.fromtimestamp(stat.st_mtime, tz=JST)
            now = datetime.now(tz=JST)
            file_size = stat.st_size

            # 既に録画中とマークされているファイルの処理
            if file_path in self._recording_files:
                recording_info = self._recording_files[file_path]
                last_checked = recording_info.last_checked
                last_size = recording_info.file_size
                mtime_continuous_start_at = recording_info.mtime_continuous_start_at

                # 前回のチェックから UPDATE_THROTTLE_SECONDS 秒以上経過していない場合はログを間引く（状態自体は更新する）
                throttle_event = False
                if (now - last_checked).total_seconds() < self.UPDATE_THROTTLE_SECONDS:
                    throttle_event = True

                # ファイルサイズが変化している場合は継続更新判定をリセット
                if file_size != last_size:
                    mtime_continuous_start_at = None
                    if not throttle_event:
                        logging.debug(f'{file_path}: File size changed.')
                # mtime が変化している場合は継続更新判定を更新
                elif last_modified > recording_info.last_modified:
                    if mtime_continuous_start_at is None:
                        mtime_continuous_start_at = last_modified
                        if not throttle_event:
                            logging.debug(f'{file_path}: File modified time changed.')
                    else:
                        continuous_duration = (now - mtime_continuous_start_at).total_seconds()
                        if continuous_duration >= self.CONTINUOUS_UPDATE_THRESHOLD_SECONDS:
                            if not throttle_event:
                                logging.debug(f'{file_path}: Still recording. (continuous mtime updates for {continuous_duration:.1f} seconds)')

                # 状態を更新
                recording_info.last_modified = last_modified
                # 前回のチェックから UPDATE_THROTTLE_SECONDS 秒以上経過していない場合は前回のチェック日時を使う
                recording_info.last_checked = last_checked if throttle_event else now
                recording_info.file_size = file_size
                recording_info.mtime_continuous_start_at = mtime_continuous_start_at

                # メタデータ解析を実行
                await self.processRecordedFile(file_path)

            # まだ録画中とマークされていないファイルの処理
            else:
                # 最終更新時刻から一定時間以上経過している場合は録画中とみなさない
                # それ以外の場合、今後継続的に追記されていく（＝録画中）可能性もあるので、録画中マークをつけておく
                if (now - last_modified).total_seconds() <= self.RECORDING_MAX_AGE_SECONDS:
                    self._recording_files[file_path] = FileRecordingInfo(
                        last_modified = last_modified,
                        last_checked = now,
                        file_size = file_size,
                        mtime_continuous_start_at = last_modified,  # 初回は必ず mtime_continuous_start_at を設定
                    )
                    logging.info(f'{file_path}: New recording or copying file detected.')

                # メタデータ解析を実行
                await self.processRecordedFile(file_path)

        except FileNotFoundError:
            # ファイルが既に削除されている場合
            pass
        except Exception as ex:
            logging.error(f'{file_path}: Error handling file change:', exc_info=ex)


    async def __handleFileDeletion(self, file_path: anyio.Path) -> None:
        """
        ファイル削除イベントを受け取り、DB からレコードを削除する

        Args:
            file_path (anyio.Path): 削除対象ファイルパス（シンボリックリンクを解決しない）
        """

        # ファイルパスに対応するロックを取得または作成
        async with self._file_locks_dict_lock:
            file_lock = self._file_locks.get(file_path)
            if file_lock is None:
                # processRecordedFile() と同じ方法で強参照を先に確保し、削除処理も必ず同一ロックへ合流させる。
                file_lock = asyncio.Lock()
                self._file_locks[file_path] = file_lock

        # 同一ファイルパスへの DB レコード操作を排他制御する
        async with file_lock:
            try:
                # 録画中とマークされていたファイルの場合は記録から削除
                self._recording_files.pop(file_path, None)

                # DB からレコードを削除
                db_recorded_video = await RecordedVideo.get_or_none(file_path=str(file_path))
                if db_recorded_video is not None:
                    # RecordedVideo の親テーブルである RecordedProgram を削除すると、
                    # CASCADE 制約により RecordedVideo も同時に削除される (Channel は親テーブルにあたるため削除されない)
                    await db_recorded_video.recorded_program.delete()
                    logging.info(f'{file_path}: Deleted record for removed file.')

            except Exception as ex:
                logging.error(f'{file_path}: Error handling file deletion inside lock:', exc_info=ex)


    async def __checkRecordingCompletion(self) -> None:
        """
        録画 (またはファイルコピー) の完了状態を定期的にチェックする
        - 30秒間ファイルの更新がない場合に録画完了 (またはファイルコピー完了) と判断
        - 完了したファイルは再度メタデータを解析して DB に保存
        """

        while self._is_running:
            try:
                now = datetime.now(tz=JST)
                completed_files: list[anyio.Path] = []

                # 録画中ファイルをチェック
                for file_path, recording_info in self._recording_files.items():
                    try:
                        # ファイルの現在の状態を取得
                        stat = await file_path.stat()
                        current_modified = datetime.fromtimestamp(stat.st_mtime, tz=JST)
                        current_size = stat.st_size

                        # RECORDING_COMPLETE_SECONDS 秒以上更新がなく、かつファイルサイズが変化していない場合は録画完了と判断
                        if ((now - current_modified).total_seconds() >= self.RECORDING_COMPLETE_SECONDS and
                            current_size == recording_info.file_size):
                            completed_files.append(file_path)
                    except FileNotFoundError:
                        # ファイルが削除された場合は記録から削除
                        completed_files.append(file_path)
                    except Exception as ex:
                        logging.error(f'{file_path}: Error checking recording completion:', exc_info=ex)

                # 完了したファイルを処理
                for file_path in completed_files:
                    try:
                        # 記録から削除
                        self._recording_files.pop(file_path, None)

                        # ファイルが存在する場合のみ再解析
                        if await self.isFileExists(file_path):
                            # この時点で、録画（またはファイルコピー）が確実に完了しているはず
                            logging.info(f'{file_path}: Recording or copying has just completed or has already completed.')
                            await self.processRecordedFile(file_path)
                    except Exception as ex:
                        logging.error(f'{file_path}: Error processing completed file:', exc_info=ex)

            except asyncio.CancelledError:
                raise
            except Exception as ex:
                logging.error('Error in recording completion check:', exc_info=ex)

            # 5秒待機
            await asyncio.sleep(5)
