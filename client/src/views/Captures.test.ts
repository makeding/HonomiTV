import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';

import Captures from '@/views/Captures.vue';

const mocks = vi.hoisted(() => ({
    fetchCaptures: vi.fn(), fetchFolders: vi.fn(), createFolder: vi.fn(), deleteCapture: vi.fn(),
    fetchUser: vi.fn(), replace: vi.fn(), route: null as any,
}));
vi.mock('@/components/HeaderBar.vue', () => ({default: {template: '<header />'}}));
vi.mock('@/components/SPHeaderBar.vue', () => ({default: {template: '<header />'}}));
vi.mock('@/components/Navigation.vue', () => ({default: {template: '<nav />'}}));
vi.mock('@/components/Breadcrumbs.vue', () => ({default: {template: '<div />'}}));
vi.mock('@/message', () => ({default: {success: vi.fn()}}));
vi.mock('@/stores/UserStore', () => ({default: () => ({
    is_logged_in: true, user: {is_admin: true}, fetchUser: mocks.fetchUser,
})}));
vi.mock('@/services/Captures', () => ({default: {
    ...mocks, getCaptureImageURL: (id: string) => `/api/captures/${encodeURIComponent(id)}`,
}}));
vi.mock('vue-router', () => ({useRoute: () => mocks.route, useRouter: () => ({replace: mocks.replace, push: vi.fn()})}));

const capture = (id: string) => ({
    id, filename: 'same.jpg', image_width: 1920, image_height: 1080, file_size: 1234,
    file_modified_at: '2026-10-23T00:00:00Z', mime_type: 'image/jpeg', capture_metadata: null,
});
const wrappers: ReturnType<typeof mount>[] = [];
const render = () => {
    const wrapper = mount(Captures, {global: {directives: {ripple: () => {}}, stubs: {
        VSelect: true, VProgressLinear: true, VCheckboxBtn: true, VSpacer: true, VPagination: true,
        VCardTitle: {template: '<h3><slot /></h3>'}, VListItemTitle: true, VListItemSubtitle: true,
        VListItem: true, VList: true, VSlideYReverseTransition: {template: '<div><slot /></div>'},
        VChip: {template: '<button><slot /></button>'}, VAlert: {template: '<div><slot /></div>'},
        Icon: true, RouterLink: true, CaptureImage: true,
        VBtn: {template: '<button><slot /></button>'},
        VDialog: {props: ['modelValue'], template: '<section v-if="modelValue"><slot /></section>'},
        VMenu: {template: '<div />'}, VCard: {template: '<div><slot /></div>'},
        VCardText: {template: '<div><slot /></div>'}, VCardActions: {template: '<div><slot /></div>'},
        VTextField: {props: ['modelValue'], emits: ['update:modelValue'],
            template: '<input :value="modelValue" @input="$emit(\'update:modelValue\', $event.target.value)">'},
    }}});
    wrappers.push(wrapper);
    return wrapper;
};

beforeEach(() => {
    vi.clearAllMocks();
    mocks.route = reactive({path: '/captures/', fullPath: '/captures/', query: {}, params: {}});
    mocks.fetchCaptures.mockResolvedValue({total: 2, captures: [capture('root-a:same.jpg'), capture('root-b:same.jpg')]});
    mocks.fetchFolders.mockResolvedValue({total: 0, folders: []});
    mocks.fetchUser.mockResolvedValue(undefined);
});
afterEach(() => { wrappers.splice(0).forEach(wrapper => wrapper.unmount()); });

describe('キャプチャ管理', () => {
    it('同名画像を別々に表示し、削除は確認後に選択した ID へ一度だけ送る', async () => {
        const wrapper = render();
        await flushPromises();
        expect(wrapper.findAll('.capture-card')).toHaveLength(2);
        await wrapper.findAll('.capture-card')[1].trigger('click');
        await wrapper.find('.captures-lightbox__delete').trigger('click');
        expect(mocks.deleteCapture).not.toHaveBeenCalled();
        let finish!: (value: boolean) => void;
        mocks.deleteCapture.mockReturnValue(new Promise(resolve => { finish = resolve; }));
        const confirm = wrapper.findAll('button').filter(button => button.text() === '削除').at(-1)!;
        await confirm.trigger('click');
        await confirm.trigger('click');
        expect(mocks.deleteCapture).toHaveBeenCalledTimes(1);
        expect(mocks.deleteCapture).toHaveBeenCalledWith('root-b:same.jpg');
        finish(true);
        await flushPromises();
        expect(wrapper.find('.captures-lightbox').exists()).toBe(false);
    });

    it('取得失敗は空状態と区別し、その場で再試行できる', async () => {
        mocks.fetchCaptures.mockRejectedValueOnce(new Error('接続できません (NetworkError)'));
        const wrapper = render();
        await flushPromises();
        expect(wrapper.find('.captures__status').text()).toContain('NetworkError');
        expect(wrapper.find('.captures__empty--show').exists()).toBe(false);
        await wrapper.find('.captures__status button').trigger('click');
        await flushPromises();
        expect(wrapper.findAll('.capture-card')).toHaveLength(2);
        expect(wrapper.find('.captures__status').text()).not.toContain('NetworkError');
    });

    it('古い検索応答が新しい一覧を上書きしない', async () => {
        let finish!: (value: unknown) => void;
        mocks.fetchCaptures.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
        const wrapper = render();
        await flushPromises();
        Object.assign(mocks.route, {path: '/captures/search', fullPath: '/captures/search?query=new', query: {query: 'new'}});
        await flushPromises();
        finish({total: 1, captures: [capture('old:stale.jpg')]});
        await flushPromises();
        expect(wrapper.findAll('.capture-card')).toHaveLength(2);
        expect(mocks.fetchCaptures).toHaveBeenLastCalledWith('desc', 1, 'new');
    });

    it('検索変更後の失敗に前の検索結果を表示しない', async () => {
        const wrapper = render();
        await flushPromises();
        mocks.fetchCaptures.mockRejectedValueOnce(new Error('保存先を読み取れません (503)'));
        Object.assign(mocks.route, {path: '/captures/search', fullPath: '/captures/search?query=error', query: {query: 'error'}});
        await flushPromises();
        expect(wrapper.findAll('.capture-card')).toHaveLength(0);
        expect(wrapper.find('.captures__title-count').text()).toBe('—件');
        expect(wrapper.find('.captures__status').text()).toContain('503');
        expect(wrapper.find('.captures__empty--show').exists()).toBe(false);
    });

    it('フォルダ作成が失敗しても入力と理由を保持する', async () => {
        mocks.createFolder.mockRejectedValue(new Error('保存できません (503)'));
        const wrapper = render();
        await flushPromises();
        await wrapper.find('.captures__chip--add').trigger('click');
        await wrapper.find('input').setValue('お気に入り');
        await wrapper.findAll('button').find(button => button.text() === '作成')!.trigger('click');
        await flushPromises();
        expect(wrapper.find('input').element.value).toBe('お気に入り');
        expect(wrapper.text()).toContain('保存できません (503)');
    });
});
