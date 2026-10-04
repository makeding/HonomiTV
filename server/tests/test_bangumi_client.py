import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app import schemas
from app.metadata.SeriesIndexer import ParseSeriesTitle
from app.utils.BangumiClient import BangumiClient


class BangumiClientTest(unittest.TestCase):
    """Bangumi コレクション候補の一括照合と視聴完了判定を検証する。"""

    def test_bs11_dotted_chapter_resolves_space_mercenary(self) -> None:
        """実際の BS11 短縮名と Chapter.1 が Bangumi の作品・第 1 話へ照合できる。"""

        parsed = ParseSeriesTitle(
            '[新]目覚めたら最強装備と宇宙船持ちだったので一戸建て目指して傭兵として自由に生きた',
            [schemas.Genre(major='アニメ・特撮', middle='国内アニメ')],
            'Chapter.1「はじめてのスペースコロニー」',
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        subject = {
            'id': 536270,
            'type': 2,
            'name': '目覚めたら最強装備と宇宙船持ちだったので 、一戸建て目指して傭兵として自由に生きたい',
            'name_cn': '一觉醒来就有了最强装备跟太空船 决定以自家独栋建筑为目标当佣兵自由过活',
        }
        self.assertEqual(BangumiClient.findSubject(parsed.display_title, [subject]), subject)
        episode_number = BangumiClient.parseEpisodeNumber(parsed.episode_number)
        self.assertEqual(episode_number, 1)
        assert episode_number is not None
        episode = {'id': 1717774, 'type': 0, 'sort': 1, 'ep': 1}
        self.assertEqual(BangumiClient._findEpisode([episode], episode_number), episode)

    def test_episode_sort_is_one_based_and_wins_over_local_number(self) -> None:
        """第 13 話を第 12 話へずらさず、分割クールの通算番号を優先する。"""

        first_season = [{'id': 1559556, 'ep': 12, 'sort': 12, 'type': 0}]
        self.assertIsNone(BangumiClient._findEpisode(first_season, 13))
        second_season = [
            {'id': 1746074, 'ep': 1, 'sort': 13, 'type': 0},
            {'id': 2, 'ep': 13, 'sort': 25, 'type': 0},
        ]
        self.assertEqual(BangumiClient._findEpisode(second_season, 13), second_season[0])
        self.assertIsNone(BangumiClient._findEpisode([*first_season, {**first_season[0], 'id': 99}], 12))

    def test_only_single_positive_integer_episode_is_accepted(self) -> None:
        """単一の正整数以外の話数は自動同期しない。"""

        self.assertEqual(BangumiClient.parseEpisodeNumber('12'), 12)
        self.assertIsNone(BangumiClient.parseEpisodeNumber('0'))
        self.assertIsNone(BangumiClient.parseEpisodeNumber('1・2'))
        self.assertIsNone(BangumiClient.parseEpisodeNumber('4.5'))
        self.assertIsNone(BangumiClient.parseEpisodeNumber(None))


    def test_long_title_matches_collection_subject_without_search(self) -> None:
        """長い EPG 作品名でもコレクション一覧内の同名作品へ完全一致できる。"""

        expected_subject: dict[str, Any] = {
            'id': 590786,
            'type': 2,
            'name': 'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた。',
            'name_cn': '『你们先走我断后』，于是10年后我成为了传说',
        }
        unrelated_subject: dict[str, Any] = {
            'id': 8365,
            'type': 2,
            'name': 'ここはグリーン・ウッド',
            'name_cn': '绿林寮',
        }

        matched_subject = BangumiClient.findSubject(
            'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた。',
            [unrelated_subject, expected_subject],
        )

        self.assertIsNotNone(matched_subject)
        assert matched_subject is not None
        self.assertEqual(matched_subject['id'], 590786)


    def test_trailing_period_difference_is_ignored(self) -> None:
        """EPG だけが長い作品名の末尾句点を省略しても同じコレクション作品として扱う。"""

        subject = {
            'id': 590786,
            'type': 2,
            'name': 'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた。',
            'name_cn': '',
        }
        matched_subject = BangumiClient.findSubject(
            'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた',
            [subject],
        )

        self.assertIsNotNone(matched_subject)


    def test_ambiguous_collection_titles_are_not_matched(self) -> None:
        """同点のコレクション作品が複数ある場合は誤って自動確定しない。"""

        subjects = [
            {'id': 1, 'type': 2, 'name': '同名作品', 'name_cn': ''},
            {'id': 2, 'type': 2, 'name': '同名作品', 'name_cn': ''},
        ]

        self.assertIsNone(BangumiClient.findSubject('同名作品', subjects))


    def test_japanese_main_title_matches_space_delimited_subtitle(self) -> None:
        """短い EPG 主題と空白区切りの正式作品名を、リモート検索なしで照合する。"""

        subject = {
            'id': 602733,
            'type': 2,
            'name': '才女のお世話 高嶺の花だらけな名門校で、学院一のお嬢様（生活能力皆無）を陰ながらお世話することになりました',
            'name_cn': '',
        }
        for series_title in ('才女のお世話', subject['name']):
            with self.subTest(series_title=series_title):
                self.assertEqual(BangumiClient.findSubject(series_title, [subject]), subject)


    def test_space_delimited_subtitle_matches_in_reverse(self) -> None:
        """正式名の EPG と短縮名の Bangumi 作品も同じ弱い候補として比較する。"""

        subject = {'id': 602733, 'type': 2, 'name': '才女のお世話', 'name_cn': ''}
        self.assertEqual(BangumiClient.findSubject('才女のお世話 高嶺の花だらけな名門校で', [subject]), subject)


    def test_remastered_ultraman_matches_original_subject(self) -> None:
        """リマスターの品質表記だけを比較から外し、原作の実写作品へ照合する。"""

        subject = {'id': 38652, 'type': 6, 'name': '帰ってきたウルトラマン', 'name_cn': '归来的奥特曼'}
        for suffix in ('4Kリマスター版', '４Ｋリマスター版', 'HDリマスター版', 'デジタルリマスター版'):
            with self.subTest(suffix=suffix):
                self.assertEqual(BangumiClient.findSubject(f'帰ってきたウルトラマン {suffix}', [subject]), subject)


    def test_main_title_does_not_match_sequel_or_movie(self) -> None:
        """数字・期・劇場版を空白で追加した別作品を主題へ誤照合しない。"""

        for suffix in ('2', '第2期', '第２期', '第2部', 'II', 'Season 2', '2nd Season', 'Final Season', '劇場版', '映画'):
            with self.subTest(suffix=suffix):
                subject = {'id': 1, 'type': 2, 'name': f'才女のお世話 {suffix}', 'name_cn': ''}
                self.assertIsNone(BangumiClient.findSubject('才女のお世話', [subject]))
                subject['name'] = '才女のお世話'
                self.assertIsNone(BangumiClient.findSubject(f'才女のお世話 {suffix}', [subject]))


    def test_arbitrary_prefix_and_english_word_boundary_do_not_match(self) -> None:
        """境界なしの接頭辞・短すぎる主題・英語の単語間空白を副題扱いしない。"""

        for short_title, long_title in (
            ('才女のお世話', '才女のお世話係'),
            ('作品', '作品 完全な別作品'),
            ('One', 'One Piece'),
            ('One Piece', 'One Piece Film Red'),
        ):
            with self.subTest(short_title=short_title, long_title=long_title):
                subject = {'id': 1, 'type': 2, 'name': long_title, 'name_cn': ''}
                self.assertIsNone(BangumiClient.findSubject(short_title, [subject]))


    def test_long_main_title_cannot_fuzzy_match_a_sequel(self) -> None:
        """長い主題の類似率が高くても、期・劇場版などの違いを曖昧一致で覆さない。"""

        main_title = 'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた'
        for suffix in ('第2期', 'Season 2', 'Final Season', 'II', '劇場版', 'OVA', ': 第2期'):
            with self.subTest(suffix=suffix):
                subject = {'id': 1, 'type': 2, 'name': f'{main_title} {suffix}', 'name_cn': ''}
                self.assertIsNone(BangumiClient.findSubject(main_title, [subject]))
                subject['name'] = main_title
                self.assertIsNone(BangumiClient.findSubject(f'{main_title} {suffix}', [subject]))


    def test_ambiguous_subtitles_do_not_match_but_exact_title_wins(self) -> None:
        """副題省略で複数候補が残る場合は拒否し、完全一致がある場合はそれを優先する。"""

        subjects = [
            {'id': 1, 'type': 2, 'name': '才女のお世話 高嶺の花だらけな名門校で', 'name_cn': ''},
            {'id': 2, 'type': 2, 'name': '才女のお世話 別の物語', 'name_cn': ''},
        ]
        self.assertIsNone(BangumiClient.findSubject('才女のお世話', subjects))
        exact_subject = {'id': 3, 'type': 2, 'name': '才女のお世話', 'name_cn': ''}
        self.assertEqual(BangumiClient.findSubject('才女のお世話', [*subjects, exact_subject]), exact_subject)


    def test_remaster_normalization_preserves_work_identity(self) -> None:
        """品質表記を吸収しても期・劇場版を消さず、空の作品名も確定しない。"""

        for local_title, subject_title in (
            ('帰ってきたウルトラマン 劇場版 4Kリマスター版', '帰ってきたウルトラマン'),
            ('才女のお世話 第2期 4Kリマスター版', '才女のお世話'),
            ('4Kリマスター版', '4Kリマスター版'),
            ('', '才女のお世話'),
        ):
            with self.subTest(local_title=local_title):
                subject = {'id': 1, 'type': 2, 'name': subject_title, 'name_cn': ''}
                self.assertIsNone(BangumiClient.findSubject(local_title, [subject]))


    def test_playback_completion_is_decided_at_ninety_percent(self) -> None:
        """30 分番組は 27 分到達時点から完了と判定する。"""

        self.assertFalse(BangumiClient.isPlaybackCompleted(1619.9, 1800.0, None))
        self.assertTrue(BangumiClient.isPlaybackCompleted(1620.0, 1800.0, None))


    def test_playback_completion_uses_three_minutes_before_last_cm(self) -> None:
        """最後の CM が 25 分開始なら、その 3 分前の 22 分から完了と判定する。"""

        cm_sections = [
            {'start_time': 600.0, 'end_time': 720.0},
            {'start_time': 1500.0, 'end_time': 1800.0},
        ]

        self.assertFalse(BangumiClient.isPlaybackCompleted(1319.9, 1800.0, cm_sections))
        self.assertTrue(BangumiClient.isPlaybackCompleted(1320.0, 1800.0, cm_sections))


    def test_short_sponsor_gap_is_merged_between_cm_sections(self) -> None:
        """CM に挟まれた 1 分未満のスポンサー表示は最後の CM 群へまとめる。"""

        cm_sections = [
            {'start_time': 1500.0, 'end_time': 1650.0},
            {'start_time': 1660.0, 'end_time': 1790.0},
        ]

        self.assertFalse(BangumiClient.isPlaybackCompleted(1319.9, 1800.0, cm_sections))
        self.assertTrue(BangumiClient.isPlaybackCompleted(1320.0, 1800.0, cm_sections))


    def test_one_minute_gap_keeps_cm_sections_separate(self) -> None:
        """CM 間がちょうど 1 分なら別の CM 群として扱う。"""

        cm_sections = [
            {'start_time': 1500.0, 'end_time': 1530.0},
            {'start_time': 1590.0, 'end_time': 1800.0},
        ]

        self.assertFalse(BangumiClient.isPlaybackCompleted(1409.9, 1800.0, cm_sections))
        self.assertTrue(BangumiClient.isPlaybackCompleted(1410.0, 1800.0, cm_sections))


class BangumiClientAsyncTest(unittest.IsolatedAsyncioTestCase):
    """Bangumi API を呼び出す前のローカル対象判定を検証する。"""

    async def test_existing_wrong_mapping_converges_to_explicit_sequel(self) -> None:
        """主 Series を変えず、第 13 話の既存誤照合を続編の通算 13 話へ更新する。"""

        series = MagicMock()
        series.bangumi_subject_id = 509355
        series.bangumi_subject_name = '野生のラスボスが現れた！'
        recording = MagicMock()
        recording.episode_number = '13'
        recording.bangumi_subject_id = 509355
        recording.bangumi_episode_id = 1559556
        recording.save = AsyncMock()
        response = MagicMock()
        response.json.return_value = [
            {'id': 616808, 'type': 2, 'relation': '续集', 'name': '野生のラスボスが現れた！第2期'},
            {'id': 123, 'type': 2, 'relation': '续集', 'name': '無関係の作品第2期'},
        ]
        empty_response = MagicMock()
        empty_response.json.return_value = []
        client = AsyncMock()
        client.__aenter__.return_value = client
        subject_response = MagicMock()
        subject_response.json.return_value = {'type': 2}
        client.get.side_effect = [subject_response, response, empty_response]
        cache = {
            509355: [{'id': 1559556, 'ep': 12, 'sort': 12, 'type': 0}],
            616808: [{'id': 1746074, 'ep': 1, 'sort': 13, 'type': 0}],
        }
        with patch('app.utils.BangumiClient.HTTPX_CLIENT', return_value=client):
            await BangumiClient.resolveRecordedEpisode(recording, series, 'token', cache)
        self.assertEqual((recording.bangumi_subject_id, recording.bangumi_episode_id), (616808, 1746074))
        self.assertEqual(series.bangumi_subject_id, 509355)
        recording.save.assert_awaited_once()

        # 再検証しても保存結果は同じで、同期元の古い ID には戻らない。
        client.get.side_effect = [subject_response, response, empty_response]
        with patch('app.utils.BangumiClient.HTTPX_CLIENT', return_value=client):
            await BangumiClient.resolveRecordedEpisode(recording, series, 'token', cache)
        recording.save.assert_awaited_once()

        # 同一通算話数の候補が複数あれば、以前の誤照合も含めて未照合に戻す。
        response.json.return_value.append({
            'id': 616809, 'type': 2, 'relation': '续集', 'name': '野生のラスボスが現れた！第3期',
        })
        cache[616809] = [{'id': 1746099, 'ep': 1, 'sort': 13, 'type': 0}]
        client.get.side_effect = [subject_response, response, empty_response, empty_response]
        with patch('app.utils.BangumiClient.HTTPX_CLIENT', return_value=client):
            await BangumiClient.resolveRecordedEpisode(recording, series, 'token', cache)
        self.assertIsNone(recording.bangumi_episode_id)

    async def test_collection_api_is_not_called_without_eligible_series(self) -> None:
        """照合対象の Series がない環境ではコレクション一覧を取得しない。"""

        series_query: asyncio.Future[list[Any]] = asyncio.Future()
        series_query.set_result([])
        get_collection_subjects = AsyncMock()
        with (
            patch('app.utils.BangumiClient.Series.all', return_value=series_query),
            patch.object(BangumiClient, '_getCollectionSubjects', get_collection_subjects),
        ):
            matched_count = await BangumiClient.syncUserCollections(AsyncMock())

        self.assertEqual(matched_count, 0)
        get_collection_subjects.assert_not_awaited()


    async def test_collection_pages_include_anime_and_live_action_only(self) -> None:
        """アニメ・実写の視聴中・視聴済みを全ページから取得し、他の種別・コレクション状態は除外する。"""

        anime = {'id': 602733, 'type': 2, 'name': '才女のお世話'}
        live_action = {'id': 38652, 'type': 6, 'name': '帰ってきたウルトラマン'}
        first_response = MagicMock()
        first_response.json.return_value = {
            'total': 6,
            'data': [
                {'type': 3, 'subject': anime},
                {'type': 2, 'subject': {'id': 7, 'type': 1, 'name': '書籍'}},
                {'type': 1, 'subject': {'id': 8, 'type': 6, 'name': '未視聴'}},
            ],
        }
        second_response = MagicMock()
        second_response.json.return_value = {
            'total': 6,
            'data': [
                {'type': 2, 'subject': live_action},
                {'type': 3, 'subject': anime},
                {'type': 4, 'subject': {'id': 9, 'type': 6, 'name': '中断'}},
            ],
        }
        httpx_client = AsyncMock()
        httpx_client.get.side_effect = [first_response, second_response]
        httpx_client.__aenter__.return_value = httpx_client
        user = MagicMock()
        user.bangumi_user_name = 'test-user'
        user.decryptBangumiAccessToken.return_value = 'test-token'
        with patch('app.utils.BangumiClient.HTTPX_CLIENT', return_value=httpx_client):
            subjects = await BangumiClient._getCollectionSubjects(user)  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(subjects, [anime, live_action])
        self.assertEqual(httpx_client.get.await_count, 2)
        for request, offset in zip(httpx_client.get.await_args_list, (0, 3), strict=True):
            self.assertNotIn('subject_type', request.kwargs['params'])
            self.assertEqual(request.kwargs['params']['offset'], offset)
            self.assertEqual(request.kwargs['headers']['Authorization'], 'Bearer test-token')


    async def test_live_action_series_reaches_existing_merge_pipeline(self) -> None:
        """特撮・連続ドラマ等を既存の統合経路へ渡し、同名アニメは実写候補から除外する。"""

        subject = {'id': 38652, 'type': 6, 'name': '帰ってきたウルトラマン', 'name_cn': ''}
        for major, middle in (
            ('アニメ・特撮', '特撮'),
            ('ドラマ', '国内ドラマ'),
            ('ドラマ', '海外ドラマ'),
            ('ドキュメンタリー・教養', '歴史・紀行'),
            ('バラエティ', 'トークバラエティ'),
            ('音楽', '国内ロック・ポップス'),
        ):
            with self.subTest(major=major, middle=middle):
                series = MagicMock()
                series.id = 238
                series.title = '帰ってきたウルトラマン 4Kリマスター版'
                series.genres = [schemas.Genre(major=major, middle=middle)]
                series.bangumi_subject_id = None
                series_query: asyncio.Future[list[Any]] = asyncio.Future()
                series_query.set_result([series])
                existence_query = MagicMock()
                existence_query.exists = AsyncMock(return_value=True)
                recorded_query = MagicMock()
                recorded_query.all = AsyncMock(return_value=[])
                user = MagicMock()
                user.decryptBangumiAccessToken.return_value = 'test-token'
                subjects = [subject]
                if major != 'アニメ・特撮':
                    subjects.append({**subject, 'id': 77, 'type': 2})
                merge = AsyncMock(return_value=series)
                with (
                    patch('app.utils.BangumiClient.Series.all', return_value=series_query),
                    patch('app.utils.BangumiClient.Series.filter', return_value=existence_query),
                    patch.object(BangumiClient, '_getCollectionSubjects', AsyncMock(return_value=subjects)),
                    patch('app.utils.BangumiClient.SeriesMerger.mergeByBangumiSubject', merge),
                    patch('app.utils.BangumiClient.RecordedProgram.filter', return_value=recorded_query),
                ):
                    matched_count = await BangumiClient.syncUserCollections(user)

                self.assertEqual(matched_count, 1)
                merge.assert_awaited_once()
                self.assertEqual(merge.await_args.kwargs['subject_id'], 38652)
                self.assertEqual(series.title, '帰ってきたウルトラマン 4Kリマスター版')


    async def test_news_series_does_not_expand_matching_scope(self) -> None:
        """ニュースなど対象外の Series は、同名作品があっても取得・照合しない。"""

        series = MagicMock()
        series.genres = [schemas.Genre(major='ニュース・報道', middle='定時・総合')]
        series_query: asyncio.Future[list[Any]] = asyncio.Future()
        series_query.set_result([series])
        get_collection_subjects = AsyncMock()
        with (
            patch('app.utils.BangumiClient.Series.all', return_value=series_query),
            patch.object(BangumiClient, '_getCollectionSubjects', get_collection_subjects),
        ):
            matched_count = await BangumiClient.syncUserCollections(MagicMock())

        self.assertEqual(matched_count, 0)
        get_collection_subjects.assert_not_awaited()


    async def test_nsfw_episode_request_uses_bearer_token(self) -> None:
        """NSFW 作品の episode 取得にも連携済みユーザーの Bearer token を渡す。"""

        response = MagicMock()
        response.status_code = 404
        httpx_client = AsyncMock()
        httpx_client.get.return_value = response
        httpx_client.__aenter__.return_value = httpx_client
        httpx_client.__aexit__.return_value = None
        with patch('app.utils.BangumiClient.HTTPX_CLIENT', return_value=httpx_client):
            episodes = await BangumiClient._getEpisodes(295001, 'test-token')  # pyright: ignore[reportPrivateUsage]

        self.assertEqual(episodes, [])
        self.assertEqual(
            httpx_client.get.await_args.kwargs['headers']['Authorization'],
            'Bearer test-token',
        )
        response.raise_for_status.assert_not_called()


if __name__ == '__main__':
    unittest.main()
