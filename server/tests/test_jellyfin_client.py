import time
import unittest
from collections import OrderedDict
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.constants import JST
from app.routers.ChannelsRouter import GetIPTVChannels
from app.routers.ChannelsRouter import router as channels_router
from app.routers.ProgramsRouter import GetIPTVTimeTable
from app.routers.ProgramsRouter import router as programs_router
from app.utils.JellyfinClient import (
    JellyfinClient,
    JellyfinError,
    JellyfinPlaybackSession,
    SetIPTVCurrentAndNextPrograms,
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

    async def test_playback_info_authenticates_before_building_user_id_and_uses_streaming_context(self) -> None:
        async def authenticate() -> None:
            JellyfinClient._user_id = 'authenticated-user'  # pyright: ignore[reportPrivateUsage]

        response = Mock()
        response.json.return_value = {
            'MediaSources': [{
                'TranscodingUrl': '/Videos/live.m3u8',
                'LiveStreamId': 'live-stream',
            }],
        }
        request = AsyncMock(return_value=response)
        with (
            patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=self._config()),
            patch.object(JellyfinClient, '_authenticate', AsyncMock(side_effect=authenticate)),
            patch.object(JellyfinClient, '_request', request),
            patch.object(JellyfinClient, '_user_id', None),
        ):
            session = await JellyfinClient.open_playback('channel')

        self.assertEqual(session.stream_type, 'hls')
        self.assertEqual(request.await_args.kwargs['params']['UserId'], 'authenticated-user')
        profile = request.await_args.kwargs['json']['DeviceProfile']['TranscodingProfiles'][0]
        self.assertEqual(profile['Context'], 'Streaming')
        self.assertEqual(profile['MinSegments'], 1)

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
        }, datetime(2026, 9, 21, 0, 15, tzinfo=JST))
        assert channel is not None
        self.assertEqual(channel.id, 'jellyfin-upstream-channel')
        self.assertEqual(channel.display_channel_id, channel.id)
        self.assertIsNone(channel.network_id)
        self.assertIsNone(channel.viewer_count)
        self.assertEqual(channel.source, 'Jellyfin')
        self.assertTrue(channel.capabilities.live_stream_session)
        self.assertTrue(channel.capabilities.remote_playback)
        self.assertFalse(channel.capabilities.recording)
        self.assertFalse(channel.capabilities.data_broadcasting)
        assert channel.program_present is not None
        self.assertEqual(channel.program_present.id, 'jellyfin-upstream-program')
        self.assertEqual(channel.program_present.source, 'Jellyfin')

    def test_cctv_channels_are_numerically_sorted_without_moving_other_channels(self) -> None:
        channels = [
            {'Name': '地方局'}, {'Name': 'CCTV-10'}, {'Name': 'CCTV-5+'}, {'Name': 'CCTV-1'},
            {'Name': '国外局'}, {'Name': 'CCTV-5'}, {'Name': 'CCTV-2'},
        ]
        sorted_channels = JellyfinClient.sort_channels(channels)
        self.assertEqual(
            [channel['Name'] for channel in sorted_channels],
            ['地方局', 'CCTV-1', 'CCTV-2', 'CCTV-5', '国外局', 'CCTV-5+', 'CCTV-10'],
        )

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

    def test_resource_map_evicts_only_the_least_recently_used_reference(self) -> None:
        session = JellyfinPlaybackSession('f' * 32, 'http://jellyfin.example:8096/live/master.m3u8', 'hls', None)
        session.resource_urls = OrderedDict(
            (f'id-{index}', f'http://jellyfin.example:8096/live/{index}.ts') for index in range(2048)
        )
        with patch('app.utils.JellyfinClient.GetJellyfinConfig', return_value=self._config()):
            JellyfinClient.get_resource_url(session, 'id-0')
            JellyfinClient.register_resource(session, 'new.ts')

        self.assertIn('id-0', session.resource_urls)
        self.assertNotIn('id-1', session.resource_urls)
        self.assertEqual(len(session.resource_urls), 2048)

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
            patch.object(JellyfinClient, 'get_programs', AsyncMock(return_value=[])),
        ):
            channels, error = await GetIPTVChannels()
            self.assertEqual(channels, [])
            self.assertEqual(error, 'タイムアウト')
            channels, error = await GetIPTVChannels()

        self.assertIsNone(error)
        self.assertEqual([channel.id for channel in channels], ['jellyfin-channel-a'])

    def test_program_handoff_gap_midnight_and_offsets(self) -> None:
        channel = ToIPTVChannel({'Id': 'channel'})
        assert channel is not None
        programs = [ToIPTVProgram(item) for item in [
            {'Id': 'next', 'ChannelId': 'channel', 'StartDate': '2026-09-22T00:30:00+09:00', 'EndDate': '2026-09-22T01:00:00+09:00'},
            {'Id': 'midnight', 'ChannelId': 'channel', 'StartDate': '2026-09-21T15:00:00Z', 'EndDate': '2026-09-21T15:15:00Z'},
            {'Id': 'first', 'ChannelId': 'channel', 'StartDate': '2026-09-21T22:30:00+08:00', 'EndDate': '2026-09-21T23:00:00+08:00'},
        ]]
        valid_programs = [program for program in programs if program is not None]
        for instant, present, following in [
            ('2026-09-21T23:59:59+09:00', 'first', 'midnight'),
            ('2026-09-22T00:00:00+09:00', 'midnight', 'next'),
            ('2026-09-21T23:00:00+08:00', 'midnight', 'next'),
            ('2026-09-22T00:15:00+09:00', None, 'next'),
            ('2026-09-22T01:00:00+09:00', None, None),
        ]:
            with self.subTest(instant=instant):
                SetIPTVCurrentAndNextPrograms([channel], valid_programs, datetime.fromisoformat(instant))
                self.assertEqual(channel.program_present.id if channel.program_present else None, f'jellyfin-{present}' if present else None)
                self.assertEqual(channel.program_following.id if channel.program_following else None, f'jellyfin-{following}' if following else None)

    def test_program_rejects_naive_or_invalid_interval_and_preserves_instant(self) -> None:
        item = {'Id': 'program', 'ChannelId': 'channel', 'StartDate': '2026-09-21T19:00:00+08:00', 'EndDate': '2026-09-21T12:00:00Z'}
        program = ToIPTVProgram(item)
        assert program is not None
        self.assertEqual(program.start_time.isoformat(), item['StartDate'])
        self.assertEqual(program.duration, 3600)
        for start in ['2026-09-21T19:00:00', 'invalid', '2026-09-21T12:00:00Z']:
            self.assertIsNone(ToIPTVProgram({**item, 'StartDate': start}))

    async def test_program_query_uses_overlap_filters_and_all_pages(self) -> None:
        request = AsyncMock(side_effect=[
            Mock(json=Mock(return_value={'Items': [{'Id': 'first'}], 'TotalRecordCount': 2})),
            Mock(json=Mock(return_value={'Items': [{'Id': 'last'}], 'TotalRecordCount': 2})),
        ])
        start = datetime(2026, 9, 21, 12, tzinfo=JST)
        end = datetime(2026, 9, 21, 13, tzinfo=JST)
        with patch.object(JellyfinClient, '_authenticate', AsyncMock()), patch.object(JellyfinClient, '_request', request):
            programs = await JellyfinClient.get_programs(start, end, None)
        self.assertEqual([program['Id'] for program in programs], ['first', 'last'])
        params = request.await_args_list[0].kwargs['params']
        self.assertEqual(params['MinEndDate'], start.isoformat())
        self.assertEqual(params['MaxStartDate'], end.isoformat())
        self.assertNotIn('MinStartDate', params)
        self.assertNotIn('MaxEndDate', params)
        self.assertEqual(request.await_args_list[1].kwargs['params']['StartIndex'], 1)

    def test_channels_list_and_detail_share_programs_and_keep_channels_on_epg_failure(self) -> None:
        now = datetime.now(JST)
        current = {
            'Id': 'current', 'ChannelId': 'channel', 'Name': '現在',
            'StartDate': (now - timedelta(minutes=30)).isoformat(),
            'EndDate': (now + timedelta(minutes=30)).isoformat(),
        }
        following = {
            'Id': 'following', 'ChannelId': 'channel', 'Name': '次',
            'StartDate': current['EndDate'], 'EndDate': (now + timedelta(hours=1)).isoformat(),
        }
        app = FastAPI()
        app.include_router(channels_router)
        with (
            patch.object(JellyfinClient, 'is_configured', return_value=True),
            patch.object(JellyfinClient, 'get_channels', AsyncMock(return_value=[{'Id': 'channel', 'CurrentProgram': current}])),
            patch.object(JellyfinClient, 'get_programs', AsyncMock(side_effect=[[following, current], [following, current], JellyfinError('EPG unavailable'), TimeoutError()])),
        ):
            client = TestClient(app)
            listing = client.get('/api/channels?source=IPTV')
            detail = client.get('/api/channels/jellyfin-channel')
            failed = client.get('/api/channels?source=IPTV')
            timeout = client.get('/api/channels/jellyfin-channel')
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(listing.json()['IPTV'][0], detail.json())
        self.assertEqual(detail.json()['capabilities'], {
            'live_stream': True, 'live_stream_session': True, 'remote_playback': True,
            'recording': False, 'data_broadcasting': False,
        })
        self.assertEqual(detail.json()['program_following']['id'], 'jellyfin-following')
        for response, channel in [(failed, failed.json()['IPTV'][0]), (timeout, timeout.json())]:
            self.assertEqual(response.status_code, 200)
            self.assertEqual(channel['program_present']['id'], 'jellyfin-current')
            self.assertIsNone(channel['program_following'])
            self.assertNotEqual(response.headers['X-Channel-Source-Errors'], '{"IPTV":null}')

    def test_channels_http_body_contains_only_arrays_and_source_error_is_safe_header_json(self) -> None:
        app = FastAPI()
        app.include_router(channels_router)
        with patch.object(JellyfinClient, 'is_configured', return_value=True), patch.object(
            JellyfinClient, 'get_channels', AsyncMock(side_effect=JellyfinError('認証に失敗しました。')),
        ):
            response = TestClient(app).get('/api/channels?source=IPTV')

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(all(isinstance(value, list) for value in body.values()))
        self.assertEqual(body['IPTV'], [])
        self.assertEqual(response.headers['X-Channel-Source-Errors'], '{"IPTV":"\\u8a8d\\u8a3c\\u306b\\u5931\\u6557\\u3057\\u307e\\u3057\\u305f\\u3002"}')

    async def test_timetable_pinned_network_channels_keep_order_and_only_return_overlapping_programs(self) -> None:
        start = datetime(2026, 9, 21, 12, 0, tzinfo=JST)
        end = datetime(2026, 9, 21, 13, 0, tzinfo=JST)
        channels = [{'Id': 'first', 'Name': '第一'}, {'Id': 'second', 'Name': '第二'}]
        programs = [
            {
                'Id': 'overlap-end', 'ChannelId': 'first', 'Name': '終端をまたぐ番組',
                'StartDate': '2026-09-21T12:30:00+09:00', 'EndDate': '2026-09-21T13:30:00+09:00',
            },
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
        self.assertEqual([program.id for program in timetable[1].programs], ['jellyfin-overlap', 'jellyfin-overlap-end'])
        self.assertIsNone(timetable[1].programs[0].reservation)  # type: ignore[union-attr]

    def test_timetable_network_filter_never_queries_broadcast_channels(self) -> None:
        """ネット分類とソース指定が放送波の全局クエリへ落ちないことを検証する。"""
        app = FastAPI()
        app.include_router(programs_router)
        for selection in ({'channel_type': 'IPTV'}, {'source': 'IPTV'}, {'source': 'Jellyfin'}):
            with self.subTest(selection=selection):
                # 日付範囲以外の SQL は失敗させ、放送局が存在しても混入する余地を残さない。
                query = AsyncMock(side_effect=[[]])
                with (
                    patch('app.routers.ProgramsRouter.connections.get', return_value=SimpleNamespace(execute_query_dict=query)),
                    patch.object(JellyfinClient, 'is_configured', return_value=True),
                    patch.object(JellyfinClient, 'get_channels', AsyncMock(return_value=[{'Id': 'channel', 'Name': 'ネット局'}])),
                    patch.object(JellyfinClient, 'get_programs', AsyncMock(return_value=[])),
                ):
                    response = TestClient(app).get('/api/programs/timetable', params={
                        **selection, 'start_time': '2026-09-21T12:00:00+09:00',
                        'end_time': '2026-09-21T13:00:00+09:00',
                    })
                self.assertEqual(response.status_code, 200)
                self.assertEqual([row['channel']['id'] for row in response.json()['channels']], ['jellyfin-channel'])
                self.assertEqual(response.json()['channels'][0]['channel']['type'], 'IPTV')
                self.assertEqual(response.json()['channels'][0]['programs'], [])
                self.assertEqual(response.json()['source_errors'], {'IPTV': None})
                query.assert_awaited_once()

    def test_timetable_http_retains_overlap_at_both_ends_and_reports_epg_failure(self) -> None:
        programs = [
            {'Id': 'last', 'ChannelId': 'channel', 'StartDate': '2026-09-21T12:30:00+09:00', 'EndDate': '2026-09-21T13:30:00+09:00'},
            {'Id': 'first', 'ChannelId': 'channel', 'StartDate': '2026-09-21T11:30:00+09:00', 'EndDate': '2026-09-21T12:30:00+09:00'},
            {'Id': 'ended', 'ChannelId': 'channel', 'StartDate': '2026-09-21T11:00:00+09:00', 'EndDate': '2026-09-21T12:00:00+09:00'},
            {'Id': 'future', 'ChannelId': 'channel', 'StartDate': '2026-09-21T13:00:00+09:00', 'EndDate': '2026-09-21T14:00:00+09:00'},
        ]
        app = FastAPI()
        app.include_router(programs_router)
        with (
            patch('app.routers.ProgramsRouter.connections.get', return_value=SimpleNamespace(execute_query_dict=AsyncMock(return_value=[]))),
            patch.object(JellyfinClient, 'is_configured', return_value=True),
            patch.object(JellyfinClient, 'get_channels', AsyncMock(return_value=[{'Id': 'channel'}])),
            patch.object(JellyfinClient, 'get_programs', AsyncMock(side_effect=[programs, JellyfinError('EPG unavailable')])),
        ):
            client = TestClient(app)
            params = {'source': 'IPTV', 'start_time': '2026-09-21T12:00:00+09:00', 'end_time': '2026-09-21T13:00:00+09:00'}
            response = client.get('/api/programs/timetable', params=params)
            failed = client.get('/api/programs/timetable', params=params)
        self.assertEqual(response.status_code, 200)
        row = response.json()['channels'][0]
        self.assertEqual(row['channel']['id'], 'jellyfin-channel')
        self.assertEqual([program['id'] for program in row['programs']], ['jellyfin-first', 'jellyfin-last'])
        self.assertEqual(row['programs'][0]['start_time'], '2026-09-21T11:30:00+09:00')
        self.assertEqual(row['programs'][1]['end_time'], '2026-09-21T13:30:00+09:00')
        self.assertEqual(failed.status_code, 200)
        self.assertEqual(failed.json()['channels'][0]['channel']['id'], 'jellyfin-channel')
        self.assertEqual(failed.json()['source_errors']['IPTV'], 'EPG unavailable')

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
