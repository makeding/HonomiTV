from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime

from tortoise import connections

from app import logging
from app.constants import JST
from app.models.RecordedProgram import RecordedProgram
from app.models.Series import Series
from app.models.SeriesAlias import SeriesAlias
from app.models.SeriesBroadcastPeriod import SeriesBroadcastPeriod
from app.schemas import Genre


@dataclass(frozen=True, slots=True)
class ParsedSeriesTitle:
    """番組タイトルから確定的に抽出したシリーズ識別情報を保持する。"""

    # 利用者へ表示する作品名。Series.title と RecordedProgram.series_title に保存する。
    display_title: str
    # 表記揺れだけを吸収した完全一致用キー。異なる作品を fuzzy に統合する用途には使わない。
    normalized_title: str
    # 番組タイトルから明示的に抽出できた話数。無話数の定期番組では None。
    episode_number: str | None
    # 話数表記の後ろにある副題。取得できない場合は None。
    subtitle: str | None


# 放送状態や短い汎用枠は、同名でも一つの作品を表さないため Series を自動生成しない。
GENERIC_SERIES_TITLES = {
    'bテレ',
    'musicアラカルト',
    'weatherreport',
    'ニュース',
    '紅白なび',
    '放送休止',
    '天気予報',
}

# バラエティ・音楽番組は話数を付けず、固定の番組名と毎回の企画名で EPG を構成することがある。
## ジャンルだけで単発番組を統合しないよう、実際に類似する別の録画がある場合に限り Series にする。
EPISODELESS_SERIES_GENRES = {'バラエティ', '音楽'}

# EPG タイトルの先頭に付与される放送枠名。作品名そのものではないため除外する。
PROGRAM_SLOT_PREFIX_PATTERN = re.compile(
    r'^(?:(?:<[^<>]+>|＜[^＜＞]+＞)|(?:アニメギルド|アニメA[・･]|火アニバル))\s*',
    flags=re.IGNORECASE,
)
PROGRAM_TYPE_PREFIX_PATTERN = re.compile(r'^(?:(?:TV|テレビ)?アニメ)\s+', flags=re.IGNORECASE)

# 作品名を引用符で囲う放送枠。通常の各話副題と区別するため、副題抽出より先に作品名を外へ出す。
QUOTED_PROGRAM_SLOT_PATTERN = re.compile(
    r'^(?:日5)「(?P<title>[^」]+)」(?P<rest>.*)$',
    flags=re.IGNORECASE,
)
QUOTED_WORK_TITLE_PATTERN = re.compile(
    r'^(?:(?:TVアニメ|時代劇)\s*)?[「『](?P<title>[^」』]+)[」』](?P<rest>.*)$',
    flags=re.IGNORECASE,
)

# 作品名の前後に付く既知の放送枠名。作品名の装飾は汎用的に削除せず、実データで確認できた枠だけを列挙する。
PROGRAM_SLOT_MARK_PATTERN = re.compile(
    r'(?:【(?:ANiMAZiNG(?:2|²)?[!！]*|スーパーアニメイズムTURBO|イマニメーションW?)】|<\+Ultra>|\s+(?:AnichU|FRIDAY ANIME NIGHT)\s*$)',
    flags=re.IGNORECASE,
)

# KonomiTV が既に番組記号として扱っている角括弧表記だけを除去する。
PROGRAM_MARK_PATTERN = re.compile(
    r'\[(?:新|終|再|交|映|手|声|多|副|字|文|CC|OP|二|S|B|SS|無|無料|C|S1|S2|S3|MV|双|デ|D|N|W|P|H|HV|SD|天|解|料|前|後|初|生|販|吹|PPV|演|移|他|収|英|韓|中|字/日|字/日英|3D|2ndScr|2K|4K|8K|5\.1|7\.1|22\.2|60P|120P|d|HC|HDR|Hi-Res|Lossless|SHV|UHD|VOD|配)\]',
    flags=re.IGNORECASE,
)

# 話数を明示する表記だけを Series の自動生成根拠として採用する。
# 「第 + 数字」は作品ごとに異なる一文字の助数詞を列挙せず、期・部・章など作品名側の番号だけを除外する。
EPISODE_PATTERN = re.compile(
    r'(?:'
    r'\(\s*第?\s*(?P<parenthesized>[0-9一二三四五六七八九十百千〇零壱弐参拾貳肆伍陸漆玖]+(?:\.[0-9]+)?)\s*(?:話|回|講|輪)?\s*\)?|'
    r'#\s*(?P<hash>[0-9]+(?:\.[0-9]+)?(?:\s*[・&／/\-～~]\s*#?\s*[0-9]+(?:\.[0-9]+)?)*)|'
    r'第\s*(?P<japanese>[0-9一二三四五六七八九十百千〇零壱弐参拾貳肆伍陸漆玖]+(?:\s*[・&／/\-～~]\s*#?\s*[0-9一二三四五六七八九十百千〇零壱弐参拾貳肆伍陸漆玖]+)*)'
    r'(?!\s*(?:期|シーズン|クール|部|章))(?:(?:\s*(?:話|回|講|輪))|[^\W\d_\s])?|'
    # BS11 の概要欄では Chapter.1 と表記するため、話数の前の区切りピリオドも許可する。
    r'\b(?:Chapter|CH)\s*\.?\s*(?P<chapter>[0-9]+(?:\.[0-9]+)?)'
    r')',
    flags=re.IGNORECASE,
)
TRAILING_EPISODE_PATTERN = re.compile(r'\s+(?P<episode>[0-9]+(?:\.[0-9]+)?)\s*$')
# 一部放送局が装飾用の閉じ波線の直後へ付けるクール番号。
## 「作品名～2 17」の最初の 2 だけを作品名から外し、末尾の 17 は通常どおり話数として扱う。
BROADCASTER_COUR_SUFFIX_PATTERN = re.compile(r'^(?P<title>.+[～~])(?P<cour>[2-9])$')
# 同一作品を放送局ごとに区別する末尾の編集版表記。作品・話数は同一なので Series 識別名からだけ除外する。
## 「ver.」全般を削ると作品名そのものを壊すため、実データで同一話数の別局版を確認できた表記だけを列挙する。
BROADCAST_EDITION_SUFFIX_PATTERN = re.compile(
    r'\s*(?:CENSORED版|青藍島ver\.)$',
    flags=re.IGNORECASE,
)
QUOTED_SUBTITLE_PATTERN = re.compile(r'[「『](?P<subtitle>.*?)[」』]')
QUOTED_LEVEL_EPISODE_PATTERN = re.compile(
    r'^Lv\s*(?P<episode>[0-9]+(?:\.[0-9]+)?)\s+(?P<subtitle>.+)$',
    flags=re.IGNORECASE,
)
JAPANESE_DIGITS = {
    '〇': 0,
    '零': 0,
    '一': 1,
    '壱': 1,
    '二': 2,
    '弐': 2,
    '三': 3,
    '参': 3,
    '貳': 3,
    '四': 4,
    '肆': 4,
    '五': 5,
    '伍': 5,
    '六': 6,
    '陸': 6,
    '七': 7,
    '漆': 7,
    '八': 8,
    '九': 9,
    '玖': 9,
}
JAPANESE_UNITS = {'十': 10, '拾': 10, '百': 100, '千': 1000}


def ParseJapaneseNumber(value: str) -> int | None:
    """
    EPG の話数に使われる漢数字を整数へ変換する。

    Args:
        value (str): 漢数字の話数。

    Returns:
        int | None: 変換できた整数。漢数字以外を含む場合は None。
    """

    if all(character in JAPANESE_DIGITS for character in value):
        return int(''.join(str(JAPANESE_DIGITS[character]) for character in value))

    total = 0
    current_digit = 0
    for character in value:
        if character in JAPANESE_DIGITS:
            current_digit = JAPANESE_DIGITS[character]
            continue
        unit = JAPANESE_UNITS.get(character)
        if unit is None:
            return None
        total += (current_digit or 1) * unit
        current_digit = 0
    return total + current_digit


def NormalizeEpisodeNumber(episode_number: str) -> str:
    """
    話数の数値表記を表示・比較しやすい形へ揃える。

    Args:
        episode_number (str): EPG タイトルから抽出した話数。

    Returns:
        str: 先頭ゼロと多話区切りの表記を正規化した話数。
    """

    # 漢数字は意味を変えず保持し、ASCII 数字だけ先頭ゼロを取り除く。
    parts = re.split(r'\s*[・&／/\-～~]\s*#?\s*', episode_number)
    normalized_parts: list[str] = []
    for part in parts:
        japanese_number = ParseJapaneseNumber(part)
        if part.isdigit():
            normalized_parts.append(str(int(part)))
        elif re.fullmatch(r'\d+\.\d+', part):
            normalized_parts.append(str(float(part)).rstrip('0').rstrip('.'))
        elif japanese_number is not None:
            normalized_parts.append(str(japanese_number))
        else:
            normalized_parts.append(part)
    separator = '-' if re.search(r'[\-～~]', episode_number) is not None else '・'
    return separator.join(normalized_parts)


def NormalizeSeriesTitle(title: str) -> str:
    """
    シリーズ同一性の完全一致判定に使うタイトルを生成する。

    Args:
        title (str): 表示用に整形済みの作品名。

    Returns:
        str: Unicode・英字大小・空白だけを正規化した比較キー。
    """

    # NFKC で全角英数字や互換文字を揃え、空白差を作品同一性へ影響させない。
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', title)).casefold()


def ParseEpisodeLessSeriesTitle(
    title: str,
    genres: list[Genre],
    similar_titles: list[str],
) -> ParsedSeriesTitle | None:
    """
    類似する過去タイトルから無話数の定期番組名を抽出する。

    Args:
        title (str): EPG 由来の番組タイトル。
        genres (list[Genre]): EPG 由来の番組ジャンル。
        similar_titles (list[str]): 同じ番組名候補で始まる別の録画タイトル。

    Returns:
        ParsedSeriesTitle | None: 別の録画で定期番組と確認できた場合の解析結果。
    """

    # 話数を付けず毎回の企画名を入れる運用が確認できたジャンルだけを対象にする。
    if {genre['major'] for genre in genres}.isdisjoint(EPISODELESS_SERIES_GENRES):
        return None

    normalized_source = unicodedata.normalize('NFKC', title).strip()
    quote_index_candidates = [
        normalized_source.find(quote)
        for quote in ('「', '『')
        if normalized_source.find(quote) >= 0
    ]
    if len(quote_index_candidates) == 0:
        return None
    quote_index = min(quote_index_candidates)
    display_title = normalized_source[:quote_index].strip()
    trailing_subtitle = normalized_source[quote_index:].strip()
    normalized_title = NormalizeSeriesTitle(display_title)
    if len(normalized_title) < 2 or normalized_title in GENERIC_SERIES_TITLES:
        return None

    # 同じ固定名で始まり、かつ全体は異なる過去録画を定期番組の根拠にする。
    ## 引用符の境界まで一致させ、似た文字列を持つ別番組の誤統合を防ぐ。
    has_similar_title = any(
        NormalizeSeriesTitle(similar_title) != NormalizeSeriesTitle(normalized_source) and
        any(
            unicodedata.normalize('NFKC', similar_title).startswith(f'{display_title}{quote}')
            for quote in ('「', '『')
        )
        for similar_title in similar_titles
    )
    if has_similar_title is False:
        return None

    quoted_subtitle_match = re.fullmatch(r'[「『](?P<subtitle>.*)[」』]', trailing_subtitle)
    subtitle = (
        quoted_subtitle_match.group('subtitle').strip()
        if quoted_subtitle_match is not None
        else trailing_subtitle
    )
    return ParsedSeriesTitle(
        display_title = display_title,
        normalized_title = normalized_title,
        episode_number = None,
        subtitle = subtitle,
    )


def IsStrictSeriesTitlePrefix(short_title: str, long_title: str) -> bool:
    """
    短縮作品名と正式作品名を、安全な副題境界を持つ前方一致として比較する。

    Args:
        short_title (str): 短縮された作品名の正規化キー。
        long_title (str): 副題まで含む正式作品名の正規化キー。

    Returns:
        bool: 長い作品名が短い作品名に明示的な副題を加えた形なら True。
    """

    if not long_title.startswith(short_title) or len(long_title) <= len(short_title) + 1:
        return False
    return long_title[len(short_title)] in {'~', '～', '-', '―', '—', ':', '：', '「', '『', '【'}


def ParseSeriesTitle(
    title: str,
    genres: list[Genre],
    description: str | None = None,
) -> ParsedSeriesTitle | None:
    """
    EPG タイトルから誤統合しにくい確定的なシリーズ情報を抽出する。

    Args:
        title (str): EPG 由来の番組タイトル。
        genres (list[Genre]): 末尾数字を話数として扱える番組ジャンル。
        description (str | None): 放送局が話数をタイトルでなく先頭行に入れる番組概要。

    Returns:
        ParsedSeriesTitle | None: 明示的な話数と十分な作品名を抽出できた場合のみ結果を返す。
    """

    # 全角英数字などを先に揃え、放送枠・番組種別・技術マークを作品名から分離する。
    normalized_source = unicodedata.normalize('NFKC', title).strip()
    normalized_source = PROGRAM_MARK_PATTERN.sub('', normalized_source)
    normalized_source = re.sub(r'\((?:二|字|再)\)', '', normalized_source)
    normalized_source = PROGRAM_SLOT_PREFIX_PATTERN.sub('', normalized_source)
    normalized_source = PROGRAM_TYPE_PREFIX_PATTERN.sub('', normalized_source)
    normalized_source = PROGRAM_SLOT_MARK_PATTERN.sub('', normalized_source)
    normalized_source = normalized_source.strip()

    # 「日5『作品名』 #2」の引用符は副題ではないため、作品名と話数を通常の配置に戻す。
    quoted_program_slot_match = QUOTED_PROGRAM_SLOT_PATTERN.fullmatch(normalized_source)
    if quoted_program_slot_match is not None:
        normalized_source = (
            quoted_program_slot_match.group('title') + quoted_program_slot_match.group('rest')
        ).strip()

    # 「TVアニメ『作品名』第2話」などは引用部分自体が作品名なので、副題抽出の前に外へ出す。
    quoted_work_title_match = QUOTED_WORK_TITLE_PATTERN.fullmatch(normalized_source)
    if quoted_work_title_match is not None:
        normalized_source = (
            quoted_work_title_match.group('title') + quoted_work_title_match.group('rest')
        ).strip()

    # 「…」内は各話副題として先に保持し、作品名の比較キーからは除外する。
    subtitle_match = QUOTED_SUBTITLE_PATTERN.search(normalized_source)
    subtitle = subtitle_match.group('subtitle').strip() if subtitle_match is not None else None
    title_without_quoted_subtitle = QUOTED_SUBTITLE_PATTERN.sub('', normalized_source).strip()

    # #6 / 第6話 / Chapter 6 など、話数だと断定できる位置より前を作品名として採用する。
    episode_source = title_without_quoted_subtitle
    episode_match = EPISODE_PATTERN.search(episode_source)
    is_episode_from_description = False
    is_episode_from_trailing_title = False
    quoted_level_match = QUOTED_LEVEL_EPISODE_PATTERN.fullmatch(subtitle) if subtitle is not None else None
    if episode_match is None:
        # 一部アニメ局は「Lv2 副題」のように話数を引用符内へ入れるため、引用符の先頭だけを追加で認識する。
        ## 作品名本体の LV999 などを話数と誤認しないよう、タイトル本体では Lv 表記を検索しない。
        if quoted_level_match is not None:
            episode_number = NormalizeEpisodeNumber(quoted_level_match.group('episode'))
            display_title = title_without_quoted_subtitle.strip(' 　・:-')
            subtitle = quoted_level_match.group('subtitle').strip()
            normalized_title = NormalizeSeriesTitle(display_title)
            if len(normalized_title) < 2 or normalized_title in GENERIC_SERIES_TITLES:
                return None
            return ParsedSeriesTitle(
                display_title = display_title,
                normalized_title = normalized_title,
                episode_number = episode_number,
                subtitle = subtitle,
            )

        # アニメ EPG では末尾の単独数字が話数として使われるため、このジャンルに限り追加で認識する。
        is_anime = any(genre['major'] == 'アニメ・特撮' for genre in genres)
        episode_match = TRAILING_EPISODE_PATTERN.search(title_without_quoted_subtitle) if is_anime else None
        is_episode_from_trailing_title = episode_match is not None
        if episode_match is None and is_anime and description is not None:
            # 放送局によっては title を毎回同じ作品名にし、description の独立行先頭へ話数を入れる。
            ## あらすじ本文に現れる数字を話数と誤認しないよう、各行の先頭一致だけを採用する。
            for description_line in description.splitlines():
                description_line = unicodedata.normalize('NFKC', description_line).strip()
                description_episode_match = EPISODE_PATTERN.match(description_line)
                if description_episode_match is not None:
                    episode_source = description_line
                    episode_match = description_episode_match
                    is_episode_from_description = True
                    break
    if episode_match is None:
        return None

    # 正規表現のうち一致した表記から話数文字列を取り出す。
    episode_number = NormalizeEpisodeNumber(next(
        value
        for value in episode_match.groupdict().values()
        if value is not None
    ))
    display_title = (
        title_without_quoted_subtitle
        if is_episode_from_description
        else title_without_quoted_subtitle[:episode_match.start()]
    ).strip(' 　・:-(（')

    # BS 日テレ 4K の「ヘルモード ...～2 17」のように、閉じ波線と末尾話数の間へ
    ## クール番号を挿入する表記だけを補正する。通常の「作品2 17」は作品名の数字を保持する。
    if is_episode_from_trailing_title:
        broadcaster_cour_suffix_match = BROADCASTER_COUR_SUFFIX_PATTERN.fullmatch(display_title)
        if broadcaster_cour_suffix_match is not None:
            display_title = broadcaster_cour_suffix_match.group('title')

    # AT-X はタイトル欄を短縮し、description の先頭行へ「■短縮名 + 正式な副題」を置くことがある。
    ## 先頭行が解析済みの作品名から始まり、かつ実際に長い場合だけ正式名として採用することで、
    ## テレビ東京の「■各話サブタイトル」のような同じ記号を使う本文は作品名へ混入させない。
    if description is not None:
        description_first_line = unicodedata.normalize('NFKC', next(iter(description.splitlines()), '')).strip()
        if description_first_line.startswith('■'):
            description_series_title = description_first_line.removeprefix('■').strip()
            normalized_display_title = NormalizeSeriesTitle(display_title)
            normalized_description_series_title = NormalizeSeriesTitle(description_series_title)
            # AT-X の表示上限で末尾が「…」になった場合は、NFKC 後の三点を外した前方一致で補完する。
            ## 省略記号がない短い作品名には適用せず、別作品を説明文へ引っ張る範囲を限定する。
            normalized_truncated_title = normalized_display_title.removesuffix('...')
            if (
                len(normalized_description_series_title) > len(normalized_display_title) and
                (
                    normalized_description_series_title.startswith(normalized_display_title) or
                    (
                        normalized_truncated_title != normalized_display_title and
                        normalized_description_series_title.startswith(normalized_truncated_title)
                    )
                )
            ):
                display_title = description_series_title

    # AT-X の独自編集版や他局の CENSORED 版は、同じ自然話数を持つ同一作品として扱う。
    ## 録画タイトル自体は変更せず、Series の表示・比較に使う作品名からだけ既知の版表記を外す。
    display_title = BROADCAST_EDITION_SUFFIX_PATTERN.sub('', display_title).strip()

    # 話数の後ろに残る語句は放送枠名を除き、副題が別途なければ副題として保存する。
    trailing_text = episode_source[episode_match.end():].strip()
    trailing_text = re.sub(r'^\s*[◆◇].*$', '', trailing_text).strip()
    if subtitle is None and trailing_text:
        trailing_subtitle_match = QUOTED_SUBTITLE_PATTERN.fullmatch(trailing_text)
        subtitle = (
            trailing_subtitle_match.group('subtitle').strip()
            if trailing_subtitle_match is not None
            else trailing_text
        )

    normalized_title = NormalizeSeriesTitle(display_title)
    if len(normalized_title) < 2 or normalized_title in GENERIC_SERIES_TITLES:
        return None

    return ParsedSeriesTitle(
        display_title = display_title,
        normalized_title = normalized_title,
        episode_number = episode_number,
        subtitle = subtitle,
    )


class SeriesIndexer:
    """録画番組を確定的な作品タイトル単位で Series へ関連付ける。"""

    @classmethod
    async def linkRecordedProgram(cls, recorded_program: RecordedProgram) -> bool:
        """
        1 件の録画番組を Series と放送期間へ関連付ける。

        Args:
            recorded_program (RecordedProgram): DB 保存済みの録画番組。

        Returns:
            bool: Series へ関連付けられた場合は True。
        """

        parsed_title = ParseSeriesTitle(
            recorded_program.title,
            recorded_program.genres,
            recorded_program.description,
        )
        episode_less_similar_programs: list[RecordedProgram] = []
        if parsed_title is None:
            # 無話数のバラエティ・音楽番組は、同じ固定タイトルで始まる別の録画を根拠にする。
            ## 引用符より前の候補で DB 検索を限定し、全録画のタイトルをロードしない。
            quote_indexes = [
                recorded_program.title.find(quote)
                for quote in ('「', '『')
                if recorded_program.title.find(quote) >= 0
            ]
            if quote_indexes:
                # DB の全角記号と一致させるため、検索には NFKC 前の EPG 原文を使う。
                episode_less_title_prefix = recorded_program.title[:min(quote_indexes)].strip()
                episode_less_similar_programs = await RecordedProgram.filter(
                    title__startswith = episode_less_title_prefix,
                ).exclude(id=recorded_program.id).all()
                parsed_title = ParseEpisodeLessSeriesTitle(
                    recorded_program.title,
                    recorded_program.genres,
                    [similar_program.title for similar_program in episode_less_similar_programs],
                )
        if parsed_title is None:
            # 現在の確定的な規則で Series にできない録画は、過去の解析結果を残さない。
            ## これにより、汎用番組を除外した後も Series 一覧に古いカードが残ることを防ぐ。
            if recorded_program.series_id is not None:
                recorded_program.series_id = None
                recorded_program.series_broadcast_period_id = None
                recorded_program.series_title = None
                recorded_program.episode_number = None
                recorded_program.subtitle = None
                await recorded_program.save(update_fields=[
                    'series_id',
                    'series_broadcast_period_id',
                    'series_title',
                    'episode_number',
                    'subtitle',
                ])
            return False

        # 原則は normalized_title の完全一致だけで Series を再利用し、fuzzy 類似度による誤統合を防ぐ。
        ## Bangumi 条目で統合済みの放送局別表記は alias に残るため、主タイトルの次に完全一致で解決する。
        series = await Series.get_or_none(normalized_title=parsed_title.normalized_title)
        if series is None:
            series_alias = await SeriesAlias.get_or_none(
                normalized_title = parsed_title.normalized_title,
            ).select_related('series')
            if series_alias is not None:
                series = series_alias.series
        is_series_created = False

        # 一部放送局は副題を丸ごと省略するため、同じ話数が別局の正式作品名へ既に存在する場合に限り、
        ## 「短縮名 + 明示的な副題境界」の前方一致を作品名 alias として扱う。
        if recorded_program.channel_id is not None and parsed_title.episode_number is not None:
            longer_series_candidates = await Series.filter(
                normalized_title__startswith = parsed_title.normalized_title,
            ).all()
            for candidate in longer_series_candidates:
                if candidate.id == (series.id if series is not None else None):
                    continue
                if not IsStrictSeriesTitlePrefix(parsed_title.normalized_title, candidate.normalized_title):
                    continue
                has_same_episode_on_another_channel = await RecordedProgram.filter(
                    series_id = candidate.id,
                    episode_number = parsed_title.episode_number,
                ).exclude(channel_id=recorded_program.channel_id).exists()
                if has_same_episode_on_another_channel:
                    series = candidate
                    break

        if series is None:
            series, is_series_created = await Series.get_or_create(
                normalized_title = parsed_title.normalized_title,
                defaults = {
                    'title': parsed_title.display_title,
                    'description': '',
                    'genres': recorded_program.genres,
                },
            )

        # 主タイトルも alias テーブルへ常に登録し、統合時にタイトル所有者を原子的に移せるようにする。
        await SeriesAlias.update_or_create(
            normalized_title = parsed_title.normalized_title,
            defaults = {'series_id': series.id},
        )

        series_broadcast_period: SeriesBroadcastPeriod | None = None
        is_period_changed = False
        if recorded_program.channel_id is not None:
            broadcast_date = recorded_program.start_time.date()
            series_broadcast_period, is_period_created = await SeriesBroadcastPeriod.get_or_create(
                series_id = series.id,
                channel_id = recorded_program.channel_id,
                defaults = {
                    'start_date': broadcast_date,
                    'end_date': broadcast_date,
                },
            )

            # 同じ作品・チャンネルの放送日範囲を、実際に保存された録画に合わせて外側へ広げる。
            update_fields: list[str] = []
            if broadcast_date < series_broadcast_period.start_date:
                series_broadcast_period.start_date = broadcast_date
                update_fields.append('start_date')
            if broadcast_date > series_broadcast_period.end_date:
                series_broadcast_period.end_date = broadcast_date
                update_fields.append('end_date')
            if update_fields:
                await series_broadcast_period.save(update_fields=update_fields)
            is_period_changed = is_period_created or len(update_fields) > 0

        # 解析結果と FK を同時に保存し、Series ページと録画詳細で同じ情報を参照できるようにする。
        is_recorded_program_changed = (
            recorded_program.series_id != series.id or
            recorded_program.series_broadcast_period_id != (
                series_broadcast_period.id if series_broadcast_period is not None else None
            ) or
            recorded_program.series_title != series.title or
            recorded_program.episode_number != parsed_title.episode_number or
            recorded_program.subtitle != parsed_title.subtitle
        )
        recorded_program.series_id = series.id
        recorded_program.series_broadcast_period_id = (
            series_broadcast_period.id if series_broadcast_period is not None else None
        )
        recorded_program.series_title = series.title
        recorded_program.episode_number = parsed_title.episode_number
        recorded_program.subtitle = parsed_title.subtitle
        if is_recorded_program_changed:
            await recorded_program.save(update_fields=[
                'series_id',
                'series_broadcast_period_id',
                'series_title',
                'episode_number',
                'subtitle',
            ])

        # 2 件目の録画で無話数の定期番組と確定した場合は、根拠になった過去録画もすぐに同じ Series へ関連付ける。
        ## 未関連付けの録画だけを再評価するため、再帰先では現在の録画が根拠となり 1 回で収束する。
        if parsed_title.episode_number is None:
            for episode_less_program in episode_less_similar_programs:
                if episode_less_program.series_id is None:
                    await cls.linkRecordedProgram(episode_less_program)

        # 新しい録画や放送期間が加わったときだけ更新日時を進め、一覧の「更新が新しい順」へ反映する。
        if is_series_created or is_period_changed or is_recorded_program_changed:
            series.updated_at = datetime.now(tz=JST)
            await series.save(update_fields=['updated_at'])
        return True

    @classmethod
    async def rebuild(cls) -> None:
        """
        既存の全録画番組へ現在の確定的な Series 解析規則を適用する。

        Returns:
            None
        """

        logging.info('Series index rebuild has started.')
        linked_count = 0
        last_seen_id = 0

        # 大量の録画を一括ロードせず、ID 順に小さなバッチで処理する。
        while True:
            recorded_programs = await RecordedProgram.filter(id__gt=last_seen_id).order_by('id').limit(100)
            if len(recorded_programs) == 0:
                break
            for recorded_program in recorded_programs:
                if await cls.linkRecordedProgram(recorded_program):
                    linked_count += 1
                last_seen_id = recorded_program.id

        # 短縮タイトルが正式タイトルより先に登録された場合でも同じ結果へ収束させる。
        ## 全録画を再走査せず、厳密な前方一致となる長い Series が実在する短い Series の録画だけを再評価する。
        all_series = await Series.all()
        for short_series in all_series:
            has_longer_candidate = any(
                candidate.id != short_series.id and
                IsStrictSeriesTitlePrefix(short_series.normalized_title, candidate.normalized_title)
                for candidate in all_series
            )
            if not has_longer_candidate:
                continue
            short_series_programs = await RecordedProgram.filter(series_id=short_series.id).all()
            for recorded_program in short_series_programs:
                await cls.linkRecordedProgram(recorded_program)

        # ルール改善で別 Series へ移った録画の古い放送期間とカードだけを後始末する。
        ## RecordedProgram がまだ参照する Series は消さないため、CASCADE で録画自体が失われることはない。
        connection = connections.get('default')
        await connection.execute_query(
            'DELETE FROM series_broadcast_periods '
            'WHERE NOT EXISTS ('
            'SELECT 1 FROM recorded_programs '
            'WHERE recorded_programs.series_broadcast_period_id = series_broadcast_periods.id'
            ')',
        )
        await connection.execute_query(
            'DELETE FROM series '
            'WHERE NOT EXISTS ('
            'SELECT 1 FROM recorded_programs WHERE recorded_programs.series_id = series.id'
            ')',
        )

        logging.info(f'Series index rebuild has completed. linked_recorded_programs: {linked_count}')
