import { flushPromises, mount } from '@vue/test-utils';
import { describe, expect, it, vi } from 'vitest';
import { defineComponent } from 'vue';

import GGuideCarousel from '@/components/GGuideCarousel.vue';
import { ILiveChannelDefault } from '@/services/Channels';
import { IProgramDefault } from '@/services/Programs';
import Programs, { IGGuideCollection } from '@/services/Programs';

vi.mock('@/services/Programs', async importOriginal => ({
    ...await importOriginal<typeof import('@/services/Programs')>(),
    default: {fetchGGuideCollection: vi.fn(), fetchGGuideProgram: vi.fn()},
}));
vi.mock('@/utils/Utils', async importOriginal => {
    const {default: Utils} = await importOriginal<typeof import('@/utils/Utils')>();
    return {default: {api_base_url: '/api', URLtoLink: Utils.URLtoLink}};
});

// 素材は合成 ID と寸法だけ。受信した放送データや接続先を fixture に保存しない。
const collection: IGGuideCollection = {status: 'Available', assets: [
    {id: 'a'.repeat(64), width: 640, height: 360, programs: [], advertisements: []},
    {id: 'b'.repeat(64), width: 640, height: 480, programs: [], advertisements: []},
    {id: 'd'.repeat(64), width: 200, height: 300, programs: [], advertisements: []},
]};
const carousel = defineComponent({
    props: ['modelValue', 'cycle', 'height', 'interval'],
    template: '<div><slot /></div>',
});
const carouselItem = defineComponent({
    props: ['eager', 'value'],
    template: '<div><slot /></div>',
});
const image = defineComponent({props: ['src'], template: '<div><slot name="error" /></div>'});
const options = {global: {stubs: {
    'v-carousel': carousel,
    'v-carousel-item': carouselItem,
    'v-img': image,
    'v-btn': {template: '<button><slot /></button>'},
    'v-progress-circular': true,
    'v-icon': true,
    'v-dialog': {props: ['modelValue'], template: '<div v-if="modelValue" role="dialog"><slot /></div>'},
    'v-card': {template: '<div><slot /></div>'},
    'v-card-title': {template: '<div><slot /></div>'},
    'v-card-text': {template: '<div><slot /></div>'},
    'v-spacer': true,
}}};

describe('H ガイドの任意コレクション', () => {
    it('画像領域と選択を維持し、フォーカスとホバーで停止して移出五秒後に再開する', async () => {
        vi.useFakeTimers();
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue(collection);
        const wrapper = mount(GGuideCarousel, options);
        expect(wrapper.find('.gguide__frame').exists()).toBe(true);
        await flushPromises();
        const slider = wrapper.getComponent(carousel);
        expect(slider.props('height')).toBe('100%');
        slider.vm.$emit('update:modelValue', collection.assets[1].id);
        await wrapper.get('section').trigger('focusin');
        await vi.advanceTimersByTimeAsync(20000);
        expect(slider.props('modelValue')).toBe(collection.assets[1].id);
        await wrapper.get('section').trigger('focusout');
        await wrapper.get('section').trigger('mouseenter');
        await wrapper.get('section').trigger('mouseleave');
        await vi.advanceTimersByTimeAsync(4999);
        expect(slider.props('modelValue')).toBe(collection.assets[1].id);
        await wrapper.get('section').trigger('mouseenter');
        await vi.advanceTimersByTimeAsync(5000);
        expect(slider.props('modelValue')).toBe(collection.assets[1].id);
        await wrapper.get('section').trigger('mouseleave');
        await vi.advanceTimersByTimeAsync(5000);
        expect(slider.props('modelValue')).toBe(collection.assets[1].id);
        await vi.advanceTimersByTimeAsync(12000);
        expect(slider.props('modelValue')).toBe(collection.assets[0].id);
        await wrapper.setProps({paused: true});
        await vi.advanceTimersByTimeAsync(20000);
        expect(slider.props('modelValue')).toBe(collection.assets[0].id);
        await wrapper.setProps({paused: false});
        await vi.advanceTimersByTimeAsync(12000);
        expect(slider.props('modelValue')).toBe(collection.assets[1].id);
        wrapper.unmount();
        vi.useRealTimers();
    });

    it('取得失敗を同じ枠内で再試行でき、画像失敗にも復旧操作を提供する', async () => {
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValueOnce({status: 'Unavailable', assets: []})
            .mockResolvedValue(collection);
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        expect(wrapper.text()).toContain('[GGUIDE_UNAVAILABLE]');
        await wrapper.get('.gguide__frame button').trigger('click');
        await flushPromises();
        expect(wrapper.findComponent(carousel).exists()).toBe(true);
        expect(wrapper.text()).toContain('[GGUIDE_IMAGE_UNAVAILABLE]');
        expect(wrapper.find('.gguide__frame').exists()).toBe(true);
        wrapper.unmount();
    });

    it('横長バナーと通常タイルを別々のカルーセルにし、番組表を維持する', async () => {
        const banner = {id: 'c'.repeat(64), width: 728, height: 136, programs: [], advertisements: []};
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({...collection, assets: [...collection.assets, banner]});
        const wrapper = mount(GGuideCarousel, {...options, slots: {default: '<div class="timetable-grid">番組表</div>'}});
        await flushPromises();
        expect(wrapper.findAllComponents(carousel)).toHaveLength(3);
        expect(wrapper.get('.gguide--banner').findComponent(carousel).props('modelValue')).toBe(banner.id);
        expect(wrapper.get('.gguide--landscape').findComponent(carousel).props('modelValue')).toBe(collection.assets[0].id);
        expect(wrapper.get('.gguide--poster').findComponent(carousel).props('modelValue')).toBe(collection.assets[2].id);
        expect(wrapper.get('.timetable-grid').text()).toBe('番組表');
        wrapper.unmount();
    });

    it('ヘッダーの実幅が足りる場合だけバナーを移動し、タイルは移動しない', async () => {
        const banner = {id: 'c'.repeat(64), width: 728, height: 136, programs: [], advertisements: []};
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({...collection, assets: [...collection.assets, banner]});
        let resize: ResizeObserverCallback = () => {};
        vi.stubGlobal('ResizeObserver', class {
            constructor(callback: ResizeObserverCallback) { resize = callback; }
            observe() {}
            disconnect() {}
        });
        const target = document.createElement('div');
        document.body.appendChild(target);
        const wrapper = mount(GGuideCarousel, options);
        try {
            await flushPromises();
            await wrapper.setProps({bannerTarget: target});
            resize([{contentRect: {width: 600}}] as ResizeObserverEntry[], {} as ResizeObserver);
            await flushPromises();
            expect(target.querySelector('.gguide--banner')).not.toBeNull();
            expect(target.querySelector('.gguide--landscape')).toBeNull();
            // 実際に必要な幅は約 361px。480px 未満でも収まり、足りない場合だけ専用行へ戻す。
            resize([{contentRect: {width: 370}}] as ResizeObserverEntry[], {} as ResizeObserver);
            await flushPromises();
            expect(target.querySelector('.gguide--banner')).not.toBeNull();
            resize([{contentRect: {width: 360}}] as ResizeObserverEntry[], {} as ResizeObserver);
            await flushPromises();
            expect(target.querySelector('.gguide--banner')).toBeNull();
            expect(wrapper.get('.gguide--banner').findComponent(carousel).props('modelValue')).toBe(banner.id);
        } finally {
            wrapper.unmount();
            target.remove();
            vi.unstubAllGlobals();
        }
    });

    it.each(['Unsupported', 'Available'] as const)('空の %s は任意セクションを表示しない', async status => {
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({status, assets: []});
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        expect(wrapper.find('section').exists()).toBe(false);
        wrapper.unmount();
    });

    it('表示中の番組は四項目が一致した場合だけ使い、範囲外の放送回は別途取得する', async () => {
        const association = {network_id: 0, service_id: 0, event_id: 0, start_at: 946652400000};
        const asset = {...collection.assets[0], programs: [association]};
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({status: 'Available', assets: [asset]});
        const program = {...IProgramDefault, reservation: null};
        const channel = {...ILiveChannelDefault};
        const wrapper = mount(GGuideCarousel, {...options, props: {
            channels: [{channel, programs: [program], subchannels: null}],
        }});
        await flushPromises();
        await wrapper.get('[aria-label="番組詳細を開く"]').trigger('click');
        expect(wrapper.emitted('select-program')?.[0]).toEqual([{asset, channel, program}]);
        await wrapper.setProps({channels: [{channel, programs: [{...program,
            start_time: '2000-01-02T00:00:00+09:00'}], subchannels: null}]});
        vi.mocked(Programs.fetchGGuideProgram).mockResolvedValueOnce({program, channel});
        await wrapper.get('[aria-label="番組詳細を開く"]').trigger('click');
        await flushPromises();
        expect(Programs.fetchGGuideProgram).toHaveBeenCalledWith([association]);
        expect(wrapper.emitted('select-program')?.[1]).toEqual([{asset, channel, program}]);
        wrapper.unmount();
    });

    it('範囲外の番組取得中は連打を止め、失敗した場合は同じ画像から再試行できる', async () => {
        const association = {network_id: 0, service_id: 0, event_id: 0, start_at: 946652400000};
        const asset = {...collection.assets[0], programs: [association]};
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({status: 'Available', assets: [asset]});
        let finish: (value: null) => void = () => {};
        vi.mocked(Programs.fetchGGuideProgram).mockClear().mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        const button = wrapper.get('[aria-label="番組詳細を開く"]');
        await button.trigger('click');
        expect(button.attributes('disabled')).toBeDefined();
        expect(button.attributes('aria-busy')).toBe('true');
        await button.trigger('click');
        expect(Programs.fetchGGuideProgram).toHaveBeenCalledTimes(1);
        finish(null);
        await flushPromises();
        expect(button.text()).toContain('[GGUIDE_PROGRAM_UNAVAILABLE]');
        expect(button.attributes('disabled')).toBeUndefined();
        const program = {...IProgramDefault, reservation: null};
        const channel = {...ILiveChannelDefault};
        vi.mocked(Programs.fetchGGuideProgram).mockResolvedValueOnce({program, channel});
        await button.trigger('click');
        await flushPromises();
        expect(wrapper.emitted('select-program')?.[0]).toEqual([{asset, channel, program}]);
        wrapper.unmount();
    });

    it('通常タイルを閉じても番組表とバナーを保持し、再表示できる', async () => {
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue(collection);
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        await wrapper.get('[aria-label="H ガイド Landscape を閉じる"]').trigger('click');
        expect(wrapper.find('.gguide--landscape').exists()).toBe(false);
        expect(wrapper.emitted('overlay-visible')?.at(-1)).toEqual([true]);
        await wrapper.get('[aria-label="H ガイド Poster を閉じる"]').trigger('click');
        expect(wrapper.emitted('overlay-visible')?.at(-1)).toEqual([false]);
        await wrapper.get('.gguide-reopen--landscape').trigger('click');
        expect(wrapper.find('.gguide--landscape').exists()).toBe(true);
        wrapper.unmount();
    });

    it('広告本文は番組詳細と同じ URL 解析でリンクを表示し、HTML を実行せず表示中は停止する', async () => {
        vi.useFakeTimers();
        const advertisement = {text: '案内\nhttp://example.com/info\nhttps://example.org/?a=1&b=2\n<script>実行しない</script>\njavascript:alert(1)'};
        const asset = {...collection.assets[0], advertisements: [advertisement]};
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({...collection, assets: [asset, collection.assets[1]]});
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        await wrapper.get('[aria-label="H ガイドの詳細を開く"]').trigger('click');
        // 本文のある画像は、クリック可能を示す常時ヒントを持つ。
        expect(wrapper.find('.gguide__hint').exists()).toBe(true);
        expect(wrapper.get('[role="dialog"]').text()).toContain(advertisement.text);
        expect(wrapper.find('[role="dialog"] script').exists()).toBe(false);
        const links = wrapper.findAll('.gguide-detail__text a');
        expect(links.map(link => link.attributes('href'))).toEqual([
            'http://example.com/info', 'https://example.org/?a=1&b=2',
        ]);
        expect(links.every(link => link.attributes('target') === '_blank')).toBe(true);
        await vi.advanceTimersByTimeAsync(24000);
        expect(wrapper.findComponent(carousel).props('modelValue')).toBe(asset.id);
        await wrapper.get('[aria-label="閉じる"]').trigger('click');
        expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
        await vi.advanceTimersByTimeAsync(12000);
        expect(wrapper.findComponent(carousel).props('modelValue')).toBe(collection.assets[1].id);
        wrapper.unmount();
        vi.useRealTimers();
    });

    it('番組も本文もない画像はクリック対象にせず、空白だけの本文も表示しない', async () => {
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({...collection, assets: [
            ...collection.assets,
            {...collection.assets[0], id: 'c'.repeat(64), advertisements: [{text: ' \n '}]},
        ]});
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        expect(wrapper.findAll('.gguide__image')).toHaveLength(4);
        expect(wrapper.find('.gguide__open').exists()).toBe(false);
        expect(wrapper.find('[role="dialog"]').exists()).toBe(false);
        wrapper.unmount();
    });

    it('表示設定がオフなら通信せず、番組表を作り直さずに表示を切り替える', async () => {
        vi.mocked(Programs.fetchGGuideCollection).mockClear().mockResolvedValue(collection);
        const wrapper = mount(GGuideCarousel, {...options, props: {enabled: false},
            slots: {default: '<div class="timetable-grid">番組表</div>'}});
        const grid = wrapper.get('.timetable-grid').element;
        await flushPromises();
        expect(Programs.fetchGGuideCollection).not.toHaveBeenCalled();
        expect(wrapper.find('section').exists()).toBe(false);
        await wrapper.setProps({enabled: true});
        await flushPromises();
        expect(wrapper.find('section').exists()).toBe(true);
        await wrapper.setProps({enabled: false});
        expect(wrapper.find('section').exists()).toBe(false);
        expect(wrapper.emitted('overlay-visible')?.at(-1)).toEqual([false]);
        expect(wrapper.get('.timetable-grid').element).toBe(grid);
        wrapper.unmount();
    });

    it('現在の画像と前後の画像だけを折り返し込みで先読みする', async () => {
        const assets = ['a', 'b', 'c', 'd', 'e'].map(id => ({...collection.assets[0], id: id.repeat(64)}));
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({status: 'Available', assets});
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        const eagerValues = () => wrapper.findAllComponents(carouselItem).map(item => item.props('eager'));
        expect(eagerValues()).toEqual([true, true, false, false, true]);
        wrapper.findComponent(carousel).vm.$emit('update:modelValue', assets[1].id);
        await flushPromises();
        expect(eagerValues()).toEqual([true, true, true, false, false]);
        wrapper.unmount();
    });

    it('三つの領域を十二秒保持し、四秒ずつずらして一か所ずつ送る', async () => {
        vi.useFakeTimers();
        const assets = [
            ...collection.assets,
            {...collection.assets[2], id: 'e'.repeat(64)},
            {...collection.assets[0], id: 'f'.repeat(64), width: 728, height: 136},
            {...collection.assets[0], id: '1'.repeat(64), width: 728, height: 136},
        ];
        vi.mocked(Programs.fetchGGuideCollection).mockResolvedValue({status: 'Available', assets});
        const wrapper = mount(GGuideCarousel, options);
        await flushPromises();
        const values = () => wrapper.findAllComponents(carousel).map(item => item.props('modelValue'));
        const initial = values();
        await vi.advanceTimersByTimeAsync(11999);
        expect(values()).toEqual(initial);
        await vi.advanceTimersByTimeAsync(1);
        expect(values()).toEqual([assets[5].id, initial[1], initial[2]]);
        await vi.advanceTimersByTimeAsync(4000);
        expect(values()).toEqual([assets[5].id, assets[1].id, initial[2]]);
        await vi.advanceTimersByTimeAsync(4000);
        expect(values()).toEqual([assets[5].id, assets[1].id, assets[3].id]);
        await vi.advanceTimersByTimeAsync(4000);
        expect(values()).toEqual([initial[0], assets[1].id, assets[3].id]);
        wrapper.unmount();
        vi.useRealTimers();
    });
});
