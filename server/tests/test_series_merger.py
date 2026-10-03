import asyncio
import importlib
import sqlite3
import unittest
from datetime import date, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from tortoise import Tortoise
from tortoise.backends.base.client import BaseDBAsyncClient

from app import schemas
from app.constants import DATABASE_CONFIG, JST
from app.metadata.SeriesIndexer import NormalizeSeriesTitle, SeriesIndexer
from app.metadata.SeriesMerger import SeriesMerger
from app.models.Channel import Channel
from app.models.RecordedProgram import RecordedProgram
from app.models.RecordedVideo import RecordedVideo
from app.models.Series import Series
from app.models.SeriesAlias import SeriesAlias
from app.models.SeriesBroadcastPeriod import SeriesBroadcastPeriod
from app.routers.SeriesRouter import router as series_router
from app.routers.VideosRouter import router as videos_router


ANIME_GENRES = [{'major': 'アニメ・特撮', 'middle': '国内アニメ'}]


class SeriesMergerTest(unittest.IsolatedAsyncioTestCase):
    """Bangumi 条目で確定した重複 Series の完全統合を検証する。"""

    async def test_video_http_readers_expose_persisted_episode_mapping(self) -> None:
        """実 SQLite と HTTP 入口で、各一覧の Bangumi ID が単体 API と一致する。"""

        series = await Series.create(normalized_title='work', title='作品', description='', genres=ANIME_GENRES)
        target = await self._createRecordedProgram(series, None, None, '13', 1746074)
        target.bangumi_subject_id = 616808
        await target.save(update_fields=['bangumi_subject_id'])
        anchor = await self._createRecordedProgram(series, None, None, '1', 1559545)
        now = datetime.now(JST)
        for recording in (target, anchor):
            recording.start_time = now
            recording.end_time = now + timedelta(minutes=30)
            await recording.save(update_fields=['start_time', 'end_time'])
            await RecordedVideo.create(
                recorded_program=recording, status='Recorded', file_path=f'/nonexistent/{recording.id}.ts',
                file_hash=str(recording.id), file_size=1, file_created_at=now, file_modified_at=now,
                duration=1800.0, container_format='MPEG-TS', video_codec='H.264', video_codec_profile='High',
                video_scan_type='Progressive', video_frame_rate=30.0, video_resolution_width=1920,
                video_resolution_height=1080, primary_audio_codec='AAC-LC', primary_audio_channel='Stereo',
                primary_audio_sampling_rate=48000,
            )
        application = FastAPI()
        application.include_router(videos_router)
        application.include_router(series_router)
        # ファイル再解析だけは別責務なので除外し、DB 読取・SQL 投影・レスポンス検証は実処理を通す。
        with patch('app.routers.VideosRouter.RecordedScanTask') as scan_task:
            scan_task.return_value.refreshRecordedFileMetadataIfNeeded = AsyncMock(return_value=False)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=application), base_url='http://test') as client:
                detail = await client.get(f'/api/videos/{target.id}')
                self.assertEqual(detail.status_code, 200, detail.text)
                expected = detail.json()
                for path in (
                    '/api/videos', '/api/videos/search?query=作品', f'/api/videos/series/{series.id}',
                    f'/api/videos/related?video_id={anchor.id}&include_other_channels=true',
                ):
                    with self.subTest(path=path):
                        response = await client.get(path)
                        self.assertEqual(response.status_code, 200, response.text)
                        actual = next(item for item in response.json()['recorded_programs'] if item['id'] == target.id)
                        # 単体 ORM と生 SQL は UTC/JST の表記が違うため、同じ時刻として全フィールドを比較する。
                        self.assertEqual(schemas.RecordedProgram.model_validate(actual), schemas.RecordedProgram.model_validate(expected))
                        self.assertEqual((actual['bangumi_subject_id'], actual['bangumi_episode_id']), (616808, 1746074))
                on_air = await client.get('/api/series/on-air')
                self.assertEqual(on_air.status_code, 200, on_air.text)
                self.assertEqual(on_air.json(), {'series_list': [{
                    'id': series.id, 'title': '作品', 'thumbnail_recorded_program_ids': [], 'channel_ids': [],
                    'recorded_episodes_count': 2, 'missing_episodes_count': 11, 'partially_recorded_episodes_count': 0,
                    'weekday': now.weekday(), 'broadcast_time': f'{now.hour:02d}:{(now.minute // 5) * 5:02d}',
                    'latest_broadcast_at': now.isoformat(),
                }]})

    async def asyncSetUp(self) -> None:
        """
        各テスト専用のメモリ内 SQLite スキーマを作成する。

        Returns:
            None
        """

        await Tortoise.init(
            db_url = 'sqlite://:memory:',
            modules = {
                'models': cast(list[str], DATABASE_CONFIG['apps']['models']['models']),
            },
        )
        await Tortoise.generate_schemas()


    async def asyncTearDown(self) -> None:
        """
        メモリ内 SQLite 接続を破棄する。

        Returns:
            None
        """

        await Tortoise.close_connections()


    async def _createChannel(self, channel_id: str, service_id: int) -> Channel:
        """
        放送期間の統合検証に必要な最小のチャンネルを作成する。

        Args:
            channel_id (str): チャンネルの永続 ID。
            service_id (int): テスト内で一意のサービス ID。

        Returns:
            Channel: 作成したチャンネル。
        """

        return await Channel.create(
            id = channel_id,
            display_channel_id = channel_id,
            network_id = 4,
            service_id = service_id,
            remocon_id = service_id,
            channel_number = str(service_id),
            type = 'BS',
            name = channel_id,
            is_subchannel = False,
            is_radiochannel = False,
            is_watchable = True,
        )


    async def _createRecordedProgram(
        self,
        series: Series,
        period: SeriesBroadcastPeriod | None,
        channel: Channel | None,
        episode_number: str | None,
        bangumi_episode_id: int | None,
    ) -> RecordedProgram:
        """
        Series 移行で欠落してはならない録画レコードを作成する。

        Args:
            series (Series): 移行元 Series。
            period (SeriesBroadcastPeriod | None): 移行元の放送期間。
            channel (Channel | None): 録画元チャンネル。
            episode_number (str | None): EPG から解決した話数。
            bangumi_episode_id (int | None): 保持すべき Bangumi エピソード ID。

        Returns:
            RecordedProgram: 作成した録画番組。
        """

        start_time = datetime(2026, 8, 1, tzinfo=JST) + timedelta(days=series.id)
        return await RecordedProgram.create(
            recording_start_margin = 0.0,
            recording_end_margin = 0.0,
            is_partially_recorded = False,
            channel = channel,
            series = series,
            series_broadcast_period = period,
            title = f'{series.title} #{episode_number or "SP"}',
            series_title = series.title,
            episode_number = episode_number,
            subtitle = None,
            bangumi_subject_id = 777 if bangumi_episode_id is not None else None,
            bangumi_episode_id = bangumi_episode_id,
            description = '',
            detail = {},
            start_time = start_time,
            end_time = start_time + timedelta(minutes=30),
            duration = 1800.0,
            is_free = True,
            genres = ANIME_GENRES,
            primary_audio_type = '2/0モード(ステレオ)',
            primary_audio_language = '日本語',
        )


    async def test_same_subject_merges_all_periods_programs_and_aliases(self) -> None:
        """同じ条目の全 Series を最小 ID へ収束させ、録画と放送期間を保持する。"""

        channel_a = await self._createChannel('bs-a', 1)
        channel_b = await self._createChannel('bs-b', 2)
        canonical = await Series.create(normalized_title='work', title='作品', description='', genres=ANIME_GENRES)
        source_a = await Series.create(
            normalized_title='work-subtitle',
            title='作品 ~副題~',
            description='',
            genres=ANIME_GENRES,
            bangumi_subject_id=777,
        )
        source_b = await Series.create(normalized_title='work-short', title='作品略称', description='', genres=ANIME_GENRES)
        canonical_period = await SeriesBroadcastPeriod.create(
            series=canonical, channel=channel_a, start_date=date(2026, 8, 10), end_date=date(2026, 8, 10),
        )
        source_a_period = await SeriesBroadcastPeriod.create(
            series=source_a, channel=channel_a, start_date=date(2026, 8, 1), end_date=date(2026, 8, 5),
        )
        source_b_period_a = await SeriesBroadcastPeriod.create(
            series=source_b, channel=channel_a, start_date=date(2026, 8, 15), end_date=date(2026, 8, 20),
        )
        source_b_period_b = await SeriesBroadcastPeriod.create(
            series=source_b, channel=channel_b, start_date=date(2026, 8, 3), end_date=date(2026, 8, 3),
        )
        programs = [
            await self._createRecordedProgram(canonical, canonical_period, channel_a, '1', 1001),
            await self._createRecordedProgram(source_a, source_a_period, channel_a, '2', 1002),
            await self._createRecordedProgram(source_b, source_b_period_a, channel_a, None, None),
            await self._createRecordedProgram(source_b, source_b_period_b, channel_b, '3', 1003),
            await self._createRecordedProgram(source_b, None, None, None, None),
        ]

        await SeriesMerger.mergeByBangumiSubject(canonical.id, 777, 'Work', None, 'Summary', 'image')
        merged = await SeriesMerger.mergeByBangumiSubject(source_b.id, 777, 'Work', None, 'Summary', 'image')

        self.assertEqual(merged.id, canonical.id)
        self.assertEqual(await Series.all().count(), 1)
        periods = await SeriesBroadcastPeriod.filter(series_id=canonical.id).order_by('channel_id')
        self.assertEqual(len(periods), 2)
        merged_channel_a_period = next(period for period in periods if period.channel_id == channel_a.id)
        self.assertEqual(merged_channel_a_period.start_date, date(2026, 8, 1))
        self.assertEqual(merged_channel_a_period.end_date, date(2026, 8, 20))
        refreshed_programs = await RecordedProgram.filter(id__in=[program.id for program in programs]).order_by('id')
        self.assertEqual(len(refreshed_programs), len(programs))
        self.assertTrue(all(program.series_id == canonical.id for program in refreshed_programs))
        self.assertTrue(all(program.series_title == canonical.title for program in refreshed_programs))
        self.assertEqual(
            [program.bangumi_episode_id for program in refreshed_programs],
            [1001, 1002, None, 1003, None],
        )
        self.assertTrue(all(
            program.series_broadcast_period_id == merged_channel_a_period.id
            for program in refreshed_programs
            if program.channel_id == channel_a.id
        ))
        aliases = await SeriesAlias.filter(series_id=canonical.id).order_by('normalized_title')
        self.assertEqual(
            [alias.normalized_title for alias in aliases],
            ['work', 'work-short', 'work-subtitle'],
        )


    async def test_merged_title_alias_prevents_series_recreation(self) -> None:
        """統合元の表記を再スキャンしても、alias から既存 Series へ復帰する。"""

        canonical = await Series.create(normalized_title='work', title='作品', description='', genres=ANIME_GENRES)
        source_title = '作品 ~Subtitle~'
        await SeriesAlias.create(normalized_title=NormalizeSeriesTitle(source_title), series=canonical)
        program = await self._createRecordedProgram(canonical, None, None, '4', None)
        program.series_id = None
        program.series_title = None
        program.title = f'{source_title} #4'
        await program.save(update_fields=['series_id', 'series_title', 'title'])

        self.assertTrue(await SeriesIndexer.linkRecordedProgram(program))
        await program.refresh_from_db()
        self.assertEqual(program.series_id, canonical.id)
        self.assertEqual(await Series.all().count(), 1)


class SeriesMergeMigrationTest(unittest.TestCase):
    """Migration 17 が既存の重複 DB を一意制約より前に収束させることを検証する。"""

    def test_existing_duplicate_subjects_converge_before_unique_index(self) -> None:
        """主 Series、同チャンネル放送期間、録画を失わずに移行する。"""

        connection = sqlite3.connect(':memory:')
        connection.executescript("""
            PRAGMA foreign_keys = ON;
            CREATE TABLE series (
                id INTEGER PRIMARY KEY,
                normalized_title TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                bangumi_subject_id INTEGER
            );
            CREATE TABLE series_broadcast_periods (
                id INTEGER PRIMARY KEY,
                series_id INTEGER NOT NULL REFERENCES series(id) ON DELETE CASCADE,
                channel_id TEXT NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL
            );
            CREATE UNIQUE INDEX series_broadcast_period_unique
            ON series_broadcast_periods(series_id, channel_id);
            CREATE TABLE recorded_programs (
                id INTEGER PRIMARY KEY,
                series_id INTEGER REFERENCES series(id) ON DELETE CASCADE,
                series_broadcast_period_id INTEGER REFERENCES series_broadcast_periods(id) ON DELETE CASCADE,
                series_title TEXT
            );
            INSERT INTO series VALUES (1, 'work', '作品', 777), (2, 'work-subtitle', '作品副題', 777);
            INSERT INTO series_broadcast_periods VALUES
                (10, 1, 'bs-a', '2026-08-10', '2026-08-10'),
                (20, 2, 'bs-a', '2026-08-01', '2026-08-20');
            INSERT INTO recorded_programs VALUES (100, 1, 10, '作品'), (200, 2, 20, '作品副題');
        """)
        migration = importlib.import_module('app.migrations.models.17_20260831120000_update')
        migration_sql = asyncio.run(migration.upgrade(cast(BaseDBAsyncClient, object())))
        connection.executescript(migration_sql)

        self.assertEqual(connection.execute('SELECT COUNT(*) FROM series').fetchone(), (1,))
        self.assertEqual(connection.execute('SELECT COUNT(*) FROM recorded_programs').fetchone(), (2,))
        self.assertEqual(
            connection.execute('SELECT series_id, series_broadcast_period_id, series_title FROM recorded_programs').fetchall(),
            [(1, 10, '作品'), (1, 10, '作品')],
        )
        self.assertEqual(
            connection.execute('SELECT start_date, end_date FROM series_broadcast_periods').fetchone(),
            ('2026-08-01', '2026-08-20'),
        )
        self.assertEqual(
            connection.execute('SELECT normalized_title, series_id FROM series_aliases ORDER BY normalized_title').fetchall(),
            [('work', 1), ('work-subtitle', 1)],
        )
        index_sql = connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'index' AND name = 'series_bangumi_subject_id'",
        ).fetchone()
        self.assertIsNotNone(index_sql)
        assert index_sql is not None
        self.assertIn('WHERE "bangumi_subject_id" IS NOT NULL', index_sql[0])
        with self.assertRaises(sqlite3.IntegrityError):
            connection.execute("INSERT INTO series VALUES (3, 'duplicate', '重複', 777)")
        connection.close()


if __name__ == '__main__':
    unittest.main()
