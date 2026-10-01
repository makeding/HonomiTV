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

describe('キャプチャフォントの失敗を再生から分離する', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.loadFonts.mockResolvedValue(undefined);
        vi.spyOn(console, 'warn').mockImplementation(() => {});
        vi.spyOn(console, 'error').mockImplementation(() => {});
    });

    it.each(['Live', 'Video'] as const)('%s でフォントの失敗がプレイヤー初期化を中断しない', async (mode) => {
        mocks.loadFonts.mockRejectedValue(new TypeError('Font request blocked'));
        const player = createPlayer();
        const manager = new CaptureManager(player, mode);
        await expect(manager.init()).resolves.toBeUndefined();
        expect(player.container.querySelector('.dplayer-capture-icon')).not.toBeNull();
        expect(player.container.querySelector('.dplayer-comment-capture-icon')).not.toBeNull();
        expect(player.notice).not.toHaveBeenCalled();
        expect(console.warn).toHaveBeenCalled();
    });

    it('コメント付きキャプチャで再試行し、失敗理由と次の操作をプレイヤー内に表示する', async () => {
        mocks.loadFonts.mockRejectedValue(new TypeError('Font request blocked'));
        const player = createPlayer();
        const manager = new CaptureManager(player, 'Video');
        await manager.init();
        player.container.querySelector<HTMLElement>('.dplayer-comment-capture-icon')!.click();
        await vi.waitFor(() => expect(player.notice).toHaveBeenCalled());
        expect(mocks.loadFonts).toHaveBeenCalledTimes(2);
        expect(player.notice).toHaveBeenCalledWith(expect.stringContaining('CAPTURE_FONT_LOAD_FAILED'),
            undefined, undefined, '#FF6F6A');
        expect(player.notice).toHaveBeenCalledWith(expect.stringContaining('もう一度キャプチャしてください'),
            undefined, undefined, '#FF6F6A');
        expect(player.container.querySelector('.dplayer-capturing')).toBeNull();
    });
});
