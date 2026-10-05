from __future__ import annotations

import asyncio
import re
import unicodedata
from collections import OrderedDict
from copy import deepcopy
from difflib import SequenceMatcher
from hashlib import sha256
from time import monotonic
from typing import Any, cast
from weakref import WeakValueDictionary

import httpx
from tortoise.exceptions import IntegrityError

from app import logging, schemas
from app.constants import API_REQUEST_HEADERS, HTTPX_CLIENT
from app.metadata.SeriesIndexer import IsStrictSeriesTitlePrefix, NormalizeSeriesTitle
from app.metadata.SeriesMerger import SeriesMerger
from app.models.RecordedProgram import RecordedProgram
from app.models.Series import Series
from app.models.User import User


class BangumiClient:
    """KonomiTV の録画番組を Bangumi の作品とエピソードへ照合する。"""

    API_BASE_URL = 'https://api.bgm.tv/v0'
    COLLECTION_PAGE_SIZE = 100
    EPISODE_PAGE_SIZE = 200
    # EPG の大分類と Bangumi の作品種別は一致しないため、特撮をアニメ・実写の両方から照合する。
    ## 既に Series として整理された連続番組だけを対象とし、ニュース・スポーツ等へは拡張しない。
    SUBJECT_TYPES_BY_GENRE = {
        'アニメ・特撮': {2, 6},
        'ドラマ': {6},
        'ドキュメンタリー・教養': {6},
        'バラエティ': {6},
        '音楽': {6},
    }
    # 放送版の品質差だけを比較キーから取り除き、作品の期・劇場版などの識別情報は保持する。
    REMASTER_SUFFIX_PATTERN = re.compile(r'\s*(?:(?:[248]K|HD|デジタル)\s*)?リマスター版?\s*$', re.IGNORECASE)
    SEQUEL_PREFIX_PATTERN = re.compile(
        r'^(?:第\s*[0-9一二三四五六七八九十]+\s*(?:期|部|章|シーズン|クール)|'
        r'[0-9]+|(?:season|part)\s*[0-9]+|'
        r'[ivx]+\b|続編|続・|劇場版|映画|the\s*movie\b|final\s*season\b|ova\b|oad\b)',
        re.IGNORECASE,
    )
    _sync_tasks: set[asyncio.Task[None]] = set()
    _subject_merge_locks: dict[int, asyncio.Lock] = {}
    # 認証ごとに読み取り結果を隔離し、公開範囲の違うアカウントへ応答を流用しない。
    ## token は保持せずハッシュだけをキーに使う。章追加を反映するため有効期間は 5 分とする。
    READ_CACHE_TTL_SECONDS = 300
    READ_CACHE_MAX_ENTRIES = 512
    _read_cache: OrderedDict[tuple[str, str, tuple[tuple[str, int], ...]], tuple[float, Any]] = OrderedDict()
    # 同一キーの並行取得は直列化し、使われなくなったロックは自動的に解放する。
    _read_locks: WeakValueDictionary[tuple[str, str, tuple[tuple[str, int], ...]], asyncio.Lock] = WeakValueDictionary()


    @classmethod
    async def _getCachedJSON(
        cls, client: httpx.AsyncClient, url: str, access_token: str, params: dict[str, int] | None = None,
    ) -> Any:
        """
        認証付き作品・章・関連作品の読み取りを期限と容量付きでキャッシュする。

        Args:
            client (httpx.AsyncClient): 呼び出し元が管理する HTTP クライアント。
            url (str): 読み取り先 URL。
            access_token (str): 連携アカウントの認証 token。
            params (dict[str, int] | None): 章一覧のページ指定等。

        Returns:
            Any: 呼び出し元の変更がキャッシュへ波及しない JSON のコピー。

        Raises:
            httpx.HTTPError: 取得に失敗した場合。失敗応答はキャッシュしない。
        """

        key = (sha256(access_token.encode()).hexdigest(), url, tuple(sorted((params or {}).items())))
        lock = cls._read_locks.setdefault(key, asyncio.Lock())
        async with lock:
            now = monotonic()
            # 期限切れの応答は削除し、認証失効や新しい章を定期的に再検証する。
            for expired_key, (expires_at, _) in list(cls._read_cache.items()):
                if expires_at <= now:
                    del cls._read_cache[expired_key]
            if key in cls._read_cache:
                cls._read_cache.move_to_end(key)
                return deepcopy(cls._read_cache[key][1])
            response = await client.get(
                url = url,
                headers = {**API_REQUEST_HEADERS, 'Authorization': f'Bearer {access_token}'},
                params = params,
            )
            response.raise_for_status()
            payload = response.json()
            # 成功した JSON だけ保存し、最近使われていない項目から容量上限まで追い出す。
            cls._read_cache[key] = (monotonic() + cls.READ_CACHE_TTL_SECONDS, deepcopy(payload))
            while len(cls._read_cache) > cls.READ_CACHE_MAX_ENTRIES:
                cls._read_cache.popitem(last=False)
            return payload


    @staticmethod
    def parseEpisodeNumber(episode_number: str | None) -> int | None:
        """
        自動同期で安全に扱える単一の正整数話数だけを取得する。

        Args:
            episode_number (str | None): EPG から抽出した話数。

        Returns:
            int | None: 単一の正整数であればその値、それ以外は None。
        """

        if episode_number is None or episode_number.isdecimal() is False:
            return None
        parsed_number = int(episode_number)
        return parsed_number if parsed_number > 0 else None


    @staticmethod
    def _normalizeSubjectTitle(title: str) -> str:
        """
        ローカル Series と Bangumi 作品のタイトル比較キーを生成する。

        Args:
            title (str): ローカル Series または Bangumi 作品のタイトル。

        Returns:
            str: Unicode・空白・末尾句読点・リマスター表記の差を吸収した比較キー。
        """

        # EPG と Bangumi では長い作品名の末尾句点だけが欠けることがあるため、その差だけを追加で吸収する。
        normalized_title = unicodedata.normalize('NFKC', title)
        normalized_title = BangumiClient.REMASTER_SUFFIX_PATTERN.sub('', normalized_title)
        return NormalizeSeriesTitle(normalized_title).rstrip('。.!！?？')


    @classmethod
    def _isSubjectTitlePrefix(cls, short_title: str, long_title: str) -> bool:
        """
        Bangumi 照合に限り、記号または空白で副題を区切る短縮名を比較する。

        Args:
            short_title (str): 副題を省略した作品名。
            long_title (str): 副題を含む作品名。

        Returns:
            bool: 続編ではない明示的な副題境界が確認できた場合に True。
        """

        short_key = cls._normalizeSubjectTitle(short_title)
        long_key = cls._normalizeSubjectTitle(long_title)
        if not short_key:
            return False
        if IsStrictSeriesTitlePrefix(short_key, long_key):
            return True

        # 空白を消す前に日本語主題の終わりを確認し、英語作品名の単語間空白を副題と誤認しない。
        ## 短すぎる汎用名と、数字・期・劇場版を後置した別作品は弱い候補にも含めない。
        title_parts = unicodedata.normalize('NFKC', long_title).strip().split(maxsplit=1)
        return (
            len(title_parts) == 2 and len(short_key) >= 4 and
            re.search(r'[ぁ-んァ-ヶ一-龯]', short_key) is not None and
            cls._normalizeSubjectTitle(title_parts[0]) == short_key and
            cls.SEQUEL_PREFIX_PATTERN.match(title_parts[1]) is None
        )


    @classmethod
    def _scoreSubjectTitle(cls, series_title: str, subject: dict[str, Any]) -> int:
        """
        ユーザーのコレクションに登録されたアニメ・実写からローカル Series の作品候補を採点する。

        Args:
            series_title (str): ローカル Series の表示タイトル。
            subject (dict[str, Any]): Bangumi コレクション一覧に含まれる作品概要。

        Returns:
            int: タイトル一致度。候補外の場合は 0。
        """

        local_title = cls._normalizeSubjectTitle(series_title)
        original_subject_titles = [
            str(subject.get('name', '')),
            str(subject.get('name_cn', '')),
        ]
        subject_titles = {cls._normalizeSubjectTitle(title) for title in original_subject_titles} - {''}
        if not local_title or len(subject_titles) == 0:
            return 0
        if local_title in subject_titles:
            return 100

        # 長い主題は期や劇場版の追加後も類似率が高いため、明示的な別作品表記を fuzzy 判定より先に拒否する。
        for subject_title in subject_titles:
            short_key, long_key = sorted((local_title, subject_title), key=len)
            if long_key.startswith(short_key):
                edition_suffix = long_key[len(short_key):].lstrip('~～-―—:：「『【')
                if cls.SEQUEL_PREFIX_PATTERN.match(edition_suffix) is not None:
                    return 0

        # 放送局が副題を省略した表記は、安全な副題境界を持つ場合だけ弱い候補として認める。
        if any(
            cls._isSubjectTitlePrefix(series_title, subject_title) or
            cls._isSubjectTitlePrefix(subject_title, series_title)
            for subject_title in original_subject_titles
        ):
            return 80

        # コレクション一覧に絞った後も僅かな記号・転写差が残るため、長いタイトル同士だけ保守的に類似判定する。
        similarity = max(
            (SequenceMatcher(None, local_title, subject_title).ratio() for subject_title in subject_titles),
            default = 0.0,
        )
        if min([len(local_title), *(len(subject_title) for subject_title in subject_titles)]) >= 8 and similarity >= 0.9:
            return int(similarity * 75)
        return 0


    @staticmethod
    def isPlaybackCompleted(
        playback_position: float,
        duration: float,
        cm_sections: list[schemas.CMSection] | None,
    ) -> bool:
        """
        プレイヤーが報告した実再生時間から Bangumi の視聴完了を判定する。

        Args:
            playback_position (float): 現在の再生位置 (秒)。
            duration (float): サーバーに保存されている録画時間 (秒)。
            cm_sections (list[schemas.CMSection] | None): サーバーで検出済みの CM 区間。

        Returns:
            bool: CM 区間を考慮した視聴完了位置まで再生済みなら True。
        """

        return playback_position >= schemas.GetPlaybackCompletionThreshold(duration, cm_sections)


    @classmethod
    def findSubject(cls, series_title: str, subjects: list[dict[str, Any]]) -> dict[str, Any] | None:
        """
        ユーザーの視聴中・視聴済みのコレクションからローカル Series に対応する作品を一意に選ぶ。

        Args:
            series_title (str): ローカル Series の表示タイトル。
            subjects (list[dict[str, Any]]): 視聴中・視聴済みのアニメ・実写作品一覧。

        Returns:
            dict[str, Any] | None: 十分に一意な最高得点候補。
        """

        candidates = sorted(
            (
                (cls._scoreSubjectTitle(series_title, subject), subject)
                for subject in subjects
            ),
            key = lambda candidate: candidate[0],
            reverse = True,
        )
        if len(candidates) == 0 or candidates[0][0] < 67:
            return None
        if len(candidates) >= 2 and candidates[0][0] - candidates[1][0] < 10:
            return None
        return candidates[0][1]


    @classmethod
    async def _getCollectionSubjects(cls, user: User) -> list[dict[str, Any]]:
        """
        連携ユーザーの「視聴中」「視聴済み」アニメ・実写作品をコレクション一覧から一括取得する。

        Args:
            user (User): Bangumi アカウント連携済みの KonomiTV ユーザー。

        Returns:
            list[dict[str, Any]]: 作品 ID ごとに重複を除いたコレクションに登録されたアニメ・実写概要。

        Raises:
            httpx.HTTPError: Bangumi API への接続または HTTP エラーが発生した場合。
        """

        assert user.bangumi_user_name is not None
        access_token = user.decryptBangumiAccessToken()
        headers = {**API_REQUEST_HEADERS, 'Authorization': f'Bearer {access_token}'}
        subjects: dict[int, dict[str, Any]] = {}
        offset = 0
        async with HTTPX_CLIENT() as httpx_client:
            while True:
                # コレクション状態・作品種別を API 側で分割せず全件取得し、視聴中・視聴済みのアニメ・実写だけをローカルで選ぶ。
                ## これにより、ユーザーごとの候補一覧はページ数分のリクエストだけで揃う。
                response = await httpx_client.get(
                    url = f'{cls.API_BASE_URL}/users/{user.bangumi_user_name}/collections',
                    headers = headers,
                    params = {
                        'limit': cls.COLLECTION_PAGE_SIZE,
                        'offset': offset,
                    },
                )
                response.raise_for_status()
                payload = cast(dict[str, Any], response.json())
                collections = cast(list[dict[str, Any]], payload.get('data', []))
                for collection in collections:
                    if int(collection.get('type', -1)) not in {2, 3}:
                        continue
                    subject = collection.get('subject')
                    if not isinstance(subject, dict) or int(subject.get('type', -1)) not in {2, 6}:
                        continue
                    subjects[int(subject['id'])] = cast(dict[str, Any], subject)

                offset += len(collections)
                if offset >= int(payload.get('total', 0)) or len(collections) == 0:
                    break
        return list(subjects.values())


    @classmethod
    async def _getEpisodes(cls, subject_id: int, access_token: str) -> list[dict[str, Any]]:
        """
        照合済み Bangumi 作品の通常エピソードを全ページ取得する。

        Args:
            subject_id (int): Bangumi 作品 ID。
            access_token (str): Bangumi 個人アクセストークン。

        Returns:
            list[dict[str, Any]]: 作品に属する通常エピソード。

        Raises:
            httpx.HTTPError: Bangumi API への接続または HTTP エラーが発生した場合。
        """

        episodes: list[dict[str, Any]] = []
        offset = 0
        async with HTTPX_CLIENT() as httpx_client:
            while True:
                try:
                    # NSFW 作品は匿名アクセスを 404 に偽装するため、コレクション一覧と同じ認証を必ず引き継ぐ。
                    payload = await cls._getCachedJSON(
                        httpx_client, f'{cls.API_BASE_URL}/episodes', access_token,
                        {'subject_id': subject_id, 'type': 0, 'limit': cls.EPISODE_PAGE_SIZE, 'offset': offset},
                    )
                # 認証後も閲覧できない作品、または削除済み作品だけ episode 未照合として扱う。
                except httpx.HTTPStatusError as ex:
                    if ex.response.status_code != 404:
                        raise
                    logging.warning(
                        f'[BangumiClient][_getEpisodes] Bangumi subject was not found. '
                        f'[subject_id: {subject_id}]',
                    )
                    return []
                page_episodes = cast(list[dict[str, Any]], payload.get('data', []))
                episodes.extend(page_episodes)
                offset += len(page_episodes)
                if offset >= int(payload.get('total', 0)) or len(page_episodes) == 0:
                    break
        return episodes


    @classmethod
    async def syncUserCollections(cls, user: User) -> int:
        """
        連携ユーザーのコレクション一覧を候補プールとしてローカル Series と全録画を照合する。

        Args:
            user (User): Bangumi アカウント連携済みの KonomiTV ユーザー。

        Returns:
            int: 今回 Bangumi 作品へ照合できた Series 数。

        Raises:
            httpx.HTTPError: Bangumi API への接続または HTTP エラーが発生した場合。
        """

        eligible_series = [
            series for series in await Series.all()
            if any(genre['major'] in cls.SUBJECT_TYPES_BY_GENRE for genre in series.genres)
        ]
        # ローカルに照合対象の Series が一件もなければ、Bangumi API 自体へアクセスしない。
        if len(eligible_series) == 0:
            return 0

        subjects = await cls._getCollectionSubjects(user)
        access_token = user.decryptBangumiAccessToken()
        episodes_by_subject_id: dict[int, list[dict[str, Any]]] = {}
        matched_series_ids: set[int] = set()

        for series in eligible_series:
            # 先行する同期処理がこの Series を最古の主レコードへ統合済みの場合は、
            ## 取得済みリストに残る削除済みオブジェクトを再処理しない。
            if await Series.filter(id=series.id).exists() is False:
                continue

            # すでに作品が確定している Series は、別ユーザーのコレクション表記で上書きしない。
            subject_types = {
                subject_type for genre in series.genres
                for subject_type in cls.SUBJECT_TYPES_BY_GENRE.get(genre['major'], set())
            }
            candidate_subjects = [subject for subject in subjects if int(subject.get('type', -1)) in subject_types]
            subject = next(
                (subject for subject in subjects if int(subject['id']) == series.bangumi_subject_id),
                None,
            ) if series.bangumi_subject_id is not None else cls.findSubject(series.title, candidate_subjects)
            if subject is None:
                continue
            subject_id = int(subject['id'])

            # コレクション一覧が返す SlimSubject を永続化すると同時に、同じ作品 ID に照合済みの Series を統合する。
            ## ロック中に外部 API は呼ばず、異なるユーザーの定期同期が重なっても subject ごとに直列化する。
            images = subject.get('images')
            image_url = str(images.get('large') or images.get('common') or '') if isinstance(images, dict) else ''
            subject_merge_lock = cls._subject_merge_locks.setdefault(subject_id, asyncio.Lock())
            async with subject_merge_lock:
                try:
                    canonical_series = await SeriesMerger.mergeByBangumiSubject(
                        series_id = series.id,
                        subject_id = subject_id,
                        subject_name = str(subject.get('name', '')) or None,
                        subject_name_cn = str(subject.get('name_cn', '')) or None,
                        subject_summary = str(subject.get('short_summary', '')) or None,
                        subject_image_url = image_url or None,
                    )
                except (IntegrityError, ValueError):
                    # 別プロセスが同じ subject を先に統合した場合は、一意索引の衝突または削除済み ID として観測される。
                    ## トランザクションのロールバック後に確定済みの主 Series を読み直し、外部 API を再試行しない。
                    canonical_series = await Series.get_or_none(bangumi_subject_id=subject_id)
                    if canonical_series is None:
                        raise
            matched_series_ids.add(canonical_series.id)

        # 作品の照合結果をすべて保存してから章を補完する。
        ## 既存作品の続編 API が接続タイムアウトしても、新番組の作品照合を妨げない。
        for series_id in sorted(matched_series_ids):
            canonical_series = await Series.get_or_none(id=series_id)
            # 別ユーザーの同期で統合された Series は、次回の同期で主レコードを処理する。
            if canonical_series is None or canonical_series.bangumi_subject_id is None:
                continue
            subject_id = canonical_series.bangumi_subject_id
            recorded_programs = await RecordedProgram.filter(series_id=canonical_series.id).all()
            # 保存済み ID も再検証し、旧 sort + 1 規則の誤照合を次の同期で収束させる。
            if recorded_programs and subject_id not in episodes_by_subject_id:
                try:
                    episodes_by_subject_id[subject_id] = await cls._getEpisodes(subject_id, access_token)
                except (httpx.HTTPError, ValueError) as ex:
                    # 外部取得の失敗を空の章一覧として扱わず、保存済みの照合結果を保持して次の作品へ進む。
                    logging.warning(
                        f'[BangumiClient][syncUserCollections] Failed to fetch episodes. '
                        f'[konomitv_user_id: {user.id}, series_id: {series_id}, subject_id: {subject_id}]',
                        exc_info = ex,
                    )
                    continue

            # Series の主条目を上書きせず、録画ごとに実際の放送期の条目と章を保存する。
            for recorded_program in recorded_programs:
                try:
                    await cls.resolveRecordedEpisode(recorded_program, canonical_series, access_token, episodes_by_subject_id)
                except (httpx.HTTPError, ValueError) as ex:
                    # 一件の続編探索に失敗しても、他の録画と作品の章補完は継続する。
                    ## 失敗した録画は書き換えず、次回の定期同期で再検証する。
                    logging.warning(
                        f'[BangumiClient][syncUserCollections] Failed to resolve recorded episode. '
                        f'[konomitv_user_id: {user.id}, series_id: {series_id}, subject_id: {subject_id}, '
                        f'video_id: {recorded_program.id}]',
                        exc_info = ex,
                    )
        return len(matched_series_ids)


    @classmethod
    async def syncAllLinkedUsers(cls) -> None:
        """
        Bangumi 連携済みユーザーごとにコレクション一覧を取得し、ローカル Series へ反映する。

        Returns:
            None
        """

        users = await User.all().exclude(bangumi_user_name=None).exclude(bangumi_access_token=None)
        for user in users:
            try:
                matched_series_count = await cls.syncUserCollections(user)
                logging.info(
                    f'[BangumiClient][syncAllLinkedUsers] Synchronized Bangumi collections. '
                    f'[konomitv_user_id: {user.id}, matched_series: {matched_series_count}]',
                )
            except (httpx.HTTPError, ValueError) as ex:
                logging.error(
                    f'[BangumiClient][syncAllLinkedUsers] Failed to synchronize Bangumi collections. '
                    f'[konomitv_user_id: {user.id}]',
                    exc_info = ex,
                )


    @classmethod
    def scheduleUserCollectionSync(cls, user: User) -> None:
        """
        アカウント連携直後のコレクション一覧同期を API レスポンスと切り離して開始する。

        Args:
            user (User): Bangumi アカウント連携済みの KonomiTV ユーザー。

        Returns:
            None
        """

        async def Sync() -> None:
            try:
                matched_series_count = await cls.syncUserCollections(user)
                logging.info(
                    f'[BangumiClient][scheduleUserCollectionSync] Synchronized Bangumi collections. '
                    f'[konomitv_user_id: {user.id}, matched_series: {matched_series_count}]',
                )
            except (httpx.HTTPError, ValueError) as ex:
                logging.error(
                    f'[BangumiClient][scheduleUserCollectionSync] Failed to synchronize Bangumi collections. '
                    f'[konomitv_user_id: {user.id}]',
                    exc_info = ex,
                )

        # 実行中タスクへの強参照を保持し、完了時だけ集合から取り除く。
        task = asyncio.create_task(Sync())
        cls._sync_tasks.add(task)
        task.add_done_callback(cls._sync_tasks.discard)


    @staticmethod
    def _findEpisode(episodes: list[dict[str, Any]], episode_number: int) -> dict[str, Any] | None:
        """
        Bangumi の対象作品から EPG 話数に対応する通常エピソードを取得する。

        Args:
            episodes (list[dict[str, Any]]): Bangumi 作品のエピソード。
            episode_number (int): EPG から抽出した話数。

        Returns:
            dict[str, Any] | None: 通算 sort を優先し、なければ ep が一意に一致する通常エピソード。
        """

        # sort は 1 始まりの通算話数であり、配列インデックスではない。
        regular_episodes = [episode for episode in episodes if int(episode.get('type', -1)) == 0]
        matched_episodes = [episode for episode in regular_episodes if float(episode.get('sort') or -1) == episode_number]
        if not matched_episodes:
            matched_episodes = [episode for episode in regular_episodes if float(episode.get('ep') or -1) == episode_number]
        if len(matched_episodes) != 1:
            return None
        return matched_episodes[0]


    @classmethod
    async def resolveRecordedEpisode(
        cls,
        recorded_program: RecordedProgram,
        series: Series,
        access_token: str,
        episodes_by_subject_id: dict[int, list[dict[str, Any]]] | None = None,
    ) -> None:
        """
        主条目と明示的な続編から録画の正しい章を解決し、既存の誤照合も更新する。

        Args:
            recorded_program (RecordedProgram): 再検証する録画。
            series (Series): ローカル Series の主条目。
            access_token (str): 連携ユーザーのトークン。
            episodes_by_subject_id (dict | None): 一括同期中だけ共有する章キャッシュ。

        Returns:
            None: 録画単位の照合結果を保存する。外部視聴状態は更新しない。
        """

        subject_id = series.bangumi_subject_id
        if subject_id is None:
            return
        cache = episodes_by_subject_id if episodes_by_subject_id is not None else {}
        episode_number = cls.parseEpisodeNumber(recorded_program.episode_number)
        resolved_subject_id = subject_id
        resolved_episode_id: int | None = None
        if episode_number is not None:
            if subject_id not in cache:
                cache[subject_id] = await cls._getEpisodes(subject_id, access_token)
            episode = cls._findEpisode(cache[subject_id], episode_number)
            if episode is not None:
                resolved_episode_id = int(episode['id'])
            elif not any(
                int(candidate.get('type', -1)) == 0 and (
                    float(candidate.get('sort') or -1) == episode_number
                    or float(candidate.get('ep') or -1) == episode_number
                )
                for candidate in cache[subject_id]
            ):
                # 続編の局所話数 ep=1 は主条目の第 1 話とは異なるため、続編では sort だけを比較する。
                matches: list[tuple[int, int]] = []
                pending = [subject_id]
                visited = {subject_id}
                season_suffix_pattern = r'\s*(?:第\s*\d+\s*期|season\s*\d+)\s*$'
                main_name = re.sub(
                    season_suffix_pattern, '', series.bangumi_subject_name or series.title, flags=re.IGNORECASE,
                )
                async with HTTPX_CLIENT() as httpx_client:
                    # EPG のジャンルだけではアニメ・特撮の区別ができないため、主条目の種別を正とする。
                    subject_payload = await cls._getCachedJSON(
                        httpx_client, f'{cls.API_BASE_URL}/subjects/{subject_id}', access_token,
                    )
                    subject_type = int(subject_payload['type'])
                    while pending:
                        parent_id = pending.pop(0)
                        relations = cast(list[dict[str, Any]], await cls._getCachedJSON(
                            httpx_client, f'{cls.API_BASE_URL}/subjects/{parent_id}/subjects', access_token,
                        ))
                        for relation in relations:
                            # 明示的な続編・同種別・期番号を除いた同名作品だけを辿る。劇場版等は含めない。
                            sequel_name = str(relation.get('name', ''))
                            base_name = re.sub(
                                season_suffix_pattern, '', sequel_name, flags=re.IGNORECASE,
                            )
                            if (
                                relation.get('relation') != '续集'
                                or int(relation.get('type', -1)) != subject_type
                                or cls._normalizeSubjectTitle(base_name) != cls._normalizeSubjectTitle(main_name)
                            ):
                                continue
                            sequel_id = int(relation['id'])
                            if sequel_id in visited:
                                continue
                            # 外部の循環・異常に大きい関係グラフでは、部分探索で一意と決めず未照合にする。
                            if len(visited) >= 16:
                                raise ValueError('Bangumi sequel graph exceeds the mapping limit')
                            visited.add(sequel_id)
                            pending.append(sequel_id)
                            if sequel_id not in cache:
                                cache[sequel_id] = await cls._getEpisodes(sequel_id, access_token)
                            matches.extend(
                                (sequel_id, int(candidate['id'])) for candidate in cache[sequel_id]
                                if int(candidate.get('type', -1)) == 0 and float(candidate.get('sort') or -1) == episode_number
                            )
                if len(matches) == 1:
                    resolved_subject_id, resolved_episode_id = matches[0]

        # subject と episode は組として保存し、Series 主条目への一括上書きを行わない。
        if (recorded_program.bangumi_subject_id, recorded_program.bangumi_episode_id) != (resolved_subject_id, resolved_episode_id):
            recorded_program.bangumi_subject_id = resolved_subject_id
            recorded_program.bangumi_episode_id = resolved_episode_id
            await recorded_program.save(update_fields=['bangumi_subject_id', 'bangumi_episode_id'])
