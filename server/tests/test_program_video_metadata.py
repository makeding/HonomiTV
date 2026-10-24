import unittest
from datetime import datetime
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

from tortoise import Tortoise

from app.constants import DATABASE_CONFIG, JST
from app.models.Channel import Channel
from app.models.Program import Program


class ProgramVideoMetadataTest(unittest.IsolatedAsyncioTestCase):
    """未定義の映像記述が EPG 更新全体を止めないことを検証する。"""

    async def test_unknown_stream_content_preserves_program_batch(self) -> None:
        """
        未知・既知の映像記述を実際の更新処理で保存する。

        Args:
            なし。

        Returns:
            None
        """
        # 本番 DB を変更せず、実際のトランザクションと保存処理を通す。
        await Tortoise.init(
            db_url='sqlite://:memory:',
            modules={'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models'])},
            timezone=DATABASE_CONFIG['timezone'],
        )
        try:
            await Tortoise.generate_schemas()
            await Channel.create(
                id='NID1-SID001', display_channel_id='bs001', network_id=1, service_id=1,
                transport_stream_id=None, remocon_id=1, channel_number='001', type='BS', name='Test',
                is_subchannel=False, is_radiochannel=False, is_watchable=True,
            )
            start_at = int(datetime.now(JST).timestamp() * 1000)
            # 9 は今回の失敗値。既知の値・未知の componentType・情報なしも後続に置く。
            videos = [
                {'streamContent': 9, 'componentType': 0xB3, 'type': 'H.265', 'resolution': '2160p'},
                {'streamContent': 1, 'componentType': 0xB3, 'type': 'MPEG-2', 'resolution': '1080i'},
                {'streamContent': 1, 'componentType': 0xFF, 'type': 'MPEG-2', 'resolution': '1080i'},
                {'streamContent': None, 'componentType': None, 'type': 'H.265', 'resolution': '2160p'},
            ]
            programs = [dict(
                networkId=1, serviceId=1, eventId=index, name=f'Test {index}', description='Test',
                startAt=start_at + index * 3600000, duration=3600000, isFree=True, video=video, audios=[
                    {'componentType': 3, 'langs': ['jpn'], 'samplingRate': 48000},
                ],
            ) for index, video in enumerate(videos)]
            client = AsyncMock()
            client.get.return_value = Mock(status_code=200, json=Mock(return_value=programs))
            with patch('app.models.Program.HTTPX_CLIENT') as factory, \
                    patch('app.models.Program.GetMirakurunAPIEndpointURL', return_value='http://tuner/api/programs'), \
                    patch('app.models.Program.logging.error') as error:
                factory.return_value.__aenter__.return_value = client
                await Program.updateFromMirakurun()
                error.assert_not_called()
            stored = await Program.all().order_by('event_id')
            self.assertEqual(len(stored), 4)
            self.assertEqual(stored[0].video_type, 'Unknown')
            self.assertEqual(stored[0].video_codec, 'H.265')
            self.assertEqual(stored[0].video_resolution, '2160p')
            self.assertNotEqual(stored[1].video_type, 'Unknown')
            self.assertEqual(stored[2].video_type, 'Unknown')
            self.assertEqual(stored[3].video_type, 'Unknown')
        finally:
            await Tortoise.close_connections()
