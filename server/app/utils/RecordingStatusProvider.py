from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import PurePath, PurePosixPath
from typing import Any, Literal

import httpx

from app import logging
from app.config import ServerSettings
from app.constants import HTTPX_CLIENT, JST
from app.utils import NormalizeToJSTDatetime
from app.utils.edcb import ReserveDataRequired
from app.utils.edcb.CtrlCmdUtil import CtrlCmdUtil


EPGSTATION_API_TIMEOUT_SECONDS = 5.0
EPGSTATION_RECORDED_SYNC_PAGE_LIMIT = 20


@dataclass(slots=True)
class ActiveRecordingFilePaths:
    """
    録画バックエンドが把握している録画中ファイルパスの一覧。

    Attributes:
        paths: 録画中と判定されたファイルパスの集合。
        backend: 実際に問い合わせに使ったバックエンド。
        is_reliable: バックエンドへ正常に問い合わせでき、paths が現在の録画中ファイル一覧として信頼できる場合は True。
    """

    paths: set[str]
    backend: Literal['EDCB', 'EPGStation', 'FileSystem']
    is_reliable: bool


@dataclass(slots=True)
class RecentRecordedFilePaths:
    """EPGStation が把握している直近の録画済みファイルパス一覧。"""

    paths: set[str]
    total: int
    is_reliable: bool


def _NormalizePathLikeString(path: str) -> str:
    """
    OS やバックエンドごとの区切り文字差異を吸収した比較用パス文字列を返す。

    Args:
        path (str): 正規化するパス文字列。

    Returns:
        str: 比較用に正規化されたパス文字列。
    """

    return path.replace('\\', '/').rstrip('/')


def _GetPathBasename(path: str) -> str:
    """
    OS 依存のパス区切り差異を吸収してファイル名だけを取得する。

    Args:
        path (str): ファイルパスまたはファイル名。

    Returns:
        str: ファイル名。
    """

    return PurePath(_NormalizePathLikeString(path)).name


def _IsAbsolutePathLikeString(path: str) -> bool:
    """
    OS 非依存で絶対パスらしい文字列かを判定する。

    Args:
        path (str): 判定対象のパス文字列。

    Returns:
        bool: 絶対パスらしい場合は True。
    """

    normalized_path = _NormalizePathLikeString(path)
    return (
        normalized_path.startswith('/') or
        (len(normalized_path) >= 3 and normalized_path[1] == ':' and normalized_path[2] == '/')
    )


def _ExpandRecordingPathCandidates(path: str, config: ServerSettings) -> set[str]:
    """
    録画バックエンドが返したパス文字列を KonomiTV 側の録画フォルダに対する候補パスへ展開する。

    Args:
        path (str): 録画バックエンドが返したパス文字列。
        config (ServerSettings): サーバー設定。

    Returns:
        set[str]: 比較に利用するパス候補の集合。
    """

    normalized_path = _NormalizePathLikeString(path)
    candidates = {normalized_path}

    # EPGStation は録画ファイル名や録画ルートからの相対パスだけを返す構成があるため、
    # KonomiTV 側の recorded_folders を録画ルート候補として総当たりで展開する。
    # 絶対パスらしい値はそのまま保持し、相対パスだけを recorded_folders 配下に展開する。
    relative_path: PurePosixPath | None = None
    if _IsAbsolutePathLikeString(normalized_path) is False:
        relative_path = PurePosixPath(normalized_path)
    elif len(normalized_path) >= 3 and normalized_path[1] == ':':
        # Windows のドライブレター付き絶対パス (EDCB-Wine の "D:\TV-Record\foo.ts" や、Windows 上の EDCB を
        # Linux 側の KonomiTV から参照する構成) は、そのままでは KonomiTV 側に存在しない。
        # ドライブ部分を除いた残りを相対パスとみなし、recorded_folders 配下に展開する。
        relative_path = PurePosixPath(normalized_path[3:])

    if relative_path is not None and len(relative_path.parts) > 0:
        for recorded_folder in config.video.recorded_folders:
            recorded_folder_path = _NormalizePathLikeString(str(recorded_folder))

            # 相対パスが録画フォルダ名を含む場合に備え、先頭要素を順に削った候補も作る。
            # 例: EPGStation が "recorded/foo.ts"、KonomiTV が "/mnt/recorded/foo.ts" として見ている場合。
            # 例: EDCB-Wine が "D:/TV-Record/foo.ts"、KonomiTV が "/mnt/tv-record/TV-Record/foo.ts" として見ている場合。
            for start_index in range(len(relative_path.parts)):
                candidates.add(_NormalizePathLikeString(str(PurePosixPath(recorded_folder_path).joinpath(*relative_path.parts[start_index:]))))

    return candidates


def _ExpandRecordingPathCandidatesSet(paths: set[str], config: ServerSettings) -> set[str]:
    """
    録画バックエンドが返した複数のパス文字列を比較候補へ展開する。

    Args:
        paths (set[str]): 録画バックエンドが返したパス文字列の集合。
        config (ServerSettings): サーバー設定。

    Returns:
        set[str]: 比較に利用するパス候補の集合。
    """

    expanded_paths: set[str] = set()
    for path in paths:
        expanded_paths.update(_ExpandRecordingPathCandidates(path, config))
    return expanded_paths


def IsActiveRecordingFilePath(file_path: str, active_recording_paths: set[str]) -> bool:
    """
    指定されたファイルパスが録画バックエンドの録画中ファイル一覧に含まれているかを判定する。

    Args:
        file_path (str): 判定対象の録画ファイルパス。
        active_recording_paths (set[str]): 録画バックエンドから取得した録画中ファイルパスの集合。

    Returns:
        bool: 録画中ファイル一覧に含まれている場合は True。
    """

    normalized_file_path = _NormalizePathLikeString(file_path)
    normalized_active_paths = {_NormalizePathLikeString(active_path) for active_path in active_recording_paths}
    if normalized_file_path in normalized_active_paths:
        return True

    # EPGStation と KonomiTV が別ホストで動く場合、録画フォルダのマウントポイントだけが異なることがある。
    # その場合でも末尾のパスが一致していれば同じ録画ファイルとみなす。
    for active_path in normalized_active_paths:
        if normalized_file_path.endswith('/' + active_path) or active_path.endswith('/' + normalized_file_path):
            return True

    # 最後のフォールバックとして、録画中ファイル一覧内でファイル名が一意な場合のみファイル名一致を許可する。
    # 同名ファイルが複数ある状態で basename だけに頼ると誤判定になり得るため、その場合は一致させない。
    file_basename = _GetPathBasename(normalized_file_path)
    active_basenames = [_GetPathBasename(active_path) for active_path in normalized_active_paths]
    return active_basenames.count(file_basename) == 1 and file_basename in active_basenames


def _ShouldCheckEDCBRecordingInProgress(reserve_data: ReserveDataRequired) -> bool:
    """
    録画中判定のために EDCB へ追加問い合わせを行うべき予約かを判定する。

    Args:
        reserve_data (ReserveDataRequired): 判定対象の予約情報。

    Returns:
        bool: 録画中判定を行うべき場合は True。
    """

    # 無効予約・視聴予約は録画ファイルパスが存在しないため、判定 API を呼ばない。
    rec_mode = reserve_data.get('rec_setting', {}).get('rec_mode', 1)
    if rec_mode >= 5 or rec_mode == 4:
        return False

    # 録画中判定を行う時間範囲 (現在時刻の2時間前〜2時間後) に絞る。
    # 予約一覧全件に対して sendGetRecFilePath() を実行すると EDCB 側の負荷が大きいため、現実的に録画中になり得る予約だけに限定する。
    current_time = datetime.now(tz=JST)
    recording_check_start = current_time - timedelta(hours=2)
    recording_check_end = current_time + timedelta(hours=2)

    reserve_start_time = NormalizeToJSTDatetime(reserve_data['start_time'])
    reserve_end_time = reserve_start_time + timedelta(seconds=reserve_data['duration_second'])

    return reserve_start_time <= recording_check_end and reserve_end_time >= recording_check_start


async def _GetActiveRecordingFilePathsFromEDCB() -> ActiveRecordingFilePaths:
    """
    EDCB から現在録画中のファイルパス一覧を取得する。

    Returns:
        ActiveRecordingFilePaths: EDCB から取得した録画中ファイルパス一覧。
    """

    edcb = CtrlCmdUtil()
    reserve_data_list = await edcb.sendEnumReserve()
    if reserve_data_list is None:
        logging.warning('[RecordingStatusProvider][EDCB] Failed to get recording reservations.')
        return ActiveRecordingFilePaths(paths=set(), backend='EDCB', is_reliable=False)

    active_paths: set[str] = set()
    for reserve_data in reserve_data_list:
        if _ShouldCheckEDCBRecordingInProgress(reserve_data) is False:
            continue
        rec_file_path = await edcb.sendGetRecFilePath(reserve_data['reserve_id'])
        if rec_file_path is not None:
            active_paths.add(rec_file_path)

    return ActiveRecordingFilePaths(paths=active_paths, backend='EDCB', is_reliable=True)


def _ExtractEPGStationRecordingIDs(payload: Any) -> set[int]:
    """
    EPGStation の録画中 API レスポンスから録画中アイテムの ID を抽出する。

    Args:
        payload (Any): EPGStation API の JSON レスポンス。

    Returns:
        set[int]: 抽出された録画中アイテム ID の集合。
    """

    recording_ids: set[int] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key == 'id' and isinstance(value, int):
                recording_ids.add(value)
            else:
                recording_ids.update(_ExtractEPGStationRecordingIDs(value))
    elif isinstance(payload, list):
        for item in payload:
            recording_ids.update(_ExtractEPGStationRecordingIDs(item))
    return recording_ids


def _ExtractEPGStationRecordingFilePaths(payload: Any) -> set[str]:
    """
    EPGStation の録画中 API レスポンスからファイルパスらしい文字列だけを再帰的に抽出する。

    Args:
        payload (Any): EPGStation API の JSON レスポンス。

    Returns:
        set[str]: 抽出された録画中ファイルパス候補の集合。
    """

    # EPGStation のバージョン差異を吸収するため、録画ファイルを指す可能性が高いキーだけを拾う。
    # 番組名や局名をファイル名として誤検出しないよう、汎用的すぎる "name" は対象にしない。
    file_path_keys = {
        'file',
        'fileName',
        'filePath',
        'file_name',
        'file_path',
        'filename',
        'path',
        'recordedFilePath',
        'recordingFilePath',
        'relativeFilePath',
        'videoFilePath',
    }

    extracted_paths: set[str] = set()
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in file_path_keys and isinstance(value, str) and value != '':
                if key == 'relativeFilePath':
                    # EPGStation の relativeFilePath は録画ルートからの相対パスとして扱う。
                    # API 表現上は "/subdir/file.ts" のように先頭スラッシュを付けるが、KonomiTV 側では recorded_folders 配下へ展開する必要がある。
                    relative_file_path = _NormalizePathLikeString(value).lstrip('/')
                    if relative_file_path != '':
                        extracted_paths.add(relative_file_path)
                else:
                    extracted_paths.add(value)
            else:
                extracted_paths.update(_ExtractEPGStationRecordingFilePaths(value))
    elif isinstance(payload, list):
        for item in payload:
            extracted_paths.update(_ExtractEPGStationRecordingFilePaths(item))

    return extracted_paths


async def GetEPGStationRecentRecordedFilePaths(config: ServerSettings) -> RecentRecordedFilePaths:
    """
    EPGStation から直近の録画済みファイルパス一覧を取得する。

    Args:
        config (ServerSettings): サーバー設定。

    Returns:
        RecentRecordedFilePaths: EPGStation から取得した直近の録画済みファイルパス一覧。
    """

    if config.general.backend != 'EPGStation':
        return RecentRecordedFilePaths(paths=set(), total=0, is_reliable=False)

    base_url = str(config.general.epgstation_url).rstrip('/')
    endpoint_url = (
        f'{base_url}/api/recorded?'
        f'isHalfWidth=false&hasOriginalFile=true&offset=0&limit={EPGSTATION_RECORDED_SYNC_PAGE_LIMIT}'
    )
    async with HTTPX_CLIENT() as client:
        try:
            response = await client.get(endpoint_url, timeout=EPGSTATION_API_TIMEOUT_SECONDS)
        except (httpx.NetworkError, httpx.TimeoutException) as ex:
            logging.warning(
                f'[RecordingStatusProvider][EPGStation] Failed to request recent recorded list. '
                f'({type(ex).__name__}: {ex})'
            )
            return RecentRecordedFilePaths(paths=set(), total=0, is_reliable=False)
        except Exception as ex:
            logging.warning('[RecordingStatusProvider][EPGStation] Failed to request recent recorded list.', exc_info=ex)
            return RecentRecordedFilePaths(paths=set(), total=0, is_reliable=False)

    if response.status_code != 200:
        logging.warning(
            f'[RecordingStatusProvider][EPGStation] Unexpected status code from recent recorded list: '
            f'{response.status_code} [{endpoint_url}]'
        )
        return RecentRecordedFilePaths(paths=set(), total=0, is_reliable=False)

    try:
        payload = response.json()
    except Exception as ex:
        logging.warning('[RecordingStatusProvider][EPGStation] Failed to parse recent recorded list.', exc_info=ex)
        return RecentRecordedFilePaths(paths=set(), total=0, is_reliable=False)

    total = payload.get('total', 0) if isinstance(payload, dict) else 0
    return RecentRecordedFilePaths(
        paths = _ExpandRecordingPathCandidatesSet(_ExtractEPGStationRecordingFilePaths(payload), config),
        total = total if isinstance(total, int) else 0,
        is_reliable = True,
    )


async def _GetActiveRecordingFilePathsFromEPGStation(config: ServerSettings) -> ActiveRecordingFilePaths:
    """
    EPGStation から現在録画中のファイルパス一覧を取得する。

    Args:
        config (ServerSettings): サーバー設定。

    Returns:
        ActiveRecordingFilePaths: EPGStation から取得した録画中ファイルパス一覧。
    """

    base_url = str(config.general.epgstation_url).rstrip('/')
    endpoint_urls = [
        f'{base_url}/api/recording?isHalfWidth=false',
        f'{base_url}/api/recording',
    ]

    async with HTTPX_CLIENT() as client:
        for endpoint_url in endpoint_urls:
            try:
                # 録画中判定は 5 秒間隔で繰り返す軽量同期なので、EPGStation 側が詰まった場合は短めに諦める。
                # ここで長時間待つと、追いかけ再生用の状態同期そのものが詰まりやすくなる。
                response = await client.get(endpoint_url, timeout=EPGSTATION_API_TIMEOUT_SECONDS)
            except (httpx.NetworkError, httpx.TimeoutException) as ex:
                logging.warning(
                    f'[RecordingStatusProvider][EPGStation] Failed to request {endpoint_url}. '
                    f'({type(ex).__name__}: {ex})'
                )
                continue
            except Exception as ex:
                logging.warning(f'[RecordingStatusProvider][EPGStation] Failed to request {endpoint_url}.', exc_info=ex)
                continue

            if response.status_code == 404:
                continue
            if response.status_code != 200:
                logging.warning(f'[RecordingStatusProvider][EPGStation] Unexpected status code: {response.status_code} [{endpoint_url}]')
                continue

            try:
                payload = response.json()
            except Exception as ex:
                logging.warning(f'[RecordingStatusProvider][EPGStation] Failed to parse JSON response. [{endpoint_url}]', exc_info=ex)
                continue

            active_paths = _ExtractEPGStationRecordingFilePaths(payload)

            # 録画中一覧の詳細レスポンスだけに video file 情報が含まれる EPGStation 構成に備え、ID が取れる場合は詳細 API も確認する。
            for recording_id in _ExtractEPGStationRecordingIDs(payload):
                try:
                    detail_url = f'{base_url}/api/recording/{recording_id}?isHalfWidth=false'
                    # 一覧 API と同じく短めの timeout に揃え、失敗時は次回同期に任せる。
                    detail_response = await client.get(detail_url, timeout=EPGSTATION_API_TIMEOUT_SECONDS)
                except (httpx.NetworkError, httpx.TimeoutException) as ex:
                    logging.warning(
                        f'[RecordingStatusProvider][EPGStation] Failed to request recording detail. '
                        f'[recording_id: {recording_id}] ({type(ex).__name__}: {ex})'
                    )
                    continue
                except Exception as ex:
                    logging.warning(f'[RecordingStatusProvider][EPGStation] Failed to request recording detail. [recording_id: {recording_id}]', exc_info=ex)
                    continue
                if detail_response.status_code != 200:
                    continue
                try:
                    active_paths.update(_ExtractEPGStationRecordingFilePaths(detail_response.json()))
                except Exception as ex:
                    logging.warning(f'[RecordingStatusProvider][EPGStation] Failed to parse recording detail. [recording_id: {recording_id}]', exc_info=ex)

            logging.debug(f'[RecordingStatusProvider][EPGStation] Active recording paths: {len(active_paths)}')
            return ActiveRecordingFilePaths(
                paths = _ExpandRecordingPathCandidatesSet(active_paths, config),
                backend = 'EPGStation',
                is_reliable = True,
            )

    return ActiveRecordingFilePaths(paths=set(), backend='EPGStation', is_reliable=False)


async def GetActiveRecordingFilePaths(config: ServerSettings) -> ActiveRecordingFilePaths:
    """
    現在のバックエンドから録画中ファイルパス一覧を取得する。

    Args:
        config (ServerSettings): サーバー設定。

    Returns:
        ActiveRecordingFilePaths: 録画中ファイルパス一覧。
    """

    if config.general.backend == 'EDCB':
        # EDCB は Windows 形式 ("D:\TV-Record\foo.ts") や別ホスト基準のパスを返すため、
        # KonomiTV 側の recorded_folders に対する候補パスへ展開してから返す。
        # 展開後の候補のうち実在するものだけが RecordedScanTask 側の存在チェックを通る。
        active_recording_file_paths = await _GetActiveRecordingFilePathsFromEDCB()
        return ActiveRecordingFilePaths(
            paths = _ExpandRecordingPathCandidatesSet(active_recording_file_paths.paths, config),
            backend = active_recording_file_paths.backend,
            is_reliable = active_recording_file_paths.is_reliable,
        )
    if config.general.backend == 'EPGStation':
        return await _GetActiveRecordingFilePathsFromEPGStation(config)
    return ActiveRecordingFilePaths(paths=set(), backend='FileSystem', is_reliable=False)
