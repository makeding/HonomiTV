import { flushPromises, mount } from '@vue/test-utils';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';

import Pair from '@/views/Pair.vue';

const mocks = vi.hoisted(() => ({fetchUser: vi.fn(), logout: vi.fn(), approve: vi.fn(), store: null as any, token: null as string | null}));
vi.mock('@/stores/UserStore', () => ({default: () => mocks.store}));
vi.mock('@/utils', () => ({default: {getAccessToken: () => mocks.token}}));
vi.mock('@/services/DeviceAuth', () => ({default: {approve: mocks.approve}}));
vi.mock('vue-router', () => ({useRoute: () => ({query: {code: 'abcd-efgh'}, fullPath: '/pair/?code=abcd-efgh'})}));

const options = {global: {stubs: {
    'v-btn': {props: ['disabled', 'to', 'loading'], template: '<button :disabled="disabled" :data-to="to"><slot /></button>'},
    'v-text-field': {props: ['modelValue', 'disabled'], emits: ['update:modelValue'],
        template: '<input :value="modelValue" :disabled="disabled" @input="$emit(\'update:modelValue\', $event.target.value)">'},
    'v-alert': {template: '<div role="alert"><slot /></div>'},
}}};

beforeEach(() => {
    vi.resetAllMocks();
    mocks.token = null;
    mocks.store = reactive({is_logged_in: true, user: {name: 'huggy', id: 42}, user_icon_url: 'blob:avatar',
        fetchUser: mocks.fetchUser, logout: mocks.logout});
    mocks.fetchUser.mockResolvedValue(mocks.store.user);
    mocks.logout.mockImplementation(() => {mocks.store.is_logged_in = false; mocks.store.user = null;});
});

describe('テレビ連携画面', () => {
    it('プロフィールの取得失敗を未ログインと取り違えず再試行を表示する', async () => {
        mocks.token = 'existing-token';
        mocks.store.is_logged_in = false;
        mocks.store.user = null;
        mocks.fetchUser.mockResolvedValue(null);
        const wrapper = mount(Pair, options);
        await flushPromises();
        expect(wrapper.text()).toContain('アカウントを読み込めませんでした');
        expect(wrapper.text()).not.toContain('ログインしていません');
        expect(wrapper.get('.pair-submit').attributes('disabled')).toBeDefined();
        wrapper.unmount();
    });

    it('既存プロフィールを表示し、ログアウト後もコードとログインの戻り先を保持する', async () => {
        const wrapper = mount(Pair, options);
        expect(wrapper.text()).toContain('アカウントを読み込んでいます');
        await flushPromises();
        expect(wrapper.text()).toContain('huggy');
        expect(wrapper.text()).toContain('User ID: 42');
        await wrapper.get('.pair-signout').trigger('click');
        expect(mocks.logout).toHaveBeenCalledOnce();
        expect(wrapper.get('input').element.value).toBe('ABCD EFGH');
        expect(wrapper.get('.pair-submit').attributes('data-to')).toContain(encodeURIComponent('/pair/?code=abcd-efgh'));
        expect(wrapper.text()).toContain('ログインしていません');
        wrapper.unmount();
    });

    it('送信中の連打と成功後の再送信を防止する', async () => {
        let finish!: (value: boolean) => void;
        mocks.approve.mockReturnValue(new Promise<boolean>(resolve => {finish = resolve;}));
        const wrapper = mount(Pair, options);
        await flushPromises();
        await wrapper.get('form').trigger('submit');
        await wrapper.get('form').trigger('submit');
        expect(mocks.approve).toHaveBeenCalledOnce();
        expect(mocks.approve).toHaveBeenCalledWith('ABCDEFGH');
        expect(wrapper.get('input').attributes('disabled')).toBeDefined();
        finish(true);
        await flushPromises();
        expect(wrapper.text()).toContain('連携しました。テレビに戻ってください。');
        expect(wrapper.get('.pair-submit').attributes('disabled')).toBeDefined();
        await wrapper.get('form').trigger('submit');
        expect(mocks.approve).toHaveBeenCalledOnce();
        wrapper.unmount();
    });

    it('失敗時はコードを維持し、隣接した案内から再試行できる', async () => {
        mocks.approve.mockResolvedValue(false);
        const wrapper = mount(Pair, options);
        await flushPromises();
        await wrapper.get('form').trigger('submit');
        await flushPromises();
        expect(wrapper.get('[role="alert"]').text()).toContain('テレビで連携画面を開き直してください');
        expect(wrapper.get('input').element.value).toBe('ABCD EFGH');
        expect(wrapper.get('.pair-submit').attributes('disabled')).toBeUndefined();
        wrapper.unmount();
    });
});
