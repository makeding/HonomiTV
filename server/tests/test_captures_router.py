import io
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2 as httpx
from fastapi import FastAPI
from PIL import Image

from app.routers.CapturesRouter import router


class CapturesRouterTest(unittest.IsolatedAsyncioTestCase):
    """キャプチャ API の ASGI 境界を、実 DB や保存先を変更せずに検証する。"""

    def setUp(self) -> None:
        self.application = FastAPI()
        self.application.include_router(router)


    async def testGalleryRoutesAndFavoriteAuthentication(self) -> None:
        """ギャラリーの公開 API とお気に入り変更の認証境界を検証する。"""

        database = SimpleNamespace(execute_query_dict=AsyncMock(return_value=[]))
        with (
            patch('app.routers.CapturesRouter.BuildCaptureWhere', new=AsyncMock(return_value=([], []))),
            patch('app.routers.CapturesRouter.connections', SimpleNamespace(get=lambda _: database)),
            patch('app.routers.CapturesRouter.SerializeCaptures', new=AsyncMock(return_value=[])),
            patch('app.routers.CapturesRouter.SerializePrograms', new=AsyncMock(return_value=[])),
            patch('app.routers.CapturesRouter.GetCaptureRows', new=AsyncMock(return_value=[])),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                response = await client.get('/api/captures')
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json(), {'captures': [], 'programs': [], 'next_cursor': None})

                response = await client.get('/api/captures/999')
                self.assertEqual(response.status_code, 404)
                self.assertEqual(response.json(), {'detail': 'Capture not found'})

                response = await client.post('/api/captures/favorites', json={'capture_ids': [1]})
                self.assertEqual(response.status_code, 401)


    async def testUploadRegistersCaptureAndRejectsTraversalFilename(self) -> None:
        """正常な画像の登録と、保存先外を指すファイル名の拒否を検証する。"""

        image = io.BytesIO()
        Image.new('RGB', (2, 2)).save(image, format='PNG')
        payload = image.getvalue()
        root = Path('/capture-upload-test')
        capture = SimpleNamespace(id=42)

        @asynccontextmanager
        async def PathLock(_: Path):
            yield

        output = SimpleNamespace(write=AsyncMock())

        @asynccontextmanager
        async def OpenFileContext():
            yield output

        async def OpenFile(*args: object, **kwargs: object):
            return OpenFileContext()

        scanner = SimpleNamespace(
            getPathLock=PathLock,
            registerFile=AsyncMock(return_value=capture),
        )
        serialized_capture = {
            'id': 42,
            'file_hash': 'registered-by-scanner',
            'file_size': len(payload),
            'captured_at': '2026-10-28T00:00:00+09:00',
            'broadcast_at': None,
            'image_width': 2,
            'image_height': 2,
            'metadata_status': 'Parsed',
            'playback_position': None,
            'caption_text': None,
            'is_caption_composited': False,
            'is_comment_composited': False,
            'capture_program_id': None,
            'is_favorite': None,
            'tag_ids': None,
        }
        with (
            patch('app.routers.CapturesRouter.Config', return_value=SimpleNamespace(
                capture=SimpleNamespace(upload_folders=[root]),
            )),
            patch('app.routers.CapturesRouter.CaptureScanTask', return_value=scanner),
            patch('app.routers.CapturesRouter.Capture.get_or_none', new=AsyncMock(return_value=None)),
            patch('app.routers.CapturesRouter.anyio.Path.is_dir', new=AsyncMock(return_value=True)),
            patch('app.routers.CapturesRouter.shutil.disk_usage', return_value=SimpleNamespace(free=100 * 1024 * 1024)),
            patch('app.routers.CapturesRouter.anyio.open_file', OpenFile),
            patch('app.routers.CapturesRouter.GetCaptureRows', new=AsyncMock(return_value=[{'id': 42}])),
            patch('app.routers.CapturesRouter.SerializeCaptures', new=AsyncMock(return_value=[serialized_capture])),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                response = await client.post('/api/captures', files={'image': ('capture.png', payload, 'image/png')})
                self.assertEqual(response.status_code, 201)
                self.assertEqual(response.json(), serialized_capture)
                scanner.registerFile.assert_awaited_once()
                output.write.assert_awaited_once_with(payload)

                invalid = await client.post('/api/captures', files={'image': ('../outside.png', payload, 'image/png')})
                self.assertEqual(invalid.status_code, 422)
                self.assertEqual(invalid.json(), {'detail': 'Specified filename is invalid'})
