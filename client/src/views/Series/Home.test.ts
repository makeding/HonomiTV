import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import Series, { ISeriesList, ISeriesSummary } from '@/services/Series';
import Home from '@/views/Series/Home.vue';

vi.mock('@/components/HeaderBar.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Navigation.vue', () => ({default: {
    props: {iconOnly: Boolean}, template: '<nav :data-icon-only="iconOnly" />',
}}));
vi.mock('@/components/SPHeaderBar.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Breadcrumbs.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Series/SeriesEpisodeList.vue', () => ({default: {
    props: ['seriesId'], template: '<section :data-series-details="seriesId" />',
}}));
vi.mock('@/stores/UserStore', () => ({default: () => ({fetchUser: vi.fn()})}));

afterEach(() => {
    vi.unstubAllGlobals();
});

describe('シリーズ一覧のコンパクト表示', () => {
    it('取得中は15枚を表示し、5列の一覧でも選択した行の直下で詳細を開閉する', async () => {
        let finishLoading!: (result: ISeriesList) => void;
        vi.spyOn(Series, 'fetchSeriesList').mockReturnValue(new Promise(resolve => {finishLoading = resolve;}));
        // happy-dom にはレイアウト計算がないため、ブラウザが解決する5列の結果を渡す。
        vi.spyOn(window, 'getComputedStyle').mockReturnValue({
            gridTemplateColumns: '276px 276px 276px 276px 276px',
        } as CSSStyleDeclaration);
        vi.stubGlobal('ResizeObserver', class {
            observe() {}
            disconnect() {}
        });
        vi.spyOn(window, 'scrollBy').mockImplementation(() => undefined);
        const router = createRouter({history: createMemoryHistory(), routes: [
            {path: '/series/:series_id?', component: Home},
        ]});
        await router.push('/series/');
        const wrapper = mount(Home, {global: {plugins: [router], directives: {ripple: {}}, stubs: {
            'v-btn': {template: '<button><slot /></button>'},
            'v-select': true,
            'v-pagination': true,
            'v-skeleton-loader': {template: '<div class="loading-card" />'},
            Icon: true,
        }}});
        try {
            await flushPromises();
            expect(wrapper.get('nav').attributes('data-icon-only')).toBe('true');
            expect(wrapper.findAll('.series-grid .loading-card')).toHaveLength(15);

            const seriesList: ISeriesSummary[] = Array.from({length: 10}, (_, index) => ({
                id: index + 1, title: `シリーズ ${index + 1}`, description: '', genres: [],
                thumbnail_recorded_program_ids: [], channel_ids: [], official_website_url: null,
                bangumi_subject_id: null, bangumi_subject_name: null, bangumi_subject_name_cn: null,
                bangumi_subject_summary: null, bangumi_subject_image_url: null,
                recorded_programs_count: 4, created_at: '', updated_at: '',
            }));
            finishLoading({total: seriesList.length, series_list: seriesList});
            await flushPromises();
            expect(wrapper.findAll('.loading-card')).toHaveLength(0);
            expect(wrapper.findAll('.series-grid__row')).toHaveLength(2);
            expect(wrapper.findAll('.series-grid__row')[0].findAll('button')).toHaveLength(5);

            const target = wrapper.get('[data-series-id="5"]');
            await target.trigger('click');
            await flushPromises();
            expect(target.attributes('aria-expanded')).toBe('true');
            expect(wrapper.get('.series-grid__row').element.nextElementSibling?.getAttribute('data-series-details')).toBe('5');
            expect(wrapper.get('[data-series-id="5"]').element).toBe(target.element);

            window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
            await flushPromises();
            expect(wrapper.find('[data-series-details]').exists()).toBe(false);
            expect(target.attributes('aria-expanded')).toBe('false');
        } finally {
            wrapper.unmount();
        }
    });
});
