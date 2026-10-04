
import json
import re
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Path, Query, status
from tortoise import connections

from app import logging, schemas
from app.constants import JST
from app.metadata.SeriesIndexer import NormalizeSeriesTitle, ParseSeriesTitle
from app.models.Series import Series
from app.utils import ParseDatetimeStringToJST


# ルーター
router = APIRouter(
    tags = ['Series'],
    prefix = '/api/series',
)

# ページングで一度に取得するシリーズ番組の数
PAGE_SIZE = 50

# EPG の公式情報欄から番組公式サイトだけを選び、SNS や配信サービスへのリンクは除外する。
OFFICIAL_WEBSITE_URL_PATTERN = re.compile(r'https?://[^\s<>"\'）)】]+')
NON_OFFICIAL_WEBSITE_HOSTS = {
    'bs4.jp',
    'instagram.com',
    'tiktok.com',
    'tver.jp',
    'video.tv-tokyo.co.jp',
    'x.com',
    'youtube.com',
}
REPEAT_BROADCAST_TITLE_PATTERN = re.compile(r'(?:\[再\]|［再］|【再】|再放送)')
# EPG に明示された最終回だけを On Air から外す根拠にする。作品名や副題に含まれる
# 「終」を誤って拾わないよう、放送マークとして使われる括弧表記に限定する。
FINAL_BROADCAST_TITLE_PATTERN = re.compile(r'(?:\[|［|【)\s*(?:完|終)\s*(?:\]|］|】)')
ON_AIR_SERIES_GENRES = {'アニメ・特撮', 'ドラマ', 'バラエティ', '音楽'}


def ExtractIntegerEpisodeNumbers(episode_number: str) -> set[int]:
    """
    正規化済みの話数表記から、自然話数として欠番判定できる整数を取り出す。

    Args:
        episode_number (str): RecordedProgram に保存された正規化済み話数。

    Returns:
        set[int]: 単話・複数話・連続話を展開した整数話数。特別編などは空集合。
    """

    normalized_episode_number = episode_number.strip()
    range_match = re.fullmatch(r'(\d+)-(\d+)', normalized_episode_number)
    if range_match is not None:
        first_episode = int(range_match.group(1))
        last_episode = int(range_match.group(2))
        if first_episode <= last_episode:
            return set(range(first_episode, last_episode + 1))
        return set()

    episode_numbers: set[int] = set()
    for part in re.split(r'[・&／/]', normalized_episode_number):
        if part.isdigit():
            episode_numbers.add(int(part))
    return episode_numbers


def GetOnAirFinalExpiry(final_broadcast_at: datetime) -> datetime:
    """
    最終回が On Air へ残る期限を JST で決定する。

    Args:
        final_broadcast_at (datetime): 最終回の放送終了時刻。

    Returns:
        datetime: 放送終了から 7 日後と翌月 1 日 00:00 の早い方。
    """

    next_month_year = final_broadcast_at.year + (1 if final_broadcast_at.month == 12 else 0)
    next_month = 1 if final_broadcast_at.month == 12 else final_broadcast_at.month + 1
    next_month_start = datetime(next_month_year, next_month, 1, tzinfo=JST)
    return min(final_broadcast_at + timedelta(days=7), next_month_start)


def GetSeasonID(season_broadcasts: list[tuple[dict[str, Any], datetime]]) -> str:
    """
    季度内最早放送の年月から季度 ID を生成する。

    Args:
        season_broadcasts: 季度内の放送リスト。

    Returns:
        str: "YYYY-MM" 形式の季度 ID 。
    """
    earliest = min(start_time for _, start_time in season_broadcasts)
    return f'{earliest.year}-{earliest.month:02d}'


def GetSeasonLabel(season_id: str) -> str:
    """
    季度 ID から表示用ラベルを生成する。

    Args:
        season_id (str): "YYYY-MM" 形式の季度 ID 。

    Returns:
        str: "YYYY年M月期" 形式のラベル。
    """
    year, month = season_id.split('-')
    return f'{year}年{int(month)}月期'


def GetHistoricalSeasonWeekday(season_broadcasts: list[tuple[dict[str, Any], datetime]]) -> tuple[int, str]:
    """
    過去季度の曜日と時刻を、季度内全放送の録画時刻のうち最も多い曜日から決定する。

    Args:
        season_broadcasts: 季度内の放送リスト。

    Returns:
        tuple[int, str]: 曜日 (0=月曜) と "HH:MM" 形式の時刻。
    """
    weekday_counts: dict[int, int] = {}
    for _, start_time in season_broadcasts:
        weekday = start_time.weekday()
        weekday_counts[weekday] = weekday_counts.get(weekday, 0) + 1
    max_count = max(weekday_counts.values())
    candidates = [wd for wd, count in weekday_counts.items() if count == max_count]
    best_weekday = min(candidates, key=lambda wd: min(
        start_time for _, start_time in season_broadcasts
        if start_time.weekday() == wd
    ))
    earliest_time = min(
        start_time for _, start_time in season_broadcasts
        if start_time.weekday() == best_weekday
    )
    hour = earliest_time.hour
    minute = (earliest_time.minute // 5) * 5
    return best_weekday, f'{hour:02d}:{minute:02d}'


def ExtractOfficialWebsiteURL(sources: list[str]) -> str | None:
    """
    EPG の公式情報欄から作品または番組の公式 Web サイトを抽出する。

    Args:
        sources (list[str]): 公式ページ系の detail フィールド値。優先度が高い順に並ぶ。

    Returns:
        str | None: SNS・動画配信サイト以外で最初に見つかった HTTP(S) URL。
    """

    for source in sources:
        for match in OFFICIAL_WEBSITE_URL_PATTERN.finditer(source):
            url = match.group(0).rstrip('。、,;')
            host = url.split('/', maxsplit=3)[2].lower().removeprefix('www.')
            if any(host == excluded_host or host.endswith(f'.{excluded_host}') for excluded_host in NON_OFFICIAL_WEBSITE_HOSTS):
                continue
            return url
    return None


@router.get(
    '',
    summary = 'シリーズ番組一覧 API',
    response_description = 'シリーズ番組のリスト。',
    response_model = schemas.SeriesSummaryList,
)
async def SeriesListAPI(
    order: Annotated[Literal['desc', 'asc'], Query(description='ソート順序 (desc or asc) 。')] = 'desc',
    page: Annotated[int, Query(description='ページ番号。')] = 1,
):
    """
    すべてのシリーズ番組を一度に 50 件ずつ取得する。<br>
    order には "desc" か "asc" を指定する。<br>
    page (ページ番号) には 1 以上の整数を指定する。
    """

    return await GetSeriesSummaries(order=order, page=page)


@router.get(
    '/search',
    summary = 'シリーズ番組検索 API',
    response_description = '検索条件に一致するシリーズ番組のリスト。',
    response_model = schemas.SeriesSummaryList,
)
async def SeriesSearchAPI(
    query: Annotated[str, Query(description='検索キーワード。title または description のいずれかに部分一致するシリーズ番組を検索する。')] = '',
    order: Annotated[Literal['desc', 'asc'], Query(description='ソート順序 (desc or asc) 。')] = 'desc',
    page: Annotated[int, Query(description='ページ番号。')] = 1,
):
    """
    指定されたキーワードでシリーズ番組を一度に 50 件ずつ検索する。<br>
    キーワードは title または description のいずれかに部分一致するシリーズ番組を検索する。<br>
    order には "desc" か "asc" を指定する。<br>
    page (ページ番号) には 1 以上の整数を指定する。
    """

    return await GetSeriesSummaries(query=query, order=order, page=page)


@router.get(
    '/on-air',
    summary = '放送中シリーズ一覧 API',
    response_description = 'ローカル録画から推定した曜日別の放送中シリーズ。',
    response_model = schemas.OnAirSeriesList,
)
async def OnAirSeriesListAPI():
    """
    最近の非再放送録画から、各 Series の最新話が最初に放送された曜日と時刻を取得する。

    Returns:
        schemas.OnAirSeriesList: 直近 21 日以内に通常放送がある Series の一覧。
    """

    now = datetime.now(JST)

    # 各 Series の通常枠と掲載期限を Python 側で集計できる最小限の列だけ取得する。
    ## 一時的な時刻変更や特番 1 件より、繰り返し現れる通常枠を優先する。
    connection = connections.get('default')
    _, rows = await connection.execute_query(
        """
        SELECT rp.series_id, s.title AS series_title, s.genres, rp.title AS program_title,
               rp.id, rp.channel_id, rp.start_time, rp.end_time, rp.episode_number, rp.is_partially_recorded,
               rv.thumbnail_info IS NOT NULL AS has_thumbnail
        FROM recorded_programs rp
        INNER JOIN series s ON s.id = rp.series_id
        LEFT JOIN recorded_videos rv ON rv.recorded_program_id = rp.id
        WHERE rp.series_id IS NOT NULL
        ORDER BY rp.series_id, rp.start_time DESC, rp.id DESC
        """,
    )
    samples_by_series: dict[int, list[dict[str, Any]]] = {}
    normal_broadcasts_by_series: dict[int, list[dict[str, Any]]] = {}
    sample_keys_by_series: dict[int, set[str]] = {}
    thumbnail_samples_by_series: dict[int, list[dict[str, Any]]] = {}
    thumbnail_sample_keys_by_series: dict[int, set[str]] = {}
    episode_numbers_by_series: dict[int, set[int]] = {}
    complete_episode_numbers_by_series: dict[int, set[int]] = {}
    partially_recorded_episode_numbers_by_series: dict[int, set[int]] = {}
    for row in rows:
        series_id = int(row['series_id'])
        episode_number = str(row['episode_number']).strip() if row['episode_number'] is not None else ''
        if episode_number:
            integer_episode_numbers = ExtractIntegerEpisodeNumbers(episode_number)
            episode_numbers_by_series.setdefault(series_id, set()).update(integer_episode_numbers)

            # 同じ自然話数に別局版がある場合、1 件でも完全な録画があれば視聴には困らない。
            # 部分録画しか存在しない自然話数だけを、一覧カードで警告できるよう分けて集計する。
            if bool(row['is_partially_recorded']):
                partially_recorded_episode_numbers_by_series.setdefault(series_id, set()).update(
                    integer_episode_numbers,
                )
            else:
                complete_episode_numbers_by_series.setdefault(series_id, set()).update(integer_episode_numbers)

        # 再放送も同じ自然話数の録画候補なので、完全・部分録画の集計には含める。
        ## 一方、通常の放送曜日・時刻の推定には混ぜず、再放送枠を通常枠と誤認しないようにする。
        if REPEAT_BROADCAST_TITLE_PATTERN.search(str(row['program_title'])) is not None:
            continue

        # 曜日推定は同一話数の別局版も含め、局ごとの連続した放送履歴を見て代表局を決める。
        # 一方でカード用のサムネイルと話数集計は、従来どおり同じ自然話数を一度だけ扱う。
        normal_broadcasts_by_series.setdefault(series_id, []).append(row)

        samples = samples_by_series.setdefault(series_id, [])
        sample_keys = sample_keys_by_series.setdefault(series_id, set())
        thumbnail_samples = thumbnail_samples_by_series.setdefault(series_id, [])
        thumbnail_sample_keys = thumbnail_sample_keys_by_series.setdefault(series_id, set())
        start_time = ParseDatetimeStringToJST(str(row['start_time']))
        sample_key = f'episode:{episode_number}' if episode_number else f'date:{start_time.date().isoformat()}'

        # Series の重ねサムネイルには、代表サムネイルが正常に生成済みの録画だけを採用する。
        ## サムネイル未生成の録画を先に重複扱いすると、同じ話数に生成済みの別録画があっても表示できないため、
        ## サムネイル用の重複判定は放送枠推定用とは分けて管理する。
        if (
            bool(row['has_thumbnail'])
            and len(thumbnail_samples) < 3
            and sample_key not in thumbnail_sample_keys
        ):
            thumbnail_samples.append(row)
            thumbnail_sample_keys.add(sample_key)

        # 同じ自然話数の別局版は Series の重ねサムネイルを重複させない。
        # 話数が取れない番組も、同日の多局同時録画は 1 枚にまとめる。
        if (
            len(samples) < 12
            and sample_key not in sample_keys
        ):
            samples.append(row)
            sample_keys.add(sample_key)

    # 初回放送直後のアニメ・ドラマ・バラエティも掲載するため、未来 EPG の明示的な話数を
    # SeriesIndexer と同じ規則で解析し、次回放送が確認できる Series を控える。
    _, future_program_rows = await connection.execute_query(
        """
        SELECT title, description, genres
        FROM programs
        WHERE start_time > ? AND start_time <= ?
        ORDER BY start_time ASC
        """,
        [now.isoformat(), (now + timedelta(days=8)).isoformat()],
    )
    future_series_titles: set[str] = set()
    for future_program_row in future_program_rows:
        genres = json.loads(str(future_program_row['genres']))
        if {str(genre['major']) for genre in genres}.isdisjoint(ON_AIR_SERIES_GENRES):
            continue
        parsed_title = ParseSeriesTitle(
            str(future_program_row['title']),
            genres,
            str(future_program_row['description']),
        )
        if (
            parsed_title is not None
            and REPEAT_BROADCAST_TITLE_PATTERN.search(str(future_program_row['title'])) is None
        ):
            future_series_titles.add(parsed_title.normalized_title)

    cutoff = now - timedelta(days=21)
    seasons_by_id: dict[str, list[schemas.OnAirSeries]] = {}
    current_season_id: str | None = None

    for series_id, samples in samples_by_series.items():
        # 1 件だけでは周期放送か判断できない。また On Air は作品を追うための一覧なので、
        # 単発の映画・紀行・ドキュメンタリーなどは Series に登録されていても対象外にする。
        genres = json.loads(str(samples[0]['genres']))
        major_genres = {str(genre['major']) for genre in genres}
        if major_genres.isdisjoint(ON_AIR_SERIES_GENRES):
            continue
        parsed_normal_broadcasts = [
            (sample, ParseDatetimeStringToJST(str(sample['start_time'])))
            for sample in normal_broadcasts_by_series[series_id]
        ]
        chronological_broadcasts = sorted(parsed_normal_broadcasts, key=lambda broadcast: broadcast[1])

        # 話数が第 1 話へ戻る放送は、新しいシーズンの開始として以降だけを現在の周期にする。
        # 最終話を別局で後から録画した場合は話数が戻らないため、現在の周期を巻き戻さない。
        current_cycle_start_at: datetime | None = None
        current_cycle_final_broadcasts: list[tuple[dict[str, Any], datetime, set[int]]] = []
        for sample, start_time in chronological_broadcasts:
            episode_numbers = ExtractIntegerEpisodeNumbers(str(sample['episode_number'] or ''))
            is_final_broadcast = FINAL_BROADCAST_TITLE_PATTERN.search(str(sample['program_title'])) is not None
            if not is_final_broadcast and current_cycle_final_broadcasts:
                latest_final_episode_numbers = current_cycle_final_broadcasts[-1][2]
                # 新シーズンは第 1 話へ戻る場合だけでなく、通算話数を継続する場合もある。最終話より
                # 大きい話数は新しい周期の開始として、古い最終話の掲載期限を引き継がない。
                if (
                    episode_numbers == {1}
                    or (
                        latest_final_episode_numbers
                        and episode_numbers
                        and max(episode_numbers) > max(latest_final_episode_numbers)
                    )
                ):
                    current_cycle_start_at = start_time
                    current_cycle_final_broadcasts = []
            if is_final_broadcast:
                current_cycle_final_broadcasts.append((sample, start_time, episode_numbers))

        current_cycle_broadcasts = [
            (sample, start_time)
            for sample, start_time in parsed_normal_broadcasts
            if current_cycle_start_at is None or start_time >= current_cycle_start_at
        ]
        latest_broadcast_at = max(start_time for _, start_time in current_cycle_broadcasts)
        if latest_broadcast_at < cutoff:
            continue

        # 同じ最終話を別局で後から録画しても掲載期限を伸ばさない。現在の周期の最終話だけを見て、
        # 同じ自然話数の最初の終了時刻を採用するため、過去シーズンの最終話にも影響されない。
        if current_cycle_final_broadcasts:
            latest_final_broadcast = max(current_cycle_final_broadcasts, key=lambda broadcast: broadcast[1])
            latest_final_episode_numbers = latest_final_broadcast[2]
            relevant_final_broadcasts = [
                (sample, start_time)
                for sample, start_time, episode_numbers in current_cycle_final_broadcasts
                if episode_numbers == latest_final_episode_numbers
            ]
            final_end_at = min(
                ParseDatetimeStringToJST(str(sample['end_time']))
                for sample, _ in relevant_final_broadcasts
            )
            if now >= GetOnAirFinalExpiry(final_end_at):
                continue

        # 現在の最新話だけを通常枠の根拠にする。複数局で同じ話を録画していれば最も早い放送を選び、
        # 以前の話を多く録画した局や未来 EPG が表示曜日を引き留めないようにする。
        episode_broadcasts = [
            (sample, start_time, ExtractIntegerEpisodeNumbers(str(sample['episode_number'] or '')))
            for sample, start_time in current_cycle_broadcasts
        ]
        natural_episode_numbers = set().union(*(episode_numbers for _, _, episode_numbers in episode_broadcasts))
        if natural_episode_numbers:
            latest_episode_number = max(natural_episode_numbers)
            schedule_sources = [
                (sample, start_time)
                for sample, start_time, episode_numbers in episode_broadcasts
                if latest_episode_number in episode_numbers
            ]
            schedule_source = min(schedule_sources, key=lambda broadcast: (broadcast[1], int(broadcast[0]['id'])))
        else:
            # 話数を持たない音楽・バラエティなどは、単発番組を On Air へ載せないよう二回以上の
            # 異なる放送実績を要件にし、そのうえで最新録画の実際の時刻を表示する。
            if len({start_time for _, start_time in current_cycle_broadcasts}) < 2:
                continue
            schedule_source = max(current_cycle_broadcasts, key=lambda broadcast: (broadcast[1], int(broadcast[0]['id'])))

        # 初回だけの新番組は、今後の同名番組が EPG に存在する場合だけ掲載する。EPG は掲載可否だけに使い、
        # 表示する曜日と時刻は必ず録画済みの初回から取る。
        if natural_episode_numbers == {1} and NormalizeSeriesTitle(str(samples[0]['series_title'])) not in future_series_titles:
            continue
        weekday = schedule_source[1].weekday()
        hour = schedule_source[1].hour
        minute = (schedule_source[1].minute // 5) * 5

        # 現在の季度を特定
        season_id = GetSeasonID(current_cycle_broadcasts)
        current_season_id = season_id

        # OnAirSeries データを作成
        on_air_series_item = schemas.OnAirSeries(
            id = series_id,
            title = str(samples[0]['series_title']),
            thumbnail_recorded_program_ids = [
                int(sample['id']) for sample in thumbnail_samples_by_series.get(series_id, [])
            ],
            channel_ids = list(dict.fromkeys(
                str(sample['channel_id']) for sample in samples if sample['channel_id'] is not None
            )),
            recorded_episodes_count = len(episode_numbers_by_series.get(series_id, set())),
            missing_episodes_count = (
                max(episode_numbers_by_series[series_id])
                - min(episode_numbers_by_series[series_id])
                + 1
                - len(episode_numbers_by_series[series_id])
                if episode_numbers_by_series.get(series_id)
                else 0
            ),
            partially_recorded_episodes_count = len(
                partially_recorded_episode_numbers_by_series.get(series_id, set())
                - complete_episode_numbers_by_series.get(series_id, set())
            ),
            weekday = weekday,
            broadcast_time = f'{hour:02d}:{minute:02d}',
            latest_broadcast_at = latest_broadcast_at,
        )
        seasons_by_id.setdefault(season_id, []).append(on_air_series_item)

        # 過去季度の処理
        # 現在の周期以外の周期を検出して処理
        past_cycles: list[list[tuple[dict[str, Any], datetime]]] = []
        past_cycle: list[tuple[dict[str, Any], datetime]] = []
        past_cycle_final_episodes: set[int] = set()

        for sample, start_time in chronological_broadcasts:
            episode_numbers = ExtractIntegerEpisodeNumbers(str(sample['episode_number'] or ''))
            is_final_broadcast = FINAL_BROADCAST_TITLE_PATTERN.search(str(sample['program_title'])) is not None

            if past_cycle and not is_final_broadcast and past_cycle_final_episodes:
                if (episode_numbers == {1} or
                    (episode_numbers and max(episode_numbers) > max(past_cycle_final_episodes))):
                    past_cycles.append(past_cycle)
                    past_cycle = []
                    past_cycle_final_episodes = set()

            past_cycle.append((sample, start_time))
            if is_final_broadcast:
                past_cycle_final_episodes.update(episode_numbers)

        if past_cycle:
            past_cycles.append(past_cycle)

        # 過去周期を処理（最後の周期は現在の周期なので除外）
        for past_cycle_broadcasts in past_cycles[:-1]:
            if len(past_cycle_broadcasts) < 2:
                continue

            past_season_id = GetSeasonID(past_cycle_broadcasts)
            if past_season_id == season_id:
                continue

            past_weekday, past_broadcast_time = GetHistoricalSeasonWeekday(past_cycle_broadcasts)

            past_on_air_series_item = schemas.OnAirSeries(
                id = series_id,
                title = str(samples[0]['series_title']),
                thumbnail_recorded_program_ids = [
                    int(sample['id']) for sample in thumbnail_samples_by_series.get(series_id, [])
                ],
                channel_ids = list(dict.fromkeys(
                    str(sample['channel_id']) for sample in samples if sample['channel_id'] is not None
                )),
                recorded_episodes_count = len(episode_numbers_by_series.get(series_id, set())),
                missing_episodes_count = (
                    max(episode_numbers_by_series[series_id])
                    - min(episode_numbers_by_series[series_id])
                    + 1
                    - len(episode_numbers_by_series[series_id])
                    if episode_numbers_by_series.get(series_id)
                    else 0
                ),
                partially_recorded_episodes_count = len(
                    partially_recorded_episode_numbers_by_series.get(series_id, set())
                    - complete_episode_numbers_by_series.get(series_id, set())
                ),
                weekday = past_weekday,
                broadcast_time = past_broadcast_time,
                latest_broadcast_at = max(start_time for _, start_time in past_cycle_broadcasts),
            )
            seasons_by_id.setdefault(past_season_id, []).append(past_on_air_series_item)

    # 季度ごとにソートして返す
    seasons: list[schemas.OnAirSeason] = []
    for sid in sorted(seasons_by_id.keys(), reverse=True):
        series_list = seasons_by_id[sid]
        series_list.sort(key=lambda series: (series.weekday, series.broadcast_time, series.title))
        seasons.append(schemas.OnAirSeason(
            season_id = sid,
            season_label = GetSeasonLabel(sid),
            is_current = sid == current_season_id,
            series_list = series_list,
        ))

    return schemas.OnAirSeriesListResponse(
        seasons = seasons,
        current_season_id = current_season_id or '',
    )


async def GetSeriesSummaries(
    query: str = '',
    order: Literal['desc', 'asc'] = 'desc',
    page: int = 1,
    series_id: int | None = None,
) -> schemas.SeriesSummaryList:
    """シリーズ一覧用の軽量な概要だけを取得する。"""

    filter_clauses: list[str] = []
    filter_params: list[Any] = []
    if query:
        filter_clauses.append('(LOWER(s.title) LIKE LOWER(?) OR LOWER(s.description) LIKE LOWER(?))')
        filter_params.extend([f'%{query}%', f'%{query}%'])
    if series_id is not None:
        filter_clauses.append('s.id = ?')
        filter_params.append(series_id)
    where_clause = f'WHERE {" AND ".join(filter_clauses)}' if filter_clauses else ''

    series_query = f"""
        SELECT
            s.id,
            s.title,
            COALESCE((
                SELECT NULLIF(TRIM(rp_description.description), '')
                FROM recorded_programs rp_description
                WHERE rp_description.series_id = s.id
                  AND TRIM(rp_description.description) != ''
                ORDER BY rp_description.start_time DESC, rp_description.id DESC
                LIMIT 1
            ), s.description) AS description,
            s.genres,
            s.bangumi_subject_id,
            s.bangumi_subject_name,
            s.bangumi_subject_name_cn,
            s.bangumi_subject_summary,
            s.bangumi_subject_image_url,
            COALESCE((
                SELECT JSON_GROUP_ARRAY(recent_recorded_programs.id)
                FROM (
                    SELECT distinct_thumbnails.id
                    FROM (
                        SELECT
                            rp_thumbnail.id,
                            rp_thumbnail.start_time,
                            ROW_NUMBER() OVER (
                                PARTITION BY CASE
                                    WHEN NULLIF(TRIM(rp_thumbnail.episode_number), '') IS NOT NULL
                                        THEN 'episode:' || TRIM(rp_thumbnail.episode_number)
                                    ELSE 'date:' || DATE(rp_thumbnail.start_time)
                                END
                                ORDER BY rp_thumbnail.start_time DESC, rp_thumbnail.id DESC
                            ) AS duplicate_rank
                        FROM recorded_programs rp_thumbnail
                        INNER JOIN recorded_videos rv_thumbnail
                            ON rv_thumbnail.recorded_program_id = rp_thumbnail.id
                        WHERE rp_thumbnail.series_id = s.id
                          AND rv_thumbnail.thumbnail_info IS NOT NULL
                    ) AS distinct_thumbnails
                    WHERE distinct_thumbnails.duplicate_rank = 1
                    ORDER BY distinct_thumbnails.start_time DESC, distinct_thumbnails.id DESC
                    LIMIT 3
                ) AS recent_recorded_programs
            ), '[]') AS thumbnail_recorded_program_ids,
            COALESCE((
                SELECT JSON_GROUP_ARRAY(series_channels.channel_id)
                FROM (
                    SELECT rp_channel.channel_id
                    FROM recorded_programs rp_channel
                    WHERE rp_channel.series_id = s.id
                      AND rp_channel.channel_id IS NOT NULL
                    GROUP BY rp_channel.channel_id
                    ORDER BY MAX(rp_channel.start_time) DESC
                ) AS series_channels
            ), '[]') AS channel_ids,
            COALESCE((
                SELECT JSON_GROUP_ARRAY(official_details.value)
                FROM (
                    SELECT DISTINCT detail_entry.value AS value,
                        CASE detail_entry.key
                            WHEN 'ホームページ' THEN 1
                            WHEN '公式サイト' THEN 2
                            WHEN '公式HP' THEN 3
                            WHEN '公式ページ' THEN 4
                            WHEN '番組HP' THEN 5
                            WHEN '番組ホームページ' THEN 6
                            ELSE 7
                        END AS priority
                    FROM recorded_programs rp_website, JSON_EACH(rp_website.detail) AS detail_entry
                    WHERE rp_website.series_id = s.id
                      AND detail_entry.key IN (
                          'ホームページ', '公式サイト', '公式HP', '公式ページ', '番組HP',
                          '番組ホームページ'
                      )
                      AND detail_entry.value LIKE '%http%'
                    ORDER BY priority, rp_website.start_time DESC
                ) AS official_details
            ), '[]') AS official_website_sources,
            COUNT(rp.id) AS recorded_programs_count,
            MAX(rv.file_created_at) AS latest_video_file_created_at,
            s.created_at,
            s.updated_at
        FROM series s
        LEFT JOIN recorded_programs rp ON rp.series_id = s.id
        LEFT JOIN recorded_videos rv ON rv.recorded_program_id = rp.id
        {where_clause}
        GROUP BY s.id
        ORDER BY latest_video_file_created_at {'DESC' if order == 'desc' else 'ASC'},
                 s.id {'DESC' if order == 'desc' else 'ASC'}
        LIMIT ? OFFSET ?
    """
    total_query = f'SELECT COUNT(*) AS count FROM series s {where_clause}'

    try:
        conn = connections.get('default')
        rows = await conn.execute_query(
            series_query,
            [*filter_params, str(PAGE_SIZE), str((page - 1) * PAGE_SIZE)],
        )
        total_result = await conn.execute_query(total_query, filter_params)

        series_list: list[schemas.SeriesSummary] = []
        for row in rows[1]:
            genres = json.loads(row['genres'])
            series_list.append(schemas.SeriesSummary.model_validate({
                **row,
                'genres': genres,
                'thumbnail_recorded_program_ids': json.loads(row['thumbnail_recorded_program_ids']),
                'channel_ids': json.loads(row['channel_ids']),
                'official_website_url': ExtractOfficialWebsiteURL(json.loads(row['official_website_sources'])),
            }))

        return schemas.SeriesSummaryList(
            total = total_result[1][0]['count'],
            series_list = series_list,
        )
    except Exception as ex:
        logging.error('[GetSeriesSummaries] Failed to execute raw SQL query:', exc_info=ex)
        raise HTTPException(
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail = 'Failed to execute raw SQL query',
        )


@router.get(
    '/{series_id}/list-position',
    summary = 'シリーズ番組一覧位置 API',
    response_description = '指定した一覧条件におけるシリーズ番組のページ番号。',
    response_model = schemas.SeriesListPosition,
)
async def SeriesListPositionAPI(
    series_id: Annotated[int, Path(description='シリーズ番組の ID 。')],
    query: Annotated[str, Query(description='検索キーワード。')] = '',
    order: Annotated[Literal['desc', 'asc'], Query(description='ソート順序 (desc or asc) 。')] = 'desc',
):
    """
    シリーズ一覧と同じ検索・並び順におけるページ番号を取得する。

    Args:
        series_id (int): ページ番号を調べるシリーズ番組の ID。
        query (str): シリーズ一覧に適用する検索キーワード。
        order (Literal['desc', 'asc']): シリーズ一覧に適用するソート順序。

    Returns:
        schemas.SeriesListPosition: 指定したシリーズ番組が含まれるページ番号。
    """

    # 深いリンクから開いた場合も一覧と完全に同じ位置へ到達できるよう、
    # 一覧 API と同じ検索条件・最終録画ファイル日時・ID の順序で行番号を付ける。
    filter_clause = ''
    filter_params: list[Any] = []
    if query:
        filter_clause = 'WHERE LOWER(s.title) LIKE LOWER(?) OR LOWER(s.description) LIKE LOWER(?)'
        filter_params.extend([f'%{query}%', f'%{query}%'])
    direction = 'DESC' if order == 'desc' else 'ASC'
    connection = connections.get('default')
    _, rows = await connection.execute_query(
        f"""
        WITH ordered_series AS (
            SELECT
                s.id,
                ROW_NUMBER() OVER (
                    ORDER BY MAX(rv.file_created_at) {direction}, s.id {direction}
                ) AS row_number
            FROM series s
            LEFT JOIN recorded_programs rp ON rp.series_id = s.id
            LEFT JOIN recorded_videos rv ON rv.recorded_program_id = rp.id
            {filter_clause}
            GROUP BY s.id
        )
        SELECT row_number
        FROM ordered_series
        WHERE id = ?
        """,
        [*filter_params, series_id],
    )
    if len(rows) == 0:
        logging.warning(
            f'[SeriesRouter][SeriesListPositionAPI] Specified series_id was not found in the list. '
            f'[series_id: {series_id}]',
        )
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified series_id was not found in the list',
        )
    return schemas.SeriesListPosition(page=((int(rows[0]['row_number']) - 1) // PAGE_SIZE) + 1)


@router.get(
    '/{series_id}/summary',
    summary = 'シリーズ番組概要 API',
    response_description = 'シリーズ番組の概要。',
    response_model = schemas.SeriesSummary,
)
async def SeriesSummaryAPI(
    series_id: Annotated[int, Path(description='シリーズ番組の ID 。')],
):
    """指定されたシリーズの概要だけを取得する。"""

    result = await GetSeriesSummaries(series_id=series_id)
    if not result.series_list:
        logging.warning(f'[SeriesRouter][SeriesSummaryAPI] Specified series_id was not found. [series_id: {series_id}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified series_id was not found',
        )
    return result.series_list[0]


@router.get(
    '/{series_id}',
    summary = 'シリーズ番組 API',
    response_description = 'シリーズ番組。',
    response_model = schemas.Series,
)
async def SeriesAPI(
    series_id: Annotated[int, Path(description='シリーズ番組の ID 。')],
):
    """
    指定されたシリーズ番組を取得する。
    """

    series = await Series.all() \
        .select_related('broadcast_periods') \
        .select_related('broadcast_periods__channel') \
        .select_related('broadcast_periods__recorded_programs') \
        .select_related('broadcast_periods__recorded_programs__recorded_video') \
        .select_related('broadcast_periods__recorded_programs__channel') \
        .get_or_none(id=series_id)
    if series is None:
        logging.warning(f'[SeriesRouter][SeriesAPI] Specified series_id was not found. [series_id: {series_id}]')
        raise HTTPException(
            status_code = status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail = 'Specified series_id was not found',
        )

    return series
