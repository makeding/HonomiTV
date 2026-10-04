import json
import unittest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from app.constants import JST
from app.routers.SeriesRouter import (
    ON_AIR_SERIES_GENRES,
    ExtractOfficialWebsiteURL,
    GetBroadcastSeasonID,
    GetOnAirFinalExpiry,
    GetSeriesSummaries,
    OnAirSeriesListAPI,
    SeriesListPositionAPI,
)


class SeriesRouterTest(unittest.TestCase):
    """Series 一覧・詳細へ公開する外部リンクの抽出を検証する。"""

    def test_official_website_is_extracted_from_epg_detail(self) -> None:
        """公式欄の SNS より後ろにある作品公式サイトを選ぶ。"""

        self.assertEqual(
            ExtractOfficialWebsiteURL([
                '【公式X】https://x.com/example\n【公式サイト】https://example-anime.com/',
            ]),
            'https://example-anime.com/',
        )

    def test_streaming_and_social_links_are_not_official_website(self) -> None:
        """SNS と見逃し配信 URL だけの項目は公式サイトとして公開しない。"""

        self.assertIsNone(ExtractOfficialWebsiteURL([
            'https://tver.jp/series/example\nhttps://www.youtube.com/@example\nhttps://x.com/example',
        ]))

    def test_broadcaster_program_page_is_not_official_website(self) -> None:
        """放送局の番組ページは作品公式サイトとして公開しない。"""

        self.assertIsNone(ExtractOfficialWebsiteURL([
            '番組ホームページ https://www.bs4.jp/magilumiere2/',
        ]))

    def test_on_air_genres_are_limited_to_episode_based_programs(self) -> None:
        """On Air には定期放送を追うアニメ・ドラマ・バラエティ・音楽だけを掲載する。"""

        self.assertEqual(ON_AIR_SERIES_GENRES, {'アニメ・特撮', 'ドラマ', 'バラエティ', '音楽'})
        self.assertNotIn('ドキュメンタリー・教養', ON_AIR_SERIES_GENRES)

    def test_season_early_premiere_boundary(self) -> None:
        """初回だけを季度開始前 7 日から次季度へ含め、旧番の最終回は残す。"""

        for year, month in [(2026, 1), (2026, 4), (2026, 7), (2026, 10)]:
            boundary = datetime(year, month, 1, tzinfo=JST)
            self.assertEqual(GetBroadcastSeasonID(boundary - timedelta(days=7), '1'), f'{year}-{month:02d}')
            self.assertNotEqual(GetBroadcastSeasonID(boundary - timedelta(days=7, seconds=1), '1'), f'{year}-{month:02d}')
            self.assertNotEqual(GetBroadcastSeasonID(boundary - timedelta(days=1), '12'), f'{year}-{month:02d}')

    def test_on_air_final_expiry_uses_seven_days_or_next_month(self) -> None:
        """最終回は 7 日後と翌月初日のうち早い時刻で掲載を終える。"""

        self.assertEqual(
            GetOnAirFinalExpiry(datetime(2026, 6, 20, 23, 30, tzinfo=JST)),
            datetime(2026, 6, 27, 23, 30, tzinfo=JST),
        )
        self.assertEqual(
            GetOnAirFinalExpiry(datetime(2026, 6, 29, 23, 30, tzinfo=JST)),
            datetime(2026, 7, 1, 0, 0, tzinfo=JST),
        )


class SeriesRouterAsyncTest(unittest.IsolatedAsyncioTestCase):
    """Series の深いリンクを現在の一覧位置へ解決する処理を検証する。"""

    async def test_series_list_position_uses_current_search_and_descending_order(self) -> None:
        """検索中の 51 件目は、同じ降順条件の 2 ページ目として返す。"""

        connection = AsyncMock()
        connection.execute_query.return_value = (1, [{'row_number': 51}])
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await SeriesListPositionAPI(123, query='作品', order='desc')

        self.assertEqual(result.page, 2)
        sql = connection.execute_query.await_args.args[0]
        self.assertIn('ORDER BY MAX(rv.file_created_at) DESC, s.id DESC', sql)
        self.assertIn('LOWER(s.title) LIKE LOWER(?)', sql)
        self.assertEqual(connection.execute_query.await_args.args[1], ['%作品%', '%作品%', 123])

    async def test_series_list_position_uses_ascending_order(self) -> None:
        """古い順の深いリンクでも一覧と同じ昇順を用いる。"""

        connection = AsyncMock()
        connection.execute_query.return_value = (1, [{'row_number': 1}])
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await SeriesListPositionAPI(456, order='asc')

        self.assertEqual(result.page, 1)
        sql = connection.execute_query.await_args.args[0]
        self.assertIn('ORDER BY MAX(rv.file_created_at) ASC, s.id ASC', sql)

    async def test_series_summary_uses_only_generated_thumbnails(self) -> None:
        """一覧の重ねサムネイル候補は、サムネイル情報が生成済みの録画だけに絞る。"""

        now = datetime(2026, 8, 20, 12, tzinfo=JST)
        row = {
            'id': 1,
            'title': '作品',
            'description': '説明',
            'genres': '[]',
            'bangumi_subject_id': None,
            'bangumi_subject_name': None,
            'bangumi_subject_name_cn': None,
            'bangumi_subject_summary': None,
            'bangumi_subject_image_url': None,
            'thumbnail_recorded_program_ids': '[102, 101]',
            'channel_ids': '[]',
            'official_website_sources': '[]',
            'recorded_programs_count': 3,
            'latest_video_file_created_at': now.isoformat(),
            'created_at': now.isoformat(),
            'updated_at': now.isoformat(),
        }
        connection = AsyncMock()
        connection.execute_query.side_effect = [(1, [row]), (1, [{'count': 1}])]
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await GetSeriesSummaries()

        self.assertEqual(next((season.series_list for season in result.seasons if season.is_current), [])[0].thumbnail_recorded_program_ids, [102, 101])
        sql = connection.execute_query.await_args_list[0].args[0]
        self.assertIn('rv_thumbnail.thumbnail_info IS NOT NULL', sql)
        # 録画件数はサムネイルの有無にかかわらず全件を数える。
        self.assertEqual(next((season.series_list for season in result.seasons if season.is_current), [])[0].recorded_programs_count, 3)

    def setUp(self) -> None:
        """相対日時の試験が季度境界で別の季度へ移らないよう、現在を固定する。"""

        clock = patch('app.routers.SeriesRouter.datetime', wraps=datetime)
        mocked_clock = clock.start()
        mocked_clock.now.return_value = datetime(2026, 8, 20, 12, tzinfo=JST)
        self.addCleanup(clock.stop)

    async def test_on_air_accepts_first_episode_with_next_epg_and_weekly_variety(self) -> None:
        """初回だけ録画済みのアニメと、履歴で週次と分かるバラエティを掲載する。"""

        now = datetime(2026, 8, 20, 12, tzinfo=JST)
        anime_genres = json.dumps([{'major': 'アニメ・特撮', 'middle': '国内アニメ'}], ensure_ascii=False)
        variety_genres = json.dumps([{'major': 'バラエティ', 'middle': 'その他'}], ensure_ascii=False)
        documentary_genres = json.dumps([{'major': 'ドキュメンタリー・教養', 'middle': '歴史・紀行'}], ensure_ascii=False)
        recorded_rows = [
            {
                'series_id': 1, 'series_title': '新番組', 'genres': anime_genres,
                'program_title': '新番組 #1', 'id': 101, 'channel_id': 'gr011',
                'start_time': (now - timedelta(days=1)).isoformat(), 'end_time': now.isoformat(), 'episode_number': '1',
                'is_partially_recorded': True, 'has_thumbnail': True,
            },
            {
                'series_id': 1, 'series_title': '新番組', 'genres': anime_genres,
                'program_title': '新番組 #1 [再]', 'id': 102, 'channel_id': 'gr011',
                'start_time': now.isoformat(), 'end_time': (now + timedelta(minutes=30)).isoformat(), 'episode_number': '1',
                'is_partially_recorded': False, 'has_thumbnail': False,
            },
            {
                'series_id': 2, 'series_title': '8K紀行', 'genres': documentary_genres,
                'program_title': '8K紀行 第1回', 'id': 201, 'channel_id': 'bs811',
                'start_time': (now - timedelta(days=1)).isoformat(), 'end_time': now.isoformat(), 'episode_number': '1',
                'is_partially_recorded': False, 'has_thumbnail': False,
            },
            {
                'series_id': 3, 'series_title': '週刊バラエティ', 'genres': variety_genres,
                'program_title': '週刊バラエティ #2', 'id': 302, 'channel_id': 'gr041',
                'start_time': (now - timedelta(days=1)).isoformat(), 'end_time': now.isoformat(), 'episode_number': '2',
                'is_partially_recorded': True, 'has_thumbnail': False,
            },
            {
                'series_id': 3, 'series_title': '週刊バラエティ', 'genres': variety_genres,
                'program_title': '週刊バラエティ #2', 'id': 303, 'channel_id': 'gr051',
                'start_time': (now - timedelta(days=1)).isoformat(), 'end_time': now.isoformat(), 'episode_number': '2',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 3, 'series_title': '週刊バラエティ', 'genres': variety_genres,
                'program_title': '週刊バラエティ #1', 'id': 301, 'channel_id': 'gr041',
                'start_time': (now - timedelta(days=8)).isoformat(), 'end_time': (now - timedelta(days=8) + timedelta(minutes=30)).isoformat(), 'episode_number': '1',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
        ]
        future_rows = [{
            'title': '新番組 #2', 'description': '', 'genres': anime_genres, 'channel_id': 'gr011',
            'start_time': (now + timedelta(days=6)).isoformat(),
        }]
        connection = AsyncMock()
        connection.execute_query.side_effect = [(len(recorded_rows), recorded_rows), (1, future_rows)]
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await OnAirSeriesListAPI()

        self.assertEqual({series.id for series in next((season.series_list for season in result.seasons if season.is_current), [])}, {1, 3})
        anime = next(series for series in next((season.series_list for season in result.seasons if season.is_current), []) if series.id == 1)
        first_broadcast = now - timedelta(days=1)
        self.assertEqual(anime.weekday, first_broadcast.weekday())
        self.assertEqual(anime.broadcast_time, f'{first_broadcast.hour:02d}:{(first_broadcast.minute // 5) * 5:02d}')
        # 完全録画の再放送があれば、同じ話数の部分録画は警告対象にしない。
        self.assertEqual(anime.partially_recorded_episodes_count, 0)
        self.assertEqual(anime.thumbnail_recorded_program_ids, [101])
        weekly_variety = next(series for series in next((season.series_list for season in result.seasons if season.is_current), []) if series.id == 3)
        self.assertEqual(weekly_variety.partially_recorded_episodes_count, 0)
        self.assertEqual(weekly_variety.thumbnail_recorded_program_ids, [303, 301])

    async def test_on_air_uses_current_cycle_final_expiry_and_resumes_for_new_cycle(self) -> None:
        """別局の最終回再録画で期限を延ばさず、現シーズンの完結と新シーズンを分ける。"""

        now = datetime(2026, 8, 20, 12, tzinfo=JST)
        anime_genres = json.dumps([{'major': 'アニメ・特撮', 'middle': '国内アニメ'}], ensure_ascii=False)
        recorded_rows = [
            {
                'series_id': 1, 'series_title': '完結作品', 'genres': anime_genres,
                'program_title': '完結作品 #12 [完]', 'id': 101, 'channel_id': 'gr011',
                'start_time': (now - timedelta(days=8, hours=1)).isoformat(),
                'end_time': (now - timedelta(days=8)).isoformat(), 'episode_number': '12',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 1, 'series_title': '完結作品', 'genres': anime_genres,
                'program_title': '完結作品 #12', 'id': 102, 'channel_id': 'bs211',
                'start_time': (now - timedelta(days=2, hours=1)).isoformat(),
                'end_time': (now - timedelta(days=2)).isoformat(), 'episode_number': '12',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': '続編作品', 'genres': anime_genres,
                'program_title': '続編作品 #12 [終]', 'id': 201, 'channel_id': 'gr021',
                'start_time': (now - timedelta(days=40, hours=1)).isoformat(),
                'end_time': (now - timedelta(days=40)).isoformat(), 'episode_number': '12',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': '続編作品', 'genres': anime_genres,
                'program_title': '続編作品 #1', 'id': 202, 'channel_id': 'gr021',
                'start_time': (now - timedelta(days=8)).isoformat(),
                'end_time': (now - timedelta(days=8) + timedelta(minutes=30)).isoformat(), 'episode_number': '1',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': '続編作品', 'genres': anime_genres,
                'program_title': '続編作品 #6 ［終］', 'id': 203, 'channel_id': 'gr021',
                'start_time': (now - timedelta(days=8, hours=1)).isoformat(),
                'end_time': (now - timedelta(days=8)).isoformat(), 'episode_number': '6',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 3, 'series_title': '新シーズン作品', 'genres': anime_genres,
                'program_title': '新シーズン作品 #12 [完]', 'id': 301, 'channel_id': 'gr031',
                'start_time': (now - timedelta(days=40, hours=1)).isoformat(),
                'end_time': (now - timedelta(days=40)).isoformat(), 'episode_number': '12',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 3, 'series_title': '新シーズン作品', 'genres': anime_genres,
                'program_title': '新シーズン作品 #1', 'id': 302, 'channel_id': 'gr031',
                'start_time': (now - timedelta(days=8)).isoformat(),
                'end_time': (now - timedelta(days=8) + timedelta(minutes=30)).isoformat(), 'episode_number': '1',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 3, 'series_title': '新シーズン作品', 'genres': anime_genres,
                'program_title': '新シーズン作品 #2', 'id': 303, 'channel_id': 'gr031',
                'start_time': (now - timedelta(days=1)).isoformat(),
                'end_time': (now - timedelta(days=1) + timedelta(minutes=30)).isoformat(), 'episode_number': '2',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 4, 'series_title': '通算続編作品', 'genres': anime_genres,
                'program_title': '通算続編作品 #12 [完]', 'id': 401, 'channel_id': 'gr041',
                'start_time': (now - timedelta(days=40, hours=1)).isoformat(),
                'end_time': (now - timedelta(days=40)).isoformat(), 'episode_number': '12',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 4, 'series_title': '通算続編作品', 'genres': anime_genres,
                'program_title': '通算続編作品 #13 [新]', 'id': 402, 'channel_id': 'gr041',
                'start_time': (now - timedelta(days=1)).isoformat(),
                'end_time': (now - timedelta(days=1) + timedelta(minutes=30)).isoformat(), 'episode_number': '13',
                'is_partially_recorded': False, 'has_thumbnail': True,
            },
        ]
        connection = AsyncMock()
        connection.execute_query.side_effect = [(len(recorded_rows), recorded_rows), (0, [])]
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await OnAirSeriesListAPI()

        self.assertEqual([series.id for series in next((season.series_list for season in result.seasons if season.is_current), [])], [3, 4])

    async def test_on_air_uses_earliest_broadcast_of_latest_episode(self) -> None:
        """過去話を多く録画した局ではなく、最新話を最初に放送した局の枠を表示する。"""

        now = datetime(2026, 8, 20, 12, tzinfo=JST)
        anime_genres = json.dumps([{'major': 'アニメ・特撮', 'middle': '国内アニメ'}], ensure_ascii=False)
        bs11_first = (now - timedelta(days=15)).replace(hour=23, minute=0, second=0, microsecond=0)
        bs11_second = (now - timedelta(days=8)).replace(hour=23, minute=0, second=0, microsecond=0)
        mx1_latest = (now - timedelta(days=1)).replace(hour=0, minute=30, second=0, microsecond=0)
        mx1_first = (now - timedelta(days=15)).replace(hour=0, minute=30, second=0, microsecond=0)
        mx1_second = (now - timedelta(days=8)).replace(hour=0, minute=30, second=0, microsecond=0)
        bs11_latest = (now - timedelta(days=1)).replace(hour=23, minute=0, second=0, microsecond=0)
        recorded_rows = [
            {
                'series_id': 1, 'series_title': 'MX1最新話', 'genres': anime_genres,
                'program_title': 'MX1最新話 #1', 'id': 101, 'channel_id': 'bs11',
                'start_time': bs11_first.isoformat(), 'end_time': (bs11_first + timedelta(minutes=30)).isoformat(),
                'episode_number': '1', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 1, 'series_title': 'MX1最新話', 'genres': anime_genres,
                'program_title': 'MX1最新話 #2', 'id': 102, 'channel_id': 'bs11',
                'start_time': bs11_second.isoformat(), 'end_time': (bs11_second + timedelta(minutes=30)).isoformat(),
                'episode_number': '2', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 1, 'series_title': 'MX1最新話', 'genres': anime_genres,
                'program_title': 'MX1最新話 #3', 'id': 103, 'channel_id': 'mx1',
                'start_time': mx1_latest.isoformat(), 'end_time': (mx1_latest + timedelta(minutes=30)).isoformat(),
                'episode_number': '3', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 1, 'series_title': 'MX1最新話', 'genres': anime_genres,
                'program_title': 'MX1最新話 #3', 'id': 104, 'channel_id': 'bs11',
                'start_time': bs11_latest.isoformat(), 'end_time': (bs11_latest + timedelta(minutes=30)).isoformat(),
                'episode_number': '3', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': 'BS11最新話', 'genres': anime_genres,
                'program_title': 'BS11最新話 #1', 'id': 201, 'channel_id': 'mx1',
                'start_time': mx1_first.isoformat(), 'end_time': (mx1_first + timedelta(minutes=30)).isoformat(),
                'episode_number': '1', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': 'BS11最新話', 'genres': anime_genres,
                'program_title': 'BS11最新話 #2', 'id': 202, 'channel_id': 'mx1',
                'start_time': mx1_second.isoformat(), 'end_time': (mx1_second + timedelta(minutes=30)).isoformat(),
                'episode_number': '2', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': 'BS11最新話', 'genres': anime_genres,
                'program_title': 'BS11最新話 #3', 'id': 203, 'channel_id': 'bs11',
                'start_time': bs11_latest.isoformat(), 'end_time': (bs11_latest + timedelta(minutes=30)).isoformat(),
                'episode_number': '3', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
        ]
        connection = AsyncMock()
        connection.execute_query.side_effect = [(len(recorded_rows), recorded_rows), (0, [])]
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await OnAirSeriesListAPI()

        mx1_series = next(series for series in next((season.series_list for season in result.seasons if season.is_current), []) if series.id == 1)
        self.assertEqual(mx1_series.weekday, mx1_latest.weekday())
        self.assertEqual(mx1_series.broadcast_time, '00:30')
        bs11_series = next(series for series in next((season.series_list for season in result.seasons if season.is_current), []) if series.id == 2)
        self.assertEqual(bs11_series.weekday, bs11_latest.weekday())
        self.assertEqual(bs11_series.broadcast_time, '23:00')

    async def test_on_air_first_episode_uses_epg_only_for_listing_eligibility(self) -> None:
        """初回だけの作品は未来 EPG で掲載可否を確認し、録画した初回枠を表示する。"""

        now = datetime(2026, 8, 20, 12, tzinfo=JST)
        anime_genres = json.dumps([{'major': 'アニメ・特撮', 'middle': '国内アニメ'}], ensure_ascii=False)
        local_sunday = now - timedelta(days=(now.weekday() + 1) % 7 + 7)
        local_sunday = local_sunday.replace(hour=2, minute=50, second=0, microsecond=0)
        fallback_sunday = local_sunday + timedelta(days=7)
        recorded_rows = [
            {
                'series_id': 1, 'series_title': '同局次回あり', 'genres': anime_genres,
                'program_title': '同局次回あり #1', 'id': 101, 'channel_id': 'gr011',
                'start_time': local_sunday.isoformat(), 'end_time': (local_sunday + timedelta(minutes=30)).isoformat(),
                'episode_number': '1', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
            {
                'series_id': 2, 'series_title': '他局のみ', 'genres': anime_genres,
                'program_title': '他局のみ #1', 'id': 201, 'channel_id': 'gr021',
                'start_time': fallback_sunday.isoformat(), 'end_time': (fallback_sunday + timedelta(minutes=30)).isoformat(),
                'episode_number': '1', 'is_partially_recorded': False, 'has_thumbnail': True,
            },
        ]
        local_next_sunday = now + timedelta(days=(6 - now.weekday()) % 7 or 7)
        local_next_sunday = local_next_sunday.replace(hour=1, minute=30, second=0, microsecond=0)
        other_monday = now + timedelta(days=(0 - now.weekday()) % 7 or 7)
        other_monday = other_monday.replace(hour=1, minute=0, second=0, microsecond=0)
        future_rows = [
            {
                'title': '同局次回あり #2', 'description': '', 'genres': anime_genres, 'channel_id': 'gr011',
                'start_time': local_next_sunday.isoformat(),
            },
            {
                'title': '同局次回あり #2', 'description': '', 'genres': anime_genres, 'channel_id': 'bs211',
                'start_time': other_monday.isoformat(),
            },
            {
                'title': '他局のみ #2', 'description': '', 'genres': anime_genres, 'channel_id': 'bs221',
                'start_time': other_monday.isoformat(),
            },
        ]
        connection = AsyncMock()
        connection.execute_query.side_effect = [(len(recorded_rows), recorded_rows), (len(future_rows), future_rows)]
        with patch('app.routers.SeriesRouter.connections.get', return_value=connection):
            result = await OnAirSeriesListAPI()

        next_local = next(series for series in next((season.series_list for season in result.seasons if season.is_current), []) if series.id == 1)
        self.assertEqual(next_local.weekday, 6)
        self.assertEqual(next_local.broadcast_time, '02:50')
        fallback_local = next(series for series in next((season.series_list for season in result.seasons if season.is_current), []) if series.id == 2)
        self.assertEqual(fallback_local.weekday, 6)
        self.assertEqual(fallback_local.broadcast_time, '02:50')


if __name__ == '__main__':
    unittest.main()
