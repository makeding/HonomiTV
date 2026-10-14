from biim.mpeg2ts import ts
from biim.mpeg2ts.packetize import packetize_section
from biim.mpeg2ts.parser import SectionParser
from biim.mpeg2ts.pat import PATSection
from biim.mpeg2ts.pmt import PMTSection
from biim.mpeg2ts.section import Section


class TSSecondaryAudioExtractor:
    """MPEG-TS セグメントから第2 AAC 音声と再生時刻基準だけを抽出する。"""

    AAC_STREAM_TYPES = {0x0F, 0x11}

    @classmethod
    def extract(cls, segment_data: bytes) -> bytes:
        """
        MPEG-TS セグメントを副音声だけを含む単一プログラム TS に変換する。

        Args:
            segment_data (bytes): 映像と複数音声を含む188バイトパケットの MPEG-TS

        Returns:
            bytes: PAT、再構築した PMT、第2 AAC、PCR 搬送パケットだけを含む MPEG-TS

        Raises:
            ValueError: PAT、PMT、または第2 AAC ストリームを取得できない場合
        """

        if len(segment_data) % ts.PACKET_SIZE != 0:
            raise ValueError('MPEG-TS segment size is not aligned to 188-byte packets.')

        packets = [
            memoryview(segment_data)[offset:offset + ts.PACKET_SIZE]
            for offset in range(0, len(segment_data), ts.PACKET_SIZE)
        ]
        if any(packet[0] != ts.SYNC_BYTE[0] for packet in packets):
            raise ValueError('Invalid MPEG-TS sync byte was found in the segment.')

        pmt_pid = cls.__getPMTPID(packets)
        pmt_section = cls.__getPMTSection(packets, pmt_pid)
        secondary_audio_pid, filtered_pmt_section = cls.__buildFilteredPMT(pmt_section)
        pmt_packet = next((
            packet for packet in packets
            if ts.pid(packet) == pmt_pid and ts.payload_unit_start_indicator(packet)
        ), None)
        if pmt_packet is None:
            raise ValueError('PMT was not found in the MPEG-TS segment.')
        filtered_pmt_packets = packetize_section(
            Section(filtered_pmt_section),
            ts.transport_error_indicator(pmt_packet),
            ts.transport_priority(pmt_packet),
            pmt_pid,
            ts.transport_scrambling_control(pmt_packet),
            ts.continuity_counter(pmt_packet),
        )

        # PAT/PMT の到着位置と副音声 PES の順序を保持し、PCR は映像を含めずに時刻だけ残す。
        output_packets: list[bytes] = []
        for packet in packets:
            pid = ts.pid(packet)
            if pid == 0:
                output_packets.append(bytes(packet))
            elif pid == pmt_pid:
                if packet is pmt_packet:
                    output_packets.extend(filtered_pmt_packets)
            elif pid == secondary_audio_pid:
                output_packets.append(bytes(packet))
            elif pid == pmt_section.PCR_PID and ts.has_pcr(packet):
                output_packets.append(cls.__buildPCROnlyPacket(packet))
        return b''.join(output_packets)

    @staticmethod
    def __getPMTPID(packets: list[memoryview]) -> int:
        """PAT から最初の番組の PMT PID を返す。"""

        pat_parser = SectionParser(PATSection)
        for packet in packets:
            if ts.pid(packet) != 0:
                continue
            pat_parser.push(packet)
            for pat in pat_parser:
                if pat.table_id() == 0x00 and pat.CRC32() == 0:
                    for program_number, pmt_pid in pat:
                        if program_number != 0:
                            return pmt_pid
        raise ValueError('PAT does not contain a program map PID.')

    @staticmethod
    def __getPMTSection(packets: list[memoryview], pmt_pid: int) -> PMTSection:
        """PMT PID から CRC が正しい PMT を返す。"""

        pmt_parser = SectionParser(PMTSection)
        for packet in packets:
            if ts.pid(packet) != pmt_pid:
                continue
            pmt_parser.push(packet)
            for pmt in pmt_parser:
                if pmt.table_id() == 0x02 and pmt.CRC32() == 0:
                    return pmt
        raise ValueError('PMT was not found in the MPEG-TS segment.')

    @classmethod
    def __buildFilteredPMT(cls, pmt: PMTSection) -> tuple[int, bytes]:
        """第2 AAC ES だけを残した PMT と PID を生成する。"""

        aac_streams = [stream for stream in pmt if stream[0] in cls.AAC_STREAM_TYPES]
        if len(aac_streams) < 2:
            raise ValueError('The second AAC stream was not found in the PMT.')

        program_info_length = ((pmt[10] & 0x0F) << 8) | pmt[11]
        entry_offset = 12 + program_info_length
        secondary_audio_pid = aac_streams[1][1]
        section_without_crc = bytearray(pmt[:entry_offset])
        for _, pid, descriptors in pmt:
            entry_length = 5 + sum(2 + len(payload) for _, payload in descriptors)
            if pid == secondary_audio_pid:
                section_without_crc.extend(pmt[entry_offset:entry_offset + entry_length])
                break
            entry_offset += entry_length

        section_length = len(section_without_crc) + 1
        section_without_crc[1] = (section_without_crc[1] & 0xF0) | ((section_length >> 8) & 0x0F)
        section_without_crc[2] = section_length & 0xFF
        crc = Section(section_without_crc).CRC32()
        return secondary_audio_pid, bytes(section_without_crc) + crc.to_bytes(4, 'big')

    @staticmethod
    def __buildPCROnlyPacket(packet: bytes | bytearray | memoryview) -> bytes:
        """PCR を保ったアダプテーションフィールド専用パケットを返す。"""

        output = bytearray(ts.STUFFING_BYTE * ts.PACKET_SIZE)
        output[:12] = packet[:12]
        output[1] &= 0xBF
        output[3] = 0x20 | (packet[3] & 0x0F)
        output[4] = ts.PACKET_SIZE - ts.HEADER_SIZE - 1
        output[5] = 0x10 | (packet[5] & 0x80)
        return bytes(output)
