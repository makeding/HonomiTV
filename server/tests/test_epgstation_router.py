import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx2 as httpx
from fastapi import FastAPI

from app.routers.EPGStationRouter import router


class EPGStationRouterTest(unittest.IsolatedAsyncioTestCase):
    """固定された EPGStation API 境界が上流の JSON と状態を正しく保持することを検証する。"""

    async def asyncSetUp(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: ASGI アプリケーションと制御可能な上流 HTTP 境界を用意する。
        """

        self.requests: list[httpx.Request] = []
        self.responses: dict[tuple[str, str], httpx.Response] = {}
        self.backend = 'EPGStation'
        self.httpx_async_client = httpx.AsyncClient
        self.application = FastAPI()
        self.application.include_router(router)
        self.config_patch = patch(
            'app.routers.EPGStationRouter.Config',
            side_effect = lambda: SimpleNamespace(
                general = SimpleNamespace(
                    backend = self.backend,
                    epgstation_url = 'http://epgstation.test/',
                ),
            ),
        )
        self.client_patch = patch(
            'app.routers.EPGStationRouter.httpx.AsyncClient',
            side_effect = lambda **kwargs: self.httpx_async_client(
                transport = httpx.MockTransport(self.HandleEPGStation),
                **kwargs,
            ),
        )
        self.config_patch.start()
        self.client_mock = self.client_patch.start()


    async def asyncTearDown(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: HTTP と設定の差し替えを終了する。
        """

        self.client_patch.stop()
        self.config_patch.stop()


    async def HandleEPGStation(self, request: httpx.Request) -> httpx.Response:
        """
        MockTransport から届いた EPGStation リクエストを記録して設定済み応答を返す。

        Args:
            request (httpx.Request): 実際にプロキシが送信した上流リクエスト。

        Returns:
            httpx.Response: テストが指定した EPGStation の応答。
        """

        self.requests.append(request)
        response = self.responses.get((request.method, request.url.path))
        if response is None:
            return httpx.Response(404, json={'code': 'not-found'})
        return response


    async def Request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        """
        ASGI アプリケーションへ HTTP リクエストを送る。

        Args:
            method (str): HTTP メソッド。
            url (str): KonomiTV API のパスと query。
            **kwargs (object): httpx に渡す本文などの追加引数。

        Returns:
            httpx.Response: KonomiTV の API 応答。
        """

        async with self.httpx_async_client(
            transport = httpx.ASGITransport(app=self.application),
            base_url = 'http://testserver',
        ) as client:
            return await client.request(method, url, **kwargs)


    async def test_native_reserve_payload_and_query_are_forwarded_without_field_loss(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: EPGStation の未知フィールドを含む予約応答がそのまま返ることを検証する。
        """

        native_payload = {
            'reserves': [{
                'id': 14,
                'isSkip': False,
                'isConflict': False,
                'isOverlap': False,
                'allowEndLack': True,
                'removeDataBroadcast': False,
                'isTimeSpecified': False,
                'isDeleteOriginalAfterEncode': False,
                'channelId': 4,
                'channelType': 'GR',
                'startAt': 1735689600000,
                'endAt': 1735693200000,
                'name': '番組',
                'futureField': {'preserved': True},
            }],
            'total': 1,
        }
        self.responses[('GET', '/api/reserves')] = httpx.Response(200, json=native_payload)

        response = await self.Request('GET', '/api/recording/epgstation/reserves?type=normal&limit=20')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), native_payload)
        self.assertEqual(self.requests[0].url.path, '/api/reserves')
        self.assertEqual(str(self.requests[0].url.query, 'utf-8'), 'type=normal&limit=20')
        self.assertFalse(self.client_mock.call_args.kwargs['follow_redirects'])


    async def test_rule_write_and_upstream_rejection_keep_native_boundary(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: ルール本文が無変換で送られ、上流の失敗状態が保持されることを検証する。
        """

        rule = {
            'isTimeSpecification': False,
            'searchOption': {'keyword': 'アニメ', 'GR': True},
            'reserveOption': {'enable': True, 'allowEndLack': True, 'avoidDuplicate': True},
            'unknownOption': ['must', 'survive'],
        }
        self.responses[('POST', '/api/rules')] = httpx.Response(201, json={'ruleId': 31})
        self.responses[('PUT', '/api/rules/31/disable')] = httpx.Response(
            409,
            json={'message': '予約の競合を解決してください: http://user:secret@epgstation.test/private'},
        )

        created = await self.Request('POST', '/api/recording/epgstation/rules', json=rule)
        rejected = await self.Request('PUT', '/api/recording/epgstation/rules/31/disable')

        self.assertEqual(created.status_code, 201)
        self.assertEqual(created.json(), {'ruleId': 31})
        self.assertEqual(json.loads(self.requests[0].content), rule)
        self.assertEqual(rejected.status_code, 409)
        self.assertEqual(rejected.json(), {
            'detail': 'EPGSTATION_UPSTREAM_ERROR: 予約の競合を解決してください: <URL>',
        })


    async def test_non_epgstation_backend_never_calls_upstream(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: EPGStation 以外の録画バックエンドで中継しないことを検証する。
        """

        self.backend = 'EDCB'

        response = await self.Request('GET', '/api/recording/epgstation/recording')

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {
            'detail': 'EPGSTATION_BACKEND_DISABLED: EPGStation バックエンドが有効ではありません。',
        })
        self.assertEqual(self.requests, [])


    async def test_program_resolution_uses_exact_channel_and_time_identity(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 番組名ではなく NID/SID と開始・終了時刻の完全一致で ID を解決することを検証する。
        """

        resolved_program = {
            'id': 999,
            'channelId': 12,
            'startAt': 1735689600000,
            'endAt': 1735693200000,
            'name': '同名番組',
        }
        self.responses[('GET', '/api/channels')] = httpx.Response(200, json=[
            {'id': 9, 'networkId': 32736, 'serviceId': 101},
            {'id': 12, 'networkId': 32737, 'serviceId': 102},
        ])
        self.responses[('GET', '/api/schedules/12')] = httpx.Response(200, json=[{
            'channel': {'id': 12},
            'programs': [
                {**resolved_program, 'id': 997, 'channelId': 9},
                {**resolved_program, 'id': 998, 'endAt': 1735693199999},
                resolved_program,
            ],
        }])

        response = await self.Request(
            'GET',
            '/api/recording/epgstation/program?network_id=32737&service_id=102'
            '&start_at=1735689600000&end_at=1735693200000',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), resolved_program)
        self.assertEqual(self.requests[1].url.path, '/api/schedules/12')
        self.assertEqual(
            str(self.requests[1].url.query, 'utf-8'),
            'startAt=1735689600000&days=1&isHalfWidth=false',
        )


    async def test_malformed_success_response_is_rejected_without_forwarding(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: JSON でない成功本文を EPGStation の有効な応答として扱わないことを検証する。
        """

        self.responses[('GET', '/api/config')] = httpx.Response(200, content=b'not-json')

        response = await self.Request('GET', '/api/recording/epgstation/config')

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {
            'detail': 'EPGSTATION_INVALID_RESPONSE: EPGStation の応答形式が不正です。',
        })
