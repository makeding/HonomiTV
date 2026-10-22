import unittest
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, patch

from app.routers.VideosRouter import ApplyRecordingPlaybackSnapshot
from app.streams.RecordingPlaybackTracker import (
    RecordingPlaybackSnapshot,
    RecordingPlaybackTracker,
    RecordingPlaybackVideo,
)
from app.streams.VideoStream import VideoStream
from app.utils.TSKeyFrameSeeker import TSStreamInfo


class RecordingPlaybackProgressTest(unittest.IsolatedAsyncioTestCase):
    """録画中ファイルの実メディア時長を API と安全な再生境界へ反映する契約を検証する。"""

    def createRecordedVideo(
        self,
        *,
        container_format: str = 'MPEG-TS',
        duration: float = 60.0,
        file_size: int = 1_000,
    ) -> RecordingPlaybackVideo:
        """
        tracker が参照する最小の録画動画情報を作成する。

        Args:
            container_format (str): テスト対象のコンテナ形式。
            duration (float): DB に保存済みの初期時長。
            file_size (int): DB に保存済みの初期ファイルサイズ。

        Returns:
            RecordingPlaybackVideo: tracker 入力用の録画動画情報。
        """

        return cast(RecordingPlaybackVideo, SimpleNamespace(
            id = 10,
            file_path = '/recordings/growing.ts',
            file_size = file_size,
            file_modified_at = datetime(2026, 10, 22, tzinfo=UTC),
            duration = duration,
            container_format = container_format,
        ))


    async def test_mpeg_ts_progress_is_monotonic_until_source_is_replaced(self) -> None:
        """PCR の一時的な揺れでは戻さず、ファイル縮小時だけ新しい時間軸へ切り替える。"""

        recorded_video = self.createRecordedVideo()
        tracker = RecordingPlaybackTracker(1, recorded_video)
        stream_info = TSStreamInfo(video_pid=256, pcr_pid=256, codec='H.264', packet_size=188)

        with (
            patch('app.streams.RecordingPlaybackTracker.Path.stat', side_effect=[
                SimpleNamespace(st_size=2_000, st_mtime=1_760_000_000, st_dev=1, st_ino=100),
                SimpleNamespace(st_size=2_500, st_mtime=1_760_000_001, st_dev=1, st_ino=100),
                SimpleNamespace(st_size=500, st_mtime=1_760_000_002, st_dev=1, st_ino=101),
            ]),
            patch('app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findStreamInfo', return_value=stream_info),
            patch('app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findBaseDTS', return_value=90_000),
            patch('app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findAvailableDuration', side_effect=[305.0, 302.0, 40.0]),
        ):
            first_snapshot = await tracker.refresh()
            tracker._last_refreshed_at = None
            second_snapshot = await tracker.refresh()
            tracker._last_refreshed_at = None
            replaced_snapshot = await tracker.refresh()

        self.assertTrue(first_snapshot.is_timeline_ready)
        self.assertEqual(first_snapshot.available_duration_seconds, 305.0)
        self.assertEqual(second_snapshot.available_duration_seconds, 305.0)
        self.assertEqual(second_snapshot.file_size, 2_500)
        self.assertTrue(replaced_snapshot.is_timeline_ready)
        self.assertEqual(replaced_snapshot.available_duration_seconds, 40.0)


    async def test_unsupported_container_keeps_initial_duration_without_media_time_claim(self) -> None:
        """MMT/TLV はファイル増加を時長へ換算せず、未対応であることを明示する。"""

        recorded_video = self.createRecordedVideo(container_format='MMT/TLV', duration=75.0)
        tracker = RecordingPlaybackTracker(1, recorded_video)

        with (
            patch(
                'app.streams.RecordingPlaybackTracker.Path.stat',
                return_value=SimpleNamespace(st_size=8_000, st_mtime=1_760_000_000, st_dev=1, st_ino=100),
            ),
            patch('app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findStreamInfo') as find_stream_info,
        ):
            snapshot = await tracker.refresh()

        self.assertFalse(snapshot.is_timeline_ready)
        self.assertEqual(snapshot.available_duration_seconds, 75.0)
        self.assertEqual(snapshot.file_size, 8_000)
        find_stream_info.assert_not_called()


    async def test_ts_probe_failure_retains_initial_metadata_fallback(self) -> None:
        """MPEG-TS の初回解析が一時的に失敗しても、DB の既知時長をゼロへ戻さない。"""

        recorded_video = self.createRecordedVideo(duration=60.0)
        tracker = RecordingPlaybackTracker(1, recorded_video)

        with (
            patch(
                'app.streams.RecordingPlaybackTracker.Path.stat',
                return_value=SimpleNamespace(st_size=2_000, st_mtime=1_760_000_000, st_dev=1, st_ino=100),
            ),
            patch(
                'app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findStreamInfo',
                side_effect=RuntimeError('PAT/PMT is not ready'),
            ),
        ):
            snapshot = await tracker.refresh()

        self.assertFalse(snapshot.is_ready)
        self.assertFalse(snapshot.is_timeline_ready)
        self.assertEqual(snapshot.available_duration_seconds, 60.0)


    async def test_same_size_replacement_invalidates_old_timeline_before_reprobe(self) -> None:
        """同じサイズの別 inode へ置き換わり再解析に失敗しても、古い時長を公開しない。"""

        recorded_video = self.createRecordedVideo()
        tracker = RecordingPlaybackTracker(1, recorded_video)
        stream_info = TSStreamInfo(video_pid=256, pcr_pid=256, codec='H.264', packet_size=188)

        with (
            patch('app.streams.RecordingPlaybackTracker.Path.stat', side_effect=[
                SimpleNamespace(st_size=2_000, st_mtime=1_760_000_000, st_dev=1, st_ino=100),
                SimpleNamespace(st_size=2_000, st_mtime=1_760_000_001, st_dev=1, st_ino=101),
            ]),
            patch(
                'app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findStreamInfo',
                side_effect=[stream_info, RuntimeError('replacement PAT/PMT is not ready')],
            ),
            patch('app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findBaseDTS', return_value=90_000),
            patch('app.streams.RecordingPlaybackTracker.TSKeyFrameSeeker.findAvailableDuration', return_value=305.0),
        ):
            first_snapshot = await tracker.refresh()
            tracker._last_refreshed_at = None
            replacement_snapshot = await tracker.refresh()

        self.assertEqual(first_snapshot.available_duration_seconds, 305.0)
        self.assertFalse(replacement_snapshot.is_timeline_ready)
        self.assertEqual(replacement_snapshot.available_duration_seconds, 0.0)


    async def test_api_uses_dynamic_duration_only_for_recording_with_timeline(self) -> None:
        """API レスポンスは録画中の実メディア時長だけを更新し、録画済みの確定値を維持する。"""

        recording_video = SimpleNamespace(
            id=10,
            status='Recording',
            file_path='/recordings/growing.ts',
            file_size=1_000,
            file_modified_at=datetime(2026, 10, 22, tzinfo=UTC),
            duration=600.0,
            container_format='MPEG-TS',
        )
        recording_program = SimpleNamespace(id=1, recorded_video=recording_video)
        snapshot = RecordingPlaybackSnapshot(
            file_size=2_000,
            file_modified_at=datetime(2026, 10, 22, 0, 5, tzinfo=UTC),
            available_duration_seconds=305.0,
            stream_info=None,
            source_base_dts=None,
            is_timeline_ready=True,
            is_metadata_fallback_valid=True,
            is_ready=True,
        )

        with patch.object(RecordingPlaybackTracker, 'getCurrentSnapshot', AsyncMock(return_value=snapshot)) as get_snapshot:
            await ApplyRecordingPlaybackSnapshot(recording_program)
            self.assertEqual(recording_video.duration, 305.0)
            recording_video.status = 'Recorded'
            recording_video.duration = 1_800.0
            await ApplyRecordingPlaybackSnapshot(recording_program)

            # 同じパスの別ファイルへ差し替わった直後は、古い DB 時長も API へ公開しない。
            recording_video.status = 'Recording'
            get_snapshot.return_value = RecordingPlaybackSnapshot(
                file_size=500,
                file_modified_at=datetime(2026, 10, 22, 0, 6, tzinfo=UTC),
                available_duration_seconds=0.0,
                stream_info=None,
                source_base_dts=None,
                is_timeline_ready=False,
                is_metadata_fallback_valid=False,
                is_ready=False,
            )
            await ApplyRecordingPlaybackSnapshot(recording_program)

        self.assertEqual(recording_video.file_size, 500)
        self.assertEqual(recording_video.duration, 0.0)
        self.assertEqual(get_snapshot.await_count, 2)


    async def test_playlist_hides_two_segments_behind_observed_ts_edge(self) -> None:
        """追いかけ再生 playlist は観測済み MPEG-TS 末尾から2セグメント分を公開しない。"""

        recorded_video = SimpleNamespace(
            id=10,
            status='Recording',
            file_path='/recordings/growing.ts',
            file_size=2_000,
            file_modified_at=datetime(2026, 10, 22, tzinfo=UTC),
            duration=600.0,
            container_format='MPEG-TS',
        )
        video_stream = object.__new__(VideoStream)
        video_stream.recorded_program = SimpleNamespace(id=1, recorded_video=recorded_video)
        video_stream._segment_duration_seconds = 6.0
        video_stream._recording_playback_tracker = None
        video_stream._ts_stream_info = None
        video_stream._ts_source_base_dts = None
        snapshot = RecordingPlaybackSnapshot(
            file_size=2_000,
            file_modified_at=recorded_video.file_modified_at,
            available_duration_seconds=305.0,
            stream_info=None,
            source_base_dts=None,
            is_timeline_ready=True,
            is_metadata_fallback_valid=True,
            is_ready=True,
        )
        tracker = SimpleNamespace(getSnapshot=lambda: snapshot)

        with patch.object(RecordingPlaybackTracker, 'getOrCreate', AsyncMock(return_value=tracker)):
            playlist_duration = await video_stream._VideoStream__getPlaylistDuration()

            # 差し替え後の再解析がまだ終わっていない場合は、DB の旧 600 秒を安全境界へ戻さない。
            tracker.getSnapshot = lambda: RecordingPlaybackSnapshot(
                file_size=500,
                file_modified_at=recorded_video.file_modified_at,
                available_duration_seconds=0.0,
                stream_info=None,
                source_base_dts=None,
                is_timeline_ready=False,
                is_metadata_fallback_valid=False,
                is_ready=False,
            )
            replaced_playlist_duration = await video_stream._VideoStream__getPlaylistDuration()

        self.assertEqual(playlist_duration, 293.0)
        self.assertEqual(replaced_playlist_duration, 0.001)


if __name__ == '__main__':
    unittest.main()
