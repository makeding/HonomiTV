import unittest
from unittest.mock import AsyncMock, Mock, patch

from app.streams.StreamEncodingOptions import SplitLiveQualityAndEncodingOptions
from app.streams.LiveEncodingTask import LiveEncodingTask


class LiveStreamEncodingOptionsTest(unittest.TestCase):
    """ライブ配信専用画質が通常のエンコード画質へ混入しないことを検証する。"""

    def test_original_is_accepted_without_encoding_options(self) -> None:
        """原始 MPEG-TS 配信の original を専用画質として受け付ける。"""

        result = SplitLiveQualityAndEncodingOptions('original')

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.quality, 'original')
        self.assertEqual(result.encoding_options.buildSuffix(), '')

    def test_raw_mmts_remains_separate_from_original(self) -> None:
        """BS4K の Raw MMTS を original と混同せず専用画質のまま保持する。"""

        result = SplitLiveQualityAndEncodingOptions('raw-mmts')

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.quality, 'raw-mmts')
        self.assertEqual(result.encoding_options.buildSuffix(), '')

    def test_recorded_copy_quality_is_rejected_for_live_streams(self) -> None:
        """録画 HLS 専用の copy をライブ配信では受け付けない。"""

        self.assertIsNone(SplitLiveQualityAndEncodingOptions('copy'))


class BS4KLiveEncodingTest(unittest.IsolatedAsyncioTestCase):
    """BS4K の 10-bit 入力と専用チューナー種別を検証する。"""

    def test_h264_high_receives_eight_bit_frames(self) -> None:
        """10-bit の BS4K を 8-bit に変換してから H.264 High に渡す。"""

        task = object.__new__(LiveEncodingTask)
        task._retry_count = 0
        task.live_stream = Mock()
        options = task.buildFFmpegOptions('1080p', 'BS4K', True, True)
        self.assertEqual(options[options.index('-vf') + 1], 'scale=1920:1080,format=yuv420p')

    async def test_bs_tuner_does_not_satisfy_bs4k_preflight(self) -> None:
        """BS 専用 PT3 が空いていても BS4K の空きチューナーとは報告しない。"""

        task = object.__new__(LiveEncodingTask)
        task.live_stream = Mock()
        task.live_stream.log_prefix = '[Live: bs4k101-1080p]'
        client = AsyncMock()
        client.get.return_value = Mock(
            headers={},
            json=Mock(return_value=[
                {'name': 'PT3-S2', 'types': ['BS'], 'isAvailable': True, 'isFree': True},
            ]),
        )
        context = AsyncMock()
        context.__aenter__.return_value = client
        with patch('app.streams.LiveEncodingTask.GetBackendForReceiving', return_value='Mirakurun'), \
                patch('app.streams.LiveEncodingTask.HTTPX_CLIENT', return_value=context), \
                patch('app.streams.LiveEncodingTask.asyncio.sleep', new_callable=AsyncMock):
            self.assertFalse(await task.acquireMirakurunTuner('BS4K'))


if __name__ == '__main__':
    unittest.main()
