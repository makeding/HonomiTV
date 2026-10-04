import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { createMemoryHistory, createRouter } from 'vue-router';

import SeriesEpisodeList from '@/components/Series/SeriesEpisodeList.vue';
import Videos, { IRecordedProgramDefault } from '@/services/Videos';

vi.mock('@/components/Videos/RecordedProgramMenu.vue', () => ({default: {template: '<div />'}}));

describe('放送中詳細の話数スクロール', () => {
    it('初回描画で話数リストだけを右端へ移動し、画像更新で手動位置を戻さない', async () => {
        vi.spyOn(Videos, 'fetchVideosBySeries').mockResolvedValue({total: 12, recorded_programs:
            Array.from({length: 12}, (_, index) => ({...IRecordedProgramDefault, id: index + 1,
                episode_number: String(index + 1)})),
        });
        vi.spyOn(HTMLElement.prototype, 'scrollWidth', 'get').mockReturnValue(2200);
        vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(400);
        const router = createRouter({history: createMemoryHistory(), routes: [
            {path: '/videos/watch/:id', component: {template: '<div />'}},
        ]});
        const wrapper = mount(SeriesEpisodeList, {props: {
            seriesId: 1, title: '作品', description: '', bangumiSubjectId: null,
            bangumiSubjectName: null, bangumiSubjectNameCn: null,
            bangumiSubjectSummary: null, bangumiSubjectImageUrl: null, initialScrollToLatest: true,
        }, global: {plugins: [router], stubs: {'v-skeleton-loader': true}}});
        await flushPromises();
        const scroller = wrapper.get('.series-episode-list__matrix-scroll').element;
        expect(scroller.scrollLeft).toBe(1800);
        expect(wrapper.findAll('.series-episode-list__episode')).toHaveLength(12);
        expect(wrapper.findAll('button')).toHaveLength(0);
        scroller.scrollLeft = 320;
        await wrapper.get('.series-episode-list__episode img').trigger('load');
        expect(scroller.scrollLeft).toBe(320);
        wrapper.unmount();
    });
});
