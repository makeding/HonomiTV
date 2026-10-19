
import { flushPromises } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { IClientSettings } from '@/services/Settings';
import type DPlayer from 'dplayer';

import LiveHistoryManager from '@/services/player/managers/LiveHistoryManager';


const state = vi.hoisted(() => ({
    now: 1000,
    token: 'account-one' as string | null,
    settings: {watched_history: [] as IClientSettings['watched_history'], video_watched_history_max_count: 50, sync_settings: true},
    save: vi.fn<(...args: unknown[]) => Promise<IClientSettings['watched_history'] | null>>(),
}));
vi.mock('@/stores/SettingsStore', () => ({default: () => ({settings: state.settings, syncClientSettingsToServer: (_force: boolean, progress: unknown) => state.save(progress)})}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => ({channel: {current: {id: 'NID4-SID101'}}})}));
vi.mock('@/utils', () => ({default: {getAccessToken: () => state.token, time: () => state.now}}));

function player() {
    const handlers = new Map<string, () => void>();
    const video = {currentTime: 100, buffered: {length: 1, end: () => video.currentTime + 5}, paused: false, ended: false, readyState: 4};
    return {
        video,
        on: (event: string, callback: () => void) => handlers.set(event, callback),
        off: (event: string) => handlers.delete(event),
        emit: (event: string) => handlers.get(event)?.(),
    };
}

beforeEach(() => {
    // happy-dom は HTMLMediaElement の readyState 定数を公開していない。
    vi.stubGlobal('HTMLMediaElement', {HAVE_FUTURE_DATA: 3});
    state.now = 1000;
    state.token = 'account-one';
    state.settings.watched_history = [];
    state.settings.sync_settings = true;
    state.save.mockReset().mockResolvedValue([]);
});

afterEach(() => vi.unstubAllGlobals());

describe('ライブの履歴保存ライフサイクル', () => {
    it('設定同期が無効ならライブ履歴をサーバーへ送らない', async () => {
        state.settings.sync_settings = false;
        const live = player();
        const manager = new LiveHistoryManager(live as unknown as DPlayer);
        await manager.init();
        live.emit('timeupdate');
        live.video.currentTime = 101;
        state.now = 1001;
        live.emit('timeupdate');
        await manager.destroy();
        expect(state.save).not.toHaveBeenCalled();
    });

    it('未入庫でも次のチェックポイントを既存の設定同期に添える', async () => {
        const live = player();
        const manager = new LiveHistoryManager(live as unknown as DPlayer);
        await manager.init();
        live.emit('timeupdate');
        live.video.currentTime = 101;
        state.now = 1001;
        live.emit('timeupdate');
        await flushPromises();
        expect(state.save).toHaveBeenLastCalledWith({channel_id: 'NID4-SID101', playback_time: 996});
        const items = [{video_id: 7, last_playback_position: 130, created_at: 1006, updated_at: 1006}];
        state.save.mockResolvedValueOnce(items);
        live.video.currentTime = 111;
        state.now = 1011;
        live.emit('timeupdate');
        await flushPromises();
        expect(state.settings.watched_history).toEqual([]);
        await manager.destroy();
    });

    it('停止中は上報せず、退室時にも最後に見た画面の時刻だけを保存する', async () => {
        const live = player();
        const manager = new LiveHistoryManager(live as unknown as DPlayer);
        await manager.init();
        live.emit('timeupdate');
        live.video.currentTime = 101;
        state.now = 1001;
        live.emit('timeupdate');
        await flushPromises();
        state.save.mockClear();
        live.video.paused = true;
        state.now = 1100;
        live.emit('timeupdate');
        expect(state.save).not.toHaveBeenCalled();
        live.emit('pause');
        await flushPromises();
        expect(state.save).toHaveBeenLastCalledWith({channel_id: 'NID4-SID101', playback_time: 996});
        await manager.destroy();
        await flushPromises();
        expect(state.save).toHaveBeenLastCalledWith({channel_id: 'NID4-SID101', playback_time: 996});
        state.save.mockClear();
        live.emit('timeupdate');
        window.dispatchEvent(new Event('pagehide'));
        expect(state.save).not.toHaveBeenCalled();
    });

    it('通信中にアカウントが変わったら古い応答を新アカウントの履歴へ入れない', async () => {
        let resolve!: (items: IClientSettings['watched_history']) => void;
        state.save.mockReturnValueOnce(new Promise(done => { resolve = done; }));
        const live = player();
        const manager = new LiveHistoryManager(live as unknown as DPlayer);
        await manager.init();
        live.emit('timeupdate');
        live.video.currentTime = 101;
        state.now = 1001;
        live.emit('timeupdate');
        state.token = 'account-two';
        resolve([{video_id: 7, last_playback_position: 130, created_at: 996, updated_at: 996}]);
        await flushPromises();
        expect(state.settings.watched_history).toEqual([]);
        await manager.destroy();
        expect(state.save).toHaveBeenCalledTimes(1);
    });

    it('通信中の複数チェックポイントを最後の一件へまとめる', async () => {
        let resolve!: (items: IClientSettings['watched_history']) => void;
        state.save.mockReturnValueOnce(new Promise(done => { resolve = done; }));
        const live = player();
        const manager = new LiveHistoryManager(live as unknown as DPlayer);
        await manager.init();
        live.emit('timeupdate');
        for (const elapsed of [1, 11, 21]) {
            state.now = 1000 + elapsed;
            live.video.currentTime = 100 + elapsed;
            live.emit('timeupdate');
        }
        expect(state.save).toHaveBeenCalledTimes(1);
        await manager.destroy();
        resolve([]);
        await flushPromises();
        expect(state.save).toHaveBeenCalledTimes(2);
        expect(state.save).toHaveBeenLastCalledWith({channel_id: 'NID4-SID101', playback_time: 1016});
    });
});
