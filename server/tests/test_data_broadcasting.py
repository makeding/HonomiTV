import asyncio
import unittest
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, Mock, patch

import httpx
from fastapi import FastAPI
from tortoise import Tortoise

from app.constants import DATABASE_CONFIG
from app.models.Channel import Channel
from app.routers.ChannelsRouter import router as channels_router
from app.routers.LiveStreamsRouter import router as streams_router
from app.streams.LiveDataBroadcastingStream import LiveDataBroadcastingStream


class DataBroadcastingTest(unittest.IsolatedAsyncioTestCase):
    """データサービスの探索・HTTP・接続の所有権を検証する。"""

    async def asyncSetUp(self) -> None:
        """既存の通常局がある SQLite から各検証を始める。"""
        await Tortoise.init(
            db_url='sqlite://:memory:',
            modules={'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models'])},
            timezone=DATABASE_CONFIG['timezone'],
        )
        await Tortoise.generate_schemas()
        Channel._data_channel_ids = set()
        await Channel.create(
            id='NID4-SID191', display_channel_id='bs191', network_id=4, service_id=191,
            transport_stream_id=None, remocon_id=9, channel_number='191', type='BS', name='TV',
            is_subchannel=False, is_radiochannel=False, is_watchable=True,
        )
        self.app = FastAPI()
        self.app.include_router(channels_router)
        self.app.include_router(streams_router)

    async def asyncTearDown(self) -> None:
        """接続とプロセス内メタデータを残さない。"""
        Channel._data_channel_ids = set()
        await Tortoise.close_connections()

    async def discover(self, backend: str) -> None:
        """
        両受信バックエンドの実際の探索処理を動かす。

        Args:
            backend (str): Mirakurun または EDCB。

        Returns:
            None
        """
        # 既存の TV・独立データ放送・地デジワンセグを同時に返す。
        services = [
            {'networkId': 4, 'serviceId': 191, 'type': 1, 'name': 'TV'},
            {'networkId': 4, 'serviceId': 791, 'type': 0xc0, 'name': 'ご案内チャンネル'},
            {'networkId': 32736, 'serviceId': 1024, 'type': 0xc0, 'name': 'ワンセグ'},
        ]
        with patch('app.models.Channel.Config', return_value=SimpleNamespace(tv=SimpleNamespace(preferred_terrestrial_region=None))):
            if backend == 'Mirakurun':
                client = AsyncMock()
                client.get.return_value = Mock(status_code=200, json=Mock(return_value=services))
                with patch('app.models.Channel.HTTPX_CLIENT') as factory, patch('app.models.Channel.GetMirakurunAPIEndpointURL', return_value='http://tuner/api/services'):
                    factory.return_value.__aenter__.return_value = client
                    await Channel.updateFromMirakurun()
            else:
                edcb_services = [
                    {'onid': service['networkId'], 'sid': service['serviceId'], 'tsid': 16432,
                     'service_type': service['type'], 'service_name': service['name'], 'remocon_id': 0}
                    for service in services
                ]
                with patch('app.models.Channel.CtrlCmdUtil') as edcb, patch('app.models.Channel.EDCBUtil.parseChSet5', return_value=edcb_services):
                    edcb.return_value.sendFileCopy = AsyncMock(return_value=b'channels')
                    edcb.return_value.sendEnumService = AsyncMock(return_value=[])
                    await Channel.updateFromEDCB()

    async def test_discovery_and_complete_channel_http_response(self) -> None:
        """通常局を保ったままデータ局が一覧・詳細の両方に見える。"""
        for backend in ['Mirakurun', 'EDCB']:
            with self.subTest(backend=backend):
                await self.discover(backend)
                self.assertEqual(await Channel.all().count(), 2)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
                    response = await client.get('/api/channels?source=Broadcast')
                    self.assertEqual(response.status_code, 200, response.text)
                    channels = response.json()['BS']
                    self.assertEqual([channel['display_channel_id'] for channel in channels], ['bs191', 'bs791'])
                    data = channels[1]
                    self.assertTrue(data['is_datachannel'])
                    self.assertFalse(channels[0]['is_datachannel'])
                    self.assertEqual(data['network_id'], 4)
                    self.assertEqual(data['service_id'], 791)
                    self.assertEqual(data['name'], 'ご案内チャンネル')
                    self.assertEqual(data['type'], 'BS')
                    self.assertTrue(data['is_display'])
                    self.assertTrue(data['is_watchable'])
                    self.assertIsNone(data['program_present'])
                    self.assertIsNone(data['program_following'])
                    detail = await client.get('/api/channels/bs791')
                    self.assertEqual(detail.status_code, 200, detail.text)
                    self.assertEqual(detail.json(), data)

    async def test_data_http_stream_and_error(self) -> None:
        """HTTP は TS を改変せず返し、映像タスクを起動しない。"""
        await self.discover('Mirakurun')
        response_upstream = Mock(status=200)
        response_upstream.content.read = AsyncMock(side_effect=[b'\x47data', b''])
        session = Mock(get=AsyncMock(return_value=response_upstream), close=AsyncMock())
        with patch('app.streams.LiveDataBroadcastingStream.GetBackendForReceiving', return_value='Mirakurun'), patch('app.streams.LiveDataBroadcastingStream.GetMirakurunAPIEndpointURL', return_value='http://tuner/api/services/400791/stream'), patch('app.streams.LiveDataBroadcastingStream.aiohttp.ClientSession', return_value=session), patch('app.streams.LiveStream.LiveEncodingTask.run', new_callable=AsyncMock) as encode:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url='http://test') as client:
                response = await client.get('/api/streams/live/bs791/data-broadcasting')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'\x47data')
                self.assertEqual(response.headers['cache-control'], 'no-store')
                response_upstream.close.assert_called_once()
                session.close.assert_awaited_once()
                encode.assert_not_awaited()
                rejected = await client.get('/api/streams/live/bs191/data-broadcasting')
                self.assertEqual(rejected.status_code, 422)
                response_upstream.status = 503
                unavailable = await client.get('/api/streams/live/bs791/data-broadcasting')
                self.assertEqual(unavailable.status_code, 503)
                self.assertIn('DATA_TUNER_HTTP', unavailable.json()['detail'])

    async def test_edcb_disconnect_and_cancel_release_only_owned_tuner(self) -> None:
        """退出と起動中のキャンセルのどちらでも EDCB チューナーを解放する。"""
        channel = await Channel.get(display_channel_id='bs191')
        tuner = Mock(setChannel=AsyncMock(return_value=True), connect=AsyncMock(), close=AsyncMock())
        reader = Mock(readexactly=AsyncMock(return_value=b'\x47' * 3008))
        tuner.connect.return_value = reader
        with patch('app.streams.LiveDataBroadcastingStream.GetBackendForReceiving', return_value='EDCB'), patch('app.streams.LiveDataBroadcastingStream.EDCBTuner.getOrCreate', return_value=tuner):
            stream = LiveDataBroadcastingStream(channel)
            await stream.open()
            chunks = stream.chunks()
            await anext(chunks)
            await chunks.aclose()
            await stream.close()
            tuner.close.assert_awaited_once()
            self.assertEqual(tuner.close.call_args.args, tuner.connect.call_args.args)
            tuner.close.reset_mock()
            tuner.setChannel.side_effect = asyncio.CancelledError
            with self.assertRaises(asyncio.CancelledError):
                await LiveDataBroadcastingStream(channel).open()
            tuner.close.assert_awaited_once()
