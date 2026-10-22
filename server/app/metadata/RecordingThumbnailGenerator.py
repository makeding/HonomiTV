from __future__ import annotations

import asyncio
import pathlib
import shutil
from typing import ClassVar, cast

import cv2
import numpy as np
from biim.mpeg2ts import ts
from numpy.typing import NDArray

from app import logging
from app.constants import LIBRARY_PATH
from app.utils.TSKeyFrameSeeker import TSKeyFrameSeeker, TSStreamInfo


class RecordingThumbnailGenerator:
    """録画中の MPEG-TS から、代表サムネイルだけを低負荷で生成する。"""

    # 番組開始からこの秒数が経過した付近を、録画中の代表サムネイル候補にする。
    PROGRAM_TARGET_SECONDS: ClassVar[float] = 5 * 60
    # ターゲット時刻の前後から少数の候補だけを取得し、録画中のディスク負荷を抑える。
    CANDIDATE_OFFSETS_SECONDS: ClassVar[tuple[float, ...]] = (-5.0, 0.0, 5.0)
    # 書き込み中の末尾 GOP を読まないよう、最新 PCR よりこの秒数以上手前だけを候補にする。
    STABLE_TAIL_SECONDS: ClassVar[float] = 10.0

    # 1候補につき FFmpeg へ渡す入力を制限し、壊れた TS や成長中ファイルを末尾まで走査させない。
    INPUT_BACKTRACK_BYTES: ClassVar[int] = 1 * 1024 * 1024
    MAX_INPUT_BYTES_PER_CANDIDATE: ClassVar[int] = 16 * 1024 * 1024
    FFMPEG_PROBE_BYTES: ClassVar[int] = 4 * 1024 * 1024
    FFMPEG_TIMEOUT_SECONDS: ClassVar[float] = 10.0
    MAX_KEYFRAME_AGE_TICKS: ClassVar[int] = 30 * 90_000

    # 代表サムネイルの寸法と WebP 品質は、録画完了後の ThumbnailGenerator と揃える。
    OUTPUT_WIDTH: ClassVar[int] = 480
    OUTPUT_HEIGHT: ClassVar[int] = 270
    WEBP_QUALITY: ClassVar[int] = 80


    def __init__(
        self,
        file_path: pathlib.Path,
        stream_info: TSStreamInfo,
        source_base_dts: int,
        readable_file_size: int,
    ) -> None:
        """
        録画中サムネイル生成器を初期化する。

        Args:
            file_path (pathlib.Path): 追記中の MPEG-TS ファイル。
            stream_info (TSStreamInfo): 共有トラッカーが確定した TS の映像/PCR 情報。
            source_base_dts (int): 録画先頭キーフレームの DTS (90kHz)。
            readable_file_size (int): スナップショット取得時点のファイルサイズ。

        Returns:
            None
        """

        # 追記中ファイルの参照先を保持する。
        ## generate() では、このパスをロックせず読み取り専用で開く。
        self._file_path = file_path
        # 共有トラッカーが解析済みのストリーム情報を再利用する。
        ## 候補ごとに PAT / PMT を全探索し直す負荷を避けるために使う。
        self._stream_info = stream_info
        # 録画先頭 DTS を保持し、番組開始から約5分の候補を同じ時間軸で探索する。
        self._source_base_dts = source_base_dts
        # スナップショットより後に追記された不安定な末尾を読まない上限として保持する。
        self._readable_file_size = max(readable_file_size, 0)


    @classmethod
    def calculateCandidateOffsets(
        cls,
        recording_start_margin: float,
        available_duration_seconds: float,
    ) -> list[float]:
        """
        実際に書き込まれた再生時間から、安全に抽出できる候補時刻を算出する。

        Args:
            recording_start_margin (float): 録画先頭から番組開始までの余白 (秒)。
            available_duration_seconds (float): 最新 PCR から求めた読み取り可能時間 (秒)。

        Returns:
            list[float]: 録画先頭基準の候補時刻。まだ早い場合は空配列。
        """

        # 「番組開始から5分」を録画先頭基準へ戻し、開始前マージンを経過判定から除外する。
        program_start = max(recording_start_margin, 0.0)
        target = program_start + cls.PROGRAM_TARGET_SECONDS
        candidates = [max(program_start, target + offset) for offset in cls.CANDIDATE_OFFSETS_SECONDS]

        # 最も後ろの候補も成長端から十分離れた時点で、3候補をまとめて抽出する。
        stable_readable_end = max(available_duration_seconds - cls.STABLE_TAIL_SECONDS, 0.0)
        if len(candidates) == 0 or candidates[-1] > stable_readable_end:
            return []
        return candidates


    async def generate(self, candidate_offsets: list[float], output_path: pathlib.Path) -> bool:
        """
        少数の候補フレームを抽出し、最良の1枚を一時 WebP として保存する。

        Args:
            candidate_offsets (list[float]): 録画先頭基準の候補時刻。
            output_path (pathlib.Path): 完成画像を書き込む一時パス。

        Returns:
            bool: 完全な WebP を一時パスへ保存できた場合は True。
        """

        frames: list[NDArray[np.uint8]] = []
        for candidate_offset in candidate_offsets:
            # キーフレーム探索と入力読み取りは同期 I/O のためワーカースレッドへ逃がす。
            source_data = await asyncio.to_thread(self.__readBoundedCandidateSource, candidate_offset)
            if source_data is None:
                continue

            # FFmpeg は候補ごとに独立して起動し、異常な候補が次の候補のデコーダ状態へ影響しないようにする。
            frame = await self.__decodeFirstFrame(source_data, candidate_offset)
            if frame is not None:
                frames.append(frame)

        if len(frames) == 0:
            logging.warning(f'{self._file_path}: No usable early thumbnail frames were extracted.')
            return False

        # OpenCV の評価と WebP エンコードは CPU 処理なので、イベントループ外で完結させる。
        ## ワーカースレッドからファイルを書かせるとキャンセル後に遅れて一時ファイルが残り得るため、bytes だけを受け取る。
        encoded_thumbnail = await asyncio.to_thread(self.__selectAndEncodeBestFrame, frames)
        if encoded_thumbnail is None:
            return False

        # 代表サムネイルは小さいため、完了点が明確な同期書き込みをイベントループ上で行う。
        ## 呼び出し元はこの一時ファイルを検証後に atomic replace する。
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(encoded_thumbnail)
        return True


    def __readBoundedCandidateSource(self, candidate_offset: float) -> bytes | None:
        """
        候補直前のキーフレームを解決し、スナップショット境界内の固定長データだけを読む。

        Args:
            candidate_offset (float): 録画先頭基準の候補時刻。

        Returns:
            bytes | None: FFmpeg へ渡す bounded MPEG-TS。探索失敗時は None。
        """

        try:
            keyframe = TSKeyFrameSeeker.seek(
                self._file_path,
                self._stream_info,
                candidate_offset,
                self._source_base_dts,
                self.MAX_KEYFRAME_AGE_TICKS,
            )

            # 候補直前から PAT / PMT などの PSI/SI を取得しつつ、候補より前の映像 PES は FFmpeg へ渡さない。
            ## 低ビットレート録画では 1MiB の backtrack が数十秒に相当し、そのまま渡すと候補時刻ではなく
            ## backtrack 先頭の I フレームが選ばれるため、対象映像 PID だけを前置区間から除去する。
            packet_size = self._stream_info.packet_size
            read_start = max(keyframe.source_file_position - self.INPUT_BACKTRACK_BYTES, 0)
            read_start = (read_start // packet_size) * packet_size
            prefix_size = keyframe.source_file_position - read_start
            read_end = min(
                keyframe.source_file_position + self.MAX_INPUT_BYTES_PER_CANDIDATE - prefix_size,
                self._readable_file_size,
            )
            if read_end <= keyframe.source_file_position:
                return None
            with self._file_path.open('rb') as source_file:
                source_file.seek(read_start)
                prefix_data = source_file.read(prefix_size)
                if len(prefix_data) != prefix_size:
                    return None
                target_data = source_file.read(read_end - keyframe.source_file_position)

            # 元の 188 / 192 byte packet framing は維持し、FFmpeg が PAT / PMT から対象 PID を認識できるようにする。
            ## 映像 PES を除いた前置区間の後ろへ、探索済みキーフレームから始まる対象区間を連結する。
            filtered_prefix_packets: list[bytes] = []
            for packet_offset in range(0, len(prefix_data) - packet_size + 1, packet_size):
                source_packet = prefix_data[packet_offset:packet_offset + packet_size]
                normalized_packet = TSKeyFrameSeeker.normalizePacket(source_packet, packet_size)
                if normalized_packet is None or ts.pid(normalized_packet) == self._stream_info.video_pid:
                    continue
                filtered_prefix_packets.append(source_packet)
            source_data = b''.join(filtered_prefix_packets) + target_data
            if len(source_data) == 0:
                return None
            return source_data
        except Exception as ex:
            logging.warning(
                f'{self._file_path}: Failed to read bounded early thumbnail source at {candidate_offset:.2f}s.',
                exc_info=ex,
            )
            return None


    async def __decodeFirstFrame(
        self,
        source_data: bytes,
        candidate_offset: float,
    ) -> NDArray[np.uint8] | None:
        """
        bounded MPEG-TS から最初の I フレームを1枚だけデコードする。

        Args:
            source_data (bytes): サイズ上限を適用済みの MPEG-TS データ。
            candidate_offset (float): ログに出す候補時刻。

        Returns:
            NDArray[np.uint8] | None: 480x270 の BGR フレーム。失敗時は None。
        """

        bundled_ffmpeg = pathlib.Path(LIBRARY_PATH['FFmpeg'])
        ffmpeg_path = str(bundled_ffmpeg) if bundled_ffmpeg.is_file() else shutil.which('ffmpeg')
        if ffmpeg_path is None:
            logging.error(f'{self._file_path}: FFmpeg executable was not found for early thumbnail generation.')
            return None

        process = await asyncio.create_subprocess_exec(
            ffmpeg_path,
            '-nostdin',
            '-hide_banner',
            '-loglevel', 'error',
            '-f', 'mpegts',
            '-probesize', str(self.FFMPEG_PROBE_BYTES),
            '-analyzeduration', '3000000',
            '-skip_frame', 'nointra',
            '-i', 'pipe:0',
            # 複数サービスを含む TS でも、共有トラッカーが確定した映像 PID だけを選ぶ。
            '-map', f'0:i:0x{self._stream_info.video_pid:x}',
            '-frames:v', '1',
            '-vf', f'scale={self.OUTPUT_WIDTH}:{self.OUTPUT_HEIGHT}',
            '-pix_fmt', 'bgr24',
            '-f', 'rawvideo',
            'pipe:1',
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=source_data),
                timeout=self.FFMPEG_TIMEOUT_SECONDS,
            )
        except (asyncio.CancelledError, TimeoutError):
            # 録画完了・サーバー終了・タイムアウト時は feeder と FFmpeg を確実に止め、孤児プロセスを残さない。
            if process.returncode is None:
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
            await process.communicate()
            current_task = asyncio.current_task()
            if current_task is not None and current_task.cancelling() > 0:
                raise
            logging.warning(
                f'{self._file_path}: Early thumbnail FFmpeg timed out at {candidate_offset:.2f}s. '
                f'[timeout: {self.FFMPEG_TIMEOUT_SECONDS:.1f}s]'
            )
            return None

        frame_size = self.OUTPUT_WIDTH * self.OUTPUT_HEIGHT * 3
        if process.returncode != 0 or len(stdout) < frame_size:
            error_message = stderr.decode('utf-8', errors='ignore')[-2000:]
            logging.warning(
                f'{self._file_path}: Early thumbnail FFmpeg failed at {candidate_offset:.2f}s. '
                f'[return_code: {process.returncode}, output_bytes: {len(stdout)}]\n{error_message}'
            )
            return None

        # rawvideo の先頭1枚だけを独立した書き込み可能配列へ変換する。
        frame = np.frombuffer(stdout[:frame_size], dtype=np.uint8)
        return cast(NDArray[np.uint8], frame.reshape((self.OUTPUT_HEIGHT, self.OUTPUT_WIDTH, 3)).copy())


    def __selectAndEncodeBestFrame(
        self,
        frames: list[NDArray[np.uint8]],
    ) -> bytes | None:
        """
        黒画面や白飛びを避けつつ最も情報量の多いフレームを選び、WebP へエンコードする。

        Args:
            frames (list[NDArray[np.uint8]]): 候補 BGR フレーム。

        Returns:
            bytes | None: 完成した WebP。候補不在・エンコード失敗時は None。
        """

        scored_frames: list[tuple[float, NDArray[np.uint8]]] = []
        for frame in frames:
            grayscale = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            mean_brightness = float(np.mean(grayscale))
            brightness_stddev = float(np.std(grayscale))

            # 完全な黒画面・白画面・ほぼ単色の遷移画面は代表画像にしない。
            if mean_brightness < 8.0 or mean_brightness > 247.0 or brightness_stddev < 4.0:
                continue
            edge_score = float(cv2.Laplacian(grayscale, cv2.CV_64F).var())
            scored_frames.append((edge_score + brightness_stddev * 4.0, frame))

        if len(scored_frames) == 0:
            logging.warning(f'{self._file_path}: All early thumbnail candidates were blank or unusable.')
            return None

        best_frame = max(scored_frames, key=lambda item: item[0])[1]
        try:
            encoded, buffer = cv2.imencode(
                '.webp',
                best_frame,
                [cv2.IMWRITE_WEBP_QUALITY, self.WEBP_QUALITY],
            )
            if encoded is False:
                return None
            return buffer.tobytes()
        except Exception as ex:
            logging.warning(f'{self._file_path}: Failed to encode early thumbnail image.', exc_info=ex)
            return None
