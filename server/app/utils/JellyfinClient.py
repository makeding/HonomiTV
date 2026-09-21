import asyncio
import re
import time
import uuid
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urljoin, urlsplit

import httpx

from app import logging, schemas
from app.config import GetJellyfinConfig
from app.constants import HTTPX_CLIENT, VERSION
from app.utils import ParseDatetimeStringToJST


class JellyfinError(Exception):
    """Jellyfin との通信失敗を利用者向けの安全な理由へ変換する例外。"""


@dataclass
class JellyfinPlaybackSession:
    """ブラウザへ秘密情報を出さずに Jellyfin の HLS リソースを代理するセッション。"""

    session_id: str
    upstream_url: str
    stream_type: Literal['hls', 'mpegts']
    live_stream_id: str | None
    resource_urls: dict[str, str] = field(default_factory=dict)
    last_accessed_at: float = field(default_factory=time.time)
    closing: bool = False


class JellyfinClient:
    """Jellyfin Live TV API とサーバー側再生セッションを管理する。"""

    _access_token: str | None = None
    _user_id: str | None = None
    _authentication_lock = asyncio.Lock()
    _playback_sessions: dict[str, JellyfinPlaybackSession] = {}
    SESSION_TIMEOUT_SECONDS = 90

    @classmethod
    def is_configured(cls) -> bool:
        return GetJellyfinConfig().enabled is True

    @classmethod
    def _base_url(cls) -> str:
        return str(GetJellyfinConfig().url).rstrip('/')

    @classmethod
    def _authorization_headers(cls) -> dict[str, str]:
        # Jellyfin の認証情報はこのヘッダーにだけ載せ、URL クエリへ api_key を追加しない。
        authorization = (
            'MediaBrowser Client="HonomiTV", Device="HonomiTV Server", '
            f'DeviceId="konomitv-server", Version="{VERSION}"'
        )
        if cls._access_token is not None:
            authorization += f', Token="{cls._access_token}"'
        return {
            'Accept': 'application/json',
            'X-Emby-Authorization': authorization,
            'Authorization': authorization,
        }

    @classmethod
    async def _authenticate(cls) -> None:
        config = GetJellyfinConfig()
        # 空のパスワードも有効な認証入力なので、可否は Jellyfin に判断させる。
        if config.username == '':
            raise JellyfinError('Jellyfin のユーザー名が設定されていません。')

        async with cls._authentication_lock:
            if cls._access_token is not None and cls._user_id is not None:
                return
            try:
                async with HTTPX_CLIENT() as client:
                    response = await client.post(
                        f'{cls._base_url()}/Users/AuthenticateByName',
                        headers={
                            'Accept': 'application/json',
                            'X-Emby-Authorization': (
                                'MediaBrowser Client="HonomiTV", Device="HonomiTV Server", '
                                f'DeviceId="konomitv-server", Version="{VERSION}"'
                            ),
                        },
                        json={'Username': config.username, 'Pw': config.password},
                        timeout=10,
                    )
            except httpx.TimeoutException as error:
                raise JellyfinError('Jellyfin への認証がタイムアウトしました。') from error
            except httpx.NetworkError as error:
                raise JellyfinError('Jellyfin に接続できません。URL とサーバーの状態を確認してください。') from error
            if response.status_code in (401, 403):
                raise JellyfinError('Jellyfin のユーザー名またはパスワードが正しくありません。')
            if response.status_code != 200:
                raise JellyfinError(f'Jellyfin の認証に失敗しました (HTTP {response.status_code})。')
            try:
                payload = response.json()
                cls._access_token = str(payload['AccessToken'])
                cls._user_id = str(payload['User']['Id'])
            except (KeyError, TypeError, ValueError) as error:
                raise JellyfinError('Jellyfin の認証応答が不正です。') from error

    @classmethod
    async def _request(cls, method: str, path: str, *, allow_disabled: bool = False, **kwargs: Any) -> httpx.Response:
        if GetJellyfinConfig().enabled is False and allow_disabled is False:
            raise JellyfinError('Jellyfin 連携が設定で無効になっています。')
        await cls._authenticate()
        request_headers = kwargs.pop('headers', {})
        if not isinstance(request_headers, dict):
            request_headers = {}
        try:
            async with HTTPX_CLIENT() as client:
                response = await client.request(
                    method,
                    f'{cls._base_url()}/{path.lstrip("/")}',
                    headers={**cls._authorization_headers(), **request_headers},
                    **kwargs,
                    timeout=10,
                )
        except httpx.TimeoutException as error:
            raise JellyfinError('Jellyfin へのリクエストがタイムアウトしました。') from error
        except httpx.NetworkError as error:
            raise JellyfinError('Jellyfin に接続できません。') from error
        if response.status_code in (401, 403):
            # トークン失効後は一度だけ再認証して同じ API を再試行する。
            cls._access_token = None
            cls._user_id = None
            await cls._authenticate()
            async with HTTPX_CLIENT() as client:
                response = await client.request(
                    method,
                    f'{cls._base_url()}/{path.lstrip("/")}',
                    headers={**cls._authorization_headers(), **request_headers},
                    **kwargs,
                    timeout=10,
                )
        if response.status_code < 200 or response.status_code >= 300:
            detail = ''
            try:
                detail = str(response.json().get('Message', '')).strip()
            except (TypeError, ValueError):
                pass
            raise JellyfinError(detail or f'Jellyfin API が HTTP {response.status_code} を返しました。')
        return response

    @classmethod
    async def get_channels(cls) -> list[dict[str, object]]:
        """Jellyfin の Live TV チャンネル一覧を取得する。"""
        response = await cls._request('GET', 'LiveTv/Channels', params={
            'UserId': cls._user_id,
            'EnableImages': 'true',
            'EnableUserData': 'false',
            'AddCurrentProgram': 'true',
        })
        payload = response.json()
        items = payload.get('Items', [])
        return items if isinstance(items, list) else []

    @classmethod
    async def get_programs(cls, start_time: datetime, end_time: datetime, channel_id: str | None) -> list[dict[str, object]]:
        """指定期間の Jellyfin 番組表を取得する。"""
        params: dict[str, object] = {
            'UserId': cls._user_id,
            'MinStartDate': start_time.isoformat(),
            'MaxEndDate': end_time.isoformat(),
            'EnableImages': 'false',
            'EnableUserData': 'false',
            'Limit': 1000,
        }
        if channel_id is not None:
            params['ChannelIds'] = channel_id
        items: list[dict[str, object]] = []
        start_index = 0
        while True:
            response = await cls._request('GET', 'LiveTv/Programs', params={**params, 'StartIndex': start_index})
            payload = response.json()
            page = payload.get('Items', [])
            if not isinstance(page, list):
                break
            valid_page = [item for item in page if isinstance(item, dict)]
            items.extend(valid_page)
            total = payload.get('TotalRecordCount')
            if len(valid_page) == 0 or (isinstance(total, int) and len(items) >= total):
                break
            start_index += len(valid_page)
        return items

    @classmethod
    async def get_channel_logo(cls, channel_id: str) -> tuple[bytes, str]:
        """ネットテレビのロゴを同一オリジン代理用に取得する。"""
        response = await cls._request(
            'GET', f'Items/{channel_id}/Images/Primary', params={'maxHeight': 256, 'maxWidth': 256},
        )
        return (response.content, response.headers.get('content-type', 'image/jpeg'))

    @classmethod
    async def open_playback(cls, channel_id: str) -> JellyfinPlaybackSession:
        """Jellyfin の Live TV 再生を開き、代理用の不透明なセッションを作る。"""
        response = await cls._request(
            'POST',
            f'Items/{channel_id}/PlaybackInfo',
            params={
                'UserId': cls._user_id,
                'StartTimeTicks': 0,
                'IsPlayback': 'true',
                'AutoOpenLiveStream': 'true',
            },
            json={
                'DeviceProfile': {
                    'Name': 'HonomiTV',
                    'MaxStreamingBitrate': 140000000,
                    'DirectPlayProfiles': [],
                    'TranscodingProfiles': [{
                        'Container': 'ts',
                        'Type': 'Video',
                        'VideoCodec': 'h264',
                        'AudioCodec': 'aac',
                        'Protocol': 'hls',
                        'Context': 'Live',
                    }],
                    'SubtitleProfiles': [],
                },
            },
        )
        payload = response.json()
        sources = payload.get('MediaSources', [])
        if not isinstance(sources, list) or len(sources) == 0:
            raise JellyfinError('Jellyfin が再生可能な Live TV ストリームを返しませんでした。')
        source = sources[0]
        if not isinstance(source, dict):
            raise JellyfinError('Jellyfin の再生情報が不正です。')
        source_url = source.get('TranscodingUrl') or source.get('DirectStreamUrl') or source.get('Path')
        if not isinstance(source_url, str) or source_url == '':
            raise JellyfinError('Jellyfin の再生 URL を取得できませんでした。')
        upstream_url = cls._resolve_resource_url(source_url, cls._base_url())
        parsed_source_url = urlsplit(upstream_url)
        stream_type = 'hls' if '.m3u8' in parsed_source_url.path or 'hls' in parsed_source_url.path.lower() else 'mpegts'
        session = JellyfinPlaybackSession(
            session_id=uuid.uuid4().hex,
            upstream_url=upstream_url,
            stream_type=stream_type,
            live_stream_id=str(source['LiveStreamId']) if source.get('LiveStreamId') else None,
        )
        cls._playback_sessions[session.session_id] = session
        return session

    @classmethod
    async def close_playback(cls, session_id: str) -> None:
        """Jellyfin 側の LiveStream を閉じ、代理セッションを破棄する。"""
        session = cls._playback_sessions.get(session_id)
        if session is None:
            return
        session.closing = True
        if session.live_stream_id is None:
            cls._playback_sessions.pop(session_id, None)
            return
        try:
            # 設定が無効化された後も、既に開いた上流 LiveStream は必ず閉じる。
            await cls._request(
                'POST',
                'LiveStreams/Close',
                allow_disabled=True,
                json={'LiveStreamId': session.live_stream_id},
            )
        except JellyfinError as error:
            logging.warning(f'[JellyfinClient] Failed to close live stream: {error}')
            return
        cls._playback_sessions.pop(session_id, None)

    @classmethod
    async def collect_expired_playbacks(cls) -> None:
        """アクセスの途絶えた Live TV セッションを上流とともに回収する。"""
        cutoff = time.time() - cls.SESSION_TIMEOUT_SECONDS
        expired_ids = [
            session_id for session_id, session in cls._playback_sessions.items()
            if session.closing is True or session.last_accessed_at < cutoff
        ]
        for session_id in expired_ids:
            await cls.close_playback(session_id)

    @classmethod
    def get_playback_session(cls, session_id: str) -> JellyfinPlaybackSession:
        """不透明なセッション ID から代理セッションを取得する。"""
        session = cls._playback_sessions.get(session_id)
        if session is None:
            raise JellyfinError('Jellyfin の再生セッションは存在しないか、すでに終了しています。')
        if session.closing is True:
            raise JellyfinError('Jellyfin の再生セッションは終了処理中です。')
        session.last_accessed_at = time.time()
        return session

    @classmethod
    def _resolve_resource_url(cls, resource_url: str, base_url: str) -> str:
        resolved_url = urljoin(base_url, resource_url)
        parsed_url = urlsplit(resolved_url)
        parsed_base_url = urlsplit(cls._base_url())
        if (parsed_url.scheme, parsed_url.netloc) != (parsed_base_url.scheme, parsed_base_url.netloc):
            raise JellyfinError('Jellyfin の再生リソース URL がサーバー外を指しています。')
        return resolved_url

    @classmethod
    def register_resource(cls, session: JellyfinPlaybackSession, resource_url: str, base_url: str | None = None) -> str:
        """HLS の内部 URL に不透明な短期参照 ID を割り当てる。"""
        upstream_url = cls._resolve_resource_url(resource_url, base_url or session.upstream_url)
        for resource_id, existing_url in session.resource_urls.items():
            if existing_url == upstream_url:
                return resource_id
        if len(session.resource_urls) >= 2048:
            # HLS の最新プレイリストを次回更新時に再登録できるよう、無制限な参照蓄積を防ぐ。
            session.resource_urls.clear()
        resource_id = uuid.uuid4().hex
        session.resource_urls[resource_id] = upstream_url
        return resource_id

    @classmethod
    async def fetch_resource(cls, session: JellyfinPlaybackSession, resource_id: str | None = None) -> tuple[bytes, str]:
        upstream_url = session.upstream_url if resource_id is None else session.resource_urls.get(resource_id)
        if upstream_url is None:
            raise JellyfinError('Jellyfin の再生リソースは存在しません。')
        async with HTTPX_CLIENT() as client:
            response = await client.get(upstream_url, headers=cls._authorization_headers(), timeout=20)
        if response.status_code < 200 or response.status_code >= 300:
            raise JellyfinError(f'Jellyfin の再生リソースが HTTP {response.status_code} を返しました。')
        return response.content, response.headers.get('content-type', 'application/octet-stream')

    @classmethod
    async def stream_resource(cls, session: JellyfinPlaybackSession, resource_id: str) -> tuple[AsyncGenerator[bytes, None], str]:
        """持続する MPEG-TS をメモリへ全量読み込みせずそのまま中継する。"""
        upstream_url = session.resource_urls.get(resource_id)
        if upstream_url is None:
            raise JellyfinError('Jellyfin の再生リソースは存在しません。')
        client = HTTPX_CLIENT()
        request = client.build_request('GET', upstream_url, headers=cls._authorization_headers())
        try:
            response = await client.send(request, stream=True)
        except (httpx.NetworkError, httpx.TimeoutException) as error:
            await client.aclose()
            raise JellyfinError('Jellyfin の再生リソースに接続できません。') from error
        if response.status_code < 200 or response.status_code >= 300:
            await response.aclose()
            await client.aclose()
            raise JellyfinError(f'Jellyfin の再生リソースが HTTP {response.status_code} を返しました。')

        async def generate() -> AsyncGenerator[bytes, None]:
            try:
                async for chunk in response.aiter_bytes():
                    session.last_accessed_at = time.time()
                    yield chunk
            finally:
                await response.aclose()
                await client.aclose()

        return (generate(), response.headers.get('content-type', 'video/mp2t'))

    @classmethod
    def rewrite_playlist(cls, session: JellyfinPlaybackSession, content: bytes, base_url: str | None = None) -> bytes:
        """プレイリスト中の全 URL を HonomiTV の同一オリジン代理 URL へ変換する。"""
        playlist = content.decode('utf-8', errors='replace')
        def replace_uri(match: re.Match[str]) -> str:
            upstream_url = match.group(1)
            resource_id = cls.register_resource(session, upstream_url, base_url)
            return f'URI="/api/streams/live/sessions/{session.session_id}/resource/{resource_id}"'
        playlist = re.sub(r'URI="([^"]+)"', replace_uri, playlist)
        lines = []
        for line in playlist.splitlines():
            if line.strip() == '' or line.startswith('#'):
                lines.append(line)
            else:
                resource_id = cls.register_resource(session, line.strip(), base_url)
                lines.append(f'/api/streams/live/sessions/{session.session_id}/resource/{resource_id}')
        return ('\n'.join(lines) + '\n').encode('utf-8')


def ToIPTVProgram(item: dict[str, object]) -> schemas.IPTVProgram | None:
    """Jellyfin 番組を不足した放送波 EPG 情報を補わない IPTV 型へ変換する。"""
    try:
        start_time = ParseDatetimeStringToJST(str(item['StartDate']))
        end_time = ParseDatetimeStringToJST(str(item['EndDate']))
        channel_id = str(item['ChannelId'])
        program_id = str(item['Id'])
    except (KeyError, TypeError, ValueError):
        return None
    duration = max((end_time - start_time).total_seconds(), 0.0)
    ticks = item.get('RunTimeTicks')
    if isinstance(ticks, (int, float)) and ticks > 0:
        duration = ticks / 10_000_000
    genres: list[schemas.Genre] = []
    source_genres = item.get('Genres')
    if isinstance(source_genres, list):
        for genre in source_genres:
            if isinstance(genre, str) and genre:
                genres.append(schemas.Genre(major=genre, middle=''))
    return schemas.IPTVProgram(
        id=f'jellyfin-{program_id}',
        channel_id=f'jellyfin-{channel_id}',
        title=str(item.get('Name') or '番組情報なし'),
        description=str(item.get('Overview') or ''),
        start_time=start_time,
        end_time=end_time,
        duration=duration,
        genres=genres,
    )


def ToIPTVChannel(item: dict[str, object]) -> schemas.IPTVChannel | None:
    """Jellyfin チャンネルを統一チャンネル一覧の IPTV カテゴリへ変換する。"""
    jellyfin_id = item.get('Id')
    if not isinstance(jellyfin_id, str) or not jellyfin_id:
        return None
    current = item.get('CurrentProgram')
    present = ToIPTVProgram(current) if isinstance(current, dict) else None
    return schemas.IPTVChannel(
        id=f'jellyfin-{jellyfin_id}',
        display_channel_id=f'jellyfin-{jellyfin_id}',
        channel_number=str(item.get('Number') or item.get('SortName') or '--'),
        name=str(item.get('Name') or '名称未設定'),
        capabilities=schemas.ChannelCapabilities(
            live_stream=True,
            live_stream_session=True,
            data_broadcasting=False,
            recording=False,
            remote_playback=False,
        ),
        program_present=present,
    )
