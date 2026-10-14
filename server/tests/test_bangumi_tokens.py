import asyncio
import json
import unittest
from time import time
from typing import cast
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI
from tortoise import Tortoise

from app import schemas
from app.config import ClientSettings
from app.constants import DATABASE_CONFIG
from app.models.User import User
from app.routers.BangumiRouter import router
from app.routers.SettingsRouter import router as settings_router
from app.routers.UsersRouter import GetCurrentUser
from app.utils.BangumiTokenManager import BangumiTokenManager


class BangumiTokenTest(unittest.IsolatedAsyncioTestCase):
    """実際の暗号化・SQLite 保存と HTTP 入口を通して自己更新を検証する。"""

    async def asyncSetUp(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 各テスト専用のメモリ内 DB と上流 HTTP を用意する。
        """

        await Tortoise.init(
            db_url='sqlite://:memory:',
            modules={'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models'])},
        )
        await Tortoise.generate_schemas()
        self.user = await User.create(
            name='token-test', password='unused', is_admin=False,
            bangumi_user_id=123, bangumi_user_name='token-test',
            client_settings=ClientSettings().model_dump(mode='json'),
        )
        self.user.bangumi_access_token = self.user.encryptBangumiAccessToken('bootstrap')
        await self.user.save(update_fields=['bangumi_access_token'])
        # 有効期限と発行履歴は上流の状態であり、アプリの DB へ期限を捏造しない。
        self.expiries = {'bootstrap': int(time()) + 365 * 86400}
        self.issued_from: list[str] = []
        self.fail_issue = False
        self.wrong_owner = False
        self.malformed_issue = False
        self.unlink_during_issue = False
        self.relink_during_issue = False
        self.requests: list[httpx.Request] = []
        self.http_factory = lambda: httpx.AsyncClient(transport=httpx.MockTransport(self.HandleProvider))
        self.provider_patch = patch('app.utils.BangumiTokenManager.HTTPX_CLIENT', self.http_factory)
        self.provider_patch.start()
        self.application = FastAPI()
        self.application.include_router(router)
        self.application.include_router(settings_router)

        async def CurrentUser() -> User:
            return await User.get(id=self.user.id)

        self.application.dependency_overrides[GetCurrentUser] = CurrentUser


    async def asyncTearDown(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: HTTP 差し替えとメモリ内 DB を破棄する。
        """

        self.provider_patch.stop()
        await Tortoise.close_connections()


    async def HandleProvider(self, request: httpx.Request) -> httpx.Response:
        """
        Args:
            request (httpx.Request): 実際にシリアライズされた上流リクエスト。

        Returns:
            httpx.Response: 発行元と期限を持つ Bangumi 模擬応答。
        """

        self.requests.append(request)
        if str(request.url) == BangumiTokenManager.STATUS_URL:
            token = parse_qs(request.content.decode())['access_token'][0]
            self.assertEqual(request.headers['content-type'], 'application/x-www-form-urlencoded')
            return httpx.Response(200, json={'expires': self.expiries[token], 'user_id': '123'})
        token = request.headers['authorization'].removeprefix('Bearer ')
        if str(request.url) == BangumiTokenManager.ISSUE_URL:
            self.assertEqual(request.method, 'POST')
            self.assertEqual(json.loads(request.content), {'name': 'KonomiTV automatic renewal', 'days': 30})
            if self.fail_issue:
                return httpx.Response(503, json={'error': 'unavailable'})
            if self.malformed_issue:
                return httpx.Response(200, json={'token': 'unexpected-object'})
            self.issued_from.append(token)
            successor = f'successor-{len(self.issued_from)}'
            self.expiries[successor] = int(time()) + 30 * 86400
            # 外部通信が中断点となり、別リクエストの解除や再連携が完了する状況を再現する。
            await asyncio.sleep(0)
            if self.unlink_during_issue:
                await User.filter(id=self.user.id).update(bangumi_access_token=None, bangumi_user_id=None)
            if self.relink_during_issue:
                await User.filter(id=self.user.id).update(
                    bangumi_access_token=self.user.encryptBangumiAccessToken('relinked'),
                )
            return httpx.Response(200, json=successor)
        self.assertEqual(str(request.url), BangumiTokenManager.PROFILE_URL)
        self.assertIn(token, self.expiries)
        return httpx.Response(200, json={
            'id': 999 if self.wrong_owner and token.startswith('successor') else 123,
            'username': 'token-test', 'nickname': 'Test', 'avatar': {'large': 'https://example.com/avatar.png'},
        })


    async def test_repeated_successors_renew_after_restart_without_schema_change(self) -> None:
        """既存の暗号化トークンから始め、再取得した DB レコードだけで次々更新できる。"""

        for index in range(1, 4):
            await BangumiTokenManager.renewAllLinkedUsers()
            current = await User.get(id=self.user.id)
            self.assertEqual(current.decryptBangumiAccessToken(), f'successor-{index}')
            self.assertNotIn(f'successor-{index}', current.bangumi_access_token or '')
            self.expiries[f'successor-{index}'] = int(time()) + 6 * 86400
        self.assertEqual(self.issued_from, ['bootstrap', 'successor-1', 'successor-2'])
        self.assertTrue(all(request.method != 'DELETE' for request in self.requests))
        self.assertNotIn('bangumi_access_token', schemas.User.model_fields)


    async def test_healthy_token_is_not_reissued_every_check(self) -> None:
        """期限に余裕がある 30 日以内のトークンは維持する。"""

        for remaining in (30 * 86400, 8 * 86400):
            self.expiries['bootstrap'] = int(time()) + remaining
            self.assertFalse(await BangumiTokenManager.renewUserToken(self.user))
        self.assertEqual(self.issued_from, [])
        self.expiries['bootstrap'] = int(time()) + 7 * 86400
        self.assertTrue(await BangumiTokenManager.renewUserToken(self.user))


    async def test_failures_preserve_credential_and_retry(self) -> None:
        """発行障害・不正応答・別所有者では保存せず、障害解消後に更新できる。"""

        saved = self.user.bangumi_access_token
        for flag in ('fail_issue', 'malformed_issue', 'wrong_owner'):
            setattr(self, flag, True)
            await BangumiTokenManager.renewAllLinkedUsers()
            self.assertEqual((await User.get(id=self.user.id)).bangumi_access_token, saved)
            setattr(self, flag, False)
        await BangumiTokenManager.renewAllLinkedUsers()
        self.assertNotEqual((await User.get(id=self.user.id)).bangumi_access_token, saved)


    async def test_expired_and_invalid_status_never_issue(self) -> None:
        """期限切れや壊れた期限を、正常な更新時期として扱わない。"""

        for expiry in (int(time()) - 1, 0, 'invalid', None):
            self.expiries['bootstrap'] = expiry  # type: ignore[assignment]
            await BangumiTokenManager.renewAllLinkedUsers()
        self.assertEqual(self.issued_from, [])


    async def test_concurrent_checks_issue_only_once(self) -> None:
        """同じアカウントの並行確認は最新の保存結果を再取得する。"""

        results = await asyncio.gather(*[BangumiTokenManager.renewUserToken(self.user) for _ in range(3)])
        self.assertEqual(results.count(True), 1)
        self.assertEqual(self.issued_from, ['bootstrap'])


    async def test_unlink_and_relink_during_issuance_win(self) -> None:
        """上流通信中の解除・再連携を定期更新が復活または上書きしない。"""

        self.relink_during_issue = True
        self.assertFalse(await BangumiTokenManager.renewUserToken(self.user))
        self.assertEqual((await User.get(id=self.user.id)).decryptBangumiAccessToken(), 'relinked')
        self.expiries['relinked'] = int(time()) + 365 * 86400
        self.relink_during_issue = False
        self.unlink_during_issue = True
        self.assertFalse(await BangumiTokenManager.renewUserToken(self.user))
        self.assertIsNone((await User.get(id=self.user.id)).bangumi_access_token)


    async def test_one_failed_account_does_not_stop_other_accounts(self) -> None:
        """復号不能なアカウントがあっても、他の有効アカウントを更新する。"""

        await User.filter(id=self.user.id).update(bangumi_access_token='invalid')
        valid_user = await User.create(
            name='valid', password='unused', is_admin=False, bangumi_user_id=123,
            bangumi_access_token=self.user.encryptBangumiAccessToken('bootstrap'),
        )
        await BangumiTokenManager.renewAllLinkedUsers()
        self.assertEqual((await User.get(id=valid_user.id)).decryptBangumiAccessToken(), 'successor-1')


    async def test_network_and_database_failure_do_not_replace_credential(self) -> None:
        """期限照会の接続障害や保存失敗後も元の暗号化情報を保持する。"""

        def NetworkFailure(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectTimeout('test connection timeout', request=request)

        with patch('app.utils.BangumiTokenManager.HTTPX_CLIENT', lambda: httpx.AsyncClient(transport=httpx.MockTransport(NetworkFailure))):
            await BangumiTokenManager.renewAllLinkedUsers()
        with patch('tortoise.queryset.UpdateQuery.__await__', side_effect=RuntimeError('database failure')):
            await BangumiTokenManager.renewAllLinkedUsers()
        self.assertEqual((await User.get(id=self.user.id)).bangumi_access_token, self.user.bangumi_access_token)


    async def test_authenticated_link_and_logout_http(self) -> None:
        """認証済み HTTP 入口が 30 日トークンを暗号化保存し、解除で消去する。"""

        with (
            patch('app.routers.BangumiRouter.HTTPX_CLIENT', self.http_factory),
            patch('app.routers.BangumiRouter.BangumiClient.scheduleUserCollectionSync'),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                response = await client.post('/api/bangumi/auth', json={'access_token': ' bootstrap '})
                self.assertEqual((response.status_code, response.content), (204, b''))
                self.assertEqual((await User.get(id=self.user.id)).decryptBangumiAccessToken(), 'successor-1')
                response = await client.delete('/api/bangumi/logout')
                self.assertEqual((response.status_code, response.content), (204, b''))
        current = await User.get(id=self.user.id)
        self.assertIsNone(current.bangumi_access_token)
        self.assertIsNone(current.bangumi_user_id)
        self.assertFalse(await BangumiTokenManager.renewUserToken(current))


    async def test_failed_link_preserves_existing_account_http(self) -> None:
        """発行失敗を HTTP で明示し、既存連携を部分更新しない。"""

        self.fail_issue = True
        with patch('app.routers.BangumiRouter.HTTPX_CLIENT', self.http_factory):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                response = await client.post('/api/bangumi/auth', json={'access_token': 'bootstrap'})
        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json(), {'detail': 'Failed to issue or verify Bangumi 30-day token (HTTP 503)'})
        self.assertEqual((await User.get(id=self.user.id)).bangumi_access_token, self.user.bangumi_access_token)


    async def test_history_write_does_not_restore_stale_token_http(self) -> None:
        """輪換前に取得した User を使った履歴更新でも、新しいトークンを保持する。"""

        self.application.dependency_overrides[GetCurrentUser] = lambda: self.user
        await BangumiTokenManager.renewAllLinkedUsers()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
            response = await client.put('/api/settings/client/watched-history', json={'items': []})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'items': []})
        self.assertEqual((await User.get(id=self.user.id)).decryptBangumiAccessToken(), 'successor-1')


if __name__ == '__main__':
    unittest.main()
