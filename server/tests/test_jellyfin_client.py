import time
import unittest
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.constants import JST
from app.routers.ChannelsRouter import GetIPTVChannels
from app.routers.ProgramsRouter import GetIPTVTimeTable
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

    async def test_empty_password_is_sent_to_jellyfin_for_authentication(self) -> None:
        config = self._config()
        config.password = ''
        response = Mock(status_code=200)
        response.json.return_value = {'AccessToken': 'test-token', 'User': {'Id': 'test-user'}}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with (
            patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=config),
            patch('app.utils.JellyfinClient.HTTPX_CLIENT', return_value=client),
            patch.object(JellyfinClient, '_access_token', None),
            patch.object(JellyfinClient, '_user_id', None),
        ):
            await JellyfinClient._authenticate()  # pyright: ignore[reportPrivateUsage]
            self.assertEqual(client.post.await_args.kwargs['json'], {'Username': 'user', 'Pw': ''})
            self.assertEqual(JellyfinClient._access_token, 'test-token')  # pyright: ignore[reportPrivateUsage]

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

    async def test_unified_channels_reports_upstream_failure_and_recovers(self) -> None:
        upstream_channel = {'Id': 'channel-a', 'Name': 'ネットA'}
        with (
            patch.object(JellyfinClient, 'is_configured', return_value=True),
            patch.object(JellyfinClient, 'get_channels', AsyncMock(side_effect=[JellyfinError('タイムアウト'), [upstream_channel]])),
        ):
            channels, error = await GetIPTVChannels()
            self.assertEqual(channels, [])
            self.assertEqual(error, 'タイムアウト')
            channels, error = await GetIPTVChannels()

        self.assertIsNone(error)
        self.assertEqual([channel.id for channel in channels], ['jellyfin-channel-a'])

    async def test_timetable_pinned_network_channels_keep_order_and_only_return_overlapping_programs(self) -> None:
        start = datetime(2026, 9, 21, 12, 0, tzinfo=JST)
        end = datetime(2026, 9, 21, 13, 0, tzinfo=JST)
        channels = [{'Id': 'first', 'Name': '第一'}, {'Id': 'second', 'Name': '第二'}]
        programs = [
            {
                'Id': 'overlap', 'ChannelId': 'first', 'Name': '重なる番組',
                'StartDate': '2026-09-21T11:30:00+09:00', 'EndDate': '2026-09-21T12:30:00+09:00',
            },
            {
                'Id': 'outside', 'ChannelId': 'second', 'Name': '範囲外',
                'StartDate': '2026-09-21T13:00:00+09:00', 'EndDate': '2026-09-21T14:00:00+09:00',
            },
        ]
        with (
            patch.object(JellyfinClient, 'is_configured', return_value=True),
            patch.object(JellyfinClient, 'get_channels', AsyncMock(return_value=channels)),
            patch.object(JellyfinClient, 'get_programs', AsyncMock(return_value=programs)),
        ):
            timetable, error = await GetIPTVTimeTable(
                start, end, None, ['jellyfin-second', 'jellyfin-first'],
            )

        self.assertIsNone(error)
        self.assertEqual([entry.channel.id for entry in timetable], ['jellyfin-second', 'jellyfin-first'])
        self.assertEqual([program.id for program in timetable[1].programs], ['jellyfin-overlap'])
        self.assertIsNone(timetable[1].programs[0].reservation)  # type: ignore[union-attr]

    async def test_broadcast_timetable_source_never_requests_jellyfin_even_with_pinned_ip_tv_id(self) -> None:
        get_channels = AsyncMock()
        with (
            patch.object(JellyfinClient, 'is_configured', return_value=True),
            patch.object(JellyfinClient, 'get_channels', get_channels),
        ):
            timetable, error = await GetIPTVTimeTable(
                datetime.now(JST), datetime.now(JST), None, ['jellyfin-channel'], 'Broadcast',
            )

        self.assertEqual(timetable, [])
        self.assertIsNone(error)
        get_channels.assert_not_awaited()

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
