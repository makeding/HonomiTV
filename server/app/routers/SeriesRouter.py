
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


def GetBroadcastSeasonID(start_time: datetime, episode_number: str) -> str:
    """
    放送日と初回先行放送の境界から掲載季度を決める。

    Args:
        start_time (datetime): JST の放送開始日時。
        episode_number (str): 正規化された話数。

    Returns:
        str: YYYY-MM 形式の季度 ID。
    """

    # 季度開始前 7 日の第 1 話だけを次季度へ送る。旧番の最終話は移動しない。
    season_date = start_time
    if ExtractIntegerEpisodeNumbers(episode_number) == {1}:
        shifted = start_time + timedelta(days=7)
        if ((shifted.month - 1) // 3, shifted.year) != ((start_time.month - 1) // 3, start_time.year):
            season_date = shifted
    quarter_month = ((season_date.month - 1) // 3) * 3 + 1
    return f'{season_date.year}-{quarter_month:02d}'


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
    response_model = schemas.OnAirSeriesListResponse,
)
async def OnAirSeriesListAPI():
    """
    各季度の非再放送録画から、各 Series の最新話が最初に放送された曜日と時刻を取得する。

    Args:
        なし。

    Returns:
        schemas.OnAirSeriesListResponse: 現在の放送中作品と、完結作品を含む季度別の履歴。
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
    # 季度の一覧は全履歴から作る。現在の掲載期限で先に絞ると、完結作品が過去季度からも消える。
    rows_by_series: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        rows_by_series.setdefault(int(row['series_id']), []).append(row)

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
    current_season_id = f'{now.year}-{((now.month - 1) // 3) * 3 + 1:02d}'
    seasons_by_id: dict[str, list[schemas.OnAirSeries]] = {}
    dates_by_season: dict[str, list[datetime]] = {}

    for series_id, series_rows in rows_by_series.items():
        # 映画などの単発番組は季度の履歴にも含めない。
        genres = json.loads(str(series_rows[0]['genres']))
        if {str(genre['major']) for genre in genres}.isdisjoint(ON_AIR_SERIES_GENRES):
            continue
        broadcasts_by_season: dict[str, list[tuple[dict[str, Any], datetime]]] = {}
        normal_rows = [
            row for row in series_rows
            if REPEAT_BROADCAST_TITLE_PATTERN.search(str(row['program_title'])) is None
        ]
        for row in normal_rows:
            start_time = ParseDatetimeStringToJST(str(row['start_time']))
            season_id = GetBroadcastSeasonID(start_time, str(row['episode_number'] or ''))
            broadcasts_by_season.setdefault(season_id, []).append((row, start_time))

        # 最新季度は「現在放送中」の一覧でもある。まだ今季度の録画がない長期番組も、
        # 前季度の直近放送と明示的な完結情報から判定し、月初だけ消えることを防ぐ。
        if normal_rows:
            broadcasts_by_season[current_season_id] = [
                (row, ParseDatetimeStringToJST(str(row['start_time']))) for row in normal_rows
            ]

        for season_id, broadcasts in broadcasts_by_season.items():
            is_current = season_id == current_season_id
            latest_broadcast_at = max(start_time for _, start_time in broadcasts)
            # 直近放送と完結期限は現在季度だけに適用し、過去の履歴を削らない。
            if is_current and latest_broadcast_at < cutoff:
                continue
            chronological = sorted(broadcasts, key=lambda broadcast: broadcast[1])
            cycle_start = chronological[0][1]
            final_broadcasts: list[tuple[dict[str, Any], datetime, set[int]]] = []
            for sample, start_time in chronological:
                numbers = ExtractIntegerEpisodeNumbers(str(sample['episode_number'] or ''))
                is_final = FINAL_BROADCAST_TITLE_PATTERN.search(str(sample['program_title'])) is not None
                # 最終回の後に第 1 話または通算話数の続きが来たら、新しい放送周期として判定する。
                if not is_final and final_broadcasts:
                    final_numbers = final_broadcasts[-1][2]
                    if numbers == {1} or (numbers and final_numbers and max(numbers) > max(final_numbers)):
                        cycle_start = start_time
                        final_broadcasts = []
                if is_final:
                    final_broadcasts.append((sample, start_time, numbers))
            if is_current and final_broadcasts:
                latest_final_numbers = final_broadcasts[-1][2]
                final_end = min(
                    ParseDatetimeStringToJST(str(sample['end_time']))
                    for sample, _, numbers in final_broadcasts if numbers == latest_final_numbers
                )
                if now >= GetOnAirFinalExpiry(final_end):
                    continue

            schedule_broadcasts = [
                (sample, start_time) for sample, start_time in broadcasts
                if not is_current or start_time >= cycle_start
            ]
            natural_numbers = set().union(*(
                ExtractIntegerEpisodeNumbers(str(sample['episode_number'] or ''))
                for sample, _ in schedule_broadcasts
            ))
            # 最新話の最初の放送を代表枠にし、遅い別局版で曜日を変えない。
            if natural_numbers:
                latest_number = max(natural_numbers)
                schedule_source = min(
                    ((sample, start_time) for sample, start_time in schedule_broadcasts
                     if latest_number in ExtractIntegerEpisodeNumbers(str(sample['episode_number'] or ''))),
                    key=lambda broadcast: (broadcast[1], int(broadcast[0]['id'])),
                )
            else:
                # 話数のない番組は別日時の放送が二回以上ある場合だけ掲載する。
                if len({start_time for _, start_time in schedule_broadcasts}) < 2:
                    continue
                schedule_source = max(schedule_broadcasts, key=lambda broadcast: (broadcast[1], int(broadcast[0]['id'])))
            # 現在の初回だけの作品は次回 EPG で確認する。過去季度には未来 EPG を要求しない。
            if (is_current and natural_numbers == {1}
                and NormalizeSeriesTitle(str(series_rows[0]['series_title'])) not in future_series_titles):
                continue

            # 話数・局・サムネイルは選択季度の録画に限定する。完全な別局版・再放送も
            # 同一話数の視聴候補として扱うが、放送枠の推定には再放送を使わない。
            season_rows = [
                row for row in series_rows
                if (
                    ParseDatetimeStringToJST(str(row['start_time'])) >= cycle_start
                    if is_current else
                    GetBroadcastSeasonID(ParseDatetimeStringToJST(str(row['start_time'])),
                                         str(row['episode_number'] or '')) == season_id
                )
            ]
            episode_numbers: set[int] = set()
            complete_numbers: set[int] = set()
            partial_numbers: set[int] = set()
            thumbnail_ids: list[int] = []
            thumbnail_keys: set[str] = set()
            for row in season_rows:
                numbers = ExtractIntegerEpisodeNumbers(str(row['episode_number'] or ''))
                episode_numbers.update(numbers)
                if bool(row['is_partially_recorded']):
                    partial_numbers.update(numbers)
                else:
                    complete_numbers.update(numbers)
                start_time = ParseDatetimeStringToJST(str(row['start_time']))
                thumbnail_key = str(row['episode_number'] or start_time.date().isoformat())
                # 未生成の画像で重複扱いにせず、生成済みの別局版を選べるようにする。
                if (bool(row['has_thumbnail']) and len(thumbnail_ids) < 3
                    and thumbnail_key not in thumbnail_keys
                    and REPEAT_BROADCAST_TITLE_PATTERN.search(str(row['program_title'])) is None):
                    thumbnail_ids.append(int(row['id']))
                    thumbnail_keys.add(thumbnail_key)
            schedule_time = schedule_source[1]
            seasons_by_id.setdefault(season_id, []).append(schemas.OnAirSeries(
                id = series_id,
                title = str(series_rows[0]['series_title']),
                thumbnail_recorded_program_ids = thumbnail_ids,
                channel_ids = list(dict.fromkeys(
                    str(row['channel_id']) for row in season_rows if row['channel_id'] is not None
                )),
                recorded_episodes_count = len(episode_numbers),
                missing_episodes_count = (
                    max(episode_numbers) - min(episode_numbers) + 1 - len(episode_numbers)
                    if episode_numbers else 0
                ),
                partially_recorded_episodes_count = len(partial_numbers - complete_numbers),
                weekday = schedule_time.weekday(),
                broadcast_time = f'{schedule_time.hour:02d}:{(schedule_time.minute // 5) * 5:02d}',
                latest_broadcast_at = latest_broadcast_at,
            ))
            dates_by_season.setdefault(season_id, []).extend(
                start_time for _, start_time in schedule_broadcasts
            )

    # 最新録画の集合ではなく、掲載された全放送の最初・最後から実際の期間を返す。
    seasons = [
        schemas.OnAirSeason(
            season_id = season_id,
            season_label = GetSeasonLabel(season_id),
            is_current = season_id == current_season_id,
            start_date = min(dates_by_season[season_id]).date(),
            end_date = max(dates_by_season[season_id]).date(),
            series_list = sorted(series_list, key=lambda series: (series.weekday, series.broadcast_time, series.title)),
        )
        for season_id, series_list in sorted(seasons_by_id.items(), reverse=True)
    ]
    return schemas.OnAirSeriesListResponse(
        seasons = seasons,
        current_season_id = current_season_id,
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
