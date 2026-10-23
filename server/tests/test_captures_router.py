import asyncio
import importlib
import shutil
import sqlite3
import unittest
from pathlib import Path
from typing import cast
from unittest.mock import patch

import httpx
from fastapi import FastAPI
from PIL import Image
from tortoise import Tortoise

from app.config import ClientSettings, ServerSettings
from app.constants import DATABASE_CONFIG
from app.models.CaptureBookmark import CaptureBookmark
from app.models.User import User
from app.routers.CapturesRouter import router
from app.routers.UsersRouter import GetCurrentUser


TEST_DIRECTORY = Path('/private/tmp/honomitv-captures-router-tests')


class CapturesRouterTest(unittest.IsolatedAsyncioTestCase):
    """実ファイル・SQLite・ASGI を通してキャプチャ管理 API の契約を検証する。"""

    async def asyncSetUp(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 隔離 SQLite、画像保存先、ASGI クライアントを用意する。
        """

        # 中断した前回のテスト出力を同一の所有パスだけから除去する。
        shutil.rmtree(TEST_DIRECTORY, ignore_errors=True)
        self.first_root = TEST_DIRECTORY / 'first'
        self.second_root = TEST_DIRECTORY / 'second'
        self.first_root.mkdir(parents=True)
        self.second_root.mkdir()

        await Tortoise.init(
            db_url='sqlite://:memory:',
            modules={'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models'])},
            timezone=DATABASE_CONFIG['timezone'],
        )
        await Tortoise.generate_schemas()
        self.user = await User.create(
            name='captures-user', password='unused', is_admin=False,
            client_settings=ClientSettings().model_dump(mode='json'),
        )
        self.admin = await User.create(
            name='captures-admin', password='unused', is_admin=True,
            client_settings=ClientSettings().model_dump(mode='json'),
        )
        self.current_user = self.user
        self.application = FastAPI()
        self.application.include_router(router)

        async def CurrentUser() -> User:
            """
            Args:
                None: 引数なし。

            Returns:
                テストごとに切り替える認証済みユーザー。
            """

            return self.current_user

        self.application.dependency_overrides[GetCurrentUser] = CurrentUser
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.application), base_url='http://test',
        )
        self.config_patch = patch(
            'app.routers.CapturesRouter.Config',
            return_value=ServerSettings(capture={'upload_folders': [self.first_root, self.second_root]}),
        )
        self.config_patch.start()

    async def asyncTearDown(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 接続と、1 GiB 未満のテスト専用画像を確実に除去する。
        """

        self.config_patch.stop()
        await self.client.aclose()
        await Tortoise.close_connections()
        shutil.rmtree(TEST_DIRECTORY, ignore_errors=True)

    def WriteImage(self, root: Path, filename: str, color: str) -> Path:
        """
        Args:
            root: 保存先ルート。
            filename: 保存するファイル名。
            color: 画像の塗りつぶし色。

        Returns:
            作成した PNG ファイルパス。
        """

        filepath = root / filename
        Image.new('RGB', (640, 360), color).save(filepath, format='PNG')
        return filepath

    async def test_list_image_and_unsafe_file_boundaries(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 複数保存先、画像配信、非画像・リンク拒否を検証する。
        """

        self.WriteImage(self.first_root, 'same-name.png', 'red')
        self.WriteImage(self.second_root, 'same-name.png', 'blue')
        (self.first_root / 'not-an-image.jpg').write_text('not an image', encoding='utf-8')
        (self.first_root / 'linked.png').symlink_to(self.second_root / 'same-name.png')

        response = await self.client.get('/api/captures', params={'search': 'same-name'})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body['total'], 2)
        self.assertEqual(len({capture['id'] for capture in body['captures']}), 2)
        capture_id = body['captures'][0]['id']
        self.assertEqual(set(body['captures'][0]), {
            'id', 'filename', 'file_size', 'file_modified_at', 'mime_type',
            'image_width', 'image_height', 'capture_metadata',
        })
        self.assertEqual((await self.client.get(f'/api/captures/{capture_id}')).headers['content-type'], 'image/png')
        thumbnail = await self.client.get(f'/api/captures/{capture_id}', params={'thumbnail': 'true'})
        self.assertEqual(thumbnail.status_code, 200, thumbnail.text)
        self.assertEqual(thumbnail.headers['content-type'], 'image/jpeg')
        download = await self.client.get(f'/api/captures/{capture_id}', params={'download': 'true'})
        self.assertIn('attachment;', download.headers['content-disposition'])
        self.assertEqual((await self.client.get('/api/captures/../../etc/passwd')).status_code, 404)

    async def test_folders_are_owned_idempotent_and_preserve_unavailable_bookmarks(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: フォルダ所有者分離、重複追加、読めない関連の保持を検証する。
        """

        filepath = self.WriteImage(self.first_root, 'bookmark.png', 'green')
        captures = (await self.client.get('/api/captures')).json()['captures']
        capture_id = captures[0]['id']
        created = await self.client.post('/api/captures/folders', json={'name': ' favourites '})
        self.assertEqual(created.status_code, 201, created.text)
        folder_id = created.json()['id']
        add_responses = await asyncio.gather(*[
            self.client.post(f'/api/captures/folders/{folder_id}/captures', json={'capture_ids': [capture_id]})
            for _ in range(2)
        ])
        self.assertEqual([response.status_code for response in add_responses], [204, 204])
        self.assertEqual(await CaptureBookmark.filter(folder_id=folder_id).count(), 1)
        folder_captures = await self.client.get(f'/api/captures/folders/{folder_id}/captures')
        self.assertEqual(folder_captures.json()['total'], 1)

        # 外部削除後は読める件数だけ減らし、保存先の一時切断と区別できるよう関連は保持する。
        filepath.unlink()
        self.assertEqual((await self.client.get(f'/api/captures/folders/{folder_id}/captures')).json()['total'], 0)
        self.assertEqual(await CaptureBookmark.filter(folder_id=folder_id).count(), 1)
        folders = await self.client.get('/api/captures/folders')
        self.assertEqual(folders.json()['folders'][0]['capture_count'], 0)

        self.current_user = self.admin
        self.assertEqual((await self.client.get(f'/api/captures/folders/{folder_id}/captures')).status_code, 404)

    async def test_admin_delete_removes_bookmarks(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 一般ユーザー拒否と管理者削除後の関連除去を検証する。
        """

        self.WriteImage(self.first_root, 'delete.png', 'purple')
        capture_id = (await self.client.get('/api/captures')).json()['captures'][0]['id']
        folder_id = (await self.client.post('/api/captures/folders', json={'name': 'delete'})).json()['id']
        await self.client.post(f'/api/captures/folders/{folder_id}/captures', json={'capture_ids': [capture_id]})
        self.assertEqual((await self.client.delete(f'/api/captures/{capture_id}')).status_code, 403)
        self.current_user = self.admin
        self.assertEqual((await self.client.delete(f'/api/captures/{capture_id}')).status_code, 204)
        self.assertEqual(await CaptureBookmark.filter(capture_id=capture_id).count(), 0)

    async def test_storage_failure_and_invalid_bookmark_are_explicit(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 保存先障害と無効 ID が空状態に偽装されないことを検証する。
        """

        with patch('app.routers.CapturesRouter.Path.is_dir', side_effect=PermissionError('denied')):
            unavailable = await self.client.get('/api/captures')
        self.assertEqual(unavailable.status_code, 503, unavailable.text)
        self.assertEqual(unavailable.json(), {'detail': 'Capture storage is unavailable'})

        folder_id = (await self.client.post('/api/captures/folders', json={'name': 'invalid'})).json()['id']
        invalid = await self.client.post(
            f'/api/captures/folders/{folder_id}/captures', json={'capture_ids': ['bad:id:with:separator']},
        )
        self.assertEqual(invalid.status_code, 422, invalid.text)
        self.assertEqual(invalid.json(), {'detail': 'Invalid capture IDs'})


class CapturesMigrationTest(unittest.IsolatedAsyncioTestCase):
    """フォルダ migration が既存ユーザーを持つ SQLite へ適用できることを検証する。"""

    async def test_upgrade_preserves_populated_prior_schema(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: 既存 users レコードを残してフォルダ表を追加できることを検証する。
        """

        database_path = TEST_DIRECTORY / 'migration.sqlite'
        connection: sqlite3.Connection | None = None
        shutil.rmtree(TEST_DIRECTORY, ignore_errors=True)
        TEST_DIRECTORY.mkdir(parents=True)
        try:
            connection = sqlite3.connect(database_path)
            connection.execute('CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)')
            connection.execute("INSERT INTO users (id, name) VALUES (1, 'existing-user')")
            migration = importlib.import_module('app.migrations.models.18_20261023000000_update')
            connection.executescript(await migration.upgrade(cast(object, None)))
            self.assertEqual(connection.execute('SELECT name FROM users WHERE id = 1').fetchone(), ('existing-user',))
            connection.execute("INSERT INTO capture_folders (name, sort_order, user_id) VALUES ('old-data-safe', 0, 1)")
            connection.execute("INSERT INTO capture_bookmarks (capture_id, folder_id) VALUES ('root:file.png', 1)")
            connection.commit()
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM capture_bookmarks').fetchone(), (1,))
        finally:
            if connection is not None:
                connection.close()
            shutil.rmtree(TEST_DIRECTORY, ignore_errors=True)
