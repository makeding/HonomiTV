import unittest

from app.metadata.SeriesIndexer import (
    IsStrictSeriesTitlePrefix,
    NormalizeSeriesTitle,
    ParseEpisodeLessSeriesTitle,
    ParseSeriesTitle,
)
from app.routers.VideosRouter import CalculateStringSimilarity
from app.schemas import Genre


ANIME_GENRES: list[Genre] = [Genre(major='アニメ・特撮', middle='国内アニメ')]
VARIETY_GENRES: list[Genre] = [Genre(major='バラエティ', middle='トークバラエティ')]
MUSIC_GENRES: list[Genre] = [Genre(major='音楽', middle='国内ロック・ポップス')]


class SeriesIndexerTest(unittest.TestCase):
    """実際の EPG 表記を使って Series の確定的なタイトル解析を検証する。"""

    def test_cross_channel_title_variants_match(self) -> None:
        """放送枠名やアニメ表記が異なっても同じ作品キーになる。"""

        title_pairs = [
            (
                '<アニメギルド>最強出涸らし皇子の暗躍帝位争い #6',
                '最強出涸らし皇子の暗躍帝位争い #6',
            ),
            (
                '追放された転生重騎士はゲーム知識で無双する#06◆スーパーアニメイズムTURBO[字][デ]',
                '[字]アニメ 追放された転生重騎士はゲーム知識で無双する Chapter 6',
            ),
            (
                'バンドリ！ ゆめ∞みた #7「こころあらたに」[字]',
                'アニメ バンドリ！ ゆめ∞みた #07 こころあらたに',
            ),
        ]

        for first_title, second_title in title_pairs:
            with self.subTest(first_title=first_title):
                first = ParseSeriesTitle(first_title, ANIME_GENRES)
                second = ParseSeriesTitle(second_title, ANIME_GENRES)
                self.assertIsNotNone(first)
                self.assertIsNotNone(second)
                assert first is not None and second is not None
                self.assertEqual(first.normalized_title, second.normalized_title)

    def test_seasons_remain_separate(self) -> None:
        """ローマ数字や期数は削除せず、別シーズンを別作品として扱う。"""

        second_season = ParseSeriesTitle('無職転生Ⅱ ～異世界行ったら本気だす～ 第21話', ANIME_GENRES)
        third_season = ParseSeriesTitle('無職転生Ⅲ ～異世界行ったら本気だす～ #01', ANIME_GENRES)
        self.assertIsNotNone(second_season)
        self.assertIsNotNone(third_season)
        assert second_season is not None and third_season is not None
        self.assertNotEqual(second_season.normalized_title, third_season.normalized_title)

    def test_generic_programs_are_not_indexed(self) -> None:
        """放送状態や汎用ニュース枠は話数らしい数字があっても Series にしない。"""

        self.assertIsNone(ParseSeriesTitle('ニュース #1', ANIME_GENRES))
        self.assertIsNone(ParseSeriesTitle('[4K](放送休止)', ANIME_GENRES))
        self.assertIsNone(ParseSeriesTitle('weather report 1', ANIME_GENRES))

    def test_episode_notations_are_extracted_without_changing_the_work(self) -> None:
        """小数話・空白入り話数・漢数字・括弧話数を同じ作品へ寄せる。"""

        cases = [
            ('奇妙なアニメ #4.5「特別編」', '4.5', '特別編'),
            ('奇妙なアニメ 第 5 話 旅立ち', '5', '旅立ち'),
            ('奇妙なアニメ 第拾壱話「帰還」', '11', '帰還'),
            ('奇妙なアニメ 第百二十三話', '123', None),
            ('奇妙なアニメ 第〇七話', '7', None),
            ('奇妙なアニメ (第6話)', '6', None),
            ('奇妙なアニメ CH 07', '7', None),
            ('奇妙なアニメ #01・#02「一挙放送」', '1・2', '一挙放送'),
            ('奇妙なアニメ #01-#12「一挙放送」', '1-12', '一挙放送'),
        ]

        expected_key = NormalizeSeriesTitle('奇妙なアニメ')
        for title, episode_number, subtitle in cases:
            with self.subTest(title=title):
                parsed = ParseSeriesTitle(title, ANIME_GENRES)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.normalized_title, expected_key)
                self.assertEqual(parsed.episode_number, episode_number)
                self.assertEqual(parsed.subtitle, subtitle)

    def test_episode_like_number_in_work_title_is_preserved(self) -> None:
        """作品名側の期数や数字を話数として消さない。"""

        parsed = ParseSeriesTitle('16bitセンセーション ANOTHER LAYER #03', ANIME_GENRES)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_title, '16bitセンセーション ANOTHER LAYER')
        self.assertEqual(parsed.episode_number, '3')

    def test_similar_episode_less_programs_are_persisted(self) -> None:
        """類似する録画がある無話数番組は、毎回の企画名を副題として分離する。"""

        cases = [
            (
                'Anison Days「番組初登場！大西亜玖璃 アーティスト像を深掘り！」',
                MUSIC_GENRES,
                ['Anison Days「MYTH & ROIDが話題の最新曲を披露！」'],
                'Anison Days',
                '番組初登場!大西亜玖璃 アーティスト像を深掘り!',
            ),
            (
                'アニゲー☆イレブン！「ソードアート・オンライン」家庭用ゲーム最新作先行プレイ',
                VARIETY_GENRES,
                ['アニゲー☆イレブン！「大空直美登場！個性あふれるウォーキングを披露！」'],
                'アニゲー☆イレブン!',
                '「ソードアート・オンライン」家庭用ゲーム最新作先行プレイ',
            ),
        ]
        for title, genres, similar_titles, expected_series_title, expected_subtitle in cases:
            with self.subTest(title=title):
                parsed = ParseEpisodeLessSeriesTitle(title, genres, similar_titles)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.display_title, expected_series_title)
                self.assertIsNone(parsed.episode_number)
                self.assertEqual(parsed.subtitle, expected_subtitle)

    def test_unknown_program_without_explicit_episode_is_not_persisted(self) -> None:
        """同名の情報番組や映画を、放送時刻だけで長期 Series にしない。"""

        self.assertIsNone(ParseSeriesTitle('アニナビ☆イレブン！ 前編', ANIME_GENRES))
        self.assertIsNone(ParseSeriesTitle('劇場版 とても長い作品名', ANIME_GENRES))
        self.assertIsNone(ParseEpisodeLessSeriesTitle(
            '今夜のスペシャル「有名ゲスト登場」',
            VARIETY_GENRES,
            [],
        ))
        self.assertIsNone(ParseEpisodeLessSeriesTitle(
            '今夜のスペシャル「有名ゲスト登場」',
            ANIME_GENRES,
            ['今夜のスペシャル「別の企画」'],
        ))

    def test_old_character_set_false_positive_is_reduced(self) -> None:
        """同じ文字を多く含むだけで順序の異なるタイトルへ高得点を与えない。"""

        first = 'アイウエオカキクケコ'
        reordered = 'コケクキカオエウイア'
        self.assertLess(CalculateStringSimilarity(first, reordered), 0.7)

    def test_unicode_and_spacing_variants_match(self) -> None:
        """全角英数字・感嘆符・日本語間の空白差だけを吸収する。"""

        self.assertEqual(
            NormalizeSeriesTitle('ＬＶ９９９ の村人！'),
            NormalizeSeriesTitle('LV999の村人!'),
        )

    def test_lv999_quoted_episode_matches_bs_title(self) -> None:
        """作品名の LV999 は保持し、引用符内の Lv2 だけを話数として解釈する。"""

        mx = ParseSeriesTitle('LV999の村人 「Lv2 最高に馬鹿だから」[字]', ANIME_GENRES)
        bs = ParseSeriesTitle('<アニメギルド>LV999 の村人 #2', ANIME_GENRES)
        self.assertIsNotNone(mx)
        self.assertIsNotNone(bs)
        assert mx is not None and bs is not None
        self.assertEqual(mx.normalized_title, bs.normalized_title)
        self.assertEqual(mx.episode_number, '2')
        self.assertEqual(mx.subtitle, '最高に馬鹿だから')
        self.assertEqual(mx.display_title, 'LV999の村人')

    def test_lv_in_work_title_is_not_an_episode_without_quoted_prefix(self) -> None:
        """作品名本体に含まれる LV999 だけでは Series を自動生成しない。"""

        self.assertIsNone(ParseSeriesTitle('LV999の村人 総集編', ANIME_GENRES))

    def test_parenthesized_episode_does_not_split_the_series(self) -> None:
        """括弧付き話数の開き括弧を作品名に残さない。"""

        parenthesized = ParseSeriesTitle(
            'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた。(第2話)',
            ANIME_GENRES,
        )
        ordinary = ParseSeriesTitle(
            'ここは俺に任せて先に行けと言ってから10年がたったら伝説になっていた。 第6話',
            ANIME_GENRES,
        )
        self.assertIsNotNone(parenthesized)
        self.assertIsNotNone(ordinary)
        assert parenthesized is not None and ordinary is not None
        self.assertEqual(parenthesized.normalized_title, ordinary.normalized_title)

    def test_real_broadcast_slot_variants_match(self) -> None:
        """引用符式と無括弧式の放送枠名だけを作品名から除外する。"""

        cases = [
            ('日5「ウィッチウォッチ」 #2[字][デ]', 'ウィッチウォッチ', '2'),
            ('アニメギルド落第賢者の学院無双#1[新]', '落第賢者の学院無双', '1'),
            ('アニメA・ポンコツ風紀委員とスカート丈が不適切なJKの話 #2', 'ポンコツ風紀委員とスカート丈が不適切なJKの話', '2'),
            ('火アニバル マリッジトキシン 第3話', 'マリッジトキシン', '3'),
            ('アポカリプスホテル AnichU', 'アポカリプスホテル', '3'),
            ('落第賢者の学院無双【ANiMAZiNG²！！！】#3', '落第賢者の学院無双', '3'),
        ]
        for title, expected_title, expected_episode in cases:
            with self.subTest(title=title):
                description = '第3話「笑顔は最高のインテリア」' if title.endswith('AnichU') else None
                parsed = ParseSeriesTitle(title, ANIME_GENRES, description)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.display_title, expected_title)
                self.assertEqual(parsed.episode_number, expected_episode)
                if title.endswith('AnichU'):
                    self.assertEqual(parsed.subtitle, '笑顔は最高のインテリア')

    def test_historical_numerals_and_episode_units_are_supported(self) -> None:
        """EPG に現れる漢数字と作品固有の一文字助数詞を話数として読む。"""

        cases = [
            ('春夏秋冬代行者 春の舞 第弐話「名残雪」', '2'),
            ('春夏秋冬代行者 春の舞 第肆話「朝凪」', '4'),
            ('春夏秋冬代行者 春の舞 第拾参話「奪還」', '13'),
            ('3年Z組銀八先生 第10講[字]', '10'),
            ('アニメ リィンカーネーションの花弁 第十輪 顔の無い男', '10'),
            ('アニメ 令和のダラさん 第七怪 在りし日の紙芝居', '7'),
        ]
        for title, expected_episode in cases:
            with self.subTest(title=title):
                parsed = ParseSeriesTitle(title, ANIME_GENRES)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.episode_number, expected_episode)

        darasan = ParseSeriesTitle('アニメ 令和のダラさん 第七怪 在りし日の紙芝居', ANIME_GENRES)
        numbered = ParseSeriesTitle('令和のダラさん #7', ANIME_GENRES)
        self.assertIsNotNone(darasan)
        self.assertIsNotNone(numbered)
        assert darasan is not None and numbered is not None
        self.assertEqual(darasan.normalized_title, numbered.normalized_title)
        self.assertEqual(darasan.subtitle, '在りし日の紙芝居')

    def test_series_part_number_is_not_treated_as_episode(self) -> None:
        """作品名に含まれる期・部・章の番号より後ろにある自然話数を採用する。"""

        parsed = ParseSeriesTitle('架空作品 第2期 第3怪 旅立ち', ANIME_GENRES)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_title, '架空作品 第2期')
        self.assertEqual(parsed.episode_number, '3')
        self.assertEqual(parsed.subtitle, '旅立ち')

    def test_episode_at_description_line_start_is_used_conservatively(self) -> None:
        """title が固定の放送局では description の独立行先頭から話数を読む。"""

        cases = [
            (
                '没落予定の貴族だけど、暇だったから魔法を極めてみた',
                '#2 リアム、冒険者になってみた\nあらすじ本文',
                '2',
                'リアム、冒険者になってみた',
            ),
            (
                '勇者のクズ[字]',
                'クズの「師匠」と自称「弟子」\n#17勇者の帰還',
                '17',
                '勇者の帰還',
            ),
            (
                'Aランクパーティを離脱した俺は、元教え子たちと迷宮深部を目指す。',
                '第2話 魔獣の棲む森',
                '2',
                '魔獣の棲む森',
            ),
        ]
        for title, description, expected_episode, expected_subtitle in cases:
            with self.subTest(title=title):
                parsed = ParseSeriesTitle(title, ANIME_GENRES, description)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.episode_number, expected_episode)
                self.assertEqual(parsed.subtitle, expected_subtitle)

    def test_numbers_inside_description_body_are_not_used_as_episode(self) -> None:
        """あらすじ本文中の数字は Series 生成の根拠にしない。"""

        parsed = ParseSeriesTitle(
            '番組タイトル',
            ANIME_GENRES,
            '主人公は第2話の事件から10年後を思い出す。',
        )
        self.assertIsNone(parsed)

    def test_bs11_dotted_chapter_in_description(self) -> None:
        """BS11 の Chapter.1 から話数と副題を抽出し、短縮された作品名を保持する。"""

        title = '[新]目覚めたら最強装備と宇宙船持ちだったので一戸建て目指して傭兵として自由に生きた'
        for heading in ('Chapter.1', 'Chapter．１', 'CH.1', 'Chapter 1'):
            with self.subTest(heading=heading):
                parsed = ParseSeriesTitle(
                    title,
                    ANIME_GENRES,
                    f'{heading}「はじめてのスペースコロニー」\n◇番組内容\n退屈で苦労の多い日々を送る会社員。',
                )
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.display_title, title.removeprefix('[新]'))
                self.assertEqual(parsed.episode_number, '1')
                self.assertEqual(parsed.subtitle, 'はじめてのスペースコロニー')

    def test_empty_description_does_not_interrupt_series_rebuild(self) -> None:
        """空文字の description は先頭行を持たなくても通常のタイトル解析を続行する。"""

        parsed = ParseSeriesTitle('空説明のアニメ #2', ANIME_GENRES, '')
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_title, '空説明のアニメ')
        self.assertEqual(parsed.episode_number, '2')

    def test_short_real_work_title_is_not_rejected_by_length(self) -> None:
        """短い実在作品名でも明示的な話数があれば Series 化する。"""

        parsed = ParseSeriesTitle('キルアオ[字]', ANIME_GENRES, '「ミツオカノレン」\n#12 殺し屋会議')
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_title, 'キルアオ')
        self.assertEqual(parsed.episode_number, '12')

    def test_quoted_work_title_after_program_type_is_preserved(self) -> None:
        """TVアニメや時代劇の種別後に引用された作品名を副題にしない。"""

        cases = [
            ('TVアニメ『乙女怪獣キャラメリゼ』第2話「開かない扉」[字]', '乙女怪獣キャラメリゼ', '開かない扉'),
            ('時代劇「長七郎江戸日記」 第10回「若後家には御用心」', '長七郎江戸日記', '若後家には御用心'),
        ]
        for title, expected_title, expected_subtitle in cases:
            with self.subTest(title=title):
                parsed = ParseSeriesTitle(title, ANIME_GENRES)
                self.assertIsNotNone(parsed)
                assert parsed is not None
                self.assertEqual(parsed.display_title, expected_title)
                self.assertEqual(parsed.subtitle, expected_subtitle)

    def test_bare_quoted_work_title_and_multiple_episodes_are_indexed(self) -> None:
        """先頭の引用作品名と複数話表記を、特別連続放送でも正しく抽出する。"""

        parsed = ParseSeriesTitle(
            '[新]『無職転生Ⅲ～異世界行ったら本気だす～』第1・2話【エリス修行編】特別連続放送',
            ANIME_GENRES,
        )
        ordinary = ParseSeriesTitle(
            '無職転生Ⅲ ～異世界行ったら本気だす～ #03「帰ってきた日常」',
            ANIME_GENRES,
        )
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(ordinary)
        assert parsed is not None and ordinary is not None
        self.assertEqual(parsed.normalized_title, ordinary.normalized_title)
        self.assertEqual(parsed.episode_number, '1・2')
        self.assertEqual(parsed.subtitle, '【エリス修行編】特別連続放送')

    def test_broadcaster_cour_suffix_before_trailing_episode_is_removed(self) -> None:
        """閉じ波線の後ろへ放送局が付けたクール番号だけを作品名から除外する。"""

        bs_4k = ParseSeriesTitle(
            'アニメ ヘルモード ～やり込み好きのゲーマーは廃設定の異世界で無双する～2 17',
            ANIME_GENRES,
        )
        tokyo_mx = ParseSeriesTitle(
            'ヘルモード ～やり込み好きのゲーマーは廃設定の異世界で無双する～ #17',
            ANIME_GENRES,
        )
        numbered_work = ParseSeriesTitle('作品2 17', ANIME_GENRES)
        self.assertIsNotNone(bs_4k)
        self.assertIsNotNone(tokyo_mx)
        self.assertIsNotNone(numbered_work)
        assert bs_4k is not None and tokyo_mx is not None and numbered_work is not None
        self.assertEqual(bs_4k.normalized_title, tokyo_mx.normalized_title)
        self.assertEqual(bs_4k.display_title, 'ヘルモード ~やり込み好きのゲーマーは廃設定の異世界で無双する~')
        self.assertEqual(bs_4k.episode_number, '17')
        self.assertEqual(numbered_work.display_title, '作品2')

    def test_at_x_full_title_in_description_matches_other_channels(self) -> None:
        """AT-X が description 先頭へ置く正式作品名を、短縮タイトルの補完に使う。"""

        at_x = ParseSeriesTitle(
            'ブチ切れ令嬢は報復を誓いました。 #02 [字]',
            ANIME_GENRES,
            '■ブチ切れ令嬢は報復を誓いました。 ～魔導書の力で祖国を叩き潰します～',
        )
        bs_11 = ParseSeriesTitle(
            '[新]ブチ切れ令嬢は報復を誓いました。～魔導書の力で祖国を叩き潰します～ 第01話',
            ANIME_GENRES,
        )
        self.assertIsNotNone(at_x)
        self.assertIsNotNone(bs_11)
        assert at_x is not None and bs_11 is not None
        self.assertEqual(at_x.normalized_title, bs_11.normalized_title)
        self.assertEqual(at_x.episode_number, '2')

    def test_broadcast_edition_suffixes_match_the_base_series(self) -> None:
        """放送局固有の編集版表記だけを外し、同じ自然話数を一つの Series へまとめる。"""

        cases = [
            (
                'ぬきたし THE ANIMATION 青藍島ver. #02',
                'ぬきたし THE ANIMATION #2',
                None,
            ),
            (
                'New PANTY & STOCKING with GAR… #02',
                'アニメ New PANTY & STOCKING with GARTERBELT CENSORED版 第2回',
                '■New PANTY & STOCKING with GARTERBELT\n≪オリジナル版≫',
            ),
        ]
        for edition_title, base_title, edition_description in cases:
            with self.subTest(edition_title=edition_title):
                edition = ParseSeriesTitle(edition_title, ANIME_GENRES, edition_description)
                base = ParseSeriesTitle(base_title, ANIME_GENRES)
                self.assertIsNotNone(edition)
                self.assertIsNotNone(base)
                assert edition is not None and base is not None
                self.assertEqual(edition.normalized_title, base.normalized_title)
                self.assertEqual(edition.display_title, base.display_title)
                self.assertEqual(edition.episode_number, base.episode_number)

    def test_unknown_version_suffix_is_preserved(self) -> None:
        """未確認の ver. 表記は作品名の可能性があるため、汎用的には削除しない。"""

        parsed = ParseSeriesTitle('架空作品 完全版ver. #2', ANIME_GENRES)
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_title, '架空作品 完全版ver.')

    def test_description_episode_heading_is_not_used_as_series_title(self) -> None:
        """description の ■ が各話見出しなら、元の作品名を維持する。"""

        parsed = ParseSeriesTitle(
            '3年Z組銀八先生 第2講[字]',
            ANIME_GENRES,
            '■見た目が変わっても中身は変わらないのが人間',
        )
        self.assertIsNotNone(parsed)
        assert parsed is not None
        self.assertEqual(parsed.display_title, '3年Z組銀八先生')

    def test_short_title_alias_requires_an_explicit_subtitle_boundary(self) -> None:
        """別局の短縮名は副題境界を持つ正式名だけへ寄せ、続編番号には寄せない。"""

        short_title = NormalizeSeriesTitle('落第賢者の学院無双')
        full_title = NormalizeSeriesTitle('落第賢者の学院無双 ～二度目の転生、Sランクチート魔術師冒険録～')
        sequel_title = NormalizeSeriesTitle('落第賢者の学院無双2')
        similar_title = NormalizeSeriesTitle('落第賢者の学院無双外伝')
        self.assertTrue(IsStrictSeriesTitlePrefix(short_title, full_title))
        self.assertFalse(IsStrictSeriesTitlePrefix(short_title, sequel_title))
        self.assertFalse(IsStrictSeriesTitlePrefix(short_title, similar_title))

    def test_generic_short_program_is_still_rejected(self) -> None:
        """短い作品名を許可しても、既知の汎用番組は Series 化しない。"""

        self.assertIsNone(ParseSeriesTitle('紅白なび(10)[字]', ANIME_GENRES))


if __name__ == '__main__':
    unittest.main()
