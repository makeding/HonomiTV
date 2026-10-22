import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import anyio

from app.metadata.RecordedScanTask import RecordedScanTask


class RecordedScanTaskLifecycleTest(unittest.IsolatedAsyncioTestCase):
    """ローカル録画監視と録画バックエンド同期のライフサイクル分離を検証する。"""

    async def asyncSetUp(self) -> None:
        """各テストでシングルトンのタスク状態を初期化する。"""

        RecordedScanTask._RecordedScanTask__instance = None  # type: ignore[attr-defined]
        minimal_config = SimpleNamespace(
            general = SimpleNamespace(backend='EPGStation'),
            video = SimpleNamespace(recorded_folders=[]),
        )
        with patch('app.metadata.RecordedScanTask.Config', return_value=minimal_config):
            self.scan_task = RecordedScanTask()


    async def asyncTearDown(self) -> None:
        """テスト終了時に生成したバックグラウンドタスクを停止する。"""

        await self.scan_task.stop()
        RecordedScanTask._RecordedScanTask__instance = None  # type: ignore[attr-defined]


    async def test_backend_sync_does_not_start_local_folder_scan_or_watch(self) -> None:
        """EDCB / EPGStation 用同期がローカル全件スキャンと変更監視を起動しないことを検証する。"""

        scan_task = self.scan_task
        with (
            patch.object(scan_task, '_RecordedScanTask__syncActiveRecordingFiles', new=AsyncMock()),
            patch.object(scan_task, '_RecordedScanTask__syncEPGStationRecentRecordedFiles', new=AsyncMock()),
            patch.object(scan_task, 'runBatchScan', new=AsyncMock()) as run_batch_scan,
            patch.object(scan_task, 'watchRecordedFolders', new=AsyncMock()) as watch_recorded_folders,
        ):
            await scan_task.startBackendRecordingSync()
            await asyncio.sleep(0)
            await scan_task.stopBackendRecordingSync()

        run_batch_scan.assert_not_awaited()
        watch_recorded_folders.assert_not_awaited()


    async def test_local_monitor_does_not_start_backend_sync(self) -> None:
        """Mirakurun 用ローカル監視が録画バックエンド同期を暗黙に起動しないことを検証する。"""

        scan_task = self.scan_task
        with (
            patch.object(scan_task, 'runBatchScan', new=AsyncMock()),
            patch.object(scan_task, 'watchRecordedFolders', new=AsyncMock()),
            patch.object(scan_task, 'startBackendRecordingSync', new=AsyncMock()) as start_backend_sync,
        ):
            await scan_task.start()
            await asyncio.sleep(0)
            await scan_task.stop()

        start_backend_sync.assert_not_awaited()


    async def test_early_thumbnail_cancellation_is_deduplicated_and_waits_for_cleanup(self) -> None:
        """同じ録画の重複起動を防ぎ、完了時の cancel が公開前 cleanup の完了まで待つことを検証する。"""

        scan_task = self.scan_task
        file_path = anyio.Path('/recordings/active.ts')
        started = asyncio.Event()
        cleanup_finished = asyncio.Event()
        run_count = 0
        publication_count = 0

        async def RunEarlyThumbnail(_file_path: anyio.Path) -> None:
            """公開待ちの録画中タスクを模擬し、キャンセル後に cleanup を完了する。"""

            nonlocal run_count, publication_count
            run_count += 1
            started.set()
            try:
                await asyncio.Future()
                publication_count += 1
            finally:
                # cancel 側が単に参照を捨てず、この cleanup の完了まで待つことを判定する。
                await asyncio.sleep(0)
                cleanup_finished.set()

        with patch.object(
            scan_task,
            '_RecordedScanTask__runEarlyRecordingThumbnail',
            new=RunEarlyThumbnail,
        ):
            scan_task._RecordedScanTask__scheduleEarlyRecordingThumbnail(file_path)  # type: ignore[attr-defined]
            first_task = scan_task._early_thumbnail_tasks[file_path]  # type: ignore[attr-defined]
            scan_task._RecordedScanTask__scheduleEarlyRecordingThumbnail(file_path)  # type: ignore[attr-defined]
            self.assertIs(scan_task._early_thumbnail_tasks[file_path], first_task)  # type: ignore[attr-defined]
            await started.wait()

            await scan_task._RecordedScanTask__cancelEarlyRecordingThumbnail(file_path)  # type: ignore[attr-defined]

        self.assertTrue(cleanup_finished.is_set())
        self.assertEqual(run_count, 1)
        self.assertEqual(publication_count, 0)
        self.assertNotIn(file_path, scan_task._early_thumbnail_tasks)  # type: ignore[attr-defined]
        await asyncio.sleep(0)
        self.assertEqual(run_count, 1)
