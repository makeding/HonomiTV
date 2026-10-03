import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app import schemas
from app.models.User import User
from app.routers.BangumiRouter import BangumiAccountLogoutAPI, BangumiAuthAPI
from app.routers.BangumiRouter import router as bangumi_router
from app.routers.UsersRouter import GetCurrentUser


class FakeBangumiResponse:
    """Bangumi API レスポンスのうち、認証ルーターが参照する情報だけを保持する。"""

    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        """
        Args:
            status_code (int): HTTP ステータスコード。
            payload (dict[str, Any]): JSON レスポンス。

        Returns:
            None: 初期化のみを行う。
        """

        self.status_code = status_code
        self.payload = payload


    def json(self) -> dict[str, Any]:
        """
        Returns:
            dict[str, Any]: 設定された JSON レスポンス。
        """

        return self.payload


class FakeHTTPXClient:
    """Bangumi API への GET を固定レスポンスへ置き換える非同期クライアント。"""

    def __init__(self, response: FakeBangumiResponse) -> None:
        """
        Args:
            response (FakeBangumiResponse): GET で返すレスポンス。

        Returns:
            None: 初期化のみを行う。
        """

        self.response = response
        self.request_url = ''
        self.request_headers: dict[str, str] = {}


    async def __aenter__(self) -> 'FakeHTTPXClient':
        """
        Returns:
            FakeHTTPXClient: コンテキスト内で利用する自身。
        """

        return self


    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        """
        Args:
            exc_type (Any): 発生した例外の型。
            exc (Any): 発生した例外。
            traceback (Any): 発生した例外のトレースバック。

        Returns:
            None: 終了処理は不要。
        """


    async def get(self, url: str, headers: dict[str, str]) -> FakeBangumiResponse:
        """
        Args:
            url (str): リクエスト先 URL。
            headers (dict[str, str]): リクエストヘッダー。

        Returns:
            FakeBangumiResponse: 設定された固定レスポンス。
        """

        self.request_url = url
        self.request_headers = headers
        return self.response


class FakeUser:
    """Bangumi 連携で更新される User モデルのフィールドと保存処理を再現する。"""

    def __init__(self) -> None:
        """
        Returns:
            None: 未連携状態のユーザーを初期化する。
        """

        self.id = 1
        self.bangumi_user_id: int | None = None
        self.bangumi_user_name: str | None = None
        self.bangumi_user_nickname: str | None = None
        self.bangumi_user_avatar_url: str | None = None
        self.bangumi_access_token: str | None = None
        self.saved = False


    def encryptBangumiAccessToken(self, plain_text: str) -> str:
        """
        Args:
            plain_text (str): 暗号化対象の個人アクセストークン。

        Returns:
            str: テスト用の暗号化済み表現。
        """

        return f'encrypted:{plain_text}'


    async def save(self) -> None:
        """
        Returns:
            None: 保存されたことだけを記録する。
        """

        self.saved = True


class BangumiRouterTest(unittest.IsolatedAsyncioTestCase):
    """Bangumi 個人アクセストークンの検証、保存、連携解除を検証する。"""

    async def test_progress_http_revalidates_before_completion_deduplication(self) -> None:
        """HTTP 入口で誤照合を直してから、正しい章の完了記録を確認・更新する。"""

        application = FastAPI()
        application.include_router(bangumi_router)
        user = MagicMock()
        user.id = 1
        user.bangumi_user_id = 123
        user.bangumi_access_token = 'encrypted-token'
        user.decryptBangumiAccessToken.return_value = 'test-token'
        application.dependency_overrides[GetCurrentUser] = lambda: user
        recording = MagicMock()
        recording.id = 2781
        recording.series_id = 90
        recording.episode_number = '13'
        recording.bangumi_subject_id = 509355
        recording.bangumi_episode_id = 1559556
        recording.recorded_video.duration = 1800.0
        recording.recorded_video.cm_sections = []
        recording.recorded_video.status = 'Recorded'
        query = MagicMock()
        query.prefetch_related = AsyncMock(return_value=recording)
        completed_query = MagicMock()
        completed_query.exists = AsyncMock(return_value=False)

        async def Resolve(*args: Any) -> None:
            recording.bangumi_subject_id = 616808
            recording.bangumi_episode_id = 1746074

        with (
            patch('app.routers.BangumiRouter.RecordedProgram.get_or_none', return_value=query),
            patch('app.routers.BangumiRouter.Series.get_or_none', AsyncMock(return_value=MagicMock())),
            patch('app.routers.BangumiRouter.BangumiClient.resolveRecordedEpisode', AsyncMock(side_effect=Resolve)),
            patch('app.routers.BangumiRouter.BangumiEpisodeCompletion.filter', return_value=completed_query) as completed_filter,
            patch('app.routers.BangumiRouter.UpdateBangumiEpisodeCollection', AsyncMock()) as update_collection,
            patch('app.routers.BangumiRouter.BangumiEpisodeCompletion.get_or_create', AsyncMock()) as completion,
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
                response = await client.post('/api/bangumi/videos/2781/progress', json={
                    'playback_position': 1700.0, 'duration': 1800.0,
                })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'Completed'})
        completed_filter.assert_called_once_with(user_id=1, bangumi_episode_id=1746074)
        update_collection.assert_awaited_once_with('test-token', 616808, 1746074)
        self.assertEqual(completion.await_args.kwargs['bangumi_episode_id'], 1746074)

        # 外部検証が失敗したときは、古い完了記録を成功扱いせず再試行できる Pending を返す。
        with (
            patch('app.routers.BangumiRouter.RecordedProgram.get_or_none', return_value=query),
            patch('app.routers.BangumiRouter.Series.get_or_none', AsyncMock(return_value=MagicMock())),
            patch('app.routers.BangumiRouter.BangumiClient.resolveRecordedEpisode', AsyncMock(side_effect=httpx.NetworkError('network error'))),
            patch('app.routers.BangumiRouter.BangumiEpisodeCompletion.filter') as completed_filter,
            patch('app.routers.BangumiRouter.UpdateBangumiEpisodeCollection', AsyncMock()) as update_collection,
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
                response = await client.post('/api/bangumi/videos/2781/progress', json={
                    'playback_position': 1700.0, 'duration': 1800.0,
                })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'Pending'})
        completed_filter.assert_not_called()
        update_collection.assert_not_awaited()

    async def test_access_token_is_encrypted_and_not_exposed_by_user_schema(self) -> None:
        """保存用トークンは暗号化され、User API のレスポンス構造には含まれない。"""

        user = User()
        encrypted_token = user.encryptBangumiAccessToken('personal-token')

        self.assertNotEqual(encrypted_token, 'personal-token')
        self.assertNotIn('personal-token', encrypted_token)
        self.assertNotIn('bangumi_access_token', schemas.User.model_fields)

    async def test_valid_access_token_links_profile_and_stores_encrypted_token(self) -> None:
        """有効なトークンでは公開プロフィールと暗号化済みトークンだけを保存する。"""

        response = FakeBangumiResponse(200, {
            'id': 123,
            'username': 'huggy',
            'nickname': 'Huggy',
            'avatar': {'large': 'https://lain.bgm.tv/avatar.jpg'},
        })
        httpx_client = FakeHTTPXClient(response)
        current_user = FakeUser()

        with (
            patch('app.routers.BangumiRouter.HTTPX_CLIENT', return_value=httpx_client),
            patch('app.routers.BangumiRouter.BangumiClient.scheduleUserCollectionSync') as schedule_sync,
        ):
            await BangumiAuthAPI(
                auth_request = schemas.BangumiAuthRequest(access_token='  personal-token\n'),
                current_user = current_user,  # type: ignore[arg-type]
            )

        self.assertEqual(httpx_client.request_url, 'https://api.bgm.tv/v0/me')
        self.assertEqual(httpx_client.request_headers['Authorization'], 'Bearer personal-token')
        self.assertEqual(current_user.bangumi_user_id, 123)
        self.assertEqual(current_user.bangumi_user_name, 'huggy')
        self.assertEqual(current_user.bangumi_user_nickname, 'Huggy')
        self.assertEqual(current_user.bangumi_user_avatar_url, 'https://lain.bgm.tv/avatar.jpg')
        self.assertEqual(current_user.bangumi_access_token, 'encrypted:personal-token')
        self.assertTrue(current_user.saved)
        schedule_sync.assert_called_once_with(current_user)


    async def test_invalid_access_token_does_not_replace_existing_link(self) -> None:
        """無効なトークンでは既存の連携情報を変更しない。"""

        httpx_client = FakeHTTPXClient(FakeBangumiResponse(401, {'detail': 'Unauthorized'}))
        current_user = FakeUser()
        current_user.bangumi_user_id = 999
        current_user.bangumi_access_token = 'encrypted:existing-token'

        with patch('app.routers.BangumiRouter.HTTPX_CLIENT', return_value=httpx_client):
            with self.assertRaises(HTTPException) as raised:
                await BangumiAuthAPI(
                    auth_request = schemas.BangumiAuthRequest(access_token='invalid-token'),
                    current_user = current_user,  # type: ignore[arg-type]
                )

        self.assertEqual(raised.exception.status_code, 422)
        self.assertEqual(current_user.bangumi_user_id, 999)
        self.assertEqual(current_user.bangumi_access_token, 'encrypted:existing-token')
        self.assertFalse(current_user.saved)


    async def test_network_error_does_not_replace_existing_link(self) -> None:
        """Bangumi API への接続失敗では既存の連携情報を変更しない。"""

        current_user = FakeUser()
        current_user.bangumi_user_id = 999

        class NetworkErrorClient(FakeHTTPXClient):
            """GET 時にネットワークエラーを送出するクライアント。"""

            async def get(self, url: str, headers: dict[str, str]) -> FakeBangumiResponse:
                """
                Args:
                    url (str): リクエスト先 URL。
                    headers (dict[str, str]): リクエストヘッダー。

                Returns:
                    FakeBangumiResponse: 常に例外を送出するため返さない。

                Raises:
                    httpx.NetworkError: 接続失敗を再現する。
                """

                raise httpx.NetworkError('network error')

        httpx_client = NetworkErrorClient(FakeBangumiResponse(200, {}))
        with patch('app.routers.BangumiRouter.HTTPX_CLIENT', return_value=httpx_client):
            with self.assertRaises(HTTPException) as raised:
                await BangumiAuthAPI(
                    auth_request = schemas.BangumiAuthRequest(access_token='personal-token'),
                    current_user = current_user,  # type: ignore[arg-type]
                )

        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(current_user.bangumi_user_id, 999)
        self.assertFalse(current_user.saved)


    async def test_logout_clears_profile_and_access_token(self) -> None:
        """連携解除では公開プロフィールと認証情報をまとめて消去する。"""

        current_user = FakeUser()
        current_user.bangumi_user_id = 123
        current_user.bangumi_user_name = 'huggy'
        current_user.bangumi_user_nickname = 'Huggy'
        current_user.bangumi_user_avatar_url = 'https://lain.bgm.tv/avatar.jpg'
        current_user.bangumi_access_token = 'encrypted:personal-token'

        await BangumiAccountLogoutAPI(current_user=current_user)  # type: ignore[arg-type]

        self.assertIsNone(current_user.bangumi_user_id)
        self.assertIsNone(current_user.bangumi_user_name)
        self.assertIsNone(current_user.bangumi_user_nickname)
        self.assertIsNone(current_user.bangumi_user_avatar_url)
        self.assertIsNone(current_user.bangumi_access_token)
        self.assertTrue(current_user.saved)


if __name__ == '__main__':
    unittest.main()
