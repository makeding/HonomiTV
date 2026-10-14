import unittest
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
from fastapi import FastAPI, HTTPException

from app.config import ClientSettings
from app.routers.SettingsRouter import router
from app.routers.UsersRouter import GetCurrentUser


class HistoryBangumiSyncTest(unittest.IsolatedAsyncioTestCase):
    """履歴保存の実 HTTP 入口と Bangumi 同期の障害分離を検証する。"""

    async def test_history_entry_points_attach_only_current_playback(self) -> None:
        application = FastAPI()
        application.include_router(router)
        user = MagicMock()
        user.id = 1
        user.bangumi_user_id = 123
        user.bangumi_access_token = 'encrypted-token'
        user.client_settings = ClientSettings().model_dump()
        user.refresh_from_db = AsyncMock()
        user.save = AsyncMock()
        application.dependency_overrides[GetCurrentUser] = lambda: user
        item = {'video_id': 1, 'last_playback_position': 1200.0, 'created_at': 1.0, 'updated_at': 2.0}
        progress = {'video_id': 1, 'playback_position': 1200.0, 'duration': 1807.0}
        with patch('app.utils.BangumiPlaybackSync.SyncBangumiPlaybackProgress', AsyncMock()) as sync:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
                response = await client.put('/api/settings/client/watched-history', json={'items': [item]})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'items': [item]})
                sync.assert_not_awaited()
                response = await client.put('/api/settings/client/watched-history', json={'items': [item], 'playback_progress': progress})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'items': [item]})
                sync.assert_awaited_once()
                self.assertEqual(sync.await_args.args[0], 1)
                self.assertEqual(sync.await_args.args[1].model_dump(), progress)
                # Web の既存設定 PUT も同じ関数へ現在位置を添える。保存 JSON に一時情報は残さない。
                sync.reset_mock()
                response = await client.put('/api/settings/client', json={**user.client_settings, 'playback_progress': progress})
                self.assertEqual(response.status_code, 204)
                sync.assert_awaited_once()
                self.assertNotIn('playback_progress', user.client_settings)
                sync.reset_mock()
                # 外部同期失敗でも、履歴の保存・読み戻しは成功する。
                sync.side_effect = HTTPException(status_code=503, detail='Failed to connect to Bangumi API')
                response = await client.put('/api/settings/client/watched-history', json={'items': [item], 'playback_progress': progress})
                self.assertEqual(response.status_code, 200)
                response = await client.get('/api/settings/client/watched-history')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'items': [item]})
