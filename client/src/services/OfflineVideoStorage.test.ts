import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IOfflineVideo } from '@/services/OfflineVideos';

import OfflineVideoStorage from '@/services/OfflineVideoStorage';


const video = {video_id: 1, generation_id: 'saved', quality: '720p'} as IOfflineVideo;
const baseURL = 'https://example.test/local/offline-videos/1/saved';
const primaryPlaylist = '#EXTM3U\n#EXT-X-TARGETDURATION:6\n#EXTINF:6,\nsegments/0.ts\n#EXTINF:3,\nsegments/1.ts\n#EXT-X-ENDLIST\n';

beforeEach(() => {
    vi.spyOn(OfflineVideoStorage, 'getStoredVideo').mockResolvedValue(video);
    const cache = {match: vi.fn(async (request: Request | string) => {
        const url = typeof request === 'string' ? request : request.url;
        if (url.includes('/playlist.m3u8')) return new Response(primaryPlaylist);
        if (url.includes('/segments/0.ts')) return new Response(new Uint8Array([1, 2, 3]));
        return undefined;
    })};
    // CacheStorage の入口だけを代替し、Service Worker が呼ぶ getResponse() はそのまま実行する。
    vi.stubGlobal('caches', {open: vi.fn().mockResolvedValue(cache)});
});
afterEach(() => vi.unstubAllGlobals());

describe('offline HLS audio responses', () => {
    it('publishes local renditions and preserves the original primary playlist', async () => {
        const master = await OfflineVideoStorage.getResponse(new Request(`${baseURL}/playlist.m3u8?type=master`));
        expect(master.headers.get('Content-Type')).toBe('application/vnd.apple.mpegurl');
        const masterText = await master.text();
        expect(masterText).toContain('URI="playlist.m3u8?type=secondary-audio"');
        expect(masterText).toContain('playlist.m3u8?type=primary-audio');
        expect(masterText).not.toContain('/api/');
        const primary = await OfflineVideoStorage.getResponse(new Request(`${baseURL}/playlist.m3u8?cache_key=seek`));
        expect(await primary.text()).toBe(primaryPlaylist);
    });
    it('uses exactly the saved timeline for secondary audio', async () => {
        const response = await OfflineVideoStorage.getResponse(new Request(`${baseURL}/playlist.m3u8?type=secondary-audio`));
        expect(await response.text()).toBe(primaryPlaylist.replaceAll('.ts', '.ts?audio=secondary'));
    });
    it('rejects malformed secondary data instead of silently returning primary audio', async () => {
        const response = await OfflineVideoStorage.getResponse(new Request(`${baseURL}/segments/0.ts?audio=secondary`));
        expect(response.status).toBe(422);
    });
});
