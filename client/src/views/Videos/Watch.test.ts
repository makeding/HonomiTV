import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Watch from '@/views/Videos/Watch.vue';

const mocks = vi.hoisted(() => ({
    fetchVideo: vi.fn(),
    getOfflineVideo: vi.fn(),
    playerInit: vi.fn(),
    playerDestroy: vi.fn(),
    routerPush: vi.fn(),
    playerStore: {
        recorded_program: {id: -1},
        is_offline_playback: false,
        offline_video: null,
    },
    settingsStore: {},
}));

vi.mock('@/components/Watch/Watch.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/services/Videos', () => ({default: {fetchVideo: mocks.fetchVideo}}));
vi.mock('@/services/OfflineVideos', () => ({default: {getVideo: mocks.getOfflineVideo}}));
vi.mock('@/services/player/PlayerController', () => ({
    default: class {
        init = mocks.playerInit;
        destroy = mocks.playerDestroy;
    },
}));
vi.mock('@/stores/PlayerStore', () => {
    const usePlayerStore = () => mocks.playerStore;
    usePlayerStore.$id = 'player';
    return {default: usePlayerStore};
});
vi.mock('@/stores/SettingsStore', () => {
    const useSettingsStore = () => mocks.settingsStore;
    useSettingsStore.$id = 'settings';
    return {default: useSettingsStore};
});

const createRecordingProgram = (duration: number) => ({
    id: 1,
    recorded_video: {status: 'Recording', duration},
});

const mountWatch = () => mount(Watch, {global: {mocks: {
    $route: {params: {video_id: '1'}, query: {}},
    $router: {push: mocks.routerPush},
}}});

beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
    mocks.getOfflineVideo.mockResolvedValue(null);
    mocks.playerDestroy.mockResolvedValue(undefined);
    mocks.playerStore.recorded_program = {id: -1};
    mocks.playerStore.is_offline_playback = false;
    mocks.playerStore.offline_video = null;
});

afterEach(() => {
    vi.useRealTimers();
});

describe('追いかけ再生中の番組情報更新', () => {
    it('プレイヤー初期化中に破棄された旧セッションは更新タイマーを作らない', async () => {
        let finishPlayerInit!: () => void;
        mocks.fetchVideo.mockResolvedValue(createRecordingProgram(300));
        mocks.playerInit.mockReturnValue(new Promise<void>(resolve => {finishPlayerInit = resolve;}));
        const setInterval = vi.spyOn(window, 'setInterval');
        const wrapper = mountWatch();
        await flushPromises();
        expect(mocks.playerInit).toHaveBeenCalledOnce();

        await (wrapper.vm as unknown as {destroy: () => Promise<void>}).destroy();
        finishPlayerInit();
        await flushPromises();

        expect(setInterval).not.toHaveBeenCalled();
        wrapper.unmount();
    });

    it('破棄後に届いた定期取得結果は現在の番組情報を上書きしない', async () => {
        const initialProgram = createRecordingProgram(300);
        const refreshedProgram = createRecordingProgram(330);
        let finishRefresh!: (value: typeof refreshedProgram) => void;
        mocks.fetchVideo
            .mockResolvedValueOnce(initialProgram)
            .mockReturnValueOnce(new Promise(resolve => {finishRefresh = resolve;}));
        mocks.playerInit.mockResolvedValue(undefined);
        const wrapper = mountWatch();
        await flushPromises();

        vi.advanceTimersByTime(30_000);
        await flushPromises();
        expect(mocks.fetchVideo).toHaveBeenCalledTimes(2);
        await (wrapper.vm as unknown as {destroy: () => Promise<void>}).destroy();
        finishRefresh(refreshedProgram);
        await flushPromises();

        expect(mocks.playerStore.recorded_program).toBe(initialProgram);
        wrapper.unmount();
    });
});
