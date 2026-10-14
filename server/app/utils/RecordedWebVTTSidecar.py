import json
import pathlib
import subprocess
import threading
from typing import Any, cast

from app import logging
from app.constants import LIBRARY_PATH


class RecordedWebVTTSidecar:
    """録画ファイルに対応する WebVTT 外部字幕を安全に検査する。"""

    # 外部字幕は DPlayer が URL から読み込むため、異常な巨大ファイルを返さない。
    MAX_SIDECAR_SIZE_BYTES = 8 * 1024 * 1024

    # 同じ録画に対する FFprobe の多重起動を防ぎ、録画ファイルの更新時は署名の変更で再検査する。
    __embedded_subtitle_cache: dict[tuple[pathlib.Path, int, int], bool | None] = {}
    __embedded_subtitle_cache_lock = threading.Lock()

    @classmethod
    def read(cls, recorded_file_path: pathlib.Path) -> bytes | None:
        """
        録画ファイルと同名の WebVTT sidecar を読み込む。

        Args:
            recorded_file_path (pathlib.Path): 録画ファイルのパス。

        Returns:
            bytes | None: 有効な WebVTT の内容。存在しないか不正な場合は None。
        """

        sidecar_path = recorded_file_path.with_suffix('.vtt')
        try:
            # サイズを先に制限し、読み込み後にも競合による増加を検出する。
            if sidecar_path.stat().st_size > cls.MAX_SIDECAR_SIZE_BYTES:
                logging.warning(f'{sidecar_path}: WebVTT sidecar exceeds the size limit.')
                return None
            with sidecar_path.open('rb') as sidecar_file:
                sidecar_data = sidecar_file.read(cls.MAX_SIDECAR_SIZE_BYTES + 1)
        except FileNotFoundError:
            return None
        except OSError as ex:
            logging.warning(f'{sidecar_path}: Failed to read WebVTT sidecar:', exc_info=ex)
            return None

        if len(sidecar_data) > cls.MAX_SIDECAR_SIZE_BYTES:
            logging.warning(f'{sidecar_path}: WebVTT sidecar exceeds the size limit.')
            return None
        try:
            sidecar_text = sidecar_data.decode('utf-8-sig')
        except UnicodeDecodeError:
            logging.warning(f'{sidecar_path}: WebVTT sidecar is not UTF-8.')
            return None
        if sidecar_text.startswith('WEBVTT') is False or (
            len(sidecar_text) > len('WEBVTT') and sidecar_text[len('WEBVTT')] not in [' ', '\t', '\r', '\n']
        ):
            logging.warning(f'{sidecar_path}: WebVTT signature was not found.')
            return None
        return sidecar_data

    @classmethod
    def readHeader(cls, recorded_file_path: pathlib.Path) -> bytes | None:
        """
        WebVTT sidecar の存在と先頭シグネチャだけを検査する。

        Args:
            recorded_file_path (pathlib.Path): 録画ファイルのパス。

        Returns:
            bytes | None: 有効な場合は空バイト列。存在しないか不正な場合は None。
        """

        sidecar_path = recorded_file_path.with_suffix('.vtt')
        try:
            if sidecar_path.stat().st_size > cls.MAX_SIDECAR_SIZE_BYTES:
                logging.warning(f'{sidecar_path}: WebVTT sidecar exceeds the size limit.')
                return None
            with sidecar_path.open('rb') as sidecar_file:
                header = sidecar_file.read(12)
        except FileNotFoundError:
            return None
        except OSError as ex:
            logging.warning(f'{sidecar_path}: Failed to read WebVTT sidecar header:', exc_info=ex)
            return None

        # HEAD は ASCII シグネチャだけを確認し、読み取り境界で途中になった UTF-8 文字を誤って拒否しない。
        header = header.removeprefix(b'\xef\xbb\xbf')
        if header.startswith(b'WEBVTT') is False or (
            len(header) > len(b'WEBVTT') and header[len(b'WEBVTT')] not in [0x20, 0x09, 0x0d, 0x0a]
        ):
            logging.warning(f'{sidecar_path}: WebVTT signature was not found.')
            return None
        return b''

    @classmethod
    def hasEmbeddedSubtitle(cls, recorded_file_path: pathlib.Path) -> bool | None:
        """
        録画ファイルにブラウザ用 sidecar より優先すべき埋め込み字幕があるか調べる。

        Args:
            recorded_file_path (pathlib.Path): 録画ファイルのパス。

        Returns:
            bool | None: 字幕の有無。FFprobe の失敗時は None。
        """

        try:
            file_stat = recorded_file_path.stat()
        except OSError as ex:
            logging.warning(f'{recorded_file_path}: Failed to stat recorded file for subtitle probe:', exc_info=ex)
            return None
        cache_key = (recorded_file_path, file_stat.st_size, file_stat.st_mtime_ns)

        # 一つの録画に同時到着した sidecar 要求でも FFprobe は一度だけ実行する。
        with cls.__embedded_subtitle_cache_lock:
            if cache_key in cls.__embedded_subtitle_cache:
                return cls.__embedded_subtitle_cache[cache_key]

            try:
                probe_result = subprocess.run(
                    [
                        LIBRARY_PATH['FFprobe'],
                        '-hide_banner',
                        '-loglevel', 'error',
                        '-analyzeduration', str(30 * 1_000_000),
                        '-probesize', '80M',
                        '-show_entries', 'stream=codec_type,codec_name',
                        '-of', 'json',
                        str(recorded_file_path),
                    ],
                    capture_output = True,
                    timeout = 30,
                )
                if probe_result.returncode != 0:
                    logging.warning(
                        f'{recorded_file_path}: FFprobe subtitle probe failed with return code '
                        f'{probe_result.returncode}: {probe_result.stderr.decode("utf-8", errors="ignore").strip()}'
                    )
                    result: bool | None = None
                else:
                    probe_json = cast(dict[str, Any], json.loads(probe_result.stdout.decode('utf-8')))
                    streams = cast(list[dict[str, Any]], probe_json.get('streams', []))
                    result = any(
                        stream.get('codec_type') == 'subtitle' or
                        str(stream.get('codec_name', '')).startswith('arib')
                        for stream in streams
                    )
            except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError, json.JSONDecodeError) as ex:
                logging.warning(f'{recorded_file_path}: Failed to run FFprobe subtitle probe:', exc_info=ex)
                result = None

            # 古い署名の結果は不要なので、更新を繰り返す追いかけ録画でもキャッシュを無限に増やさない。
            cls.__embedded_subtitle_cache = {
                key: value
                for key, value in cls.__embedded_subtitle_cache.items()
                if key[0] != recorded_file_path
            }
            # 一時的な FFprobe 失敗は次の HEAD / GET で再検査できるよう、失敗結果を固定しない。
            if result is None:
                return None
            # 異なる録画の sidecar を大量に開いても常駐キャッシュが増え続けないよう上限を設ける。
            while len(cls.__embedded_subtitle_cache) >= 512:
                cls.__embedded_subtitle_cache.pop(next(iter(cls.__embedded_subtitle_cache)))
            cls.__embedded_subtitle_cache[cache_key] = result
            return result
