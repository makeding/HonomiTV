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
from app.streams.LiveEncodingTask import LiveEncodingTask
from app.utils.MirakurunStreamFormat import MirakurunStreamFormat


class MirakurunStreamFormatTest(unittest.IsolatedAsyncioTestCase):
    """Mirakurun の OpenAPI に基づく Service Stream format 指定を検証する。"""

    def setUp(self) -> None:
        """プロセス内キャッシュをテスト間で共有しないよう初期化する。

        Args:
            なし。

        Returns:
            None
        """

        MirakurunStreamFormat._format_support_cache.clear()

    async def test_format_url_uses_path_level_openapi_parameter(self) -> None:
        """path-level format enum を公開する Mirakurun では入力別の復号済み URL を選ぶ。"""

        openapi = {
            'swagger': '2.0',
            'paths': {
                '/services/{id}/stream': {
                    'parameters': [
                        {'name': 'format', 'in': 'query', 'type': 'string', 'enum': ['ts', 'tlv']},
                    ],
                    'get': {},
                },
            },
        }
        response = Mock(status_code=200, json=Mock(return_value=openapi))
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        with (
            patch('app.utils.MirakurunStreamFormat.GetMirakurunAPIEndpointURL', side_effect=lambda path: f'http://tuner{path}'),
            patch('app.utils.MirakurunStreamFormat.HTTPX_CLIENT', return_value=context),
        ):
            tlv_url = await MirakurunStreamFormat.getServiceStreamURL(1101, 'tlv', fallback_decode=False)
            ts_url = await MirakurunStreamFormat.getServiceStreamURL(1101, 'ts', fallback_decode=None)

        self.assertEqual(tlv_url, 'http://tuner/api/services/1101/stream?decode=1&format=tlv')
        self.assertEqual(ts_url, 'http://tuner/api/services/1101/stream?decode=1&format=ts')
        client.get.assert_awaited_once_with('http://tuner/api/docs', timeout=2)

    async def test_format_url_keeps_legacy_raw_mmts_request_without_openapi_support(self) -> None:
        """format 非対応の Mirakurun / mirakc には既存の decode=0 Raw MMTS 要求を送る。"""

        response = Mock(status_code=200, json=Mock(return_value={'swagger': '2.0', 'paths': {}}))
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        with (
            patch('app.utils.MirakurunStreamFormat.GetMirakurunAPIEndpointURL', side_effect=lambda path: f'http://tuner{path}'),
            patch('app.utils.MirakurunStreamFormat.HTTPX_CLIENT', return_value=context),
        ):
            url = await MirakurunStreamFormat.getServiceStreamURL(1101, 'tlv', fallback_decode=False)

        self.assertEqual(url, 'http://tuner/api/services/1101/stream?decode=0')

    async def test_network_failure_is_not_cached(self) -> None:
        """一時的な OpenAPI 接続失敗が復旧後の format 利用を妨げない。"""

        context = AsyncMock()
        context.__aenter__.return_value.get.side_effect = httpx.ConnectError('offline')
        with (
            patch('app.utils.MirakurunStreamFormat.GetMirakurunAPIEndpointURL', side_effect=lambda path: f'http://tuner{path}'),
            patch('app.utils.MirakurunStreamFormat.HTTPX_CLIENT', return_value=context),
        ):
            self.assertFalse(await MirakurunStreamFormat.supportsServiceStreamFormat())

        self.assertEqual(MirakurunStreamFormat._format_support_cache, {})


class MirakurunStreamIDChannelAPITest(unittest.IsolatedAsyncioTestCase):
    """Mirakurun の StreamID が KonomiTV の Channel API まで保存されることを検証する。"""

    async def asyncSetUp(self) -> None:
        """チャンネル API を実際の ASGI 経路で検証できる SQLite を用意する。

        Args:
            なし。

        Returns:
            None
        """

        await Tortoise.init(
            db_url='sqlite://:memory:',
            modules={'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models'])},
            timezone=DATABASE_CONFIG['timezone'],
        )
        await Tortoise.generate_schemas()
        self.application = FastAPI()
        self.application.include_router(channels_router)

    async def asyncTearDown(self) -> None:
        """SQLite 接続をテスト終了時に閉じる。

        Args:
            なし。

        Returns:
            None
        """

        await Tortoise.close_connections()

    async def test_stream_id_is_exposed_by_channel_api(self) -> None:
        """Mirakurun Service の有効な StreamID を transport_stream_id として公開する。"""

        services = [
            {
                'networkId': 11,
                'serviceId': 101,
                'streamId': 45328,
                'type': 0xad,
                'name': 'NHK BSP4K',
            },
        ]
        response = Mock(status_code=200, json=Mock(return_value=services))
        client = AsyncMock()
        client.get.return_value = response
        context = AsyncMock()
        context.__aenter__.return_value = client
        with (
            patch('app.models.Channel.Config', return_value=SimpleNamespace(tv=SimpleNamespace(preferred_terrestrial_region=None))),
            patch('app.models.Channel.HTTPX_CLIENT', return_value=context),
            patch('app.models.Channel.GetMirakurunAPIEndpointURL', return_value='http://tuner/api/services'),
        ):
            await Channel.updateFromMirakurun()

        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
            response = await client.get('/api/channels?source=Broadcast')

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['BS4K'][0]['transport_stream_id'], 45328)


class RawMMTSStreamFormatWiringTest(unittest.IsolatedAsyncioTestCase):
    """Raw MMTS の実際の受信経路が format 協商結果をそのまま利用することを検証する。"""

    async def test_decoded_tlv_url_and_body_are_used_without_conversion(self) -> None:
        """Raw MMTS は復号済み TLV URL を一度だけ開き、受信バイト列を改変しない。"""

        class FakeTunerResponse:
            """一度だけ TLV チャンクを返して切断を通知するチューナー応答。"""

            def __init__(self) -> None:
                """HTTP 200 と TLV の MIME 型を持つ最小応答を初期化する。

                Args:
                    なし。

                Returns:
                    None
                """

                self.status = 200
                self.headers = {'Content-Type': 'application/octet-stream'}
                self.content = self
                self._is_closed = False
                self._has_read = False

            @property
            def closed(self) -> bool:
                """接続切断状態を返す。

                Args:
                    なし。

                Returns:
                    bool: TLV チャンク送信後に True。
                """

                return self._is_closed

            async def read(self, _: int) -> bytes:
                """検証用 TLV 本文を一度だけ返す。

                Args:
                    _ (int): 要求チャンクサイズ。

                Returns:
                    bytes: 受信 TLV 本文。
                """

                if self._has_read is True:
                    return b''
                self._has_read = True
                self._is_closed = True
                return b'\x00TLV'

            def close(self) -> None:
                """aiohttp 応答と同じ close 操作を受け付ける。

                Args:
                    なし。

                Returns:
                    None
                """

                self._is_closed = True

        live_status = SimpleNamespace(status='Standby', detail='チューナーを起動しています…', client_count=1, updated_at=0.0)
        live_stream = SimpleNamespace(log_prefix='[Live: bs4k101-raw-mmts]')

        def setStatus(status: str, detail: str) -> None:
            """Raw MMTS タスクが参照する最小ライブ状態を更新する。

            Args:
                status (str): 新しいライブストリーム状態。
                detail (str): 状態詳細。

            Returns:
                None
            """

            live_status.status = status
            live_status.detail = detail

        live_stream.getStatus = lambda: live_status
        live_stream.setStatus = setStatus
        live_stream.writeStreamData = Mock()
        live_stream.disconnectAll = Mock()

        response = FakeTunerResponse()
        session = Mock(get=AsyncMock(return_value=response), close=AsyncMock())
        task = object.__new__(LiveEncodingTask)
        task._retry_count = task.MAX_RETRY_COUNT
        task.live_stream = live_stream
        task.acquireMirakurunTuner = AsyncMock(return_value=True)

        with (
            patch('app.streams.LiveEncodingTask.aiohttp.ClientSession', return_value=session),
            patch(
                'app.streams.LiveEncodingTask.MirakurunStreamFormat.getServiceStreamURL',
                new_callable=AsyncMock,
                return_value='http://tuner/api/services/1100101/stream?decode=1&format=tlv',
            ) as get_stream_url,
        ):
            await task.runRawMMTSStream(
                SimpleNamespace(tv=SimpleNamespace(max_alive_time=300)),
                SimpleNamespace(network_id=11, service_id=101, type='BS4K'),
                None,
            )

        get_stream_url.assert_awaited_once_with(1100101, 'tlv', fallback_decode=False)
        session.get.assert_awaited_once()
        self.assertEqual(session.get.await_args.kwargs['url'], 'http://tuner/api/services/1100101/stream?decode=1&format=tlv')
        self.assertEqual(response.headers['Content-Type'], 'application/octet-stream')
        live_stream.writeStreamData.assert_called_once_with(b'\x00TLV')
        session.close.assert_awaited_once()

    async def test_conversion_406_does_not_retry_with_legacy_url(self) -> None:
        """format 指定後の 406 は decode=0 への暗黙フォールバックを行わない。"""

        response = Mock(status=406, headers={}, close=Mock())
        session = Mock(get=AsyncMock(return_value=response), close=AsyncMock())
        live_stream = Mock(log_prefix='[Live: bs4k101-raw-mmts]')
        live_stream.getStatus.return_value = SimpleNamespace(status='Standby', detail='', client_count=1, updated_at=0.0)
        task = object.__new__(LiveEncodingTask)
        task._retry_count = 0
        task.live_stream = live_stream
        task.acquireMirakurunTuner = AsyncMock(return_value=True)

        with (
            patch('app.streams.LiveEncodingTask.aiohttp.ClientSession', return_value=session),
            patch(
                'app.streams.LiveEncodingTask.MirakurunStreamFormat.getServiceStreamURL',
                new_callable=AsyncMock,
                return_value='http://tuner/api/services/1100101/stream?decode=1&format=tlv',
            ),
        ):
            await task.runRawMMTSStream(
                SimpleNamespace(tv=SimpleNamespace(max_alive_time=300)),
                SimpleNamespace(network_id=11, service_id=101, type='BS4K'),
                None,
            )

        session.get.assert_awaited_once()
        self.assertEqual(session.get.await_args.kwargs['url'], 'http://tuner/api/services/1100101/stream?decode=1&format=tlv')
        live_stream.disconnectAll.assert_called_once()


if __name__ == '__main__':
    unittest.main()
