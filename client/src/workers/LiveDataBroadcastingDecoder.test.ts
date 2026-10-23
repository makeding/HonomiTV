import { afterEach, describe, expect, it, vi } from 'vitest';

import type { ILiveChannel } from '@/services/Channels';

const state = vi.hoisted(() => ({
    Decoder: null as unknown as new (channel: ILiveChannel, quality: string) => {
        run(callback: (message: unknown) => void, errorCallback: (reason: string) => void): void;
        destroy(): void;
    },
    parse: vi.fn(),
}));
vi.mock('comlink', () => ({expose: (Decoder: typeof state.Decoder) => {state.Decoder = Decoder;}}));
vi.mock('web-bml/worker', () => ({decodeTS: () => ({parse: state.parse, on: vi.fn()})}));
vi.mock('@/utils', () => ({default: {api_base_url: '/api'}, ProgramUtils: {}}));
import '@/workers/LivePSIArchivedDataDecoder';

const channel = {display_channel_id: 'bs791', service_id: 791, is_datachannel: true, capabilities: {data_broadcasting: true}} as ILiveChannel;

describe('standalone raw TS decoding', () => {
    afterEach(() => {
        vi.unstubAllGlobals();
        state.parse.mockClear();
    });

    it('requests the independent endpoint and passes fragmented TS bytes straight to web-bml', async () => {
        const bytes = new Uint8Array([0x47, 1, 2, 3]);
        const fetch = vi.fn().mockResolvedValue(new Response(bytes));
        vi.stubGlobal('fetch', fetch);
        const error = vi.fn();
        const decoder = new state.Decoder(channel, 'original');
        decoder.run(vi.fn(), error);
        await vi.waitFor(() => expect(state.parse).toHaveBeenCalledOnce());
        expect([...state.parse.mock.calls[0][0]]).toEqual([...bytes]);
        expect(fetch.mock.calls[0][0]).toBe('/api/streams/live/bs791/data-broadcasting');
        await vi.waitFor(() => expect(error).toHaveBeenCalledWith(expect.stringContaining('DATA_STREAM_ENDED')));
        decoder.destroy();
    });

    it('preserves a safe backend failure without feeding its JSON body to the TS parser', async () => {
        vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({detail: 'No tuner [DATA_TUNER_HTTP]'}), {status: 503})));
        const error = vi.fn();
        const decoder = new state.Decoder(channel, '1080p');
        decoder.run(vi.fn(), error);
        await vi.waitFor(() => expect(error).toHaveBeenCalledWith(expect.stringContaining('DATA_TUNER_HTTP')));
        expect(state.parse).not.toHaveBeenCalled();
        decoder.destroy();
    });

    it('aborts reception on exit without reporting an old worker failure', async () => {
        const fetch = vi.fn((_url: string, init: RequestInit) => new Promise((_resolve, reject) => {
            init.signal!.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')));
        }));
        vi.stubGlobal('fetch', fetch);
        const error = vi.fn();
        const decoder = new state.Decoder(channel, '1080p');
        decoder.run(vi.fn(), error);
        decoder.destroy();
        await Promise.resolve();
        await Promise.resolve();
        expect(fetch.mock.calls[0][1].signal!.aborted).toBe(true);
        expect(error).not.toHaveBeenCalled();
    });

    it('keeps the archive endpoint for ordinary video channels', async () => {
        const fetch = vi.fn().mockResolvedValue(new Response(new Uint8Array()));
        vi.stubGlobal('fetch', fetch);
        const error = vi.fn();
        const decoder = new state.Decoder({...channel, is_datachannel: false}, '1080p');
        decoder.run(vi.fn(), error);
        await Promise.resolve();
        expect(fetch.mock.calls[0][0]).toBe('/api/streams/live/bs791/1080p/psi-archived-data');
        expect(error).not.toHaveBeenCalled();
        decoder.destroy();
    });
});
