# Type Hints を指定できるように
from __future__ import annotations

import time
from typing import Any, ClassVar, Literal

import httpx2 as httpx

from app.constants import HTTPX_CLIENT
from app.utils import GetMirakurunAPIEndpointURL


class MirakurunStreamFormat:
    """Mirakurun の Service Stream に対する format 指定の互換性を判定して URL を組み立てる。"""

    # OpenAPI を取得できた Mirakurun / mirakc ごとの format パラメータ対応状況を保持する。
    # ライブ視聴開始のたびに /api/docs を取得しないよう 60 秒だけ再利用する。
    _format_support_cache: ClassVar[dict[str, tuple[float, bool]]] = {}

    # 接続先の構成変更を長時間見逃さず、同時視聴時の不要な OpenAPI 取得も抑えるキャッシュ時間。
    _CACHE_TTL_SECONDS: ClassVar[int] = 60

    @classmethod
    async def getServiceStreamURL(
        cls,
        service_id: int,
        output_format: Literal['ts', 'tlv'],
        fallback_decode: bool | None,
    ) -> str:
        """
        接続先が format パラメータを公開していれば、復号とコンテナを独立指定した Service Stream URL を返す。

        Args:
            service_id (int): Mirakurun 形式のサービス ID。
            output_format (Literal['ts', 'tlv']): 受信側が必要とするコンテナ形式。
            fallback_decode (bool | None): format 非対応時に維持する従来の decode 指定。None なら指定しない。

        Returns:
            str: 利用可能な API 契約に合わせた Service Stream URL。
        """

        stream_path = f'/api/services/{service_id}/stream'

        # format=tlv / format=ts に対応する Mirakurun では B61 復号を有効にしたまま必要なコンテナを選べる。
        if await cls.supportsServiceStreamFormat() is True:
            return GetMirakurunAPIEndpointURL(f'{stream_path}?decode=1&format={output_format}')

        # 古い Mirakurun / mirakc は format を知らないため、既存の decode 指定を厳密に維持する。
        if fallback_decode is True:
            return GetMirakurunAPIEndpointURL(f'{stream_path}?decode=1')
        if fallback_decode is False:
            return GetMirakurunAPIEndpointURL(f'{stream_path}?decode=0')
        return GetMirakurunAPIEndpointURL(stream_path)

    @classmethod
    async def supportsServiceStreamFormat(cls) -> bool:
        """
        現在の Mirakurun / mirakc が Service Stream の format=ts|tlv を明示的に公開するか判定する。

        Args:
            なし。

        Returns:
            bool: OpenAPI が format=ts|tlv を公開しているときだけ True。
        """

        docs_url = GetMirakurunAPIEndpointURL('/api/docs')
        cached = cls._format_support_cache.get(docs_url)
        if cached is not None and (time.monotonic() - cached[0]) < cls._CACHE_TTL_SECONDS:
            return cached[1]

        # ネットワーク障害は一時的であり、False をキャッシュすると復旧後の B61 復号まで妨げるため保存しない。
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.get(docs_url, timeout=2)
        except (httpx.NetworkError, httpx.TimeoutException):
            return False

        # 壊れた JSON は互換性を示す根拠にできないため format 非対応として扱う。
        try:
            openapi = response.json() if response.status_code == 200 else None
        except ValueError:
            openapi = None

        # 応答を得られた場合だけ、OpenAPI に現れた契約を短時間キャッシュする。
        is_supported = cls._hasServiceStreamFormat(openapi)
        cls._format_support_cache[docs_url] = (time.monotonic(), is_supported)
        return is_supported

    @staticmethod
    def _hasServiceStreamFormat(openapi: Any) -> bool:
        """
        Swagger 2.0 OpenAPI から Service Stream の format=ts|tlv 定義を探す。

        Args:
            openapi (Any): /api/docs が返した JSON。

        Returns:
            bool: path-level または GET-level に完全な format enum があるとき True。
        """

        if not isinstance(openapi, dict) or openapi.get('swagger') != '2.0':
            return False
        paths = openapi.get('paths')
        if not isinstance(paths, dict):
            return False
        service_stream = paths.get('/services/{id}/stream')
        if not isinstance(service_stream, dict):
            return False

        parameters: list[Any] = []
        path_parameters = service_stream.get('parameters')
        if isinstance(path_parameters, list):
            parameters.extend(path_parameters)
        get_operation = service_stream.get('get')
        if isinstance(get_operation, dict):
            get_parameters = get_operation.get('parameters')
            if isinstance(get_parameters, list):
                parameters.extend(get_parameters)

        for parameter in parameters:
            if not isinstance(parameter, dict):
                continue
            if parameter.get('in') != 'query' or parameter.get('name') != 'format' or parameter.get('type') != 'string':
                continue
            enum = parameter.get('enum')
            if isinstance(enum, list) and 'ts' in enum and 'tlv' in enum:
                return True
        return False
