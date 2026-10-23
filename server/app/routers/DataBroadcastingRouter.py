
import asyncio
import ipaddress
import json
import re
import socket
import time
from typing import Annotated, cast
from urllib.parse import unquote, urljoin, urlsplit

import aiohttp
import httpx
from aiohttp.abc import AbstractResolver, ResolveResult
from fastapi import APIRouter, Form, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse
from ping3 import ping

from app import logging, schemas
from app.constants import API_REQUEST_HEADERS


# ルーター
router = APIRouter(
    tags = ['Data Broadcasting'],
    prefix = '/api/data-broadcasting',
)

# ARIB STD-B62 HTML5 データ放送からの外部通信を許可するホスト。
## 放送アプリの JavaScript から任意 URL を渡せるため、サブドメインの後方一致は使わず、
## 実際の BS4K/BS8K 放送アプリで確認できたホストだけを完全一致で許可する。
## dev / stg 用ホストやオーサリング環境のプライベート IP は、放送アプリに文字列が含まれていても許可しない。
ARIB_HTML5_EXTERNAL_PROXY_ALLOWED_HOSTS: frozenset[str] = frozenset({
    '4kdata-p.qvc.jp',
    'api.qvc.jp',
    'api.nhk.or.jp',
    'beacon.nhk.jp',
    'img.nhk.jp',
    'hc.nhk.or.jp',
    'hc.nhk.jp',
    'nhk.jp',
    'qvc.jp',
    'qvc.scene7.com',
    'shv.nhk.jp',
    'tv-stream.nhk.jp',
    'tvip.hc.skyperfectv.co.jp',
    'www.nhk-cs.jp',
    'www.nhk.or.jp',
})

# 放送アプリの外部通信は短い API / 静的リソース取得が中心なので、
## KonomiTV サーバーを巨大ファイルの中継や長時間接続に使われないよう上限を設ける。
ARIB_HTML5_EXTERNAL_PROXY_MAX_REQUEST_SIZE = 64 * 1024
ARIB_HTML5_EXTERNAL_PROXY_MAX_RESPONSE_SIZE = 32 * 1024 * 1024
ARIB_HTML5_EXTERNAL_PROXY_MAX_REDIRECTS = 3


class ARIBHTML5ExternalProxyResolver(AbstractResolver):
    """ ARIB HTML5 外部通信プロキシ用に、接続先を公開 IP アドレスだけへ制限する。 """

    def __init__(self) -> None:
        """
        OS 標準の名前解決器を初期化する。

        Args:
            なし

        Returns:
            なし
        """

        # 許可ホストの DNS 名前解決に使う aiohttp 標準 Resolver。
        ## resolve() で返された全アドレスを検査し、TCPConnector が実際に使う結果だけを返す。
        self.resolver = aiohttp.DefaultResolver()

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[ResolveResult]:
        """
        ホスト名を公開 IP アドレスへ解決する。

        Args:
            host (str): 解決対象のホスト名
            port (int): 接続先ポート
            family (socket.AddressFamily): アドレスファミリー

        Returns:
            list[aiohttp.abc.ResolveResult]: TCPConnector が接続に使う名前解決結果
        """

        resolved_addresses = await self.resolver.resolve(host, port, family)

        # 1 件でもプライベート・ループバック・リンクローカルなどが含まれる場合は、
        ## DNS ラウンドロビンの別アドレスへフォールバックさせずリクエスト全体を拒否する。
        for resolved_address in resolved_addresses:
            ip_address = ipaddress.ip_address(resolved_address['host'])
            if ip_address.is_global is False:
                raise OSError(f'Non-public IP address is not allowed: {ip_address}')

        return resolved_addresses

    async def close(self) -> None:
        """
        内部 Resolver を終了する。

        Args:
            なし

        Returns:
            なし
        """

        await self.resolver.close()


def ValidateARIBHTML5ExternalProxyURL(request_url: str) -> str:
    """
    ARIB HTML5 外部通信プロキシの転送先 URL を検証する。

    Args:
        request_url (str): 放送アプリが要求した転送先 URL

    Returns:
        str: 検証済みの転送先 URL
    """

    # ヘッダーインジェクションにつながる制御文字や、URL として曖昧な空白を拒否する。
    if any(ord(character) < 0x20 or ord(character) == 0x7f or character.isspace() for character in request_url):
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Request URL contains invalid characters',
        )

    try:
        parsed_url = urlsplit(request_url)
        hostname = parsed_url.hostname
        port = parsed_url.port
    except ValueError as ex:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Request URL is invalid',
        ) from ex

    # HTTP(S) の絶対 URL 以外は受け付けない。
    if parsed_url.scheme not in ('http', 'https') or hostname is None or parsed_url.netloc == '':
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Request URL must be an absolute HTTP or HTTPS URL',
        )

    # ユーザー情報・フラグメント・末尾ドット・非標準ポートは、
    ## ホスト判定の解釈差や別サービスへのアクセスを避けるため禁止する。
    if (
        parsed_url.username is not None or
        parsed_url.password is not None or
        parsed_url.fragment != '' or
        hostname.endswith('.') or
        (port is not None and port != (80 if parsed_url.scheme == 'http' else 443))
    ):
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail = 'Request URL is not allowed',
        )

    # 国際化ドメインを ASCII に正規化してから完全一致で判定する。
    try:
        normalized_hostname = hostname.encode('idna').decode('ascii').lower()
    except UnicodeError as ex:
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Request URL hostname is invalid',
        ) from ex
    if normalized_hostname not in ARIB_HTML5_EXTERNAL_PROXY_ALLOWED_HOSTS:
        logging.warning(
            '[DataBroadcastingRouter][ValidateARIBHTML5ExternalProxyURL] '
            f'Blocked an external request to a non-allowlisted host. [host: {normalized_hostname}]'
        )
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail = 'Request URL host is not allowed',
        )

    # SkyPerfecTV の実験は公開コンテンツだけに限定し、リダイレクト先にも同じ制約を適用する。
    decoded_path = unquote(parsed_url.path)
    if normalized_hostname == 'tvip.hc.skyperfectv.co.jp' and (
        not decoded_path.startswith('/content-sky/') or '..' in decoded_path.split('/') or '\\' in decoded_path
    ):
        raise HTTPException(status_code = status.HTTP_403_FORBIDDEN, detail = 'Hybridcast public content GET only')

    return request_url


def ReplaceReceiverIdentityInARIBHTML5JSON(value: object) -> object:
    """
    ARIB HTML5 放送アプリが生成した JSON 内の開発用受信機名を互換値へ置換する。

    Args:
        value (object): JSON からデコードした値

    Returns:
        object: 文字列値に含まれる huggy を test へ置換した JSON 値
    """

    # JSON の文字列値に含まれる開発用受信機名だけを、大文字・小文字を区別せず置換する。
    ## キー名は放送局 API の契約なので変更しない。
    if isinstance(value, str):
        return re.sub('huggy', 'test', value, flags=re.IGNORECASE)

    # 入れ子になった denbun なども漏れなく処理する。
    if isinstance(value, list):
        return [ReplaceReceiverIdentityInARIBHTML5JSON(item) for item in value]
    if isinstance(value, dict):
        return {
            key: ReplaceReceiverIdentityInARIBHTML5JSON(item)
            for key, item in value.items()
        }

    # 数値・真偽値・null はそのまま維持する。
    return value


# 以下の API 実装は web-bml での実装を Python に移植したもの (with GPT-4)
# ref: https://github.com/tsukumijima/web-bml/blob/master/server/index.ts#L195-L296


@router.api_route(
    '/arib-html5/request',
    methods = ['GET', 'POST'],
    summary = 'ARIB HTML5 データ放送外部通信プロキシ API',
    response_description = '許可された外部 URL に対するリクエストのレスポンス。',
)
async def ARIBHTML5BrowserRequestProxyAPI(
    request: Request,
    request_url: Annotated[str, Query(alias='url', description='リクエスト URL 。')],
):
    """
    ARIB STD-B62 HTML5 データ放送の外部通信を、許可ホストに限定して中継する。<br>
    ブラウザから外部サイトへ直接アクセスした場合に発生する CORS 制約を回避しつつ、
    KonomiTV サーバーが任意 URL へアクセスできるオープンプロキシになることを防ぐ。

    Args:
        request (Request): 放送アプリからのプロキシリクエスト
        request_url (str): 放送アプリが要求した転送先 URL

    Returns:
        StreamingResponse: 外部サイトからのレスポンス
    """

    current_url = ValidateARIBHTML5ExternalProxyURL(request_url)

    # Hybridcast の実験で必要なのは公開 CMS とアプリ資源のみ。
    ## カード番号・アカウント情報を要求する gateway や POST は中継しない。
    parsed_hybridcast_url = urlsplit(current_url)
    if parsed_hybridcast_url.hostname == 'tvip.hc.skyperfectv.co.jp' and (
        request.method != 'GET' or not parsed_hybridcast_url.path.startswith('/content-sky/')
    ):
        raise HTTPException(status_code = status.HTTP_403_FORBIDDEN, detail = 'Hybridcast public content GET only')

    # POST 本文は視聴者参加やビーコン用途だけを想定し、巨大な本文をメモリへ読み込まない。
    request_body = b''
    if request.method == 'POST':
        request_body_chunks: list[bytes] = []
        request_body_size = 0
        async for request_body_chunk in request.stream():
            request_body_size += len(request_body_chunk)
            if request_body_size > ARIB_HTML5_EXTERNAL_PROXY_MAX_REQUEST_SIZE:
                raise HTTPException(
                    status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail = 'Request body is too large',
                )
            request_body_chunks.append(request_body_chunk)
        request_body = b''.join(request_body_chunks)

        # 放送アプリの JSON に libaribhtml5 の開発用受信機名 huggy が含まれる場合は、
        ## 外部 API へ送る直前に互換用の test へ置換する。
        ## JSON 以外の本文や壊れた JSON は、放送局固有の電文を破壊しないよう原文のまま転送する。
        content_type = request.headers.get('Content-Type', '').split(';', maxsplit=1)[0].strip().lower()
        parsed_current_url = urlsplit(current_url)
        if (
            parsed_current_url.hostname == '4kdata-p.qvc.jp' and
            parsed_current_url.path.startswith('/v1/') and
            (content_type == 'application/json' or content_type.endswith('+json'))
        ):
            try:
                decoded_request_body = cast(object, json.loads(request_body))
                replaced_request_body = ReplaceReceiverIdentityInARIBHTML5JSON(decoded_request_body)
                request_body = json.dumps(
                    replaced_request_body,
                    ensure_ascii = False,
                    separators = (',', ':'),
                ).encode('utf-8')
            except (json.JSONDecodeError, UnicodeDecodeError):
                pass

    # 放送アプリが必要とする条件付き取得・Range・本文形式だけを上流へ渡す。
    ## Cookie・Authorization・Host・X-Forwarded-* など、KonomiTV 側の情報は絶対に転送しない。
    request_headers = {
        'Accept': request.headers.get('Accept', '*/*'),
        'Accept-Language': 'ja',
        # HTML5 放送受信機として要求し、PC 向け案内ページへの振り分けを防ぐ。
        'User-Agent': 'Hybridcast/1.0',
        'Cache-Control': request.headers.get('Cache-Control', 'no-cache'),
        'Pragma': 'no-cache',
    }
    # QVC の BS4K データ放送 API は、放送アプリに同梱された X-API-Key を要求する。
    ## 値はログや設定へ保存せず、このリクエストの上流転送だけに使う。
    for header_name in ['Content-Type', 'If-Modified-Since', 'If-None-Match', 'Range', 'X-API-Key']:
        header_value = request.headers.get(header_name)
        if header_value is not None:
            request_headers[header_name] = header_value

    resolver = ARIBHTML5ExternalProxyResolver()
    connector = aiohttp.TCPConnector(
        resolver = resolver,
        use_dns_cache = False,
    )
    session = aiohttp.ClientSession(
        connector = connector,
        headers = {**API_REQUEST_HEADERS, **request_headers},
        timeout = aiohttp.ClientTimeout(total=30, connect=10, sock_connect=10, sock_read=15),
        auto_decompress = False,
    )

    response: aiohttp.ClientResponse | None = None
    upstream_method = request.method
    upstream_body = request_body
    try:
        # リダイレクト先も毎回 URL ホワイトリストと公開 IP Resolver を通す。
        ## 301 / 302 / 303 は一般的なブラウザ挙動に合わせて GET へ切り替え、307 / 308 は本文を維持する。
        for redirect_count in range(ARIB_HTML5_EXTERNAL_PROXY_MAX_REDIRECTS + 1):
            # 他の許可ホストからの 307 / 308 でも、SkyPerfecTV へ本文を転送しない。
            if urlsplit(current_url).hostname == 'tvip.hc.skyperfectv.co.jp' and upstream_method != 'GET':
                raise HTTPException(status_code = status.HTTP_403_FORBIDDEN, detail = 'Hybridcast public content GET only')
            response = await session.request(
                method = upstream_method,
                url = current_url,
                data = upstream_body if upstream_method == 'POST' else None,
                allow_redirects = False,
            )
            if response.status not in (301, 302, 303, 307, 308):
                break

            redirect_status = response.status
            redirect_location = response.headers.get('Location')
            if redirect_location is None or redirect_count >= ARIB_HTML5_EXTERNAL_PROXY_MAX_REDIRECTS:
                response.release()
                raise HTTPException(
                    status_code = status.HTTP_502_BAD_GATEWAY,
                    detail = 'External server returned an invalid redirect',
                )

            redirected_url = urljoin(current_url, redirect_location)
            current_url = ValidateARIBHTML5ExternalProxyURL(redirected_url)
            response.release()
            response = None
            if upstream_method == 'POST' and redirect_status in (301, 302, 303):
                # 307 / 308 以外では POST を GET に変更する。
                upstream_method = 'GET'
                upstream_body = b''
        else:
            raise HTTPException(
                status_code = status.HTTP_502_BAD_GATEWAY,
                detail = 'External server returned too many redirects',
            )
    except HTTPException:
        await session.close()
        raise
    except (aiohttp.ClientError, TimeoutError, OSError) as ex:
        await session.close()
        logging.warning(
            '[DataBroadcastingRouter][ARIBHTML5BrowserRequestProxyAPI] Failed to request an external resource.',
            exc_info = ex,
        )
        raise HTTPException(
            status_code = status.HTTP_502_BAD_GATEWAY,
            detail = 'Failed to request an external resource',
        ) from ex

    if response is None:
        await session.close()
        raise HTTPException(
            status_code = status.HTTP_502_BAD_GATEWAY,
            detail = 'External server did not return a response',
        )

    # Content-Length が既知の場合は、レスポンス開始前にサイズ上限を検査する。
    content_length = response.content_length
    if content_length is not None and content_length > ARIB_HTML5_EXTERNAL_PROXY_MAX_RESPONSE_SIZE:
        response.release()
        await session.close()
        raise HTTPException(
            status_code = status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail = 'External response is too large',
        )

    # 外部サイトの Set-Cookie・Location・CSP などを同一 origin の KonomiTV 応答へ持ち込まない。
    allowed_response_headers = {
        'accept-ranges',
        'age',
        'cache-control',
        'content-encoding',
        'content-language',
        'content-length',
        'content-type',
        'date',
        'etag',
        'expires',
        'last-modified',
    }
    response_headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() in allowed_response_headers
    }

    async def GenerateResponseBody():
        """
        上流レスポンスをサイズ上限付きでブラウザへ中継する。

        Args:
            なし

        Yields:
            bytes: 外部サイトから受信したレスポンス本文のチャンク
        """

        streamed_size = 0
        try:
            async for chunk in response.content.iter_chunked(256 * 1024):
                streamed_size += len(chunk)
                if streamed_size > ARIB_HTML5_EXTERNAL_PROXY_MAX_RESPONSE_SIZE:
                    logging.warning(
                        '[DataBroadcastingRouter][ARIBHTML5BrowserRequestProxyAPI] '
                        'Stopped an external response that exceeded the size limit.'
                    )
                    break
                yield chunk
        finally:
            response.release()
            await session.close()

    return StreamingResponse(
        GenerateResponseBody(),
        status_code = response.status,
        headers = response_headers,
    )


@router.get(
    '/request/{request_url:path}',
    summary = 'データ放送ブラウザ HTTP (GET) リクエストプロキシ API',
    response_description = 'リクエスト URL に対する GET リクエストのレスポンス。',
)
async def BMLBrowserRequestGETProxyAPI(
    request_url: Annotated[str, Path(description='リクエスト URL 。')],
    request: Request,
):
    """
    データ放送ブラウザ (web-bml) のネット接続機能から利用される、HTTP (GET) プロキシ。<br>
    Web ブラウザからの HTTP リクエストには CORS の制限があるため、この API を経由してリクエストを送信する。<br>
    web-bml のネット接続機能専用の API で、web-bml 以外からは利用されない。
    """

    # パスパラメーターに含まれる転送先 URL のクエリは FastAPI 側では request.query_params に分離されるため復元する
    if request.url.query:
        request_url += f'?{request.url.query}'

    # URLが HTTP または HTTPS URL かのバリデーション
    if not (request_url.startswith("http://") or request_url.startswith("https://")):
        logging.error(f'[DataBroadcastingRouter][BMLBrowserRequestGETProxyAPI] Request URL must be http or https URL: {request_url}')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Request URL must be http or https URL',
        )

    logging.debug(f'Request URL: {request_url}')

    headers = {
        'Accept': '*/*',
        'Accept-Language': 'ja',
        'Pragma': 'no-cache',
    }
    allowed_request_headers = ['if-modified-since', 'cache-control']
    for key, value in request.headers.items():
        if key.lower() in allowed_request_headers:
            headers[key] = value

    # タイムアウトはデータ放送の動作を壊さないようにあえて設定しない
    # さらにデータ放送からアクセスされるサイトは HTTPS の場合でも証明書が切れていることが日常茶飯事なので、証明書の検証を行わない
    ## 正確には放送波経由で古い規格の HTTPS 証明書が降ってきているらしいが、どのみち実装困難なので証明書の状態は無視する
    async with httpx.AsyncClient(headers={**API_REQUEST_HEADERS, **headers}, follow_redirects=True, verify=False) as client:
        try:
            response = await client.get(request_url)
        except Exception as ex:
            # リクエスト中に例外が発生した場合は、エラーメッセージをログに出力して 500 エラーを返す
            ## HTTP リクエスト自体が DNS 名前解決エラーや接続エラーで失敗した場合に発生する
            logging.error('[DataBroadcastingRouter][BMLBrowserRequestGETProxyAPI] Failed to request:', exc_info=ex)
            raise HTTPException(
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail = f'Failed to request: {ex}',
            )

    allowed_response_headers = [
        'accept-ranges',
        'authentication-info',
        'last-modified',
        'pragma',
        'date',
        'cache-control',
        'age',
        'expire',
        'content-language',
        'content-location',
        'content-type',
    ]
    response_headers = {key: value for key, value in response.headers.items() if key.lower() in allowed_response_headers}

    return StreamingResponse(response.iter_bytes(), headers=response_headers)


@router.post(
    '/request/{request_url:path}',
    summary = 'データ放送ブラウザ HTTP (POST) リクエストプロキシ API',
    response_description = 'リクエスト URL に対する POST リクエストのレスポンス。',
)
async def BMLBrowserRequestPOSTProxyAPI(
    request_url: Annotated[str, Path(description='リクエスト URL 。')],
    request: Request,
    Denbun: Annotated[str, Form(max_length=4096, description='データ放送ブラウザからのリクエストボディ (Denbun) 。')] = '',
):
    """
    データ放送ブラウザ (web-bml) のネット接続機能から利用される、HTTP (POST) プロキシ。<br>
    Web ブラウザからの HTTP リクエストには CORS の制限があるため、この API を経由してリクエストを送信する。<br>
    web-bml のネット接続機能専用の API で、web-bml 以外からは利用されない。<br>
    Denbun は仕様書いわく「電文」のことらしく、データ放送ブラウザからの x-www-form-urlencoded 形式の値のキー名は Denbun で固定されている。
    """

    # パスパラメーターに含まれる転送先 URL のクエリは FastAPI 側では request.query_params に分離されるため復元する
    if request.url.query:
        request_url += f'?{request.url.query}'

    # URLが HTTP または HTTPS URL かのバリデーション
    if not (request_url.startswith("http://") or request_url.startswith("https://")):
        logging.error(f'[DataBroadcastingRouter][BMLBrowserRequestPOSTProxyAPI] Request URL must be http or https URL: {request_url}')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Request URL must be http or https URL',
        )

    logging.debug(f'Request URL: {request_url}')
    logging.debug(f'Denbun: {Denbun}')

    headers = {
        'Accept': '*/*',
        'Pragma': 'no-cache',
        'Content-Type': 'application/x-www-form-urlencoded',
    }

    # タイムアウトはデータ放送の動作を壊さないようにあえて設定しない
    # さらにデータ放送からアクセスされるサイトは HTTPS の場合でも証明書が切れていることが日常茶飯事なので、証明書の検証を行わない
    ## 正確には放送波経由で古い規格の HTTPS 証明書が降ってきているらしいが、どのみち実装困難なので証明書の状態は無視する
    async with httpx.AsyncClient(headers={**API_REQUEST_HEADERS, **headers}, follow_redirects=True, verify=False) as client:
        try:
            response = await client.post(request_url, content=f'Denbun={Denbun}')
        except Exception as ex:
            # リクエスト中に例外が発生した場合は、エラーメッセージをログに出力して 500 エラーを返す
            ## HTTP リクエスト自体が DNS 名前解決エラーや接続エラーで失敗した場合に発生する
            logging.error('[DataBroadcastingRouter][BMLBrowserRequestPOSTProxyAPI] Failed to request:', exc_info=ex)
            raise HTTPException(
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail = f'Failed to request: {ex}',
            )

    allowed_response_headers = [
        'accept-ranges',
        'authentication-info',
        'last-modified',
        'pragma',
        'date',
        'cache-control',
        'age',
        'expire',
        'content-language',
        'content-location',
        'content-type',
    ]
    response_headers = {key: value for key, value in response.headers.items() if key.lower() in allowed_response_headers}

    return StreamingResponse(response.iter_bytes(), headers=response_headers)


@router.get(
    '/internet-status',
    summary = 'データ放送ブラウザネット接続状態確認 API',
    response_description = 'データ放送ブラウザ向けのネット接続状態。',
    response_model = schemas.DataBroadcastingInternetStatus,
)
async def BMLBrowserInternetStatusAPI(
    destination: Annotated[str, Query(description='接続先のホスト名または IP アドレス。')],
    is_icmp: Annotated[bool, Query(description='HTTP の代わりに ICMP (Ping) を使用するかどうか。')] = False,
    timeout_milliseconds: Annotated[int, Query(description='タイムアウト時間 (ミリ秒) 。')] = 3000,
):
    """
    データ放送ブラウザ (web-bml) のネット接続機能から利用される、ネット接続状態確認 API。<br>
    Web ブラウザからの HTTP リクエストには CORS の制限があるため、この API により KonomiTV サーバー側がネットに接続できるかが確認される。<br>
    web-bml のネット接続機能専用の API で、web-bml 以外からは利用されない。
    """

    # ICMP を使用する場合は ping3 ライブラリで ICMP パケットのレスポンス時間を取得
    if is_icmp is True:
        response_time = ping(destination, timeout=int(timeout_milliseconds / 1000))
        success = response_time is not None

    # ICMP を使用しない場合は asyncio.open_connection() でレスポンス時間を取得
    else:
        start = time.time()
        try:
            await asyncio.wait_for(asyncio.open_connection(destination, 80), timeout=timeout_milliseconds / 1000)
            success = True
            response_time = time.time() - start
        except Exception:
            success = False
            response_time = None

    # ミリ秒単位のレスポンス時間
    response_time_milliseconds = int(response_time * 1000) if response_time is not None else None

    return schemas.DataBroadcastingInternetStatus(
        success = success,
        ip_address = socket.gethostbyname(destination) if success else None,
        response_time_milliseconds = response_time_milliseconds,
    )
