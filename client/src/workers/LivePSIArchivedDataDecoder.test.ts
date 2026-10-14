import { Buffer } from 'buffer';

import { describe, expect, it, vi } from 'vitest';

import type { IProgram } from '@/services/Programs';


const exposed = vi.hoisted(() => ({decoder: null as unknown as new (...args: unknown[]) => {
    generateIProgramFromEPG: (eit: unknown) => IProgram;
}}));
vi.mock('comlink', () => ({expose: (decoder: typeof exposed.decoder) => {exposed.decoder = decoder;}}));
vi.mock('web-bml/worker', () => ({decodeTS: vi.fn()}));
vi.mock('@tsukumijima/aribts', () => ({TsChar: class {
    constructor(private bytes: Buffer) {}
    decode() {return this.bytes.toString();}
}}));
vi.mock('@/utils', () => ({default: {isSafari: () => true}, ProgramUtils: {formatString: (text: string) => text}}));
import '@/workers/LivePSIArchivedDataDecoder';

describe('EPG extended detail preservation', () => {
    it('preserves an initial continuation and headings that collide after normalization', () => {
        const decoder = new exposed.decoder({network_id: 1, service_id: 2}, '1080p');
        const items = [['', '最初の本文'], ['◇題', '本文A'], ['題', '本文B']].map(([heading, body]) => ({
            item_description_length: Buffer.byteLength(heading),
            item_description_char: Buffer.from(heading), item_char: Buffer.from(body),
        }));
        const program = decoder.generateIProgramFromEPG({
            current_next_indicator: 1, original_network_id: 1, service_id: 2,
            events: [{event_id: 3, free_CA_mode: 0, start_time: Buffer.alloc(5, 0xff), duration: Buffer.alloc(3, 0xff),
                descriptors: [{descriptor_tag: 0x4e, items}]}],
        });
        expect(program.detail).toEqual({'番組内容': '最初の本文', '題': '本文A', '題\t': '本文B'});
    });
});
