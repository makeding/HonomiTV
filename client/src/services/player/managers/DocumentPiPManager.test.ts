import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type DPlayer from 'dplayer';

import DocumentPiPManager from '@/services/player/managers/DocumentPiPManager';


vi.mock('web-bml', () => ({bmlBrowserFontNames: {roundGothic: 'round', squareGothic: 'square'}}));
vi.mock('@/services/player/managers/KeyboardShortcutManager', () => ({default: class {init() {} destroy() {}}}));
vi.mock('@/services/player/managers/LiveDataBroadcastingManager', () => ({default: {ROUND_GOTHIC: {source: ''}, SQUARE_GOTHIC: {source: ''}}}));
vi.mock('@/stores/PlayerStore', () => ({default: () => ({is_document_pip: false, is_control_display: false, event_emitter: {emit: vi.fn()}})}));

beforeEach(() => {
    vi.useFakeTimers();
    document.body.innerHTML = '<div class="watch-content"><div class="watch-header"></div><div class="watch-player"></div></div>';
    vi.stubGlobal('FontFace', class {load() {return Promise.resolve(this);}});
});
afterEach(() => {vi.useRealTimers(); vi.unstubAllGlobals(); document.body.innerHTML = '';});

async function enterPictureInPicture(paused: boolean) {
    const pipDocument = document.implementation.createHTMLDocument();
    Object.defineProperty(pipDocument, 'fonts', {value: {add: vi.fn()}});
    const pipWindow = {document: pipDocument, onpagehide: null as null | (() => Promise<void>), close: vi.fn()};
    const api = {window: null, onenter: null, requestWindow: vi.fn().mockResolvedValue(pipWindow)};
    vi.stubGlobal('documentPictureInPicture', api);
    const video = document.createElement('video');
    Object.defineProperty(video, 'paused', {value: paused, configurable: true});
    const pause = vi.spyOn(video, 'pause').mockImplementation(() => {});
    const play = vi.spyOn(video, 'play').mockResolvedValue();
    const player = {video, on: vi.fn()} as unknown as DPlayer;
    const manager = new DocumentPiPManager(player, 'Video');
    await manager.init();
    await video.requestPictureInPicture();
    return {manager, pipWindow, play, pause};
}

describe('Document PiP frame recovery', () => {
    it('restarts active playback after moving the player back', async () => {
        const {manager, pipWindow, play, pause} = await enterPictureInPicture(false);
        const exit = pipWindow.onpagehide!();
        expect(document.querySelector('.watch-content .watch-player')).not.toBeNull();
        expect(pause).toHaveBeenCalledTimes(1);
        await vi.runAllTimersAsync();
        await exit;
        expect(play).toHaveBeenCalledTimes(1);
        await manager.destroy();
    });
    it('leaves paused playback paused', async () => {
        const {manager, pipWindow, play, pause} = await enterPictureInPicture(true);
        await pipWindow.onpagehide!();
        expect(pause).not.toHaveBeenCalled();
        expect(play).not.toHaveBeenCalled();
        await manager.destroy();
    });
    it('does not restart playback if teardown starts during the frame delay', async () => {
        const {manager, pipWindow, play} = await enterPictureInPicture(false);
        const exit = pipWindow.onpagehide!();
        await manager.destroy();
        await vi.runAllTimersAsync();
        await exit;
        expect(play).not.toHaveBeenCalled();
    });
});
