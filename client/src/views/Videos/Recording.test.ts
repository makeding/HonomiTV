import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import Recording from '@/views/Videos/Recording.vue';

const mocks = vi.hoisted(() => ({
    fetchUser: vi.fn(),
    fetchVideos: vi.fn(),
    replace: vi.fn(),
}));

vi.mock('@/components/Breadcrumbs.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/HeaderBar.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Navigation.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/SPHeaderBar.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Videos/RecordedProgramList.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/services/Videos', () => ({
    default: {fetchVideos: mocks.fetchVideos},
    isChasePlaybackProgram: (program: {recorded_video: {status: string}}) => {
        return program.recorded_video.status === 'Recording';
    },
}));
vi.mock('@/stores/UserStore', () => ({default: () => ({fetchUser: mocks.fetchUser})}));
vi.mock('vue-router', () => ({
    useRoute: () => ({query: {}}),
    useRouter: () => ({replace: mocks.replace}),
}));

beforeEach(() => {
    vi.useFakeTimers();
    vi.resetAllMocks();
    mocks.fetchUser.mockResolvedValue(null);
    mocks.fetchVideos.mockResolvedValue({recorded_programs: [], total: 0});
});

afterEach(() => {
    vi.useRealTimers();
});

describe('追いかけ再生一覧の定期更新', () => {
    it('取得中の重複実行を防ぎ、ページ離脱時にタイマーを破棄する', async () => {
        const setInterval = vi.spyOn(window, 'setInterval');
        const clearInterval = vi.spyOn(window, 'clearInterval');
        const wrapper = mount(Recording);
        await flushPromises();
        expect(mocks.fetchVideos).toHaveBeenCalledTimes(1);
        expect(setInterval).toHaveBeenCalledOnce();
        const refreshTimer = setInterval.mock.results[0].value;

        let finishRefresh!: (value: {recorded_programs: []; total: number}) => void;
        mocks.fetchVideos.mockReturnValueOnce(new Promise(resolve => {finishRefresh = resolve;}));
        vi.advanceTimersByTime(30_000);
        await flushPromises();
        expect(mocks.fetchVideos).toHaveBeenCalledTimes(2);

        // 前回の API 応答を待っている間に次の周期へ進んでも、同じ一覧取得を重ねない。
        vi.advanceTimersByTime(60_000);
        await flushPromises();
        expect(mocks.fetchVideos).toHaveBeenCalledTimes(2);

        finishRefresh({recorded_programs: [], total: 0});
        await flushPromises();
        wrapper.unmount();
        expect(clearInterval).toHaveBeenCalledWith(refreshTimer);

        vi.advanceTimersByTime(60_000);
        await flushPromises();
        expect(mocks.fetchVideos).toHaveBeenCalledTimes(2);
    });

    it('初回取得中にページを離れても後からタイマーを作らない', async () => {
        const setInterval = vi.spyOn(window, 'setInterval');
        let finishInitialFetch!: (value: {recorded_programs: []; total: number}) => void;
        mocks.fetchVideos.mockReturnValueOnce(new Promise(resolve => {finishInitialFetch = resolve;}));
        const wrapper = mount(Recording);
        await flushPromises();
        expect(mocks.fetchVideos).toHaveBeenCalledTimes(1);

        wrapper.unmount();
        finishInitialFetch({recorded_programs: [], total: 0});
        await flushPromises();

        expect(setInterval).not.toHaveBeenCalled();
    });
});
