# pyright: reportPrivateUsage=false

import asyncio
import unittest
from unittest.mock import patch

import anyio

from app.metadata.CMSectionsDetector import CMSectionsDetector


class CMSectionsDetectorPolicyTest(unittest.TestCase):
    """コンテナ形式と設定による CM 解析対象の選択を検証する。"""

    def test_mmt_tlv_analysis_is_disabled_by_default(self) -> None:
        """高負荷な MMT/TLV 解析は明示的に有効化されるまで実行しない。"""

        self.assertFalse(CMSectionsDetector.shouldAnalyze('MMT/TLV', False))
        self.assertTrue(CMSectionsDetector.shouldAnalyze('MMT/TLV', True))


    def test_mpeg_ts_analysis_is_not_affected_by_mmt_tlv_setting(self) -> None:
        """通常の MPEG-TS 解析は MMT/TLV 設定が無効でも継続する。"""

        self.assertTrue(CMSectionsDetector.shouldAnalyze('MPEG-TS', False))


    def test_opencv_mode_skips_non_mpeg_ts(self) -> None:
        """OpenCV 方式は放送 TS のみを対象とし、MPEG-4 / MMT/TLV は解析しない。"""

        self.assertTrue(CMSectionsDetector.shouldAnalyze('MPEG-TS', False, 'OpenCV'))
        self.assertFalse(CMSectionsDetector.shouldAnalyze('MPEG-4', False, 'OpenCV'))
        self.assertFalse(CMSectionsDetector.shouldAnalyze('MMT/TLV', True, 'OpenCV'))


    def test_fallback_mode_keeps_mmt_tlv_policy(self) -> None:
        """Fallback 方式は join_logo_scp を優先するため、MMT/TLV の解析可否は従来通り設定に従う。"""

        self.assertFalse(CMSectionsDetector.shouldAnalyze('MMT/TLV', False, 'Fallback'))
        self.assertTrue(CMSectionsDetector.shouldAnalyze('MMT/TLV', True, 'Fallback'))


class CMSectionsDetectorConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    """CM 区間検出のサーバー全体における独占実行を検証する。"""

    async def asyncSetUp(self) -> None:
        """テストごとに実行中のイベントループに紐づくセマフォを用意する。"""

        CMSectionsDetector._CMSectionsDetector__detection_semaphore = asyncio.Semaphore(1)


    @staticmethod
    def CreateDetector(file_name: str) -> CMSectionsDetector:
        """
        実ファイルを読まずに並行制御だけを検証する Detector を作成する。

        Args:
            file_name (str): 識別用の録画ファイル名。

        Returns:
            CMSectionsDetector: テスト対象の Detector。
        """

        return CMSectionsDetector(
            file_path=anyio.Path(file_name),
            duration_sec=60.0,
            container_format='MPEG-TS',
            # 並行制御だけを検証したいので、外部ツール不要の OpenCV 方式にはフォールバックさせず join_logo_scp 経路に固定する
            cm_detect_mode='JoinLogoScp',
        )


    async def test_detect_and_save_runs_only_one_detection_at_a_time(self) -> None:
        """複数の録画が同時に解析待ちになっても CM 検出は1件ずつ実行する。"""

        first_detection_entered = asyncio.Event()
        release_first_detection = asyncio.Event()
        active_detection_count = 0
        maximum_active_detection_count = 0
        detection_call_count = 0

        async def DetectFromChapterFile(_detector: CMSectionsDetector):
            return None

        async def DetectWithJLS(_detector: CMSectionsDetector):
            nonlocal active_detection_count, maximum_active_detection_count, detection_call_count
            detection_call_count += 1
            active_detection_count += 1
            maximum_active_detection_count = max(maximum_active_detection_count, active_detection_count)
            try:
                # 先頭の検出を停止し、2件目が検出本体へ入れないことを確認する。
                if detection_call_count == 1:
                    first_detection_entered.set()
                    await release_first_detection.wait()
                return None
            finally:
                active_detection_count -= 1

        with (
            patch.object(
                CMSectionsDetector,
                '_CMSectionsDetector__detectFromChapterFile',
                DetectFromChapterFile,
            ),
            patch.object(
                CMSectionsDetector,
                '_CMSectionsDetector__detectWithJLS',
                DetectWithJLS,
            ),
        ):
            first_task = asyncio.create_task(self.CreateDetector('first.ts').detectAndSave())
            await first_detection_entered.wait()
            second_task = asyncio.create_task(self.CreateDetector('second.ts').detectAndSave())
            await asyncio.sleep(0)

            self.assertEqual(detection_call_count, 1)
            self.assertEqual(maximum_active_detection_count, 1)

            release_first_detection.set()
            await asyncio.gather(first_task, second_task)

        self.assertEqual(detection_call_count, 2)
        self.assertEqual(maximum_active_detection_count, 1)


    async def test_cancelling_active_detection_releases_next_waiter(self) -> None:
        """実行中の CM 検出がキャンセルされた場合も次の待機タスクを開始できる。"""

        first_detection_entered = asyncio.Event()
        second_detection_entered = asyncio.Event()
        block_first_detection = asyncio.Event()
        detection_call_count = 0

        async def DetectFromChapterFile(_detector: CMSectionsDetector):
            return None

        async def DetectWithJLS(_detector: CMSectionsDetector):
            nonlocal detection_call_count
            detection_call_count += 1
            if detection_call_count == 1:
                first_detection_entered.set()
                await block_first_detection.wait()
            else:
                second_detection_entered.set()
            return None

        with (
            patch.object(
                CMSectionsDetector,
                '_CMSectionsDetector__detectFromChapterFile',
                DetectFromChapterFile,
            ),
            patch.object(
                CMSectionsDetector,
                '_CMSectionsDetector__detectWithJLS',
                DetectWithJLS,
            ),
        ):
            first_task = asyncio.create_task(self.CreateDetector('first.ts').detectAndSave())
            await first_detection_entered.wait()
            second_task = asyncio.create_task(self.CreateDetector('second.ts').detectAndSave())
            await asyncio.sleep(0)

            first_task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await first_task

            await asyncio.wait_for(second_detection_entered.wait(), timeout=1.0)
            await second_task

        self.assertEqual(detection_call_count, 2)
