import { beforeEach, describe, expect, it, vi } from 'vitest';

import type DPlayer from 'dplayer';

import CaptureManager from '@/services/player/managers/CaptureManager';


const mocks = vi.hoisted(() => ({loadFonts: vi.fn()}));
vi.mock('@/workers/CaptureCompositorProxy', () => ({default: {loadFonts: mocks.loadFonts}}));
vi.mock('@/utils', () => ({default: {time: () => 0}, dayjs: vi.fn(), dayjsOriginal: vi.fn()}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => ({})}));
vi.mock('@/stores/PlayerStore', () => ({default: () => ({})}));
vi.mock('@/stores/SettingsStore', () => ({default: () => ({settings: {}})}));
vi.mock('@/services/Captures', () => ({default: {}}));
vi.mock('mpeg2toh264/yadif', () => ({Deinterlacer: vi.fn()}));

function createPlayer() {
    const container = document.createElement('div');
    container.innerHTML = '<div class="dplayer-player-restart-icon"></div>';
    return {
        container, video: {videoWidth: 1920, videoHeight: 1080},
        danmaku: {showing: true}, notice: vi.fn(),
    } as unknown as DPlayer;
}

describe('上流キャプチャの初期化とプロバイダー接続', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.loadFonts.mockResolvedValue(undefined);
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    it.each(['Live', 'Video'] as const)('%s でキャプチャボタンと上流フォントを初期化する', async (mode) => {
        const player = createPlayer();
        const manager = new CaptureManager(player, mode);
        await manager.init();
        expect(player.container.querySelector('.dplayer-capture-icon')).not.toBeNull();
        expect(player.container.querySelector('.dplayer-comment-capture-icon')).not.toBeNull();
        expect(mocks.loadFonts).toHaveBeenCalledTimes(1);
    });

    it('コメント機能がないプロバイダーでは合成を開始せず理由を表示する', async () => {
        const player = createPlayer();
        player.danmaku = null;
        const manager = new CaptureManager(player, 'Video');
        await manager.init();
        player.container.querySelector<HTMLElement>('.dplayer-comment-capture-icon')!.click();
        expect(player.notice).toHaveBeenCalledWith('ネットテレビではコメント付きキャプチャを利用できません。',
            undefined, undefined, '#FF6F6A');
        expect(player.container.querySelector('.dplayer-capturing')).toBeNull();
    });
});
