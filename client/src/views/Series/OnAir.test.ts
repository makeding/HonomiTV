import { flushPromises, mount } from '@vue/test-utils';
import { createPinia } from 'pinia';
import { describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import Series from '@/services/Series';
import OnAir from '@/views/Series/OnAir.vue';

vi.mock('@/components/HeaderBar.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Navigation.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/SPHeaderBar.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Breadcrumbs.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/components/Series/SeriesEpisodeList.vue', () => ({default: {template: '<div />'}}));

describe('放送中一覧の作品開閉と季度 URL', () => {
    it('開閉・Escape・季度変更・URL の戻りで季度と横位置を維持する', async () => {
        const item = {id: 103, title: '作品', thumbnail_recorded_program_ids: [], channel_ids: [],
            recorded_episodes_count: 12, missing_episodes_count: 0, partially_recorded_episodes_count: 0,
            weekday: 1, broadcast_time: '18:00', latest_broadcast_at: '2026-03-31T18:00:00+09:00'};
        vi.spyOn(Series, 'fetchOnAirSeriesList').mockResolvedValue({current_season_id: '2026-10', seasons: [
            {season_id: '2026-10', season_label: '2026年10月期', is_current: true,
                start_date: '2026-10-01', end_date: '2026-10-04', series_list: [item]},
            {season_id: '2026-01', season_label: '2026年1月期', is_current: false,
                start_date: '2026-01-01', end_date: '2026-03-31', series_list: [item]},
        ]});
        vi.spyOn(Series, 'fetchSeriesSummary').mockResolvedValue(null);
        vi.spyOn(window, 'scrollBy').mockImplementation(() => undefined);
        const router = createRouter({history: createMemoryHistory(), routes: [
            {path: '/series/on-air/:series_id?', component: OnAir},
        ]});
        await router.push('/series/on-air?season=2026-01&other=keep');
        const wrapper = mount(OnAir, {global: {plugins: [router, createPinia()], stubs: {
            'v-btn': {template: '<button><slot /></button>'}, 'v-skeleton-loader': true,
        }}});
        await flushPromises();
        // スマートフォンで単位を省略しても、統計と全シリーズの導線は名前を保持する。
        expect(wrapper.get('.on-air-stat').attributes('aria-label')).toBe('1 作品');
        expect(wrapper.get('.on-air-stat--complete').attributes('aria-label')).toBe('1 完録');
        expect(wrapper.get('.on-air-all-series').attributes('aria-label')).toBe('すべてのシリーズ');
        expect(wrapper.get('.on-air-all-series').attributes('to')).toBe('/series/');
        const grid = wrapper.get('.on-air-week').element;
        grid.scrollLeft = 350;
        await wrapper.get('[data-series-id="103"]').trigger('click');
        await flushPromises();
        expect(router.currentRoute.value.fullPath).toBe('/series/on-air/103?season=2026-01&other=keep');
        expect(grid.scrollLeft).toBe(350);
        await wrapper.get('[data-series-id="103"]').trigger('click');
        await flushPromises();
        expect(router.currentRoute.value.query.season).toBe('2026-01');
        await wrapper.get('[data-series-id="103"]').trigger('click');
        await flushPromises();
        window.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}));
        await flushPromises();
        expect(router.currentRoute.value.path).toBe('/series/on-air');
        expect(router.currentRoute.value.query.season).toBe('2026-01');
        await router.push('/series/on-air?season=2026-10');
        await flushPromises();
        expect(wrapper.text()).toContain('2026年10月期');
        await router.push('/series/on-air/103?season=2026-01');
        await flushPromises();
        expect(wrapper.text()).toContain('2026年1月期');
        wrapper.unmount();
    });
});
