import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type DPlayer from 'dplayer';

import LiveSessionPlaybackManager from '@/services/player/managers/LiveSessionPlaybackManager';


const state = vi.hoisted(() => ({
    is_loading: true, is_video_buffering: true, is_background_display: true, live_stream_status: '',
}));
vi.mock('@/stores/PlayerStore', () => ({default: () => state}));
vi.mock('mpegts.js', () => ({default: {Events: {ERROR: 'error'}}}));

function createPlayer() {
    const video = document.createElement('video');
    const handlers = new Map<string, (...args: any[]) => void>();
    const hls = {
        on: vi.fn((event: string, callback: (...args: any[]) => void) => handlers.set(event, callback)),
        off: vi.fn(),
    };
    const player = {video, plugins: {hls}} as unknown as DPlayer;
    return {player, video, handlers, hls};
}

describe('ネットテレビの実メディア状態', () => {
    beforeEach(() => {
        vi.useFakeTimers();
        Object.assign(state, {is_loading: true, is_video_buffering: true, is_background_display: true, live_stream_status: ''});
    });
    afterEach(() => vi.useRealTimers());

    it('放送イベントなしで playing を受けると待機状態を解除する', async () => {
        const {player, video} = createPlayer();
        const failure = vi.fn();
        const manager = new LiveSessionPlaybackManager(player, failure);
        await manager.init();
        expect(state.live_stream_status).toBe('Standby');
        video.dispatchEvent(new Event('playing'));
        expect(state.is_loading).toBe(false);
        expect(state.is_video_buffering).toBe(false);
        expect(state.live_stream_status).toBe('ONAir');
        await vi.advanceTimersByTimeAsync(30000);
        expect(failure).not.toHaveBeenCalled();
        await manager.destroy();
    });

    it('再生が届かないとタイムアウト理由を保ち、一度だけ解放を要求する', async () => {
        const {player, video} = createPlayer();
        const failure = vi.fn();
        const manager = new LiveSessionPlaybackManager(player, failure);
        await manager.init();
        await vi.advanceTimersByTimeAsync(30000);
        expect(failure).toHaveBeenCalledWith(expect.stringContaining('LIVE_START_TIMEOUT'));
        video.dispatchEvent(new Event('error'));
        expect(failure).toHaveBeenCalledTimes(1);
        await manager.destroy();
    });

    it('終了後のイベントとタイマーが次のチャンネルの状態を上書きしない', async () => {
        const {player, video, hls} = createPlayer();
        const failure = vi.fn();
        const manager = new LiveSessionPlaybackManager(player, failure);
        await manager.init();
        await manager.destroy();
        state.live_stream_status = 'new-channel';
        video.dispatchEvent(new Event('playing'));
        video.dispatchEvent(new Event('error'));
        await vi.advanceTimersByTimeAsync(30000);
        expect(state.live_stream_status).toBe('new-channel');
        expect(failure).not.toHaveBeenCalled();
        expect(hls.off).toHaveBeenCalled();
    });

    it('致命的 HLS エラーの詳細をユーザー向け失敗に残す', async () => {
        const {player, handlers} = createPlayer();
        const failure = vi.fn();
        const manager = new LiveSessionPlaybackManager(player, failure);
        await manager.init();
        handlers.get('hlsError')?.('hlsError', {fatal: false, details: 'fragLoadError'});
        expect(failure).not.toHaveBeenCalled();
        handlers.get('hlsError')?.('hlsError', {fatal: true, details: 'manifestLoadError'});
        expect(failure).toHaveBeenCalledWith(expect.stringContaining('HLS_manifestLoadError'));
        await manager.destroy();
    });
});
