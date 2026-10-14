import asyncio
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import httpx
from biim.mpeg2ts import ts
from biim.mpeg2ts.packetize import packetize_section
from biim.mpeg2ts.parser import SectionParser
from biim.mpeg2ts.pmt import PMTSection
from biim.mpeg2ts.section import Section
from fastapi import FastAPI, HTTPException, status

from app.routers.VideosRouter import GetRecordedProgram
from app.routers.VideosRouter import router as videos_router
from app.routers.VideoStreamsRouter import (
    ValidateQuality,
    ValidateVideoID,
)
from app.routers.VideoStreamsRouter import (
    router as video_streams_router,
)
from app.streams.StreamEncodingOptions import (
    StreamEncodingOptions,
    StreamQualityWithOptions,
)
from app.streams.VideoEncodingTask import VideoEncodingTask
from app.utils.DisconnectAwareFileResponse import DisconnectAwareFileResponse
from app.utils.RecordedWebVTTSidecar import RecordedWebVTTSidecar
from app.utils.TSSecondaryAudioExtractor import TSSecondaryAudioExtractor


class FakeVideoStream:
    """HLS ルーターの HTTP 契約だけを検証するための VideoStream 代替。"""

    segment_exception: Exception | None = None

    def __init__(self, *_: object, **__: object) -> None:
        """
        テスト用ストリームを初期化する。

        Args:
            *_ (object): 実際の VideoStream コンストラクタ引数。
        """

        self.log_prefix = '[FakeVideoStream]'

    async def getVirtualPlaylist(self, cache_key: str | None, audio: str) -> str:
        """
        指定音声を含む最小プレイリストを返す。

        Args:
            cache_key (str | None): キャッシュ制御用のキー。
            audio (str): 要求された音声トラック。

        Returns:
            str: テスト用プレイリスト。
        """

        return f'#EXTM3U\n# {cache_key} {audio}\n'

    async def getSegment(self, _: int, __: str) -> bytes:
        """
        設定済みの成功または失敗を返す。

        Args:
            _ (int): セグメント番号。
            __ (str): 要求された音声トラック。

        Returns:
            bytes: テスト用 MPEG-TS バイト列。

        Raises:
            Exception: テストで設定したエラー。
        """

        if self.segment_exception is not None:
            raise self.segment_exception
        return b'fake-mpegts'


class UpstreamPlaybackAPITest(unittest.IsolatedAsyncioTestCase):
    """上流から取り込んだ HLS と WebVTT の HTTP 契約を検証する。"""

    async def asyncSetUp(self) -> None:
        """
        依存先を DB なしの録画へ置き換えた ASGI アプリを作成する。

        Returns:
            None
        """

        self.recorded_program = SimpleNamespace(
            id = 1,
            recorded_video = SimpleNamespace(
                file_path='/recordings/sample.ts',
                status='Recorded',
                container_format='MPEG-TS',
            ),
        )
        self.application = FastAPI()
        self.application.include_router(video_streams_router)
        self.application.include_router(videos_router)

        async def GetTestRecordedProgram() -> SimpleNamespace:
            """テスト対象の録画を返す。

            Returns:
                SimpleNamespace: 最小の録画番組情報。
            """

            return self.recorded_program

        async def GetTestQuality() -> StreamQualityWithOptions:
            """通常エンコード品質を返す。

            Returns:
                StreamQualityWithOptions: HLS 用の 720p 品質。
            """

            return StreamQualityWithOptions('720p', StreamEncodingOptions())

        self.application.dependency_overrides[ValidateVideoID] = GetTestRecordedProgram
        self.application.dependency_overrides[GetRecordedProgram] = GetTestRecordedProgram
        self.application.dependency_overrides[ValidateQuality] = GetTestQuality
        FakeVideoStream.segment_exception = None

    async def test_master_playlist_exposes_primary_and_secondary_audio(self) -> None:
        """master playlist は主・副音声の各 rendition URI を返す。"""

        with patch('app.routers.VideoStreamsRouter.VideoStream', FakeVideoStream):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                response = await client.get(
                    '/api/streams/video/1/720p/playlist?session_id=test&cache_key=cache-1&recording=1&type=master'
                )

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertIn('NAME="主音声"', response.text)
        self.assertIn('NAME="副音声"', response.text)
        self.assertIn('type=primary-audio', response.text)
        self.assertIn('type=secondary-audio', response.text)
        self.assertIn('cache_key=cache-1', response.text)
        self.assertIn('recording=1', response.text)

    async def test_segment_preserves_http_exception_and_translates_encoder_errors(self) -> None:
        """既存 HTTPException は維持し、エンコーダー例外は安定した HTTP エラーへ変換する。"""

        cases: list[tuple[Exception, int]] = [
            (HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Waiting'), status.HTTP_503_SERVICE_UNAVAILABLE),
            (ValueError('Invalid secondary audio'), status.HTTP_422_UNPROCESSABLE_ENTITY),
            (RuntimeError('Encoder stopped'), status.HTTP_500_INTERNAL_SERVER_ERROR),
        ]
        with patch('app.routers.VideoStreamsRouter.VideoStream', FakeVideoStream):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                for segment_exception, expected_status in cases:
                    with self.subTest(expected_status=expected_status):
                        FakeVideoStream.segment_exception = segment_exception
                        response = await client.get('/api/streams/video/1/720p/segment?session_id=test&sequence=0&cache_key=test&audio=secondary')
                        self.assertEqual(response.status_code, expected_status, response.text)

    async def test_webvtt_get_and_head_use_the_same_availability_contract(self) -> None:
        """GET は WebVTT 本文、HEAD は本文なしの存在確認を返す。"""

        with (
            patch('app.routers.VideosRouter.RecordedWebVTTSidecar.read', return_value=b'WEBVTT\n\n'),
            patch('app.routers.VideosRouter.RecordedWebVTTSidecar.readHeader', return_value=b''),
            patch('app.routers.VideosRouter.RecordedWebVTTSidecar.hasEmbeddedSubtitle', return_value=False),
        ):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
                get_response = await client.get('/api/videos/1/subtitles/webvtt')
                head_response = await client.head('/api/videos/1/subtitles/webvtt')

        self.assertEqual(get_response.status_code, status.HTTP_200_OK, get_response.text)
        self.assertEqual(get_response.content, b'WEBVTT\n\n')
        self.assertEqual(head_response.status_code, status.HTTP_200_OK, head_response.text)
        self.assertEqual(head_response.content, b'')
        self.assertTrue(head_response.headers['content-type'].startswith('text/vtt'))

    async def test_download_api_returns_the_disconnect_aware_file_response(self) -> None:
        """ダウンロード API は既存ファイルを DisconnectAwareFileResponse 経由で返す。"""

        # リポジトリにある小さな静的 JSON を使い、DB や一時ファイルを作らずに実際の FileResponse 経路を通す。
        static_file_path = Path(__file__).parents[1] / 'static/jikkyo_channels.json'
        self.recorded_program.recorded_video.file_path = str(static_file_path)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.application), base_url='http://test') as client:
            response = await client.get('/api/videos/1/download')

        self.assertEqual(response.status_code, status.HTTP_200_OK, response.text)
        self.assertEqual(response.headers['content-type'], 'video/mp2t')
        self.assertGreater(len(response.content), 0)

    async def test_disconnect_aware_file_response_stops_range_on_disconnect(self) -> None:
        """ASGI 2.3 の Range 配信は切断通知後に残りのファイルを読み続けない。"""

        response = DisconnectAwareFileResponse(Path(__file__).parents[1] / 'static/jikkyo_channels.json')
        response.chunk_size = 1
        messages: list[dict[str, object]] = []

        async def Receive() -> dict[str, str]:
            """接続が切れたことを通知する。

            Returns:
                dict[str, str]: ASGI 切断メッセージ。
            """

            return {'type': 'http.disconnect'}

        async def Send(message: dict[str, object]) -> None:
            """応答を保存して監視タスクへ制御を渡す。

            Args:
                message (dict[str, object]): ASGI 応答メッセージ。

            Returns:
                None
            """

            messages.append(message)
            await asyncio.sleep(0)

        await response(
            {'type': 'http', 'method': 'GET', 'headers': [(b'range', b'bytes=0-1023')], 'asgi': {'spec_version': '2.3'}},
            Receive,
            Send,
        )
        body_messages = [message for message in messages if message['type'] == 'http.response.body']
        # 切断を start より先に検出できた場合は応答自体を送らない。この場合も読み出し停止として正しい。
        if len(messages) > 0:
            self.assertEqual(messages[0]['type'], 'http.response.start')
        self.assertLessEqual(len(body_messages), 1)

    async def test_disconnect_aware_file_response_keeps_head_semantics(self) -> None:
        """HEAD は標準 FileResponse と同じ空本文を返す。"""

        response = DisconnectAwareFileResponse(Path(__file__).parents[1] / 'static/jikkyo_channels.json')
        messages: list[dict[str, object]] = []

        async def Receive() -> dict[str, str]:
            """HEAD で使用しない受信関数を定義する。

            Returns:
                dict[str, str]: ASGI 通常リクエストメッセージ。
            """

            return {'type': 'http.request'}

        async def Send(message: dict[str, object]) -> None:
            """HEAD 応答を保存する。

            Args:
                message (dict[str, object]): ASGI 応答メッセージ。

            Returns:
                None
            """

            messages.append(message)

        await response({'type': 'http', 'method': 'HEAD', 'headers': [], 'asgi': {'spec_version': '2.3'}}, Receive, Send)
        body_messages = [message for message in messages if message['type'] == 'http.response.body']
        self.assertEqual(body_messages, [{'type': 'http.response.body', 'body': b'', 'more_body': False}])

    async def test_secondary_audio_extractor_keeps_valid_pmt_and_pcr(self) -> None:
        """副音声抽出は PMT CRC と PCR を保ち、主音声を除外する。"""

        pmt_pid, video_pid, primary_pid, secondary_pid = (0x100, 0x101, 0x102, 0x103)
        source = b''.join([
            *self._packetizePAT(pmt_pid),
            *self._packetizePMT(pmt_pid, video_pid, primary_pid, secondary_pid),
            self._buildPCRPacket(video_pid),
            self._buildPayloadPacket(primary_pid, b'primary'),
            self._buildPayloadPacket(secondary_pid, b'secondary'),
        ])
        result = TSSecondaryAudioExtractor.extract(source)
        packets = [result[offset:offset + ts.PACKET_SIZE] for offset in range(0, len(result), ts.PACKET_SIZE)]
        pids = [ts.pid(packet) for packet in packets]
        self.assertIn(0, pids)
        self.assertIn(pmt_pid, pids)
        self.assertNotIn(primary_pid, pids)
        self.assertIn(secondary_pid, pids)
        self.assertTrue(any(ts.pid(packet) == video_pid and ts.has_pcr(packet) for packet in packets))

        parser = SectionParser(PMTSection)
        filtered_pmt: PMTSection | None = None
        for packet in packets:
            if ts.pid(packet) == pmt_pid:
                parser.push(packet)
                filtered_pmt = next(iter(parser), None)
        self.assertIsNotNone(filtered_pmt)
        assert filtered_pmt is not None
        self.assertEqual(filtered_pmt.CRC32(), 0)
        self.assertEqual([(stream_type, pid) for stream_type, pid, _ in filtered_pmt], [(0x0F, secondary_pid)])

    async def test_secondary_audio_extractor_rejects_bad_pmt_crc(self) -> None:
        """破損した PMT からは副音声を推測しない。"""

        packets = [*self._packetizePAT(0x100), *self._packetizePMT(0x100, 0x101, 0x102, 0x103)]
        corrupted_pmt_packet = bytearray(packets[1])
        corrupted_pmt_packet[10] ^= 0x01
        packets[1] = bytes(corrupted_pmt_packet)
        with self.assertRaises(ValueError):
            TSSecondaryAudioExtractor.extract(b''.join(packets))

    async def test_copy_options_always_supply_second_aac(self) -> None:
        """通常 copy は tsreadex の第2 AAC を必須にし、MMT は無音 AAC を追加する。"""

        encoding_task = object.__new__(VideoEncodingTask)
        normal_options = encoding_task.buildFFmpegCopyOptions(0.0)
        mmt_options = encoding_task.buildFFmpegCopyOptions(0.0, mmt_seek_seconds=0.0, mmt_input_file_path='/recording.mmt')
        self.assertIn('0:a:1', normal_options)
        self.assertIn('anullsrc=channel_layout=stereo:sample_rate=48000', mmt_options)
        self.assertIn('1:a:0', mmt_options)
        self.assertIn('-shortest', mmt_options)

    async def test_webvtt_head_accepts_utf8_header_split_at_read_boundary(self) -> None:
        """HEAD の固定長読み込みは途中の UTF-8 文字を不正ファイル扱いしない。

        Returns:
            None
        """

        data = 'WEBVTT 日本語\n\n'.encode()
        with (
            patch('pathlib.Path.stat', return_value=SimpleNamespace(st_size=len(data))),
            patch('pathlib.Path.open', return_value=io.BytesIO(data)),
        ):
            self.assertEqual(RecordedWebVTTSidecar.readHeader(Path('/recordings/sample.ts')), b'')

    @staticmethod
    def _buildSection(section_without_crc: bytearray) -> bytes:
        """PSI section に長さと CRC を付ける。

        Args:
            section_without_crc (bytearray): CRC を除く section。

        Returns:
            bytes: CRC を含む section。
        """

        section_length = len(section_without_crc) + 1
        section_without_crc[1] = (section_without_crc[1] & 0xF0) | ((section_length >> 8) & 0x0F)
        section_without_crc[2] = section_length & 0xFF
        return bytes(section_without_crc) + Section(section_without_crc).CRC32().to_bytes(4, 'big')

    @classmethod
    def _packetizePAT(cls, pmt_pid: int) -> list[bytes]:
        """単一番組 PAT を TS 化する。

        Args:
            pmt_pid (int): PMT PID。

        Returns:
            list[bytes]: PAT パケット。
        """

        pat = cls._buildSection(bytearray([0x00, 0xB0, 0x00, 0, 1, 0xC1, 0, 0, 0, 1, 0xE0 | (pmt_pid >> 8), pmt_pid & 0xFF]))
        return packetize_section(Section(pat), False, False, 0, 0, 0)

    @classmethod
    def _packetizePMT(cls, pmt_pid: int, video_pid: int, primary_pid: int, secondary_pid: int) -> list[bytes]:
        """映像と主副 AAC を含む PMT を TS 化する。

        Args:
            pmt_pid (int): PMT PID。
            video_pid (int): 映像 PID。
            primary_pid (int): 主音声 PID。
            secondary_pid (int): 副音声 PID。

        Returns:
            list[bytes]: PMT パケット。
        """

        pmt = bytearray([0x02, 0xB0, 0x00, 0, 1, 0xC1, 0, 0, 0xE0 | (video_pid >> 8), video_pid & 0xFF, 0xF0, 0x00])
        for stream_type, pid in [(0x1B, video_pid), (0x0F, primary_pid), (0x0F, secondary_pid)]:
            pmt.extend([stream_type, 0xE0 | (pid >> 8), pid & 0xFF, 0xF0, 0x00])
        return packetize_section(Section(cls._buildSection(pmt)), False, False, pmt_pid, 0, 0)

    @staticmethod
    def _buildPayloadPacket(pid: int, payload: bytes) -> bytes:
        """payload-only TS パケットを作る。

        Args:
            pid (int): TS PID。
            payload (bytes): 先頭ペイロード。

        Returns:
            bytes: TS パケット。
        """

        packet = bytearray([0x47, 0x40 | (pid >> 8), pid & 0xFF, 0x10])
        packet.extend(payload)
        packet.extend(b'\xff' * (ts.PACKET_SIZE - len(packet)))
        return bytes(packet)

    @staticmethod
    def _buildPCRPacket(pid: int) -> bytes:
        """PCR 専用 TS パケットを作る。

        Args:
            pid (int): PCR PID。

        Returns:
            bytes: TS パケット。
        """

        packet = bytearray(b'\xff' * ts.PACKET_SIZE)
        packet[0:6] = bytes([0x47, (pid >> 8) & 0x1F, pid & 0xFF, 0x20, 183, 0x10])
        packet[6:12] = b'\x00' * 6
        return bytes(packet)
