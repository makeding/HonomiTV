import os
import pathlib
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import cv2

from app.constants import LIBRARY_PATH
from app.metadata.RecordingThumbnailGenerator import RecordingThumbnailGenerator
from app.utils.TSKeyFrameSeeker import TSKeyFrameSeeker


class RecordingThumbnailCandidateTest(unittest.TestCase):
    """録画開始マージンと安定済み末尾を含む候補時刻判定を検証する。"""

    def test_candidate_offsets_wait_for_program_five_minutes_and_stable_tail(self) -> None:
        """録画開始前30秒を除いた番組5分と、末尾10秒の安定余白が揃うまで候補を返さない。"""

        self.assertEqual(
            RecordingThumbnailGenerator.calculateCandidateOffsets(
                recording_start_margin=30.0,
                available_duration_seconds=344.9,
            ),
            [],
        )
        self.assertEqual(
            RecordingThumbnailGenerator.calculateCandidateOffsets(
                recording_start_margin=30.0,
                available_duration_seconds=345.0,
            ),
            [325.0, 330.0, 335.0],
        )


class RecordingThumbnailExtractionTest(unittest.IsolatedAsyncioTestCase):
    """成長端を含む実 MPEG-TS から bounded 入力で代表 WebP を生成できることを検証する。"""

    # テストが使用する一時領域は固定名・64MiB 未満とし、次回開始時と finally の双方で限定削除する。
    TEMPORARY_SIZE_BUDGET_BYTES = 64 * 1024 * 1024
    WORK_DIRECTORY = (
        pathlib.Path(tempfile.gettempdir())
        if os.name == 'nt'
        else pathlib.Path('/tmp')
    ) / 'konomitv-recording-thumbnail-generator-test'

    async def test_extracts_cover_without_reading_bytes_appended_after_snapshot(self) -> None:
        """スナップショット後に増えた末尾を候補入力へ含めず、実 FFmpeg で完全な WebP を生成する。"""

        bundled_ffmpeg = pathlib.Path(LIBRARY_PATH['FFmpeg'])
        ffmpeg_path = str(bundled_ffmpeg) if bundled_ffmpeg.is_file() else shutil.which('ffmpeg')
        if ffmpeg_path is None:
            self.skipTest('FFmpeg is not available.')

        if self.WORK_DIRECTORY.exists():
            shutil.rmtree(self.WORK_DIRECTORY)
        self.WORK_DIRECTORY.mkdir(parents=True)
        try:
            work_directory = self.WORK_DIRECTORY
            source_path = work_directory / 'growing.ts'
            output_path = work_directory / 'cover.webp'

            # 前半を赤、後半を青で強く着色し、backtrack 先頭ではなく15秒付近が選ばれたことを画像から判定する。
            command = [
                ffmpeg_path,
                '-nostdin',
                '-hide_banner',
                '-loglevel', 'error',
                '-f', 'lavfi',
                '-i', 'testsrc2=size=320x180:rate=5',
                '-t', '20',
                '-vf', (
                    "drawbox=x=0:y=0:w=iw:h=ih:color=red@0.8:t=fill:enable='lt(t,10)',"
                    "drawbox=x=0:y=0:w=iw:h=ih:color=blue@0.8:t=fill:enable='gte(t,10)'"
                ),
                '-c:v', 'mpeg2video',
                '-g', '5',
                '-b:v', '500k',
                '-an',
                '-f', 'mpegts',
                str(source_path),
            ]
            try:
                completed_process = subprocess.run(command, check=False, capture_output=True, timeout=30)
            except subprocess.TimeoutExpired as ex:
                self.fail(
                    f'FFmpeg fixture generation timed out.\nstdout:\n{ex.stdout!r}\nstderr:\n{ex.stderr!r}'
                )
            if completed_process.returncode != 0:
                self.fail(
                    f'FFmpeg fixture generation failed ({completed_process.returncode}).\n'
                    f'stdout:\n{completed_process.stdout.decode("utf-8", errors="replace")}\n'
                    f'stderr:\n{completed_process.stderr.decode("utf-8", errors="replace")}'
                )

            stream_info = TSKeyFrameSeeker.findStreamInfo(source_path)
            source_base_dts = TSKeyFrameSeeker.findBaseDTS(source_path, stream_info)
            readable_file_size = source_path.stat().st_size

            # 取得済みスナップショットより後の追記を模擬する。
            ## Generator へ渡す readable_file_size は追記前の値なので、この領域は FFmpeg 入力へ含まれない。
            appended_size = 20 * 1024 * 1024
            self.assertLess(readable_file_size + appended_size, self.TEMPORARY_SIZE_BUDGET_BYTES)
            with source_path.open('ab') as source_file:
                appended_chunk = bytes(1024 * 1024)
                for _ in range(appended_size // len(appended_chunk)):
                    source_file.write(appended_chunk)

            generator = RecordingThumbnailGenerator(
                source_path,
                stream_info,
                source_base_dts,
                readable_file_size,
            )
            decode_first_frame = generator._RecordingThumbnailGenerator__decodeFirstFrame  # type: ignore[attr-defined]
            with patch.object(
                generator,
                '_RecordingThumbnailGenerator__decodeFirstFrame',
                new=AsyncMock(wraps=decode_first_frame),
            ) as decode_mock:
                self.assertTrue(await generator.generate([15.0], output_path))

            decode_arguments = decode_mock.await_args
            assert decode_arguments is not None
            bounded_input = decode_arguments.args[0]
            self.assertLessEqual(len(bounded_input), generator.MAX_INPUT_BYTES_PER_CANDIDATE)
            self.assertLessEqual(len(bounded_input), readable_file_size)

            thumbnail = cv2.imread(str(output_path), cv2.IMREAD_COLOR)
            self.assertIsNotNone(thumbnail)
            assert thumbnail is not None
            self.assertEqual(thumbnail.shape, (270, 480, 3))
            # OpenCV は BGR 順。15秒は青の区間なので、先頭の赤フレームを誤選択していないことを確認する。
            self.assertGreater(float(thumbnail[:, :, 0].mean()), float(thumbnail[:, :, 2].mean()) * 1.5)
            total_artifact_size = sum(path.stat().st_size for path in work_directory.iterdir())
            self.assertLess(total_artifact_size, self.TEMPORARY_SIZE_BUDGET_BYTES)
        finally:
            if self.WORK_DIRECTORY.exists():
                shutil.rmtree(self.WORK_DIRECTORY)


if __name__ == '__main__':
    unittest.main()
