import asyncio
from collections.abc import AsyncGenerator
from uuid import uuid4

import aiohttp
import anyio
from fastapi import HTTPException

from app import logging
from app.constants import API_REQUEST_HEADERS
from app.models.Channel import Channel
from app.utils import GetBackendForReceiving, GetMirakurunAPIEndpointURL
from app.utils.edcb.EDCBTuner import EDCBTuner
from app.utils.edcb.PipeStreamReader import PipeStreamReader


class LiveDataBroadcastingStream:
    """独立データ放送の TS を、映像タスクを作らずリクエスト単位で受信する。"""

    def __init__(self, channel: Channel) -> None:
        """
        Args:
            channel (Channel): データ放送を受信するチャンネル。

        Returns:
            None
        """

        # open() で使う受信対象。サービス種別はルーターで検証済みである。
        self._channel = channel
        # EDCB の制御権を close() まで保持し、別リクエストのチューナーを閉じない。
        self._owner_id = f'data-{uuid4()}'
        # open()/chunks()/close() が共有する Mirakurun 接続。EDCB 利用時は None のまま。
        self._session: aiohttp.ClientSession | None = None
        self._response: aiohttp.ClientResponse | None = None
        # open()/close() が所有する EDCB チューナーと chunks() の読み取り元。
        self._tuner: EDCBTuner | None = None
        self._reader: asyncio.StreamReader | PipeStreamReader | None = None

    async def open(self) -> None:
        """
        HTTP ヘッダーを返す前に受信元への接続を確立する。

        Args:
            なし

        Returns:
            None
        """

        try:
            # 接続はリクエストが所有し、失敗・キャンセル時も必ず解放する。
            if GetBackendForReceiving() == 'Mirakurun':
                service_id = self._channel.network_id * 100000 + self._channel.service_id
                self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=None, connect=15, sock_read=15))
                # BML に暗号化済み TS は渡せないため、通常のサービス受信と同じ復号済み TS を要求する。
                self._response = await self._session.get(
                    GetMirakurunAPIEndpointURL(f'/api/services/{service_id}/stream'),
                    headers={**API_REQUEST_HEADERS, 'X-Mirakurun-Priority': '0'},
                )
                if self._response.status != 200:
                    raise HTTPException(503, f'Data broadcasting tuner returned HTTP {self._response.status} [DATA_TUNER_HTTP]')
            else:
                self._tuner = EDCBTuner.getOrCreate(self._owner_id)
                self._tuner.lock(self._owner_id)
                if not await self._tuner.setChannel(
                    self._channel.network_id, self._channel.service_id,
                    self._channel.transport_stream_id or 0, self._owner_id,
                ):
                    raise HTTPException(503, 'No EDCB tuner is available [DATA_TUNER_UNAVAILABLE]')
                self._reader = await self._tuner.connect(self._owner_id)
                if self._reader is None:
                    raise HTTPException(503, 'Could not connect to EDCB tuner [DATA_TUNER_CONNECT]')
        except BaseException as ex:
            await self.close()
            # 接続失敗の理由は安全なコード付きでクライアントに返す。
            if isinstance(ex, (aiohttp.ClientError, TimeoutError)):
                raise HTTPException(503, 'Could not connect to data broadcasting tuner [DATA_TUNER_CONNECT]') from ex
            raise

    async def chunks(self) -> AsyncGenerator[bytes, None]:
        """
        放送波を元の TS のまま逐次配信し、切断時に受信元を解放する。

        Args:
            なし

        Yields:
            bytes: BML デコーダーへ渡す TS チャンク。

        Returns:
            None
        """

        try:
            while True:
                # 小さなチャンクで受信するため、低ビットレートのデータ放送でも蓄積待ちを長引かせない。
                if self._response is not None:
                    chunk = await self._response.content.read(188 * 64)
                elif self._reader is not None:
                    chunk = await asyncio.wait_for(self._reader.readexactly(188 * 16), timeout=15)
                else:
                    break
                if not chunk:
                    break
                yield chunk
        except (aiohttp.ClientError, TimeoutError, asyncio.IncompleteReadError, OSError) as ex:
            # 応答開始後は EOF で切断を通知し、UI が再試行操作とともに受信失敗を表示する。
            logging.warning(f'[LiveDataBroadcastingStream] Reception ended: {type(ex).__name__}')
        finally:
            await self.close()

    async def close(self) -> None:
        """
        自分が所有する受信接続のみを冪等に解放する。

        Args:
            なし

        Returns:
            None
        """

        # StreamingResponse の終了処理とジェネレーターの両方から呼ばれても二重に閉じない。
        # Starlette の切断時キャンセルスコープの中でも、チューナー解放の await を完了させる。
        with anyio.CancelScope(shield=True):
            if self._response is not None:
                self._response.close()
                self._response = None
            if self._session is not None:
                await self._session.close()
                self._session = None
            if self._tuner is not None:
                tuner, self._tuner = self._tuner, None
                self._reader = None
                await tuner.close(self._owner_id)
