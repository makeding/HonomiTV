import unittest
from datetime import datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from tortoise import Tortoise

from app.config import ClientSettings
from app.constants import DATABASE_CONFIG, JST
from app.models.Channel import Channel
from app.models.RecordedProgram import RecordedProgram
from app.models.RecordedVideo import RecordedVideo
from app.models.User import User
from app.routers.SettingsRouter import router
from app.routers.UsersRouter import GenerateAccessToken


class LiveWatchedHistoryTest(unittest.IsolatedAsyncioTestCase):
    """JWT 認証・SQLite 保存・履歴 HTTP 読み戻しを通してライブの録画対応を検証する。"""

    async def asyncSetUp(self) -> None:
        await Tortoise.init(
            db_url='sqlite://:memory:',
            modules={'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models'])},
            timezone=DATABASE_CONFIG['timezone'],
        )
        await Tortoise.generate_schemas()
        self.user = await User.create(
            name='live-history', password='unused', is_admin=False,
            client_settings=ClientSettings().model_dump(mode='json'),
        )
        self.start = (datetime.now(JST) - timedelta(hours=1)).replace(microsecond=0)
        self.channel = await Channel.create(
            id='NID4-SID101', display_channel_id='bs101', network_id=4, service_id=101,
            remocon_id=1, channel_number='101', type='BS', name='Test',
            is_subchannel=False, is_radiochannel=False, is_watchable=True,
        )
        application = FastAPI()
        application.include_router(router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=application), base_url='http://test',
            headers={'Authorization': f'Bearer {GenerateAccessToken(self.user.id)}'},
        )

    async def asyncTearDown(self) -> None:
        await self.client.aclose()
        await Tortoise.close_connections()

    async def CreateRecording(self, start_offset: int = 0, margin: int = 10) -> RecordedProgram:
        """本編と実ファイルの時刻が異なる入庫済み録画を用意する。"""
        start = self.start + timedelta(seconds=start_offset)
        program = await RecordedProgram.create(
            channel=self.channel, recording_start_margin=margin, recording_end_margin=0,
            is_partially_recorded=margin < 0, title='Live programme', description='', detail={},
            start_time=start, end_time=start + timedelta(minutes=30), duration=1800,
            is_free=True, genres=[], primary_audio_type='Stereo', primary_audio_language='日本語',
        )
        await RecordedVideo.create(
            recorded_program=program, status='Recording', file_path=f'/nonexistent/live-{program.id}.ts',
            file_hash=str(program.id), file_size=1, file_created_at=start, file_modified_at=start,
            recording_start_time=start - timedelta(seconds=margin), duration=30,
            container_format='MPEG-TS', video_codec='H.264', video_codec_profile='High',
            video_scan_type='Progressive', video_frame_rate=30, video_resolution_width=1920,
            video_resolution_height=1080, primary_audio_codec='AAC-LC', primary_audio_channel='Stereo',
            primary_audio_sampling_rate=48000,
        )
        return program

    async def Report(self, offset: int, channel_id: str | None = None) -> list[dict[str, float | int]]:
        response = await self.client.put('/api/settings/client/watched-history', json={
            'items': [], 'live_playback_progress': {
                'channel_id': channel_id or self.channel.id,
                'playback_time': (self.start + timedelta(seconds=offset)).timestamp(),
            },
        })
        self.assertEqual(response.status_code, 200, response.text)
        # 全レスポンスを保存済みの公開 HTTP 読み取り口と照合する。
        readback = await self.client.get('/api/settings/client/watched-history')
        self.assertEqual(readback.status_code, 200, readback.text)
        self.assertEqual(readback.json(), response.json())
        return response.json()['items']

    async def test_late_insertion_and_recording_duration_do_not_drop_live_progress(self) -> None:
        self.assertEqual(await self.Report(100), [])
        program = await self.CreateRecording()
        with patch('app.utils.BangumiPlaybackSync.SyncBangumiPlaybackProgress', AsyncMock()) as bangumi:
            items = await self.Report(120)
            observed_at = (self.start + timedelta(seconds=120)).timestamp()
            self.assertEqual(items, [{
                'video_id': program.id, 'last_playback_position': 130.0,
                'created_at': observed_at, 'updated_at': observed_at,
            }])
            bangumi.assert_not_awaited()
        # 受信順が逆でも、最後に観測した位置を古い最終保存で巻き戻さない。
        self.assertEqual(await self.Report(110), items)
        settings = await self.client.get('/api/settings/client')
        self.assertEqual(settings.status_code, 200)
        self.assertEqual(settings.json()['watched_history'], items)
        self.assertNotIn('live_playback_progress', settings.json())

    async def test_channel_and_programme_boundaries(self) -> None:
        first = await self.CreateRecording()
        second = await self.CreateRecording(start_offset=1800)
        self.assertEqual(await self.Report(100, 'NID4-SID102'), [])
        await self.Report(1799)
        items = await self.Report(1800)
        self.assertEqual([(item['video_id'], item['last_playback_position']) for item in items], [
            (second.id, 10.0), (first.id, 1809.0),
        ])

    async def test_web_settings_sync_resolves_live_recording_without_extra_history_put(self) -> None:
        settings = ClientSettings().model_dump(mode='json')
        for offset, inserted in [(100, False), (120, True)]:
            if inserted:
                program = await self.CreateRecording()
            report_time = (self.start + timedelta(seconds=offset)).timestamp()
            response = await self.client.put('/api/settings/client', json={
                **settings, 'live_playback_progress': {'channel_id': self.channel.id, 'playback_time': report_time},
            })
            self.assertEqual(response.status_code, 204, response.text)
            readback = await self.client.get('/api/settings/client')
            self.assertEqual(readback.status_code, 200, readback.text)
            expected = [{
                'video_id': program.id, 'last_playback_position': 130.0,
                'created_at': report_time, 'updated_at': report_time,
            }] if inserted else []
            self.assertEqual(readback.json(), {**settings, 'watched_history': expected})
            history = await self.client.get('/api/settings/client/watched-history')
            self.assertEqual(history.status_code, 200)
            self.assertEqual(history.json(), {'items': expected})

    async def test_partial_recording_missing_start_and_completed_duration(self) -> None:
        program = await self.CreateRecording(margin=-100)
        self.assertEqual(await self.Report(50), [])
        items = await self.Report(120)
        self.assertEqual(items[0]['last_playback_position'], 20)
        video = await RecordedVideo.get(recorded_program=program)
        video.recording_start_time = None
        await video.save(update_fields=['recording_start_time'])
        self.assertEqual(await self.Report(130), items)
        video.recording_start_time = self.start + timedelta(seconds=100)
        video.recording_end_time = self.start + timedelta(seconds=300)
        video.duration = 180
        video.status = 'Recorded'
        await video.save()
        self.assertEqual((await self.Report(400))[0]['last_playback_position'], 180)

    async def test_authentication_validation_and_independent_users(self) -> None:
        program = await self.CreateRecording()
        await self.Report(120)
        other = await User.create(name='other', password='unused', is_admin=False, client_settings={})
        other_headers = {'Authorization': f'Bearer {GenerateAccessToken(other.id)}'}
        response = await self.client.get('/api/settings/client/watched-history', headers=other_headers)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'items': []})
        for value in [-1, 'NaN', 'Infinity']:
            response = await self.client.put('/api/settings/client/watched-history', json={
                'items': [], 'live_playback_progress': {'channel_id': self.channel.id, 'playback_time': value},
            })
            self.assertEqual(response.status_code, 422, response.text)
        # 放送時刻として解釈できない有限値もサーバーエラーにしない。
        response = await self.client.put('/api/settings/client/watched-history', headers=other_headers, json={
            'items': [], 'live_playback_progress': {'channel_id': self.channel.id, 'playback_time': 1e100},
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'items': []})
        self.client.headers.pop('Authorization')
        for method in (self.client.get, self.client.put):
            response = await method('/api/settings/client/watched-history')
            self.assertEqual(response.status_code, 401)
        self.assertIsNotNone(await RecordedProgram.get_or_none(id=program.id))
