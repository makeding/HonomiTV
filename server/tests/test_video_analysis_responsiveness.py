import asyncio
import threading
import time
import unittest
from unittest.mock import patch

import anyio
import httpx
from fastapi import FastAPI

from app.utils.DriveIOLimiter import DriveIOLimiter
from app.utils.ProcessLimiter import ProcessLimiter


class VideoAnalysisResponsivenessTest(unittest.IsolatedAsyncioTestCase):
    """動画解析の資源待機中も API のイベントループが応答できることを検証する。"""

    async def asyncSetUp(self) -> None:
        """テストごとにプロセスとドライブの実行枠を初期化する。"""

        DriveIOLimiter._drive_semaphores.clear()
        ProcessLimiter._semaphores.clear()


    async def test_slow_drive_lookup_does_not_block_event_loop(self) -> None:
        """マウント情報の取得が待機してもイベントループ上の別リクエスト相当の処理は進む。"""

        lookup_started = threading.Event()
        release_lookup = threading.Event()

        def BlockDiskPartitions(*, all: bool) -> list[object]:
            """スレッド側のディスク列挙をテストが解放するまで待たせる。

            Args:
                all (bool): psutil.disk_partitions() の引数。

            Returns:
                list[object]: テスト用の空のマウント一覧。
            """

            lookup_started.set()
            release_lookup.wait(timeout=2)
            return []

        # 実際の ASGI リクエストを同じイベントループへ流し、解析開始が待機中でも API が返ることを確認する。
        app = FastAPI()

        @app.get('/scan')
        async def ScanAPI() -> dict[str, bool]:
            """録画解析開始時のディスク識別を模擬する。

            Returns:
                dict[str, bool]: 解析開始処理の完了状態。
            """

            await DriveIOLimiter.getSemaphore(anyio.Path('/recordings/video.ts'))
            return {'scanned': True}

        @app.get('/ping')
        async def PingAPI() -> dict[str, bool]:
            """解析とは無関係な通常の API リクエストを模擬する。

            Returns:
                dict[str, bool]: API の応答状態。
            """

            return {'ready': True}

        with (
            patch('app.utils.DriveIOLimiter.psutil.WINDOWS', False),
            patch('app.utils.DriveIOLimiter.psutil.disk_partitions', side_effect=BlockDiskPartitions),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://testserver') as client:
                scan_task = asyncio.create_task(client.get('/scan'))
                try:
                    self.assertTrue(await asyncio.to_thread(lookup_started.wait, 1))
                    started_at = time.monotonic()
                    response = await asyncio.wait_for(client.get('/ping'), timeout=0.5)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json(), {'ready': True})
                    self.assertLess(time.monotonic() - started_at, 0.5)
                    self.assertFalse(scan_task.done())
                finally:
                    release_lookup.set()
                    self.assertEqual((await scan_task).status_code, 200)


    async def test_video_analysis_slots_are_reused_and_bounded(self) -> None:
        """メタデータ解析と後続解析に各1枠を確保し、同じ種類の2件目を待たせる。"""

        metadata_slot = ProcessLimiter.getSemaphore('RecordedScanTask.MetadataAnalyzer', max_concurrency=1)
        background_slot = ProcessLimiter.getSemaphore('RecordedScanTask', max_concurrency=1)
        self.assertIs(metadata_slot, ProcessLimiter.getSemaphore('RecordedScanTask.MetadataAnalyzer', max_concurrency=1))
        self.assertIsNot(metadata_slot, background_slot)

        await metadata_slot.acquire()
        await background_slot.acquire()
        try:
            waiting_metadata = asyncio.create_task(metadata_slot.acquire())
            waiting_background = asyncio.create_task(background_slot.acquire())
            await asyncio.sleep(0)
            self.assertFalse(waiting_metadata.done())
            self.assertFalse(waiting_background.done())
        finally:
            metadata_slot.release()
            background_slot.release()

        self.assertTrue(await asyncio.wait_for(waiting_metadata, timeout=0.5))
        self.assertTrue(await asyncio.wait_for(waiting_background, timeout=0.5))
        metadata_slot.release()
        background_slot.release()
