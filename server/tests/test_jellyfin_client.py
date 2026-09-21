import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.utils.JellyfinClient import (
    JellyfinClient,
    JellyfinError,
    JellyfinPlaybackSession,
    ToIPTVChannel,
    ToIPTVProgram,
)


class JellyfinClientTest(unittest.IsolatedAsyncioTestCase):
    """ネットテレビの統合契約と上流再生セッションを検証する。"""

    def setUp(self) -> None:
        JellyfinClient._playback_sessions.clear()  # pyright: ignore[reportPrivateUsage]

    def _config(self, enabled: bool = True) -> SimpleNamespace:
        return SimpleNamespace(enabled=enabled, url='http://jellyfin.example:8096/', username='user', password='password')

    def test_channel_and_program_keep_jellyfin_stable_ids_and_missing_broadcast_fields(self) -> None:
        channel = ToIPTVChannel({
            'Id': 'upstream-channel',
            'Number': '101',
            'Name': 'ネットテレビ',
            'CurrentProgram': {
                'Id': 'upstream-program',
                'ChannelId': 'upstream-channel',
                'Name': '番組',
                'StartDate': '2026-09-21T00:00:00+09:00',
                'EndDate': '2026-09-21T00:30:00+09:00',
            },
        })
        assert channel is not None
        self.assertEqual(channel.id, 'jellyfin-upstream-channel')
        self.assertEqual(channel.display_channel_id, channel.id)
        self.assertIsNone(channel.network_id)
        self.assertIsNone(channel.viewer_count)
        self.assertEqual(channel.source, 'Jellyfin')
        self.assertTrue(channel.capabilities.live_stream_session)
        assert channel.program_present is not None
        self.assertEqual(channel.program_present.id, 'jellyfin-upstream-program')
        self.assertEqual(channel.program_present.source, 'Jellyfin')

    def test_invalid_incomplete_program_is_not_converted_to_a_broadcast_program(self) -> None:
        self.assertIsNone(ToIPTVProgram({'Id': 'missing-dates'}))

    def test_nested_hls_relative_playlist_key_and_segments_keep_parent_base_url(self) -> None:
        session = JellyfinPlaybackSession('a' * 32, 'http://jellyfin.example:8096/live/master.m3u8', 'hls', None)
        with patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=self._config()):
            rewritten = JellyfinClient.rewrite_playlist(
                session,
                b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="keys/live.key"\nchild/stream.m3u8\nsegment.ts\n',
                'http://jellyfin.example:8096/live/nested/index.m3u8',
            ).decode()

        self.assertNotIn('keys/live.key', rewritten)
        self.assertNotIn('child/stream.m3u8', rewritten)
        self.assertEqual(set(session.resource_urls.values()), {
            'http://jellyfin.example:8096/live/nested/keys/live.key',
            'http://jellyfin.example:8096/live/nested/child/stream.m3u8',
            'http://jellyfin.example:8096/live/nested/segment.ts',
        })

    def test_external_playlist_url_is_rejected_before_credentials_can_be_forwarded(self) -> None:
        session = JellyfinPlaybackSession('b' * 32, 'http://jellyfin.example:8096/live/master.m3u8', 'hls', None)
        with patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=self._config()):
            with self.assertRaises(JellyfinError):
                JellyfinClient.register_resource(session, 'https://other.example/segment.ts')

    async def test_close_failure_is_retained_and_retried_even_after_integration_is_disabled(self) -> None:
        session = JellyfinPlaybackSession('c' * 32, 'http://jellyfin.example:8096/live.ts', 'mpegts', 'live-1')
        JellyfinClient._playback_sessions[session.session_id] = session  # pyright: ignore[reportPrivateUsage]
        request = AsyncMock(side_effect=[JellyfinError('一時障害'), SimpleNamespace(status_code=204)])
        with (
            patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=self._config(enabled=False)),
            patch.object(JellyfinClient, '_request', request),
        ):
            await JellyfinClient.close_playback(session.session_id)
            self.assertIn(session.session_id, JellyfinClient._playback_sessions)  # pyright: ignore[reportPrivateUsage]
            self.assertTrue(session.closing)
            await JellyfinClient.collect_expired_playbacks()

        self.assertNotIn(session.session_id, JellyfinClient._playback_sessions)  # pyright: ignore[reportPrivateUsage]
        self.assertEqual(request.await_count, 2)
        self.assertTrue(request.await_args_list[0].kwargs['allow_disabled'])

    async def test_expired_session_is_collected(self) -> None:
        session = JellyfinPlaybackSession('d' * 32, 'http://jellyfin.example:8096/live.ts', 'mpegts', None)
        session.last_accessed_at = time.time() - JellyfinClient.SESSION_TIMEOUT_SECONDS - 1
        JellyfinClient._playback_sessions[session.session_id] = session  # pyright: ignore[reportPrivateUsage]
        await JellyfinClient.collect_expired_playbacks()
        self.assertNotIn(session.session_id, JellyfinClient._playback_sessions)  # pyright: ignore[reportPrivateUsage]

    async def test_mpegts_generator_yields_upstream_chunks_without_reading_response_content(self) -> None:
        session = JellyfinPlaybackSession('e' * 32, 'http://jellyfin.example:8096/live.ts', 'mpegts', None)
        session.resource_urls['resource'] = session.upstream_url

        class Response:
            status_code = 200
            headers = {'content-type': 'video/mp2t'}
            content = property(lambda _: (_ for _ in ()).throw(AssertionError('must not buffer MPEG-TS')))

            async def aiter_bytes(self):
                yield b'first'
                yield b'second'

            async def aclose(self) -> None:
                return None

        class Client:
            def build_request(self, *args, **kwargs):
                return object()

            async def send(self, *args, **kwargs):
                return Response()

            async def aclose(self) -> None:
                return None

        with (
            patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=self._config()),
            patch('app.utils.JellyfinClient.HTTPX_CLIENT', return_value=Client()),
        ):
            stream, media_type = await JellyfinClient.stream_resource(session, 'resource')
            self.assertEqual(media_type, 'video/mp2t')
            self.assertEqual([chunk async for chunk in stream], [b'first', b'second'])


if __name__ == '__main__':
    unittest.main()
