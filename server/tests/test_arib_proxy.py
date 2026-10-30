import unittest
from unittest.mock import patch

import httpx
from fastapi import FastAPI

from app.routers.DataBroadcastingRouter import ValidateARIBHTML5ExternalProxyURL, router


class ARIBProxyTest(unittest.IsolatedAsyncioTestCase):

    async def test_blocked_error_page(self) -> None:
        app = FastAPI()
        app.include_router(router)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            with patch('app.routers.DataBroadcastingRouter.aiohttp.ClientSession') as upstream:
                for method in ['GET', 'POST']:
                    for url in [
                        'http://hc.bsjhc.jp/error/bsjhc/error.php',
                        'https://hc.bsjhc.jp/error/bsjhc/error.php?reason=runtime',
                        'http://HC.BSJHC.JP:80/error/bsjhc/%65rror.php',
                    ]:
                        with self.subTest(method=method, url=url):
                            response = await client.request(
                                method,
                                '/api/data-broadcasting/arib-html5/request',
                                params={'url': url},
                            )
                            self.assertEqual(response.status_code, 403)
                            self.assertEqual(response.json(), {'detail': 'Request URL path is not allowed'})
                upstream.assert_not_called()

        for url in [
            'http://hc.bsjhc.jp/hc/entry/html_mm/startup.min.html',
            'http://hc.bsjhc.jp/hc/lib/x_bjhc/x_bjhc.min.js',
        ]:
            self.assertEqual(ValidateARIBHTML5ExternalProxyURL(url), url)
