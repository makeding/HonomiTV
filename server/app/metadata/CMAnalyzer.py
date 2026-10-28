from __future__ import annotations

import asyncio
import configparser
import errno
import hashlib
import json
import math
import os
import re
import shlex
import signal
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from pathlib import Path
from typing import Literal, Protocol, TypedDict, cast

from app import logging
from app.constants import LIBRARY_PATH


CMAnalyzerStatus = Literal[
    'completed',
    'analysis_failed',
    'unsupported',
    'interrupted',
]
CMDecodeMode = Literal['Hardware', 'CPU']
type CMContainerFormat = Literal['MPEG-TS', 'MPEG-4', 'MMT/TLV']
CMAnalysisStage = Literal[
    'PreparingMedia',
    'IndexingMedia',
    'ChapterAnalyzing',
    'LogoAnalyzing',
    'HardwareFallback',
    'CombiningCM',
]
CMAnalysisStageCallback = Callable[[CMAnalysisStage, float | None], Awaitable[None]]


class CMSectionJSON(TypedDict):
    """CM 区間を秒単位の半開区間で表す。"""

    start_time: float
    end_time: float


@dataclass(frozen=True, slots=True)
class CMInputDescriptor:
    """実ファイルを FFprobe して確定した CM 解析入力。"""

    format_name: str | None
    video_stream_index: int
    audio_stream_index: int | None
    video_codec_name: str | None
    pixel_format: str | None
    bit_depth: int | None
    width: int
    height: int
    field_order: str | None
    time_base: Fraction | None
    source_frame_rate: Fraction | None
    duration_seconds: float
    program_id: int | None
    service_id: int | None
    has_variable_video_format: bool = False
    has_variable_audio_stream: bool = False
    video_start_time_seconds: float | None = None
    audio_start_time_seconds: float | None = None

    def toJSON(self) -> dict[str, object]:
        """解析 key に利用できる決定的な JSON 値へ変換する。"""

        return {
            'format_name': self.format_name,
            'video_stream_index': self.video_stream_index,
            'audio_stream_index': self.audio_stream_index,
            'video_codec_name': self.video_codec_name,
            'pixel_format': self.pixel_format,
            'bit_depth': self.bit_depth,
            'width': self.width,
            'height': self.height,
            'field_order': self.field_order,
            'time_base': str(self.time_base) if self.time_base is not None else None,
            'source_frame_rate': str(self.source_frame_rate) if self.source_frame_rate is not None else None,
            'duration_seconds': self.duration_seconds,
            'program_id': self.program_id,
            'service_id': self.service_id,
            'has_variable_video_format': self.has_variable_video_format,
            'has_variable_audio_stream': self.has_variable_audio_stream,
            'video_start_time_seconds': self.video_start_time_seconds,
            'audio_start_time_seconds': self.audio_start_time_seconds,
        }


@dataclass(frozen=True, slots=True)
class CMAnalyzerRequest:
    """汎用 CM 解析バックエンドへ渡す要求。"""

    recorded_file_path: Path
    work_directory: Path
    service_id: int | None = None
    logo_paths: tuple[Path, ...] = ()
    hardware_devices: tuple[str, ...] = ()
    hardware_environment: Mapping[str, str] | None = None
    duration_seconds: float = 0.0
    has_variable_video_format: bool = False
    input_descriptor: CMInputDescriptor | None = None
    stage_callback: CMAnalysisStageCallback | None = None
    container_format: CMContainerFormat = 'MPEG-TS'


@dataclass(frozen=True, slots=True)
class CMAnalyzerResult:
    """解析バックエンドから返す構造化結果。"""

    status: CMAnalyzerStatus
    chapter_file: Path | None
    sections: tuple[CMSectionJSON, ...] = ()
    matched_logo: str | None = None
    analysis_fps: str | None = None
    library: str | None = None
    analyzer_version: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    warnings: tuple[str, ...] = ()
    descriptor: CMInputDescriptor | None = None
    decode_mode: CMDecodeMode | None = None
    total_frames: int | None = None

    def toJSON(self) -> dict[str, object]:
        """ログ・テスト用の JSON 互換値を返す。"""

        return {
            'status': self.status,
            'sections': list(self.sections),
            'matched_logo': self.matched_logo,
            'analysis_fps': self.analysis_fps,
            'chapter_file': str(self.chapter_file) if self.chapter_file is not None else None,
            'library': self.library,
            'analyzer_version': self.analyzer_version,
            'error_code': self.error_code,
            'error_message': self.error_message,
            'warnings': list(self.warnings),
            'descriptor': self.descriptor.toJSON() if self.descriptor is not None else None,
            'decode_mode': self.decode_mode,
            'total_frames': self.total_frames,
        }


@dataclass(frozen=True, slots=True)
class CMLogoValidationResult:
    """将来のロゴ生成入口との互換用。現行解析からは呼び出さない。"""

    matched: bool
    logo_ratio: float | None
    error_code: str | None = None
    error_message: str | None = None


class CMAnalyzer(Protocol):
    """CM 解析バックエンドの非同期インターフェイス。"""

    @property
    def runtimeFingerprint(self) -> dict[str, object]:
        """解析結果を無効化すべきランタイム構成を返す。"""
        ...

    async def resolveInputDescriptor(self, request: CMAnalyzerRequest) -> CMInputDescriptor:
        """実ファイルをprobeし、解析対象ストリームを確定する。"""
        ...

    async def analyze(self, request: CMAnalyzerRequest) -> CMAnalyzerResult:
        """録画を解析して検証前の chapter を返す。"""
        ...


@dataclass(frozen=True, slots=True)
class _ProcessResult:
    return_code: int
    output: str
    error_output: str = ''
    command: tuple[str, ...] = ()
    started_at: str | None = None
    elapsed_seconds: float | None = None

    @property
    def diagnostic(self) -> str:
        """外部プロセスを同じ条件で再現できる完全な診断情報を返す。"""

        lines: list[str] = []
        if self.command:
            lines.append(f'Command: {shlex.join(self.command)}')
        if self.started_at is not None:
            lines.append(f'Started at: {self.started_at}')
        if self.elapsed_seconds is not None:
            lines.append(f'Elapsed: {self.elapsed_seconds:.3f} sec')
        lines.extend((
            f'Exit code: {self.return_code}',
            '----- stdout -----',
            self.output or '(empty)',
            '----- stderr -----',
            self.error_output or '(empty)',
        ))
        return '\n'.join(lines)


@dataclass(frozen=True, slots=True)
class _LogoFrameOutput:
    path: Path | None
    matched_logo: str | None
    frame_count: int | None
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _PreparedMedia:
    """一つのCM解析job内で全native解析が共有する正規化媒体。"""

    media_path: Path
    index_path: Path
    video_stream_index: int
    audio_path: Path
    audio_index_path: Path
    audio_stream_index: int


class CMInputUnsupportedError(Exception):
    """入力probeだけで確定できる、同一入力では再試行不要な非対応状態。"""

    def __init__(self, code: str, message: str | None = None) -> None:
        super().__init__(message or code)
        self.code = code
        self.message = message


class GenericCMAnalyzer:
    """全登録メディアを FFMS2 の共有媒体・索引で解析する CM 解析器。"""

    ANALYSIS_FPS = Fraction(30_000, 1001)
    ANALYZER_VERSION = 'KonomiTV-CM-7'
    NORMALIZATION_POLICY_VERSION = 4
    _LOGO_FRAME_MAX_WORKERS = 15
    _LOGO_FRAME_MIN_FRAMES_PER_WORKER = 600
    _TRIM_PATTERN = re.compile(r'\btrim\s*\(\s*(\d+)\s*,\s*(\d+)\s*\)', re.IGNORECASE)
    _VIDEO_FRAME_COUNT_PATTERN = re.compile(r'Video Frames:\s*(\d+)\b')
    _FINAL_SCENE_PATTERN = re.compile(r'^#\s*SCPos:\s*(\d+)\s+(\d+)\s*$', re.MULTILINE)
    _HARDWARE_FAILURE_MARKER = 'ffms2-hw:'
    _DECODER_UNAVAILABLE_MARKERS = (
        'codec not found', 'decoder not found', 'decoder unavailable', 'no decoder',
        'unknown decoder', 'unsupported codec',
    )
    _STORAGE_INSUFFICIENT_MARKERS = ('no space left on device', 'disk quota exceeded', 'enospc', 'edquot')
    _STORAGE_UNAVAILABLE_MARKERS = ('permission denied', 'read-only file system', 'erofs', 'eacces')

    def __init__(
        self,
        runtime_directory: Path | None = None,
        ffms2_path: Path | None = None,
        ffmsindex_path: Path | None = None,
        chapter_executable_path: Path | None = None,
        logoframe_path: Path | None = None,
        join_logo_scp_path: Path | None = None,
        join_logo_scp_command_path: Path | None = None,
        runtime_manifest_path: Path | None = None,
        ffmpeg_path: Path | None = None,
        ffprobe_path: Path | None = None,
    ) -> None:
        if runtime_directory is None:
            runtime_directory = Path(LIBRARY_PATH['FFmpeg']).parent.parent / 'CMAnalysis'
        self.runtime_directory = runtime_directory
        self.ffms2_path = ffms2_path or runtime_directory / 'libffms2.so'
        self.ffmsindex_path = ffmsindex_path or runtime_directory / 'ffmsindex'
        self.chapter_executable_path = chapter_executable_path or runtime_directory / 'chapter_exe'
        self.logoframe_path = logoframe_path or runtime_directory / 'logoframe'
        self.join_logo_scp_path = join_logo_scp_path or runtime_directory / 'join_logo_scp'
        self.join_logo_scp_command_path = (
            join_logo_scp_command_path or runtime_directory / 'JL/JL_標準.txt'
        )
        self.runtime_manifest_path = runtime_manifest_path or runtime_directory / 'Runtime-Manifest.json'
        self.ffmpeg_path = ffmpeg_path or Path(LIBRARY_PATH['FFmpeg'])
        self.ffprobe_path = ffprobe_path or Path(LIBRARY_PATH['FFprobe'])

    @property
    def runtimeFingerprint(self) -> dict[str, object]:
        """解析 key に含める固定ランタイム識別情報。"""

        manifest_sha256, manifest_error = self._runtimeManifestFingerprint()
        media_preparer_sha256, media_preparer_error = self._fileFingerprint(self.ffmpeg_path)
        return {
            'analyzer_version': self.ANALYZER_VERSION,
            'normalization_policy_version': self.NORMALIZATION_POLICY_VERSION,
            'analysis_fps': str(self.ANALYSIS_FPS),
            'native_runtime_manifest_sha256': manifest_sha256,
            'native_runtime_manifest_error': manifest_error,
            'media_preparer_sha256': media_preparer_sha256,
            'media_preparer_error': media_preparer_error,
        }

    async def analyze(self, request: CMAnalyzerRequest) -> CMAnalyzerResult:
        """実ストリームを解決し、GPU 一回・CPU 一回の上限で解析する。"""

        missing = [
            str(path) for path in (
                self.ffms2_path,
                self.ffmsindex_path,
                self.chapter_executable_path,
                self.logoframe_path,
                self.join_logo_scp_path,
                self.join_logo_scp_command_path,
                self.runtime_manifest_path,
                self.ffmpeg_path,
                self.ffprobe_path,
            )
            if path.is_file() is False
        ]
        if missing:
            return CMAnalyzerResult(
                status='unsupported',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='AnalyzerUnavailable',
                error_message=f'Required CM analyzer files are missing: {", ".join(missing)}',
            )
        _, manifest_error = self._runtimeManifestFingerprint()
        if manifest_error is not None:
            return CMAnalyzerResult(
                status='unsupported',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='RuntimeManifestInvalid',
                error_message=manifest_error,
            )

        try:
            descriptor = request.input_descriptor or await self.resolveInputDescriptor(request)
        except CMInputUnsupportedError as ex:
            return CMAnalyzerResult(
                status='unsupported',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code=ex.code,
                error_message=ex.message,
            )
        except (OSError, ValueError, json.JSONDecodeError) as ex:
            return CMAnalyzerResult(
                status='analysis_failed',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='MediaProbeFailed',
                error_message=str(ex),
            )
        # 一つの固定Avisynth clipへ安全に正規化できない途中format変更は、
        # 欠落時間軸を正常公開するより入力が変わるまで安定した非対応にする。
        if descriptor.has_variable_video_format:
            return CMAnalyzerResult(
                status='unsupported',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='VariableVideoFormat',
                descriptor=descriptor,
            )
        # 単一 stream を -map する音声正規化では、主音声 PID/stream が途中で交代した
        # 録画の一部を無音のまま成功扱いにしてしまう。連結実装までは安定した非対応にする。
        if descriptor.has_variable_audio_stream:
            return CMAnalyzerResult(
                status='unsupported',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='VariableAudioStream',
                descriptor=descriptor,
            )

        if request.work_directory.is_dir() is False:
            return CMAnalyzerResult(
                status='analysis_failed',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='TemporaryStorageUnavailable',
                error_message='The CM analysis workspace has not been created.',
                descriptor=descriptor,
            )

        # コンテナ・codec・放送種別に依存せず、選択映像だけのMatroskaと
        # chapter_exe用の固定PCM音声へ一度だけ正規化する。映像は全sourceと
        # fallbackが共有し、音声は途中format変更でも時系列順に保持できるWAVに分離する。
        await self._emitStage(request, 'PreparingMedia', 0.10)
        prepared_media_path = request.work_directory / 'prepared-media.cmwork'
        prepared_audio_path = request.work_directory / 'prepared-audio.wav'
        prepare_process = await self._prepareMedia(
            request,
            descriptor,
            prepared_media_path,
            prepared_audio_path,
            self._buildMediaEnvironment(request),
        )
        if (
            prepare_process.return_code != 0
            or prepared_media_path.is_file() is False
            or prepared_audio_path.is_file() is False
        ):
            storage_error = self._temporaryStorageErrorCode(prepare_process.diagnostic)
            decoder_unavailable = self._isDecoderUnavailable(prepare_process.diagnostic)
            return CMAnalyzerResult(
                status='unsupported' if storage_error is None and decoder_unavailable else 'analysis_failed',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code=(
                    storage_error
                    or ('DecoderUnavailable' if decoder_unavailable else 'MediaPreparationFailed')
                ),
                error_message=prepare_process.diagnostic or 'FFmpeg did not produce prepared media.',
                descriptor=descriptor,
            )

        await self._emitStage(request, 'IndexingMedia', 0.25)
        index_path = request.work_directory / 'prepared.ffindex'
        audio_index_path = request.work_directory / 'prepared-audio.ffindex'
        index_process, audio_index_process = await asyncio.gather(
            self._indexMedia(
                request,
                prepared_media_path,
                index_path,
                self._buildEnvironment(request),
            ),
            self._indexMedia(
                request,
                prepared_audio_path,
                audio_index_path,
                self._buildEnvironment(request),
            ),
        )
        failed_index = next(
            (
                process for process, path in (
                    (index_process, index_path),
                    (audio_index_process, audio_index_path),
                )
                if process.return_code != 0 or path.is_file() is False
            ),
            None,
        )
        if failed_index is not None:
            storage_error = self._temporaryStorageErrorCode(failed_index.diagnostic)
            decoder_unavailable = self._isDecoderUnavailable(failed_index.diagnostic)
            return CMAnalyzerResult(
                status='unsupported' if storage_error is None and decoder_unavailable else 'analysis_failed',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code=storage_error or ('DecoderUnavailable' if decoder_unavailable else 'MediaIndexFailed'),
                error_message=failed_index.diagnostic or 'FFMS2 did not produce an index.',
                descriptor=descriptor,
            )
        try:
            prepared_video_index, prepared_audio_index = await asyncio.gather(
                self._resolvePreparedTrack(request, prepared_media_path, 'video'),
                self._resolvePreparedTrack(request, prepared_audio_path, 'audio'),
            )
        except (OSError, ValueError, json.JSONDecodeError) as ex:
            storage_error = self._temporaryStorageExceptionCode(ex) if isinstance(ex, OSError) else None
            return CMAnalyzerResult(
                status='analysis_failed',
                chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code=storage_error or 'PreparedMediaInvalid',
                error_message=str(ex),
                descriptor=descriptor,
            )
        prepared = _PreparedMedia(
            media_path=prepared_media_path,
            index_path=index_path,
            video_stream_index=prepared_video_index,
            audio_path=prepared_audio_path,
            audio_index_path=audio_index_path,
            audio_stream_index=prepared_audio_index,
        )

        attempts: list[tuple[CMDecodeMode, str | None]] = [
            ('Hardware', hardware_device) for hardware_device in request.hardware_devices
        ]
        attempts.append(('CPU', None))
        hardware_failure: CMAnalyzerResult | None = None
        hardware_attempt_index = 0
        for decode_mode, hardware_device in attempts:
            if decode_mode == 'Hardware':
                hardware_attempt_index += 1
                attempt_directory = request.work_directory / f'hardware-{hardware_attempt_index}'
            else:
                attempt_directory = request.work_directory / 'cpu'
            logging.info(
                f'{request.recorded_file_path}: Starting CM decode attempt. '
                f'[decode_mode: {decode_mode}] [hardware_device: {hardware_device or "None"}]'
            )
            try:
                result = await self._analyzeOnce(
                    request,
                    descriptor,
                    prepared,
                    attempt_directory,
                    decode_mode,
                    hardware_device,
                )
            except OSError as ex:
                result = CMAnalyzerResult(
                    status='analysis_failed',
                    chapter_file=None,
                    analyzer_version=self.ANALYZER_VERSION,
                    error_code=self._temporaryStorageExceptionCode(ex) or 'AnalyzerIOFailed',
                    error_message=str(ex),
                    descriptor=descriptor,
                    decode_mode=decode_mode,
                )
            if decode_mode == 'Hardware' and self._isHardwareDecodeFailure(result):
                hardware_failure = result
                logging.warning(
                    f'{request.recorded_file_path}: CM hardware decode attempt failed. Trying next decoder. '
                    f'[hardware_device: {hardware_device}] [error_code: {result.error_code}] '
                    f'[error_message: {result.error_message}]'
                )
                await self._emitStage(request, 'HardwareFallback', None)
                continue
            if hardware_failure is not None and result.status == 'completed':
                logging.info(
                    f'{request.recorded_file_path}: CM decode fallback completed. '
                    f'[decode_mode: {decode_mode}] [hardware_device: {hardware_device or "None"}]'
                )
                return replace(result, warnings=('HardwareDecodeFallback', *result.warnings))
            if (
                decode_mode == 'CPU'
                and result.status == 'analysis_failed'
                and self._isDecoderUnavailable(result.error_message)
            ):
                return replace(result, status='unsupported', error_code='DecoderUnavailable')
            return result

        assert hardware_failure is not None
        return hardware_failure

    async def resolveInputDescriptor(self, request: CMAnalyzerRequest) -> CMInputDescriptor:
        """FFprobe の実データから対象 stream を決定する。"""

        process = await self._runProcessWithDiagnostics((
            str(self.ffprobe_path),
            '-v', 'error',
            *self._inputFormatOptions(request),
            '-show_format',
            '-show_streams',
            '-show_programs',
            '-of', 'json',
            str(request.recorded_file_path),
        ), self._buildMediaEnvironment(request), request.work_directory / 'processes.log')
        if process.return_code != 0:
            raise OSError(process.diagnostic or 'FFprobe failed.')
        payload = json.loads(process.output)
        raw_streams = payload.get('streams')
        if not isinstance(raw_streams, list):
            raise ValueError('FFprobe did not return a stream list.')
        streams = [stream for stream in raw_streams if isinstance(stream, dict)]
        videos = [
            stream for stream in streams
            if stream.get('codec_type') == 'video' and self._isAttachedPicture(stream) is False
        ]
        if not videos:
            raise CMInputUnsupportedError('VideoStreamUnavailable')

        programs = [program for program in payload.get('programs', []) if isinstance(program, dict)]
        matching_program = next(
            (
                program for program in programs
                if request.service_id is not None
                and self._parseInteger(program.get('program_id', program.get('program_num'))) == request.service_id
            ),
            None,
        )
        selected_program: dict[str, object] | None = None
        if matching_program is not None:
            indexes = self._programStreamIndexes(matching_program)
            program_videos = [stream for stream in videos if self._streamIndex(stream) in indexes]
            if program_videos:
                videos = program_videos
                selected_program = matching_program
        video = max(videos, key=self._videoSelectionKey)
        video_index = self._streamIndex(video)

        if selected_program is None:
            selected_program = next(
                (program for program in programs if video_index in self._programStreamIndexes(program)),
                None,
            )
        audios = [stream for stream in streams if stream.get('codec_type') == 'audio']
        if selected_program is not None:
            indexes = self._programStreamIndexes(selected_program)
            audios = [stream for stream in audios if self._streamIndex(stream) in indexes]
        audio = max(audios, key=self._audioSelectionKey) if audios else None
        if audio is None:
            raise CMInputUnsupportedError('AudioStreamUnavailable')

        format_payload = payload.get('format') if isinstance(payload.get('format'), dict) else {}
        duration = self._parsePositiveFloat(format_payload.get('duration'))
        if duration is None:
            duration = self._parsePositiveFloat(video.get('duration'))
        if duration is None and math.isfinite(request.duration_seconds) and request.duration_seconds > 0:
            duration = request.duration_seconds
        if duration is None:
            raise CMInputUnsupportedError('DurationUnavailable')

        width = self._parseInteger(video.get('width')) or 0
        height = self._parseInteger(video.get('height')) or 0
        if width <= 0 or height <= 0:
            raise CMInputUnsupportedError('VideoGeometryUnavailable')
        pixel_format = str(video['pix_fmt']) if video.get('pix_fmt') is not None else None
        bit_depth = self._parseInteger(video.get('bits_per_raw_sample'))
        if bit_depth is None:
            bit_depth = self._inferBitDepth(pixel_format)
        program_id = (
            self._parseInteger(selected_program.get('program_id', selected_program.get('program_num')))
            if selected_program is not None else None
        )
        return CMInputDescriptor(
            format_name=(
                str(format_payload['format_name']) if format_payload.get('format_name') is not None else None
            ),
            video_stream_index=video_index,
            audio_stream_index=self._streamIndex(audio) if audio is not None else None,
            video_codec_name=str(video['codec_name']) if video.get('codec_name') is not None else None,
            pixel_format=pixel_format,
            bit_depth=bit_depth,
            width=width,
            height=height,
            field_order=str(video['field_order']) if video.get('field_order') is not None else None,
            time_base=self._parseFraction(video.get('time_base')),
            source_frame_rate=(
                self._parseFraction(video.get('avg_frame_rate'))
                or self._parseFraction(video.get('r_frame_rate'))
            ),
            duration_seconds=duration,
            program_id=program_id,
            service_id=request.service_id,
            has_variable_video_format=request.has_variable_video_format,
            video_start_time_seconds=self._streamStartSeconds(video),
            audio_start_time_seconds=self._streamStartSeconds(audio),
        )

    async def _analyzeOnce(
        self,
        request: CMAnalyzerRequest,
        descriptor: CMInputDescriptor,
        prepared: _PreparedMedia,
        work_directory: Path,
        decode_mode: CMDecodeMode,
        hardware_device: str | None,
    ) -> CMAnalyzerResult:
        work_directory.mkdir(parents=True, exist_ok=True)
        environment = self._buildEnvironment(request)
        warnings: list[str] = []
        # chapter_exe と logoframe は別々にデコードするが、完成済みMatroskaと
        # FFMS2索引は共有する。索引生成はattempt外で完了している。
        await self._emitStage(request, 'ChapterAnalyzing', 0.30)
        chapter_script = work_directory / 'chapter.avs'
        chapter_output = work_directory / 'chapter-analysis.txt'
        chapter_script.write_text(
            self._buildAviSynthScript(
                descriptor,
                prepared,
                hardware_device,
                for_chapter=True,
            ),
            encoding='utf-8',
        )
        chapter_process = await self._runProcessWithDiagnostics((
            str(self.chapter_executable_path),
            '-v', str(chapter_script),
            '-o', str(chapter_output),
            '-s', '10',
        ), environment, request.work_directory / 'processes.log')
        # chapter_exe は AviSynth の読み込み失敗時にも 0 を返し、不正な出力を
        # 作ることがある。出力中の明示的な AviSynth エラーも工程失敗として扱う。
        if (
            chapter_process.return_code != 0
            or chapter_output.is_file() is False
            or 'avisynth error:' in chapter_process.diagnostic.lower()
        ):
            return self._failure(
                'ChapterExeFailed', chapter_process, descriptor, decode_mode, warnings,
            )
        try:
            chapter_text = chapter_output.read_text(encoding='utf-8-sig')
            total_frames = self._parseChapterFrameCount(chapter_process, chapter_text)
        except OSError as ex:
            return CMAnalyzerResult(
                status='analysis_failed', chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code=self._temporaryStorageExceptionCode(ex) or 'ChapterOutputInvalid',
                error_message=str(ex),
                warnings=tuple(warnings), descriptor=descriptor, decode_mode=decode_mode,
            )
        except ValueError as ex:
            return CMAnalyzerResult(
                status='analysis_failed', chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='ChapterOutputInvalid', error_message=str(ex),
                warnings=tuple(warnings), descriptor=descriptor, decode_mode=decode_mode,
            )

        logo_output = _LogoFrameOutput(None, None, None)
        usable_logos = tuple(path for path in request.logo_paths if path.is_file())
        if len(usable_logos) != len(request.logo_paths):
            warnings.append('LogoTemplateMissing')
        if usable_logos:
            await self._emitStage(request, 'LogoAnalyzing', 0.45)
            logo_worker_count = self._logoFrameWorkerCount(total_frames)
            logo_script = work_directory / 'logo.avs'
            logo_script.write_text(
                self._buildAviSynthScript(
                    descriptor,
                    prepared,
                    # 範囲分割 worker ごとに HW decoder context を作ると、単一 GPU 上で
                    # decode が直列化・競合して CPU 並列より大幅に遅くなる。chapter は
                    # HW のまま、logoframe は Amatsukaze と同じ範囲分割を CPU で並列する。
                    None,
                    for_chapter=False,
                    logo_worker_count=logo_worker_count,
                ),
                encoding='utf-8',
            )
            logo_process, logo_output = await self._runLogoFrame(
                logo_script,
                usable_logos,
                work_directory,
                environment,
                logo_worker_count,
                request.work_directory / 'processes.log',
            )
            if logo_process.return_code != 0:
                return self._failure(
                    'LogoFrameFailed', logo_process, descriptor, decode_mode, warnings,
                )
            warnings.extend(logo_output.warnings)
            if logo_output.frame_count is not None and logo_output.frame_count != total_frames:
                return CMAnalyzerResult(
                    status='analysis_failed', chapter_file=None,
                    analyzer_version=self.ANALYZER_VERSION,
                    error_code='AnalyzerFrameCountMismatch',
                    error_message=(
                        f'chapter_exe={total_frames}, logoframe={logo_output.frame_count}'
                    ),
                    warnings=tuple(warnings), descriptor=descriptor, decode_mode=decode_mode,
                )
        else:
            warnings.append('LogoUnavailable')

        await self._emitStage(request, 'CombiningCM', 0.90)
        trim_output = work_directory / 'trim.avs'
        detail_output = work_directory / 'jls-detail.txt'
        div_output = work_directory / 'jls-div.txt'
        command: list[str] = [str(self.join_logo_scp_path)]
        if logo_output.path is not None:
            command.extend(('-inlogo', str(logo_output.path)))
        command.extend((
            '-inscp', str(chapter_output),
            '-incmd', str(self.join_logo_scp_command_path),
            '-o', str(trim_output),
            '-oscp', str(detail_output),
            '-odiv', str(div_output),
        ))
        jls_environment = dict(environment)
        jls_environment.update({
            'CLI_IN_PATH': str(request.recorded_file_path),
            'TS_IN_PATH': str(request.recorded_file_path),
            'SERVICE_ID': str(descriptor.program_id or request.service_id or 0),
            'CLI_OUT_PATH': str(work_directory / 'result'),
        })
        jls_process = await self._runProcessWithDiagnostics(
            tuple(command),
            jls_environment,
            request.work_directory / 'processes.log',
        )
        if jls_process.return_code != 0 or trim_output.is_file() is False:
            return self._failure(
                'JoinLogoScpFailed', jls_process, descriptor, decode_mode, warnings,
            )
        try:
            trim_text = trim_output.read_text(encoding='utf-8-sig')
            sections = self._parseCMSections(trim_text, total_frames, self.ANALYSIS_FPS)
            normalized_duration = total_frames / float(self.ANALYSIS_FPS)
            chapter_path = work_directory / 'generated.cmchapter'
            chapter_path.write_text(
                self._buildChapterText(sections, normalized_duration),
                encoding='utf-8',
            )
        except OSError as ex:
            return CMAnalyzerResult(
                status='analysis_failed', chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code=self._temporaryStorageExceptionCode(ex) or 'AnalyzerOutputInvalid',
                error_message=str(ex),
                warnings=tuple(warnings), descriptor=descriptor, decode_mode=decode_mode,
                total_frames=total_frames,
            )
        except ValueError as ex:
            return CMAnalyzerResult(
                status='analysis_failed', chapter_file=None,
                analyzer_version=self.ANALYZER_VERSION,
                error_code='AnalyzerOutputInvalid', error_message=str(ex),
                warnings=tuple(warnings), descriptor=descriptor, decode_mode=decode_mode,
                total_frames=total_frames,
            )
        return CMAnalyzerResult(
            status='completed',
            chapter_file=chapter_path,
            sections=tuple(sections),
            matched_logo=logo_output.matched_logo,
            analysis_fps=str(self.ANALYSIS_FPS),
            library='FFMS2-FFmpeg8-LGPL',
            analyzer_version=self.ANALYZER_VERSION,
            warnings=tuple(warnings),
            descriptor=descriptor,
            decode_mode=decode_mode,
            total_frames=total_frames,
        )

    async def _runLogoFrame(
        self,
        script_path: Path,
        logo_paths: tuple[Path, ...],
        work_directory: Path,
        environment: dict[str, str],
        worker_count: int,
        diagnostic_log_path: Path,
    ) -> tuple[_ProcessResult, _LogoFrameOutput]:
        analysis_output = work_directory / 'logoframe-analysis.txt'
        command = [
            str(self.logoframe_path), str(script_path),
            '-oanum', str(len(logo_paths)),
            '-oasel', '1',
            '-oa', str(analysis_output),
            '-parallel', str(worker_count),
            '-dispoff', '1',
            '-paramoff', '1',
        ]
        for index, logo_path in enumerate(logo_paths, start=1):
            command.extend((f'-logo{index}', str(logo_path)))
        process = await self._runProcessWithDiagnostics(tuple(command), environment, diagnostic_log_path)
        if process.return_code != 0:
            return process, _LogoFrameOutput(None, None, None)
        list_path = analysis_output.with_name(f'{analysis_output.stem}_list.ini')
        if list_path.is_file() is False:
            return process, _LogoFrameOutput(None, None, None, ('LogoNotMatched',))
        try:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read(list_path, encoding='utf-8-sig')
            section = parser['logodata']
            frame_count = int(section['FrameTotal'])
            if int(section.get('LogoTotalN', '0')) <= 0:
                return process, _LogoFrameOutput(None, None, frame_count, ('LogoNotMatched',))
            matched_logo = Path(section['LogoName_N1']).name
            output_name = section.get('oaFileName_N1')
            output_path = Path(output_name) if output_name else None
            if output_path is not None and output_path.is_absolute() is False:
                output_path = work_directory / output_path
            if output_path is None or output_path.is_file() is False or output_path.stat().st_size == 0:
                return process, _LogoFrameOutput(None, None, frame_count, ('LogoMatchAmbiguous',))
            return process, _LogoFrameOutput(output_path, matched_logo, frame_count)
        except (KeyError, OSError, ValueError, configparser.Error) as ex:
            return process, _LogoFrameOutput(None, None, None, (f'LogoResultInvalid:{type(ex).__name__}',))

    @classmethod
    def _logoFrameWorkerCount(cls, total_frames: int) -> int:
        """Amatsukaze と同じ基準で範囲分割ロゴ走査の worker 数を決める。"""

        processor_count = cls._effectiveCPUCount()
        preferred_workers = cls._preferredLogoWorkers(processor_count)
        workers_for_duration = max(
            1,
            (max(0, total_frames) + cls._LOGO_FRAME_MIN_FRAMES_PER_WORKER // 2)
            // cls._LOGO_FRAME_MIN_FRAMES_PER_WORKER,
        )
        return max(1, min(processor_count, preferred_workers, workers_for_duration))

    @classmethod
    def _preferredLogoWorkers(cls, processor_count: int) -> int:
        candidates = range(1, cls._LOGO_FRAME_MAX_WORKERS + 1)
        return min(
            candidates,
            key=lambda workers: (
                processor_count % workers
                + max(0, processor_count // workers - 4),
                abs(workers - 8),
                workers,
            ),
        )

    @staticmethod
    def _effectiveCPUCount() -> int:
        try:
            return max(1, len(os.sched_getaffinity(0)))
        except (AttributeError, OSError):
            return max(1, os.cpu_count() or 1)

    @classmethod
    def _chapterDecoderThreadCount(cls) -> int:
        """全編を1プロセスで走査する chapter_exe 用の decoder thread 数。"""

        return min(16, cls._effectiveCPUCount())

    @classmethod
    def _logoDecoderThreadCount(cls, worker_count: int) -> int:
        """範囲分割された各 logoframe worker 用の decoder thread 数。"""

        processors = cls._effectiveCPUCount()
        return max(1, min(16, processors // max(1, worker_count)))

    def _buildAviSynthScript(
        self,
        descriptor: CMInputDescriptor,
        prepared: _PreparedMedia,
        hardware_device: str | None,
        *,
        for_chapter: bool,
        logo_worker_count: int | None = None,
    ) -> str:
        source = self._escapeAviSynthString(str(prepared.media_path))
        plugin = self._escapeAviSynthString(str(self.ffms2_path))
        cache = self._escapeAviSynthString(str(prepared.index_path))
        hardware = '' if hardware_device is None else (
            f', hwdevice="{self._escapeAviSynthString(hardware_device)}", extrahwframes=8'
        )
        if for_chapter:
            decoder_threads = self._chapterDecoderThreadCount()
        else:
            if logo_worker_count is None:
                raise ValueError('logo_worker_count is required for the logoframe script')
            decoder_threads = self._logoDecoderThreadCount(logo_worker_count)
        # logoframe には FFMS2 の native planar luma をそのまま渡す。
        # colorspace=Y* を強制すると swscale が limited/full range を誤変換し得るほか、
        # Y14 は AviSynth+ の明示的な出力色空間として扱えない。
        video_common = (
            f'fpsnum={self.ANALYSIS_FPS.numerator}, fpsden={self.ANALYSIS_FPS.denominator}, '
            f'cache=true, cachefile="{cache}", threads={decoder_threads}'
            f'{hardware}'
        )
        lines = [
            'ClearAutoloadDirs()',
            f'LoadPlugin("{plugin}")',
            (
                f'video = FFVideoSource("{source}", track={prepared.video_stream_index}, '
                f'{video_common})'
            ),
        ]
        if for_chapter:
            audio_source = self._escapeAviSynthString(str(prepared.audio_path))
            audio_cache = self._escapeAviSynthString(str(prepared.audio_index_path))
            lines.append(
                f'audio = FFAudioSource("{audio_source}", track={prepared.audio_stream_index}, '
                f'cache=true, cachefile="{audio_cache}", adjustdelay=-3, fill_gaps=1)'
            )
            lines.append('clip = AudioDubEx(video, audio)')
            if (
                descriptor.video_start_time_seconds is not None
                and descriptor.audio_start_time_seconds is not None
            ):
                audio_delay = descriptor.audio_start_time_seconds - descriptor.video_start_time_seconds
                if abs(audio_delay) >= (0.5 / 48_000):
                    lines.append(f'clip = DelayAudio(clip, {audio_delay:.9f})')
        else:
            lines.append('clip = video')
        if descriptor.field_order in ('tt', 'tb', 'tff'):
            lines.append('clip = AssumeTFF(clip)')
        elif descriptor.field_order in ('bb', 'bt', 'bff'):
            lines.append('clip = AssumeBFF(clip)')
        if for_chapter:
            lines.extend((
                'clip = (Width(clip) % 2 == 0 && Height(clip) % 2 == 0) ? clip : '
                'AddBorders(clip, 0, 0, Width(clip) % 2, Height(clip) % 2)',
                'clip = ConvertBits(clip, 8)',
                'clip = ConvertToYV12(clip)',
            ))
            target_width, target_height = self._chapterDimensions(descriptor.width, descriptor.height)
            if target_width != descriptor.width or target_height != descriptor.height:
                lines.append(f'clip = Spline36Resize(clip, {target_width}, {target_height})')
        lines.extend(('clip = Prefetch(clip, 1)', 'return clip'))
        return '\n'.join(lines) + '\n'

    async def _prepareMedia(
        self,
        request: CMAnalyzerRequest,
        descriptor: CMInputDescriptor,
        video_output_path: Path,
        audio_output_path: Path,
        environment: Mapping[str, str],
    ) -> _ProcessResult:
        """選択映像と固定PCM音声を、一回の入力走査で別々の媒体へ正規化する。"""

        if descriptor.audio_stream_index is None:
            return _ProcessResult(-1, '', 'The selected audio stream is missing.')
        video_partial_path = video_output_path.with_name(f'{video_output_path.name}.partial')
        raw_audio_partial_path = audio_output_path.with_name(
            f'{audio_output_path.stem}.normalized.partial.wav',
        )
        filters = ['asetpts=PTS-STARTPTS', 'aresample=48000:async=1000:first_pts=0']
        temporary_paths = (
            video_partial_path,
            raw_audio_partial_path,
        )
        published_paths: list[Path] = []
        try:
            process = await self._runProcessWithDiagnostics((
                str(self.ffmpeg_path),
                '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                '-fflags', '+genpts+discardcorrupt',
                *self._inputFormatOptions(request),
                '-i', str(request.recorded_file_path),
                # video-only Matroska: chapterとlogoがこの同じ媒体・indexを読む。
                '-map', f'0:{descriptor.video_stream_index}',
                '-an', '-sn', '-dn',
                '-c:v', 'copy',
                '-map_metadata', '-1', '-map_chapters', '-1',
                '-f', 'matroska',
                str(video_partial_path),
                # 独立WAV muxerは、同一stream内の2ch/5.1ch切替でfilter graphが
                # 再初期化されても、正規化済みsampleを順番通り保持する。
                '-map', f'0:{descriptor.audio_stream_index}',
                '-vn', '-sn', '-dn',
                '-filter:a', ','.join(filters),
                '-ac', '1', '-ar', '48000', '-sample_fmt', 's16',
                '-c:a', 'pcm_s16le',
                '-map_metadata', '-1', '-map_chapters', '-1',
                '-rf64', 'auto',
                '-f', 'wav',
                str(raw_audio_partial_path),
            ), environment, request.work_directory / 'processes.log')
            if process.return_code != 0:
                self._removeFiles(temporary_paths)
                return process
            if video_partial_path.is_file() is False or raw_audio_partial_path.is_file() is False:
                self._removeFiles(temporary_paths)
                return _ProcessResult(
                    -1,
                    process.output,
                    'FFmpeg did not produce both prepared video and audio.',
                )

            # work directoryはjob専用でconsumerはこの関数の完了後にだけ起動する。
            # 両方が完成してから公開し、片方のrename失敗時は公開済み側も除去する。
            os.replace(video_partial_path, video_output_path)
            published_paths.append(video_output_path)
            os.replace(raw_audio_partial_path, audio_output_path)
            published_paths.append(audio_output_path)
            self._removeFiles(temporary_paths)
            return process
        except asyncio.CancelledError:
            self._removeFiles((*temporary_paths, *published_paths))
            raise
        except OSError as ex:
            self._removeFiles((*temporary_paths, *published_paths))
            return _ProcessResult(-1, '', str(ex))

    @staticmethod
    def _removeFiles(paths: tuple[Path, ...]) -> None:
        for path in paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    async def _indexMedia(
        self,
        request: CMAnalyzerRequest,
        media_path: Path,
        index_path: Path,
        environment: Mapping[str, str],
    ) -> _ProcessResult:
        """全trackを一度だけindexし、完成後に原子的に公開する。"""

        partial_path = index_path.with_name(f'{index_path.name}.partial')
        process = await self._runProcessWithDiagnostics((
            str(self.ffmsindex_path),
            '-f', '-t', '-1',
            str(media_path), str(partial_path),
        ), environment, request.work_directory / 'processes.log')
        if process.return_code == 0 and partial_path.is_file():
            try:
                os.replace(partial_path, index_path)
            except OSError as ex:
                return _ProcessResult(-1, process.output, str(ex))
        return process

    async def _resolvePreparedTrack(
        self,
        request: CMAnalyzerRequest,
        media_path: Path,
        track_type: Literal['video', 'audio'],
    ) -> int:
        process = await self._runProcessWithDiagnostics((
            str(self.ffprobe_path),
            '-v', 'error', '-show_streams', '-of', 'json', str(media_path),
        ), self._buildMediaEnvironment(request), request.work_directory / 'processes.log')
        if process.return_code != 0:
            raise OSError(process.diagnostic or 'Prepared media FFprobe failed.')
        payload = json.loads(process.output)
        streams = payload.get('streams')
        if not isinstance(streams, list):
            raise ValueError('Prepared media has no stream list.')
        videos = [stream for stream in streams if isinstance(stream, dict) and stream.get('codec_type') == 'video']
        audios = [stream for stream in streams if isinstance(stream, dict) and stream.get('codec_type') == 'audio']
        if track_type == 'video' and (len(videos) != 1 or audios):
            raise ValueError('Prepared video media must contain exactly one video and no audio tracks.')
        if track_type == 'audio' and (len(audios) != 1 or videos):
            raise ValueError('Prepared audio media must contain exactly one audio and no video tracks.')
        return self._streamIndex(videos[0] if track_type == 'video' else audios[0])

    @staticmethod
    async def _emitStage(
        request: CMAnalyzerRequest,
        stage: CMAnalysisStage,
        progress: float | None,
    ) -> None:
        if request.stage_callback is not None:
            await request.stage_callback(stage, progress)

    @staticmethod
    def _inputFormatOptions(request: CMAnalyzerRequest) -> tuple[str, ...]:
        """録画コンテナに応じて FFmpeg / FFprobe の入力 demuxer を固定する。"""

        # Raw TLV は拡張子や先頭バイトだけでは安定して自動判定できないため、
        # libaribtlv を組み込んだ同梱 FFmpeg に demuxer を明示する。
        if request.container_format == 'MMT/TLV':
            return ('-f', 'libaribtlv')
        return ()

    @classmethod
    def _parseCMSections(
        cls,
        trim_text: str,
        total_frames: int,
        fps: Fraction,
    ) -> list[CMSectionJSON]:
        keep_ranges = [
            (int(match.group(1)), int(match.group(2)) + 1)
            for match in cls._TRIM_PATTERN.finditer(trim_text)
        ]
        if not keep_ranges:
            raise ValueError('JLS did not emit a determinate Trim range.')
        if total_frames <= 0:
            raise ValueError('Analysis frame count must be positive.')
        previous_end = 0
        for start, end in keep_ranges:
            if start < 0 or end <= start or end > total_frames:
                raise ValueError(f'Invalid JLS Trim range: {start}-{end - 1}')
            if start < previous_end:
                raise ValueError('JLS Trim ranges overlap or are out of order.')
            previous_end = end
        cursor = 0
        sections: list[CMSectionJSON] = []
        for start, end in keep_ranges:
            if cursor < start:
                sections.append(CMSectionJSON(
                    start_time=round(cursor / float(fps), 6),
                    end_time=round(start / float(fps), 6),
                ))
            cursor = end
        if cursor < total_frames:
            sections.append(CMSectionJSON(
                start_time=round(cursor / float(fps), 6),
                end_time=round(total_frames / float(fps), 6),
            ))
        return sections

    @staticmethod
    def _buildChapterText(sections: list[CMSectionJSON], duration_seconds: float) -> str:
        boundaries: dict[int, str] = {0: 'Program'}
        duration_ms = max(0, round(duration_seconds * 1000))
        for section in sections:
            start_ms = max(0, min(duration_ms, round(section['start_time'] * 1000)))
            end_ms = max(0, min(duration_ms, round(section['end_time'] * 1000)))
            if end_ms <= start_ms:
                raise ValueError('CM section end must be greater than start.')
            boundaries[start_ms] = 'CM'
            if end_ms < duration_ms:
                boundaries[end_ms] = 'Program'
        if len(boundaries) > 99:
            raise ValueError('Generated chapter contains more than 99 entries.')
        lines: list[str] = []
        for index, (milliseconds, name) in enumerate(sorted(boundaries.items()), start=1):
            hours, remainder = divmod(milliseconds, 3_600_000)
            minutes, remainder = divmod(remainder, 60_000)
            seconds, millis = divmod(remainder, 1000)
            lines.append(f'CHAPTER{index:02d}={hours:02d}:{minutes:02d}:{seconds:02d}.{millis:03d}')
            lines.append(f'CHAPTER{index:02d}NAME={name}')
        return '\n'.join(lines) + '\n'

    async def _runProcess(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
    ) -> _ProcessResult:
        """
        外部プロセスを実行し、省略していない標準出力と標準エラーを返す。

        Args:
            command (tuple[str, ...]): 実行するコマンドと引数。
            environment (Mapping[str, str]): プロセスへ渡す環境変数。

        Returns:
            _ProcessResult: 終了コードと標準出力・標準エラー。
        """

        started_at = datetime.now().astimezone().isoformat(timespec='milliseconds')
        started_monotonic = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=dict(environment),
                start_new_session=True,
            )
        except OSError as ex:
            result = _ProcessResult(
                -1,
                '',
                str(ex),
                command,
                started_at,
                time.monotonic() - started_monotonic,
            )
            return result
        try:
            stdout, stderr = await process.communicate()
        except asyncio.CancelledError:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.wait(), timeout=10)
            except TimeoutError:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                await process.wait()
            raise
        result = _ProcessResult(
            process.returncode or 0,
            stdout.decode('utf-8', errors='replace').strip(),
            stderr.decode('utf-8', errors='replace').strip(),
            command,
            started_at,
            time.monotonic() - started_monotonic,
        )
        return result

    async def _runProcessWithDiagnostics(
        self,
        command: tuple[str, ...],
        environment: Mapping[str, str],
        diagnostic_log_path: Path,
    ) -> _ProcessResult:
        """
        共通実行境界の結果へ再現条件を補い、完全な診断ログを保存する。

        Args:
            command (tuple[str, ...]): 実行するコマンドと引数。
            environment (Mapping[str, str]): プロセスへ渡す環境変数。
            diagnostic_log_path (Path): 全工程の診断ログを追記するファイル。

        Returns:
            _ProcessResult: 終了コードと省略していない標準出力・標準エラー。
        """

        started_at = datetime.now().astimezone().isoformat(timespec='milliseconds')
        started_monotonic = time.monotonic()
        process_result = await self._runProcess(command, environment)
        result = replace(
            process_result,
            command=command,
            started_at=process_result.started_at or started_at,
            elapsed_seconds=(
                process_result.elapsed_seconds
                if process_result.elapsed_seconds is not None
                else time.monotonic() - started_monotonic
            ),
        )
        await self._appendProcessDiagnostic(diagnostic_log_path, result, environment)
        return result

    @staticmethod
    async def _appendProcessDiagnostic(
        diagnostic_log_path: Path,
        result: _ProcessResult,
        environment: Mapping[str, str],
    ) -> None:
        """
        一つの外部プロセスの再現条件と完全な出力を job 共通ログへ追記する。

        Args:
            diagnostic_log_path (Path): 追記先。
            result (_ProcessResult): 保存するプロセス実行結果。
            environment (Mapping[str, str]): 実行時の環境変数。

        Returns:
            None
        """

        # native library・VAAPI・一時領域の差を比較できる値だけを残す。
        ## token や credential を含み得る環境変数は値を出力しない。
        environment_keys = (
            'HOME',
            'LANG',
            'LC_ALL',
            'LD_LIBRARY_PATH',
            'LIBVA_DRIVER_NAME',
            'LIBVA_DRIVERS_PATH',
            'PATH',
            'PWD',
            'TMPDIR',
            'USER',
            'XDG_RUNTIME_DIR',
        )
        environment_lines = [
            f'{key}={environment[key]}'
            for key in environment_keys
            if key in environment
        ]
        diagnostic = '\n'.join((
            '========== CM analyzer process ==========',
            f'Working directory: {os.getcwd()}',
            '----- environment -----',
            *(environment_lines or ['(no relevant variables set)']),
            result.diagnostic,
            '',
        ))

        def Append() -> None:
            """
            診断ログへ一回分の情報を追記する。

            Returns:
                None
            """

            with diagnostic_log_path.open('a', encoding='utf-8') as file:
                file.write(diagnostic)

        try:
            await asyncio.to_thread(Append)
        except OSError as ex:
            # 診断ログ自体の保存失敗で本来の解析結果を上書きしない。
            logging.warning(
                f'Failed to write CM analyzer diagnostic log. [path: {diagnostic_log_path}]',
                exc_info=ex,
            )

    def _buildEnvironment(self, request: CMAnalyzerRequest) -> dict[str, str]:
        """AviSynth/FFMS2用private libraryを優先するnative解析環境を返す。"""

        environment = self._buildMediaEnvironment(request)
        # AviSynth+ 3.7.3 は初期化時に getenv("HOME") の戻り値を null check せず
        ## std::string へ渡すため、HOME を設定しない systemd service では chapter_exe が
        ## basic_string::_M_construct null not valid で即座に abort する。
        ## job 専用ディレクトリを HOME にして、ユーザー側 AviSynth plugin の自動読込も避ける。
        environment['HOME'] = str(request.work_directory)
        existing = environment.get('LD_LIBRARY_PATH')
        paths = [str(self.runtime_directory)]
        if existing:
            paths.append(existing)
        environment['LD_LIBRARY_PATH'] = ':'.join(paths)
        return environment

    @staticmethod
    def _buildMediaEnvironment(request: CMAnalyzerRequest) -> dict[str, str]:
        """playback FFmpeg8/FFprobe8が自身のRUNPATHを使う媒体処理環境を返す。"""

        environment = dict(os.environ)
        environment['LC_ALL'] = 'C.UTF-8'
        if request.hardware_environment is not None:
            environment.update(request.hardware_environment)
        return environment

    def _runtimeManifestFingerprint(self) -> tuple[str | None, str | None]:
        """固定manifestを検証し、attempt keyへ入れる内容SHA-256を返す。"""

        try:
            content = self.runtime_manifest_path.read_bytes()
        except OSError as ex:
            return None, f'{type(ex).__name__}: {ex}'
        digest = hashlib.sha256(content).hexdigest()
        try:
            payload = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as ex:
            return digest, f'{type(ex).__name__}: {ex}'
        if (
            not isinstance(payload, dict)
            or payload.get('schema_version') != 1
            or not isinstance(payload.get('components'), dict)
            or not isinstance(payload.get('patches'), dict)
        ):
            return digest, 'The CM runtime manifest schema is invalid.'
        return digest, None

    @staticmethod
    def _fileFingerprint(path: Path) -> tuple[str | None, str | None]:
        """外部実行ファイルの内容をattempt keyへ含める。"""

        try:
            return hashlib.sha256(path.read_bytes()).hexdigest(), None
        except OSError as ex:
            return None, f'{type(ex).__name__}: {ex}'

    @classmethod
    def _parseChapterFrameCount(cls, process: _ProcessResult, chapter_text: str) -> int:
        match = cls._VIDEO_FRAME_COUNT_PATTERN.search(f'{process.output}\n{process.error_output}')
        if match is None:
            raise ValueError('chapter_exe did not report its analysis frame count.')
        total_frames = int(match.group(1))
        if total_frames <= 0:
            raise ValueError('chapter_exe reported an invalid frame count.')
        final_scene = list(cls._FINAL_SCENE_PATTERN.finditer(chapter_text))
        if not final_scene:
            raise ValueError('chapter_exe output has no final SCPos marker.')
        if int(final_scene[-1].group(1)) != total_frames - 1:
            raise ValueError('chapter_exe frame count and final SCPos disagree.')
        return total_frames

    @staticmethod
    def _streamIndex(stream: dict[str, object] | None) -> int:
        if stream is None:
            raise ValueError('Stream is missing.')
        value = GenericCMAnalyzer._parseInteger(stream.get('index'))
        if value is None or value < 0:
            raise ValueError('Stream index is missing or invalid.')
        return value

    @staticmethod
    def _programStreamIndexes(program: dict[str, object]) -> set[int]:
        result: set[int] = set()
        streams = program.get('streams')
        if isinstance(streams, list):
            for stream in streams:
                if not isinstance(stream, dict):
                    continue
                index = GenericCMAnalyzer._parseInteger(stream.get('index'))
                if index is not None:
                    result.add(index)
        return result

    @classmethod
    def _videoSelectionKey(cls, stream: dict[str, object]) -> tuple[int, float, int, int]:
        width = cls._parseInteger(stream.get('width')) or 0
        height = cls._parseInteger(stream.get('height')) or 0
        return (
            cls._dispositionValue(stream, 'default'),
            cls._parsePositiveFloat(stream.get('duration')) or 0.0,
            width * height,
            -cls._streamIndex(stream),
        )

    @classmethod
    def _audioSelectionKey(cls, stream: dict[str, object]) -> tuple[int, float, int]:
        return (
            cls._dispositionValue(stream, 'default'),
            cls._parsePositiveFloat(stream.get('duration')) or 0.0,
            -cls._streamIndex(stream),
        )

    @staticmethod
    def _dispositionValue(stream: dict[str, object], key: str) -> int:
        disposition = stream.get('disposition')
        if isinstance(disposition, dict):
            return 1 if disposition.get(key) in (1, '1', True) else 0
        return 0

    @classmethod
    def _isAttachedPicture(cls, stream: dict[str, object]) -> bool:
        return cls._dispositionValue(stream, 'attached_pic') == 1

    @staticmethod
    def _parseInteger(value: object) -> int | None:
        try:
            return int(cast(str | int, value))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parsePositiveFloat(value: object) -> float | None:
        try:
            parsed = float(cast(str | int | float, value))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) and parsed > 0 else None

    @staticmethod
    def _parseFraction(value: object) -> Fraction | None:
        if not isinstance(value, str) or value in ('', '0/0', 'N/A'):
            return None
        try:
            parsed = Fraction(value)
        except (ValueError, ZeroDivisionError):
            return None
        return parsed if parsed > 0 else None

    @classmethod
    def _streamStartSeconds(cls, stream: dict[str, object] | None) -> float | None:
        if stream is None:
            return None
        start_time = cls._parseFiniteFloat(stream.get('start_time'))
        if start_time is not None:
            return start_time
        start_pts = cls._parseInteger(stream.get('start_pts'))
        time_base = cls._parseFraction(stream.get('time_base'))
        if start_pts is None or time_base is None:
            return None
        return float(start_pts * time_base)

    @staticmethod
    def _parseFiniteFloat(value: object) -> float | None:
        try:
            parsed = float(cast(str | int | float, value))
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _inferBitDepth(pixel_format: str | None) -> int | None:
        if pixel_format is None:
            return None
        match = re.search(r'(9|10|12|14|16)(?:le|be)?$', pixel_format)
        return int(match.group(1)) if match is not None else 8

    @staticmethod
    def _chapterDimensions(width: int, height: int) -> tuple[int, int]:
        if width <= 1920 and height <= 1080 and width % 16 == 0 and height % 2 == 0:
            return width, height
        scale = min(1.0, 1920 / width, 1080 / height)
        target_width = max(16, math.floor(width * scale / 16) * 16)
        target_height = max(2, math.floor(height * scale / 2) * 2)
        return target_width, target_height

    @classmethod
    def _isHardwareDecodeFailure(cls, result: CMAnalyzerResult) -> bool:
        if result.status != 'analysis_failed':
            return False
        # FFMS2 の VAAPI 経路で chapter_exe 自体が abort した場合、FFMS2-HW の識別文字列が stderr に残らない。
        ## 同じ入力が別の render node や CPU では正常に解析できるため、ハードウェア実行時の ChapterExeFailed も再試行する。
        if result.error_code == 'ChapterExeFailed':
            return True
        text = ' '.join(filter(None, (result.error_code, result.error_message))).lower()
        return cls._HARDWARE_FAILURE_MARKER in text

    @classmethod
    def _isDecoderUnavailable(cls, message: str | None) -> bool:
        text = (message or '').lower()
        return any(marker in text for marker in cls._DECODER_UNAVAILABLE_MARKERS)

    @classmethod
    def _temporaryStorageErrorCode(cls, message: str | None) -> str | None:
        text = (message or '').lower()
        if any(marker in text for marker in cls._STORAGE_INSUFFICIENT_MARKERS):
            return 'TemporaryStorageInsufficient'
        if any(marker in text for marker in cls._STORAGE_UNAVAILABLE_MARKERS):
            return 'TemporaryStorageUnavailable'
        return None

    @classmethod
    def _temporaryStorageExceptionCode(cls, exception: OSError) -> str | None:
        if exception.errno in (errno.ENOSPC, errno.EDQUOT):
            return 'TemporaryStorageInsufficient'
        if exception.errno in (errno.EACCES, errno.EPERM, errno.EROFS):
            return 'TemporaryStorageUnavailable'
        return cls._temporaryStorageErrorCode(str(exception))

    @staticmethod
    def _escapeAviSynthString(value: str) -> str:
        return value.replace('\\', '\\\\').replace('"', '\\"')

    def _failure(
        self,
        code: str,
        process: _ProcessResult,
        descriptor: CMInputDescriptor,
        decode_mode: CMDecodeMode,
        warnings: list[str],
    ) -> CMAnalyzerResult:
        diagnostic = process.diagnostic or f'Process exited with code {process.return_code}.'
        return CMAnalyzerResult(
            status='analysis_failed',
            chapter_file=None,
            analyzer_version=self.ANALYZER_VERSION,
            error_code=self._temporaryStorageErrorCode(diagnostic) or code,
            error_message=diagnostic,
            warnings=tuple(warnings),
            descriptor=descriptor,
            decode_mode=decode_mode,
        )


class UnavailableCMAnalyzer:
    """CM ランタイムを搭載しない環境向け。"""

    @property
    def runtimeFingerprint(self) -> dict[str, object]:
        """ランタイム非搭載状態を自動再試行keyへ含める。"""

        return {
            'analyzer_version': GenericCMAnalyzer.ANALYZER_VERSION,
            'available': False,
        }

    async def resolveInputDescriptor(self, request: CMAnalyzerRequest) -> CMInputDescriptor:
        """ランタイム非搭載をprobe前に確定する。"""

        del request
        raise CMInputUnsupportedError(
            'AnalyzerUnavailable',
            'The KonomiTV CM analyzer runtime is not installed.',
        )

    async def analyze(self, request: CMAnalyzerRequest) -> CMAnalyzerResult:
        del request
        return CMAnalyzerResult(
            status='unsupported',
            chapter_file=None,
            analyzer_version=GenericCMAnalyzer.ANALYZER_VERSION,
            error_code='AnalyzerUnavailable',
            error_message='The KonomiTV CM analyzer runtime is not installed.',
        )
