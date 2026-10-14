import { afterEach, describe, expect, it, vi } from 'vitest';

import type { IClientSettings } from '@/services/Settings';

import APIClient from '@/services/APIClient';
import Settings from '@/services/Settings';

vi.mock('@/services/APIClient', () => ({default: {put: vi.fn(async () => ({type: 'success'}))}}));
vi.mock('@/stores/SettingsStore', () => ({getSyncableClientSettings: (value: unknown) => value}));

const settings = {watched_history: [{video_id: 1, last_playback_position: 1200, created_at: 1, updated_at: 2}]} as IClientSettings;
const cleanups: Array<() => void> = [];

afterEach(() => {
    cleanups.forEach(cleanup => cleanup());
    cleanups.length = 0;
    vi.clearAllMocks();
});

describe('既存の履歴クラウド同期だけで現在位置を送る', () => {
    it('登録自体は通信せず、既存の PUT に現在位置を添える', async () => {
        cleanups.push(Settings.bindPlaybackProgress(() => ({video_id: 1, playback_position: 1205, duration: 1807})));
        expect(APIClient.put).not.toHaveBeenCalled();
        await Settings.updateClientSettings(settings);
        expect(APIClient.put).toHaveBeenCalledTimes(1);
        expect(APIClient.put).toHaveBeenCalledWith('/settings/client', {
            ...settings, playback_progress: {video_id: 1, playback_position: 1205, duration: 1807},
        });
        expect(settings).not.toHaveProperty('playback_progress');
    });

    it('旧プレイヤーの解除で新プレイヤーの現在位置を消さない', async () => {
        const oldCleanup = Settings.bindPlaybackProgress(() => ({video_id: 2, playback_position: 3000, duration: 10807}));
        cleanups.push(Settings.bindPlaybackProgress(() => ({video_id: 1, playback_position: 1205, duration: 1807})));
        oldCleanup();
        await Settings.updateClientSettings(settings);
        expect(APIClient.put).toHaveBeenCalledWith('/settings/client', expect.objectContaining({
            playback_progress: {video_id: 1, playback_position: 1205, duration: 1807},
        }));
    });

    it('再生終了後の一括履歴同期に現在位置を添えない', async () => {
        const cleanup = Settings.bindPlaybackProgress(() => ({video_id: 1, playback_position: 1200, duration: 1807}));
        cleanup();
        await Settings.updateClientSettings(settings);
        expect(APIClient.put).toHaveBeenCalledTimes(1);
        expect(APIClient.put).toHaveBeenCalledWith('/settings/client', {...settings, playback_progress: null});
    });
});
