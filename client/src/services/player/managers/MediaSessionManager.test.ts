import { afterEach, describe, expect, it, vi } from 'vitest';

import type DPlayer from 'dplayer';

import MediaSessionManager from '@/services/player/managers/MediaSessionManager';


vi.mock('@/router', () => ({default: {push: vi.fn()}}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => ({})}));
vi.mock('@/stores/PlayerStore', () => ({default: () => ({recorded_program: {id: 1, title: '録画', series_title: null}})}));
vi.mock('@/utils', () => ({default: {api_base_url: '/api', isSafari: () => true}}));

afterEach(() => vi.unstubAllGlobals());

describe('MediaSession metadata readiness', () => {
    it('ignores unknown duration, updates after metadata, and ignores teardown events', async () => {
        const setPositionState = vi.fn();
        vi.stubGlobal('navigator', {mediaSession: {setPositionState, setActionHandler: vi.fn()}});
        vi.stubGlobal('MediaMetadata', class {});
        const handlers = new Map<string, () => void>();
        const video = {duration: NaN, currentTime: 12, playbackRate: 1};
        const player = {video, on: (event: string, callback: () => void) => handlers.set(event, callback), off: vi.fn()} as unknown as DPlayer;
        const manager = new MediaSessionManager(player, 'Video');
        await manager.init();
        handlers.get('ratechange')?.();
        video.duration = Infinity;
        handlers.get('seeking')?.();
        expect(setPositionState).not.toHaveBeenCalled();
        video.duration = 120;
        handlers.get('seeked')?.();
        expect(setPositionState).toHaveBeenCalledWith({duration: 120, playbackRate: 1, position: 12});
        await manager.destroy();
        handlers.get('seeked')?.();
        expect(setPositionState).toHaveBeenCalledTimes(1);
    });
});
