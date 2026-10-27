import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import mock_open, patch

import httpx
from fastapi import FastAPI
from PIL import Image

from app.routers.CapturesRouter import router


class CapturesRouterTest(unittest.IsolatedAsyncioTestCase):
    """上流のアップロード API と廃止した管理 API の境界を検証する。"""

    async def testUploadAndRemovedGallery(self) -> None:
        """
        Args:
            None: 引数なし。

        Returns:
            None: メモリ内 HTTP と保存先のモックで応答・画像内容を検証する。
        """

        # 実 DB や画像保存先は変更せず、現在のルーターへ multipart を送る。
        application = FastAPI()
        application.include_router(router)
        image = io.BytesIO()
        Image.new('RGB', (2, 2)).save(image, format='PNG')
        payload = image.getvalue()
        root = Path('/capture-upload-test')
        output = mock_open()
        with (
            patch('app.routers.CapturesRouter.Config', return_value=SimpleNamespace(
                capture=SimpleNamespace(upload_folders=[root]),
            )),
            patch('app.routers.CapturesRouter.Path.exists', autospec=True, side_effect=lambda path: path == root),
            patch('app.routers.CapturesRouter.shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024 * 1024)),
            patch('app.routers.CapturesRouter.open', output),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
                response = await client.post('/api/captures', files={'image': ('capture.png', payload, 'image/png')})
                self.assertEqual(response.status_code, 204)
                self.assertEqual(response.content, b'')
                output.assert_called_once_with(root / 'capture.png', mode='wb')
                self.assertEqual(b''.join(call.args[0] for call in output().write.call_args_list), payload)

                # 旧一覧・フォルダ・削除のエンドポイントは提供しない。
                self.assertEqual((await client.get('/api/captures')).status_code, 405)
                self.assertEqual((await client.get('/api/captures/folders')).status_code, 404)
                self.assertEqual((await client.delete('/api/captures/old-image')).status_code, 404)

                # アップロードのパス検証も上流のレスポンスを保つ。
                invalid = await client.post('/api/captures', files={'image': ('../outside.png', payload, 'image/png')})
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(invalid.json(), {'detail': 'Specified filename is invalid'})
