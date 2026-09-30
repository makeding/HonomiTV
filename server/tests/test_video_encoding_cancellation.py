import asyncio
import unittest
from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from app.streams.VideoEncodingTask import VideoEncodingTask


SectionEntry = tuple[int, int] | tuple[int, int, bytes]


class FakeStreamReader:
    """決められた MPEG-TS パケットだけを返す、待機しない StreamReader の代用品。"""

    def __init__(self, chunks: list[bytes], *, eof_barrier: asyncio.Event | None = None,
                 eof_release: asyncio.Event | None = None) -> None:
        """テストで返すデータ列と、EOF 到達時の任意のバリアを保持する。"""

        # stdout / stderr として返す未読データ。readexactly() から順に消費する。
        self._chunks = chunks
        # EOF 到達をテスト本体へ伝えるイベント。None なら停止地点を作らない。
        self._eof_barrier = eof_barrier
        # EOF を返す前に待機するイベント。キャンセル要求を確実に挟むためだけに使う。
        self._eof_release = eof_release
        # 後着エンコーダーの stdout を読み始めていないことを検証する観測値。
        self.read_count = 0

    async def read(self, size: int = -1) -> bytes:
        """stderr 監視タスクを必ず EOF で終了させる。"""

        return b''

    async def readexactly(self, size: int) -> bytes:
        """指定サイズの次のデータを返し、尽きたら IncompleteReadError にする。"""

        self.read_count += 1
        if len(self._chunks) > 0:
            chunk = self._chunks.pop(0)
            if len(chunk) == size:
                return chunk
            raise AssertionError(f'Unexpected read size: expected {size}, got {len(chunk)}.')
        if self._eof_barrier is not None:
            self._eof_barrier.set()
        if self._eof_release is not None:
            await self._eof_release.wait()
        raise asyncio.IncompleteReadError(partial=b'', expected=size)


class FakeProcess:
    """kill() と wait() の呼び出しを観測でき、パイプを詰まらせない子プロセス。"""

    def __init__(self, *, stdout: FakeStreamReader | None = None) -> None:
        """標準出力と終了状態を初期化する。"""

        # run() が stdout を読むエンコーダーだけに渡す読み取り元。上流プロセスは None でよい。
        self.stdout = stdout
        # stderr observer が必ず EOF へ到達し、待機やパイプ詰まりを起こさない読み取り元。
        self.stderr = FakeStreamReader([])
        # kill() / wait() 後の実プロセスと同じ終了状態。None は起動中を表す。
        self.returncode: int | None = None
        # cancel() と finally の両方から回収されたことを確認する kill 回数。
        self.kill_count = 0
        # 後着プロセスを wait() まで回収したことを確認する待機回数。
        self.wait_count = 0

    def kill(self) -> None:
        """終了シグナルを受けたことを記録し、待機可能な状態にする。"""

        self.kill_count += 1
        self.returncode = -9

    async def wait(self) -> int:
        """実プロセスの終了待機と同じ形で、即座に終了コードを返す。"""

        self.wait_count += 1
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class FakeInputFile:
    """PAT / PMT の事前探索に必要な seek / read だけを持つ録画ファイルの代用品。"""

    def __init__(self, data: bytes) -> None:
        """探索対象の固定データと現在位置を初期化する。"""

        # PAT / PMT の先読みと FeedTSStream が共有する固定入力データ。
        self._data = data
        # seek() / read() が参照する現在位置。実ファイルと同じく byte offset で保持する。
        self._position = 0
        # run() の finally が入力を閉じたことを確認する状態。
        self.is_closed = False

    def seek(self, position: int) -> None:
        """読み取り位置を設定する。"""

        self._position = position

    def tell(self) -> int:
        """現在の読み取り位置を返す。"""

        return self._position

    def read(self, size: int = -1) -> bytes:
        """指定範囲の入力データを返す。"""

        if size == -1:
            result = self._data[self._position:]
            self._position = len(self._data)
            return result
        result = self._data[self._position:self._position + size]
        self._position += len(result)
        return result

    def close(self) -> None:
        """最終 cleanup から閉じられたことを記録する。"""

        self.is_closed = True


class FakeSection:
    """PAT / PMT の最小限の反復インターフェースを持つセクション。"""

    def __init__(self, entries: list[SectionEntry]) -> None:
        """パーサーが返す PID 情報を保持する。"""

        # PAT は program_number / PMT PID、PMT は stream_type / elementary PID / descriptor を保持する。
        self._entries = entries

    def CRC32(self) -> int:
        """正しいセクションとして扱わせる CRC を返す。"""

        return 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        """PAT / PMT の要素を反復する。"""

        return iter(self._entries)


class FakeSectionParser:
    """パケット受信時に一度だけ指定の PAT または PMT を返す。"""

    def __init__(self, section: FakeSection) -> None:
        """返却対象のセクションを保持する。"""

        # push() 後に一度だけ返す PAT または PMT。実際の SectionParser の消費形態に合わせる。
        self._section = section
        # 同じ section を複数回返さないための受信状態。
        self._is_pushed = False

    def push(self, packet: bytes) -> None:
        """パケットを受信済みとして記録する。"""

        self._is_pushed = True

    def __iter__(self):  # type: ignore[no-untyped-def]
        """初回の push() 後だけセクションを返す。"""

        if self._is_pushed:
            self._is_pushed = False
            return iter([self._section])
        return iter([])


class VideoEncodingCancellationTest(unittest.IsolatedAsyncioTestCase):
    """VideoEncodingTask.run() の起動途中キャンセルと PID リトライを実行経路で検証する。"""

    # ガードが欠けた場合にもテストを無期限に待機させず、失敗地点を特定できるようにする上限。
    BARRIER_TIMEOUT_SECONDS = 5.0

    def setUp(self) -> None:
        """テスト中の cleanup ログも、実サーバー設定を読まずに出力できるようにする。"""

        logging_config_patcher = patch('app.logging.Config', return_value=self.createConfig())
        logging_config_patcher.start()
        self.addCleanup(logging_config_patcher.stop)

    def createVideoStream(self, container_format: str) -> SimpleNamespace:
        """run() が参照する最小の VideoStream / RecordedProgram グラフを作る。"""

        loop = asyncio.get_running_loop()
        segment = SimpleNamespace(
            encode_status='Pending',
            is_gap=False,
            source_start_dts=0,
            source_start_seconds=0.0,
            source_file_position=0,
            playlist_start_seconds=0.0,
            source_recorded_program_id=None,
            duration_seconds=3.0,
            encoded_segment_ts_future=loop.create_future(),
        )
        recorded_video = SimpleNamespace(
            container_format=container_format,
            file_path='/recordings/cancellation-test.ts',
            has_video_stream_changes=False,
            video_codec='H.264',
            video_resolution_width=1440,
            video_resolution_height=1080,
            video_scan_type='Progressive',
            video_frame_rate=29.97,
            id=1,
        )
        recorded_program = SimpleNamespace(
            recorded_video=recorded_video,
            channel=SimpleNamespace(network_id=1, transport_stream_id=2, service_id=3),
        )
        return SimpleNamespace(
            quality='1080p',
            segments=[segment],
            recorded_program=recorded_program,
            ts_stream_info=None,
            log_prefix='[Video: cancellation-test]',
            SEGMENT_MAP_SAVE_BATCH_SIZE=16,
            getSourceRecordedProgram=Mock(return_value=recorded_program),
            ensureTSKeyFrameContext=AsyncMock(),
            createSegmentMapEntriesFromKeyFrames=Mock(return_value=[]),
            saveSegmentMapEntries=AsyncMock(),
        )

    def createTask(self, container_format: str) -> tuple[VideoEncodingTask, SimpleNamespace]:
        """実際の VideoEncodingTask と、その入力用の最小ストリームを対応付ける。"""

        video_stream = self.createVideoStream(container_format)
        return VideoEncodingTask(video_stream), video_stream

    def createConfig(self) -> SimpleNamespace:
        """FFmpeg を選択し、stderr の追加ログを無効化した設定を返す。"""

        return SimpleNamespace(general=SimpleNamespace(encoder='FFmpeg', debug_encoder=False, debug=False))

    def createProcessFactory(
        self,
        processes: dict[str, FakeProcess],
        *,
        barrier_executable: str | None = None,
        barrier_entered: asyncio.Event | None = None,
        barrier_release: asyncio.Event | None = None,
    ) -> Callable[..., Awaitable[FakeProcess]]:
        """実行ファイル名ごとにプロセスを返し、指定地点だけ非同期作成を停止する。"""

        async def CreateSubprocess(executable: str, *args: object, **kwargs: object) -> FakeProcess:
            """起動要求を記録済みのプロセスに結び付け、必要なら返却前に停止する。"""

            executable_name = executable.rsplit('/', maxsplit=1)[-1]
            if executable_name == barrier_executable:
                assert barrier_entered is not None and barrier_release is not None
                barrier_entered.set()
                await barrier_release.wait()
            return processes[executable_name]

        return CreateSubprocess

    def patchSectionParsers(self) -> patch:
        """PAT と PMT を実際の stdout 読み取り後に一度ずつ返すパーサーへ差し替える。"""

        parsers = iter([
            FakeSectionParser(FakeSection([(1, 100)])),
            FakeSectionParser(FakeSection([(0x1B, 200, b''), (0x0F, 201, b'')])),
            Mock(),
            Mock(),
        ])
        return patch('app.streams.VideoEncodingTask.SectionParser', side_effect=lambda *args: next(parsers))

    async def assertLateProcessIsReaped(
        self,
        barrier_executable: str,
        expected_spawned: set[str],
    ) -> None:
        """create_subprocess_exec() の返却と cancel() が競合しても、後着プロセスを回収する。"""

        task, _ = self.createTask('MPEG-4')
        processes = {
            'psisimux': FakeProcess(),
            'tsreadex': FakeProcess(),
            'FFmpeg': FakeProcess(stdout=FakeStreamReader([])),
        }
        barrier_entered = asyncio.Event()
        barrier_release = asyncio.Event()
        spawn_order: list[str] = []
        factory = self.createProcessFactory(
            processes,
            barrier_executable=barrier_executable,
            barrier_entered=barrier_entered,
            barrier_release=barrier_release,
        )

        async def RecordSpawn(executable: str, *args: object, **kwargs: object) -> FakeProcess:
            """起動順を保存してから、停止可能な実行ファイルファクトリを呼ぶ。"""

            spawn_order.append(executable.rsplit('/', maxsplit=1)[-1])
            return await factory(executable, *args, **kwargs)

        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec', new=RecordSpawn), \
                patch('app.streams.VideoEncodingTask.LIBRARY_PATH', {
                    'psisimux': 'psisimux', 'tsreadex': 'tsreadex', 'FFmpeg': 'FFmpeg',
                }):
            running_task = asyncio.create_task(task.run(0))
            await asyncio.wait_for(barrier_entered.wait(), timeout=self.BARRIER_TIMEOUT_SECONDS)
            task.cancel()
            barrier_release.set()
            await asyncio.wait_for(running_task, timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertEqual(set(spawn_order), expected_spawned)
        late_process = processes[barrier_executable]
        self.assertGreaterEqual(late_process.kill_count, 1)
        self.assertGreaterEqual(late_process.wait_count, 1)
        # encoder 起動後のガードは stderr observer を残して回収するため、stdout の PID 読み取りへは進まない。
        if late_process.stdout is not None:
            self.assertEqual(late_process.stdout.read_count, 0)
        self.assertTrue(task._is_cancelled)

    async def test_precancelled_run_does_not_read_config_open_file_or_spawn(self) -> None:
        """run() 開始前の cancel() では Config・ファイル・外部プロセスに一切触れない。"""

        task, _ = self.createTask('MPEG-TS')
        task.cancel()
        with patch('app.streams.VideoEncodingTask.Config', side_effect=AssertionError('Config must not be read.')), \
                patch('builtins.open', side_effect=AssertionError('Input file must not be opened.')), \
                patch('app.streams.VideoEncodingTask.os.pipe', side_effect=AssertionError('Pipe must not be created.')), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec',
                      side_effect=AssertionError('Process must not be spawned.')):
            await task.run(0)

        self.assertTrue(task._is_cancelled)
        self.assertFalse(task._is_finished)

    async def test_cancel_during_keyframe_context_does_not_open_input_file(self) -> None:
        """ensureTSKeyFrameContext() の待機中にキャンセルした場合、録画ファイルを開かない。"""

        task, video_stream = self.createTask('MPEG-TS')
        context_entered = asyncio.Event()
        context_release = asyncio.Event()

        async def EnsureContext() -> None:
            """キーフレーム文脈初期化を、キャンセル要求まで停止する。"""

            context_entered.set()
            await context_release.wait()

        video_stream.ensureTSKeyFrameContext.side_effect = EnsureContext
        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('builtins.open', side_effect=AssertionError('Cancelled task must not open the input file.')):
            running_task = asyncio.create_task(task.run(0))
            await asyncio.wait_for(context_entered.wait(), timeout=self.BARRIER_TIMEOUT_SECONDS)
            task.cancel()
            context_release.set()
            await asyncio.wait_for(running_task, timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertTrue(task._is_cancelled)

    async def test_cancel_during_psisimux_creation_reaps_late_process_without_downstream_spawn(self) -> None:
        """psisimux の非同期作成中のキャンセルでは、後着プロセスだけを回収して後段を起動しない。"""

        await self.assertLateProcessIsReaped('psisimux', {'psisimux'})

    async def test_cancel_during_tsreadex_creation_reaps_late_process_without_encoder_or_feed(self) -> None:
        """tsreadex の非同期作成中のキャンセルでは、エンコーダーや feed を開始しない。"""

        await self.assertLateProcessIsReaped('tsreadex', {'psisimux', 'tsreadex'})

    async def test_cancel_during_pat_pmt_tsreadex_creation_does_not_start_feed(self) -> None:
        """PAT / PMT 先行投入経路でも、後着 tsreadex の回収前に feed スレッドを作らない。"""

        task, video_stream = self.createTask('MPEG-TS')
        video_stream.segments[0].source_file_position = 376
        input_file = FakeInputFile(b'\x47' + (b'\x00' * 187) + b'\x47' + (b'\x00' * 187))
        late_tsreadex = FakeProcess()
        tsreadex_entered = asyncio.Event()
        tsreadex_release = asyncio.Event()

        async def CreateSubprocess(executable: str, *args: object, **kwargs: object) -> FakeProcess:
            """tsreadex の作成だけをキャンセル要求まで停止する。"""

            self.assertEqual(executable, 'tsreadex')
            tsreadex_entered.set()
            await tsreadex_release.wait()
            return late_tsreadex

        parser_instances = iter([
            # リトライ試行本体の PAT / PMT パーサー
            Mock(),
            Mock(),
            # その後の MPEG-TS 入力の PAT / PMT 先読みパーサー
            FakeSectionParser(FakeSection([(1, 100)])),
            FakeSectionParser(FakeSection([(0x1B, 200, b''), (0x0F, 201, b'')])),
        ])
        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('builtins.open', return_value=input_file), \
                patch('app.streams.VideoEncodingTask.SectionParser',
                      side_effect=lambda *args: next(parser_instances)), \
                patch('app.streams.VideoEncodingTask.ts.pid', side_effect=[0, 100]), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec', new=CreateSubprocess), \
                patch('app.streams.VideoEncodingTask.LIBRARY_PATH', {'tsreadex': 'tsreadex'}), \
                patch.object(asyncio.BaseEventLoop, 'run_in_executor',
                             side_effect=AssertionError('Cancelled task must not start FeedTSStream.')):
            running_task = asyncio.create_task(task.run(0))
            entered_waiter = asyncio.create_task(tsreadex_entered.wait())
            done_tasks, _ = await asyncio.wait_for(
                asyncio.wait(
                    {running_task, entered_waiter},
                    return_when=asyncio.FIRST_COMPLETED,
                ),
                timeout=self.BARRIER_TIMEOUT_SECONDS,
            )
            if running_task in done_tasks:
                running_task.result()
                self.fail('PAT / PMT 先行投入経路で tsreadex の作成に到達しなかった。')
            task.cancel()
            tsreadex_release.set()
            await asyncio.wait_for(running_task, timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertGreaterEqual(late_tsreadex.kill_count, 1)
        self.assertGreaterEqual(late_tsreadex.wait_count, 1)
        self.assertTrue(input_file.is_closed)

    async def test_cancel_during_encoder_creation_reaps_late_process_without_output_read(self) -> None:
        """エンコーダーの非同期作成中のキャンセルでは、後着プロセスの出力を読まない。"""

        await self.assertLateProcessIsReaped('FFmpeg', {'psisimux', 'tsreadex', 'FFmpeg'})

    async def test_cancel_after_pid_acquisition_does_not_increment_retry_count(self) -> None:
        """PAT / PMT で PID を得た後のキャンセルは、欠損 PID リトライとして数えない。"""

        task, _ = self.createTask('MPEG-4')
        eof_entered = asyncio.Event()
        eof_release = asyncio.Event()
        output = FakeStreamReader([
            b'\x47', b'\x00' * 187,
            b'\x47', b'\x00' * 187,
        ], eof_barrier=eof_entered, eof_release=eof_release)
        processes = {
            'psisimux': FakeProcess(),
            'tsreadex': FakeProcess(),
            'FFmpeg': FakeProcess(stdout=output),
        }
        factory = self.createProcessFactory(processes)

        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec', new=factory), \
                patch('app.streams.VideoEncodingTask.LIBRARY_PATH', {
                    'psisimux': 'psisimux', 'tsreadex': 'tsreadex', 'FFmpeg': 'FFmpeg',
                }), \
                patch('app.streams.VideoEncodingTask.ts.pid', side_effect=[0, 100]), \
                self.patchSectionParsers(), \
                patch('app.streams.VideoEncodingTask.packetize_section', return_value=[]):
            running_task = asyncio.create_task(task.run(0))
            await asyncio.wait_for(eof_entered.wait(), timeout=self.BARRIER_TIMEOUT_SECONDS)
            task.cancel()
            eof_release.set()
            await asyncio.wait_for(running_task, timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertEqual(task._retry_count, 0)
        self.assertTrue(task._is_cancelled)

    async def test_cancel_during_missing_pid_retry_cleanup_does_not_start_next_attempt(self) -> None:
        """PID 未取得試行の終了処理中のキャンセルでは、次のリトライへ進まない。"""

        task, _ = self.createTask('MPEG-4')
        cleanup_entered = asyncio.Event()
        cleanup_release = asyncio.Event()

        class CleanupBarrierProcess(FakeProcess):
            """最初の encoder wait() を停止し、cleanup 中の cancel() を確実に挟む。"""

            async def wait(self) -> int:
                """kill() 後の cleanup を待機させてから終了する。"""

                self.wait_count += 1
                cleanup_entered.set()
                await cleanup_release.wait()
                return await super().wait()

        processes = {
            'psisimux': FakeProcess(),
            'tsreadex': FakeProcess(),
            'FFmpeg': CleanupBarrierProcess(stdout=FakeStreamReader([])),
        }
        factory = self.createProcessFactory(processes)
        spawn_order: list[str] = []

        async def RecordSpawn(executable: str, *args: object, **kwargs: object) -> FakeProcess:
            """不要な二試行目が起動されないことを検証するため、実行名を残す。"""

            spawn_order.append(executable.rsplit('/', maxsplit=1)[-1])
            return await factory(executable, *args, **kwargs)

        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec', new=RecordSpawn), \
                patch('app.streams.VideoEncodingTask.LIBRARY_PATH', {
                    'psisimux': 'psisimux', 'tsreadex': 'tsreadex', 'FFmpeg': 'FFmpeg',
                }):
            running_task = asyncio.create_task(task.run(0))
            await asyncio.wait_for(cleanup_entered.wait(), timeout=self.BARRIER_TIMEOUT_SECONDS)
            task.cancel()
            cleanup_release.set()
            await asyncio.wait_for(running_task, timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertEqual(spawn_order, ['psisimux', 'tsreadex', 'FFmpeg'])
        self.assertEqual(task._retry_count, 0)

    async def test_uncancelled_missing_pid_retries_exactly_ten_times(self) -> None:
        """キャンセルされない PID 未取得は、従来どおり最大 10 回で終了する。"""

        task, _ = self.createTask('MPEG-4')
        spawned_encoder_count = 0

        async def CreateSubprocess(executable: str, *args: object, **kwargs: object) -> FakeProcess:
            """全試行で EOF の encoder を返し、PID を一度も得られない経路を作る。"""

            nonlocal spawned_encoder_count
            executable_name = executable.rsplit('/', maxsplit=1)[-1]
            if executable_name == 'FFmpeg':
                spawned_encoder_count += 1
                return FakeProcess(stdout=FakeStreamReader([]))
            return FakeProcess()

        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec', new=CreateSubprocess), \
                patch('app.streams.VideoEncodingTask.LIBRARY_PATH', {
                    'psisimux': 'psisimux', 'tsreadex': 'tsreadex', 'FFmpeg': 'FFmpeg',
                }):
            await asyncio.wait_for(task.run(0), timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertEqual(task._retry_count, VideoEncodingTask.MAX_RETRY_COUNT)
        self.assertEqual(spawned_encoder_count, VideoEncodingTask.MAX_RETRY_COUNT)
        self.assertTrue(task._is_finished)

    async def test_valid_pat_and_pmt_complete_without_retry(self) -> None:
        """有効な PAT / PMT から映像・音声 PID を取得できる通常経路は成功する。"""

        task, video_stream = self.createTask('MPEG-4')
        output = FakeStreamReader([
            b'\x47', b'\x00' * 187,
            b'\x47', b'\x00' * 187,
        ])
        processes = {
            'psisimux': FakeProcess(),
            'tsreadex': FakeProcess(),
            'FFmpeg': FakeProcess(stdout=output),
        }
        factory = self.createProcessFactory(processes)

        with patch('app.streams.VideoEncodingTask.Config', return_value=self.createConfig()), \
                patch('app.streams.VideoEncodingTask.asyncio.subprocess.create_subprocess_exec', new=factory), \
                patch('app.streams.VideoEncodingTask.LIBRARY_PATH', {
                    'psisimux': 'psisimux', 'tsreadex': 'tsreadex', 'FFmpeg': 'FFmpeg',
                }), \
                patch('app.streams.VideoEncodingTask.ts.pid', side_effect=[0, 100]), \
                self.patchSectionParsers(), \
                patch('app.streams.VideoEncodingTask.packetize_section', return_value=[]):
            await asyncio.wait_for(task.run(0), timeout=self.BARRIER_TIMEOUT_SECONDS)

        self.assertEqual(task._retry_count, 0)
        self.assertTrue(task._is_finished)
        self.assertFalse(task._is_cancelled)
        self.assertEqual(video_stream.segments[0].encode_status, 'Completed')
        self.assertTrue(video_stream.segments[0].encoded_segment_ts_future.done())


if __name__ == '__main__':
    unittest.main()
