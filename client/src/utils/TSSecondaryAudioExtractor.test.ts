import { calculateCRC32 } from 'arib-mmt-tlv-ts/crc-32.js';
import { readSection } from 'arib-mmt-tlv-ts/ts/si.js';
import { concatBuffers } from 'arib-mmt-tlv-ts/utils.js';
import { describe, expect, it } from 'vitest';

import MPEGTSSecondaryAudioExtractor from '@/utils/TSSecondaryAudioExtractor';


function section(bytes: number[]): Uint8Array {
    const data = new Uint8Array(bytes);
    const length = data.length + 1;
    data[1] = (data[1] & 0xf0) | (length >> 8);
    data[2] = length & 0xff;
    const crc = new Uint8Array(4);
    new DataView(crc.buffer).setUint32(0, calculateCRC32(data, -1));
    return concatBuffers([data, crc]);
}
function packet(pid: number, payload: Uint8Array): Uint8Array {
    const data = new Uint8Array(188).fill(0xff);
    data.set([0x47, 0x40 | (pid >> 8), pid & 0xff, 0x10]);
    data.set(payload, 4);
    return data;
}
function fixture(): Uint8Array[] {
    const pat = section([0, 0xb0, 0, 0, 1, 0xc1, 0, 0, 0, 1, 0xe1, 0]);
    const pmt = section([2, 0xb0, 0, 0, 1, 0xc1, 0, 0, 0xe1, 1, 0xf0, 0,
        0x1b, 0xe1, 1, 0xf0, 0, 0x0f, 0xe1, 2, 0xf0, 0, 0x0f, 0xe1, 3, 0xf0, 0]);
    const pcr = packet(0x101, new Uint8Array([7, 0x90, 1, 2, 3, 4, 5, 6, 0xaa]));
    pcr[3] = 0x30;
    return [packet(0, concatBuffers([new Uint8Array([0]), pat])),
        packet(0x100, concatBuffers([new Uint8Array([0]), pmt])), pcr,
        packet(0x102, new Uint8Array([0xaa])), packet(0x103, new Uint8Array([0xbb]))];
}

describe('secondary AAC extraction', () => {
    it('keeps secondary payload, PCR timing and valid PMT CRC while removing primary audio/video', () => {
        const source = fixture();
        const output = MPEGTSSecondaryAudioExtractor.extract(concatBuffers(source));
        const packets = Array.from({length: output.length / 188}, (_, index) => output.subarray(index * 188, (index + 1) * 188));
        expect(packets.map(data => ((data[1] & 0x1f) << 8) | data[2])).toEqual([0, 0x100, 0x101, 0x103]);
        const pmt = readSection(packets[1].subarray(5));
        expect(pmt?.tableId).toBe('PMT');
        if (pmt?.tableId === 'PMT') expect(pmt.streams.map(stream => stream.elementaryPID)).toEqual([0x103]);
        expect(packets[2][3] & 0x30).toBe(0x20);
        expect(packets[2].subarray(6, 12)).toEqual(source[2].subarray(6, 12));
        expect(packets[3]).toEqual(source[4]);
    });
    it('rejects a PMT with a corrupted CRC instead of guessing its audio tracks', () => {
        const source = fixture();
        source[1][10] ^= 1;
        expect(() => MPEGTSSecondaryAudioExtractor.extract(concatBuffers(source))).toThrow();
    });
});
