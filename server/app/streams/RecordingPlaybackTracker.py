from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar, Literal, Protocol, cast

from app import logging
from app.models.RecordedProgram import RecordedProgram
from app.utils.TSKeyFrameSeeker import TSKeyFrameSeeker, TSStreamInfo


class RecordingPlaybackVideo(Protocol):
    """ 録画中ファイルの進行状況を追跡するために必要な録画動画情報 """

    id: int
    file_path: str
    file_size: int
    file_modified_at: datetime
    duration: float
    container_format: Literal['MPEG-TS', 'MPEG-4', 'MMT/TLV']


@dataclass(frozen=True, slots=True)
class RecordingPlaybackSnapshot:
    """
    録画中ファイルの追っかけ再生に必要な、現時点の読み取り可能範囲を表すスナップショット
    """

    # ファイルの現在サイズ
    file_size: int
    # ファイルの最終更新日時
    file_modified_at: datetime | None
    # 現時点で安全に参照できると推定した再生時間 (秒)
    available_duration_seconds: float
    # MPEG-TS の PID / パケットサイズ情報
    stream_info: TSStreamInfo | None
    # 録画先頭キーフレームの DTS (90kHz)
    source_base_dts: int | None
    # メディア内の PCR / DTS に基づく再生時間を取得できたかどうか
    ## True の場合だけ、available_duration_seconds を録画中の動的な再生時間として公開できる。
    is_timeline_ready: bool
    # DB の初期時長を一時的な解析失敗時の下限として使えるかどうか
    ## ファイル差し替え後は古いファイル由来になるため False にする。
    is_metadata_fallback_valid: bool
    # 最後の更新が成功しているかどうか
    is_ready: bool


class RecordingPlaybackTracker:
    """ 録画中ファイルの追っかけ再生に使うファイル単位の共有トラッカー """

    # 録画ファイルの末尾を確認する間隔 (秒)
    POLL_INTERVAL_SECONDS: ClassVar[float] = 1.0

    # 最後の参照からこの秒数が過ぎたら worker を停止する
    IDLE_TIMEOUT_SECONDS: ClassVar[float] = 20.0

    # 一覧・詳細 API だけが参照した tracker の解析コンテキストを保持する時間
    ## バックグラウンド走査は起動せず、次の API 更新で PAT / PMT と先頭 DTS を再解析しないためだけに保持する。
    PASSIVE_CACHE_TIMEOUT_SECONDS: ClassVar[float] = 5 * 60.0

    # 録画ファイルパスをキーとした共有トラッカー
    __instances: ClassVar[dict[str, RecordingPlaybackTracker]] = {}

    # 共有トラッカーの生成・破棄を直列化するロック
    __instances_lock: ClassVar[asyncio.Lock] = asyncio.Lock()


    def __init__(self, recorded_program_id: int, recorded_video: RecordingPlaybackVideo) -> None:
        """
        RecordingPlaybackTracker のインスタンスを初期化する

        Args:
            recorded_program_id (int): 追っかけ再生対象の録画番組 ID
            recorded_video (RecordingPlaybackVideo): 追跡に必要な録画動画情報
        """

        # 共有単位になる録画ファイルパスを保持する
        ## 同じ録画ファイルを複数セッション・複数画質で開いても、末尾追跡は1本に集約する。
        self._file_path = Path(recorded_video.file_path)
        # ログや初期値のため、録画番組 ID と録画動画 ID を保持する
        self._recorded_program_id = recorded_program_id
        self._recorded_video_id = recorded_video.id
        # 追っかけ再生 v1 は MPEG-TS を対象にするが、コンテナ判定は呼び出し側から見えるようここにも保持する
        self._container_format = recorded_video.container_format
        # DB 側で最後に分かっている録画時間を保持する
        ## PCR の読み取りに失敗した瞬間でも、プレイリストをゼロに戻さないための下限として使う。
        self._initial_duration_seconds = max(recorded_video.duration, 0.0)
        # MPEG-TS の PID / パケットサイズ情報を保持する
        ## PAT/PMT は録画先頭から一度取得できれば同じ tracker 内で使い回せる。
        self._stream_info: TSStreamInfo | None = None
        # 録画先頭キーフレームの DTS を保持する
        ## 末尾 PCR をプレイリスト上の相対秒へ変換する基準として使う。
        self._source_base_dts: int | None = None
        # ファイルの置き換えを検出するため、最初の stat で得たデバイス番号と inode を保持する
        ## 原子的な rename で同じパス・同じサイズの別ファイルへ切り替わっても、古い時刻基準を引き継がない。
        self._file_identity: tuple[int, int] | None = None
        # DB の初期時長が現在のファイルに対応しているかどうかを保持する
        self._is_metadata_fallback_valid = True
        # 最新の読み取り可能範囲を保持する
        self._snapshot = RecordingPlaybackSnapshot(
            file_size = recorded_video.file_size,
            file_modified_at = recorded_video.file_modified_at,
            available_duration_seconds = self._initial_duration_seconds,
            stream_info = None,
            source_base_dts = None,
            is_timeline_ready = False,
            is_metadata_fallback_valid = True,
            is_ready = False,
        )
        # 最後にプレイリスト / セグメント / keep-alive から参照された時刻を保持する
        self._last_accessed_at = time.monotonic()
        # ファイル末尾の再読込を直列化する
        self._refresh_lock = asyncio.Lock()
        # 最後にファイル末尾を読み直した monotonic 時刻を保持する
        ## 一覧 API・詳細 API・HLS playlist・worker が同時に参照しても、同じ1秒内に末尾走査を重複させない。
        self._last_refreshed_at: float | None = None
        # バックグラウンド worker の Task 参照を保持する
        ## asyncio の Task は弱参照管理のため、自然終了まで明示的に強参照する。
        self._worker_task_ref: asyncio.Task[None] | None = None


    @classmethod
    async def getOrCreate(cls, recorded_program: RecordedProgram) -> RecordingPlaybackTracker:
        """
        録画ファイル単位の共有トラッカーを取得する

        Args:
            recorded_program (RecordedProgram): 追っかけ再生対象の録画番組

        Returns:
            RecordingPlaybackTracker: 共有トラッカー
        """

        recorded_video = cast(RecordingPlaybackVideo, recorded_program.recorded_video)
        return await cls.__getOrCreateForVideo(recorded_program.id, recorded_video, start_worker=True)


    @classmethod
    async def getCurrentSnapshot(
        cls,
        recorded_program_id: int,
        recorded_video: RecordingPlaybackVideo,
    ) -> RecordingPlaybackSnapshot:
        """
        録画中ファイルの最新の進行状況を取得する

        一覧・詳細 API、追っかけ再生、録画中サムネイル生成が同じファイル単位の tracker を共有し、
        それぞれが重い末尾解析を重複して実行しないための共通入口として使用する。

        Args:
            recorded_program_id (int): 追っかけ再生対象の録画番組 ID
            recorded_video (RecordingPlaybackVideo): 追跡に必要な録画動画情報

        Returns:
            RecordingPlaybackSnapshot: 現時点のファイル進行状況
        """

        tracker = await cls.__getOrCreateForVideo(recorded_program_id, recorded_video, start_worker=False)
        return tracker.getSnapshot()


    @classmethod
    async def __getOrCreateForVideo(
        cls,
        recorded_program_id: int,
        recorded_video: RecordingPlaybackVideo,
        *,
        start_worker: bool,
    ) -> RecordingPlaybackTracker:
        """
        録画動画情報からファイル単位の共有トラッカーを取得する

        Args:
            recorded_program_id (int): 追っかけ再生対象の録画番組 ID
            recorded_video (RecordingPlaybackVideo): 追跡に必要な録画動画情報
            start_worker (bool): HLS 再生向けの定期末尾追跡 worker を起動するかどうか

        Returns:
            RecordingPlaybackTracker: 共有トラッカー
        """

        file_path = Path(recorded_video.file_path)
        tracker_key = str(file_path.resolve(strict=False))

        async with cls.__instances_lock:
            # API レスポンス作成だけに使われ、長時間参照されていない tracker を次の取得時に掃除する。
            ## 一覧取得を契機に1秒おきの末尾走査を始めることは避けつつ、30秒間隔の更新では解析コンテキストを再利用する。
            current_monotonic_time = time.monotonic()
            for stale_tracker_key, stale_tracker in list(cls.__instances.items()):
                if (
                    stale_tracker._worker_task_ref is None and
                    current_monotonic_time - stale_tracker._last_accessed_at > cls.PASSIVE_CACHE_TIMEOUT_SECONDS
                ):
                    cls.__instances.pop(stale_tracker_key, None)

            tracker = cls.__instances.get(tracker_key)
            # 同じパスが別の録画レコードへ再利用された場合は、以前の PCR / DTS 基準を引き継がない。
            if tracker is not None and tracker._recorded_video_id != recorded_video.id:
                # 古い worker は次のループで自然終了させる。
                ## finally 側は辞書の値が自分自身の場合だけ削除するため、新しい tracker を取り除くことはない。
                tracker._last_accessed_at = time.monotonic() - tracker.IDLE_TIMEOUT_SECONDS - 1.0
                tracker = None
            if tracker is None:
                tracker = RecordingPlaybackTracker(recorded_program_id, recorded_video)
                cls.__instances[tracker_key] = tracker
            if start_worker is True:
                tracker.start()

        tracker.touch()
        await tracker.refresh()
        return tracker


    def start(self) -> None:
        """
        録画ファイルの追跡 worker を開始する
        """

        if self._worker_task_ref is not None and self._worker_task_ref.done() is False:
            return

        self._worker_task_ref = asyncio.create_task(self.__run())
        logging.info(
            f'[RecordingPlaybackTracker: {self._recorded_program_id}/{self._recorded_video_id}] '
            f'Worker started. [file_path: {self._file_path}]'
        )


    def touch(self) -> None:
        """
        トラッカーがまだ利用中であることを記録する
        """

        self._last_accessed_at = time.monotonic()


    def getSnapshot(self) -> RecordingPlaybackSnapshot:
        """
        最新のスナップショットを取得する

        Returns:
            RecordingPlaybackSnapshot: 最新の読み取り可能範囲
        """

        return self._snapshot


    async def refresh(self) -> RecordingPlaybackSnapshot:
        """
        録画ファイルの現在状態を読み直してスナップショットを更新する

        Returns:
            RecordingPlaybackSnapshot: 更新後の読み取り可能範囲
        """

        async with self._refresh_lock:
            # worker と API リクエストが同時に到着しても、同じ期間内では直近スナップショットを再利用する。
            current_monotonic_time = time.monotonic()
            if (
                self._last_refreshed_at is not None and
                current_monotonic_time - self._last_refreshed_at < self.POLL_INTERVAL_SECONDS
            ):
                return self._snapshot

            # 途中の解析が失敗しても、stat や差し替え検出までに確定した安全な値は保持する。
            fallback_file_size = self._snapshot.file_size
            fallback_file_modified_at = self._snapshot.file_modified_at
            fallback_duration_seconds = self._snapshot.available_duration_seconds
            fallback_is_timeline_ready = self._snapshot.is_timeline_ready
            try:
                file_stat = await asyncio.to_thread(self._file_path.stat)
                file_modified_at = datetime.fromtimestamp(file_stat.st_mtime, UTC)
                fallback_file_size = file_stat.st_size
                fallback_file_modified_at = file_modified_at
                available_duration_seconds = self._snapshot.available_duration_seconds
                is_timeline_ready = self._snapshot.is_timeline_ready
                file_identity = (file_stat.st_dev, file_stat.st_ino)

                # inode が変わったかファイルサイズが小さくなった場合は、録画プロセスによる再作成・差し替えとして扱う。
                ## 古いファイルの先頭 DTS と長さを使い続けると、新しい録画の API 時長と安全境界を過大評価してしまう。
                if (
                    (self._file_identity is not None and file_identity != self._file_identity) or
                    file_stat.st_size < self._snapshot.file_size
                ):
                    self._stream_info = None
                    self._source_base_dts = None
                    # DB 値も差し替え前ファイル由来なので、新しいメディア時刻を読めるまで下限として使わない。
                    self._initial_duration_seconds = 0.0
                    self._is_metadata_fallback_valid = False
                    available_duration_seconds = 0.0
                    is_timeline_ready = False
                    fallback_duration_seconds = 0.0
                    fallback_is_timeline_ready = False
                self._file_identity = file_identity

                # v1 は MPEG-TS の PCR から追っかけ再生可能範囲を推定する。
                ## MP4 は moov が録画完了まで確定しないケースが多いため、DB にある既知の長さ以上へは伸ばさない。
                if self._container_format == 'MPEG-TS':
                    if self._stream_info is None:
                        self._stream_info = await asyncio.to_thread(
                            TSKeyFrameSeeker.findStreamInfo,
                            self._file_path,
                        )
                    if self._source_base_dts is None:
                        self._source_base_dts = await asyncio.to_thread(
                            TSKeyFrameSeeker.findBaseDTS,
                            self._file_path,
                            self._stream_info,
                        )

                    estimated_duration_seconds = await asyncio.to_thread(
                        TSKeyFrameSeeker.findAvailableDuration,
                        self._file_path,
                        self._stream_info,
                        self._source_base_dts,
                        previous_duration_seconds = available_duration_seconds,
                    )
                    if estimated_duration_seconds is not None:
                        # 録画中はファイルが伸びる方向なので、瞬間的な PCR 読み取りの揺れでプレイリストを縮めない。
                        available_duration_seconds = max(
                            available_duration_seconds,
                            estimated_duration_seconds,
                            self._initial_duration_seconds,
                        )
                        is_timeline_ready = True

                self._snapshot = RecordingPlaybackSnapshot(
                    file_size = file_stat.st_size,
                    file_modified_at = file_modified_at,
                    available_duration_seconds = available_duration_seconds,
                    stream_info = self._stream_info,
                    source_base_dts = self._source_base_dts,
                    is_timeline_ready = is_timeline_ready,
                    is_metadata_fallback_valid = self._is_metadata_fallback_valid,
                    is_ready = True,
                )
            except Exception as ex:
                logging.warning(
                    f'[RecordingPlaybackTracker: {self._recorded_program_id}/{self._recorded_video_id}] '
                    f'Failed to refresh recording playback snapshot.',
                    exc_info=ex,
                )
                self._snapshot = RecordingPlaybackSnapshot(
                    file_size = fallback_file_size,
                    file_modified_at = fallback_file_modified_at,
                    available_duration_seconds = max(
                        fallback_duration_seconds,
                        self._initial_duration_seconds,
                    ),
                    stream_info = self._stream_info,
                    source_base_dts = self._source_base_dts,
                    is_timeline_ready = fallback_is_timeline_ready,
                    is_metadata_fallback_valid = self._is_metadata_fallback_valid,
                    is_ready = False,
                )
            finally:
                # 成功・失敗のどちらでも短時間の再試行ループを避け、次の定期更新へ委ねる。
                self._last_refreshed_at = current_monotonic_time

            return self._snapshot


    async def __run(self) -> None:
        """
        録画ファイルを定期的に追跡する worker 本体
        """

        try:
            while True:
                idle_seconds = time.monotonic() - self._last_accessed_at
                if idle_seconds > self.IDLE_TIMEOUT_SECONDS:
                    break
                await self.refresh()
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
        finally:
            tracker_key = str(self._file_path.resolve(strict=False))
            async with self.__instances_lock:
                if self.__instances.get(tracker_key) is self:
                    self.__instances.pop(tracker_key)
            logging.info(
                f'[RecordingPlaybackTracker: {self._recorded_program_id}/{self._recorded_video_id}] '
                f'Worker stopped. [file_path: {self._file_path}]'
            )
