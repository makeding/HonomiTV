import { mount } from '@vue/test-utils';
import { AxiosError } from 'axios';
import { createPinia, setActivePinia } from 'pinia';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { nextTick } from 'vue';

import WatchNavigation from '@/components/Watch/Navigation.vue';
import APIClient from '@/services/APIClient';
import CloudflareZerotrust from '@/services/CloudflareZerotrust';
import { getNormalizedLocalClientSettings } from '@/stores/SettingsStore';
import useUserStore from '@/stores/UserStore';


vi.mock('@/services/APIClient', () => ({default: {get: vi.fn()}}));
vi.mock('@/components/OfflineDownloadBadge.vue', () => ({default: {template: '<span></span>'}}));
vi.mock('@/stores/PlayerStore', async () => {
    const { defineStore } = await import('pinia');
    return {default: defineStore('player', {state: () => ({event_emitter: {emit: vi.fn()}})})};
});

beforeEach(() => {
    setActivePinia(createPinia());
    vi.stubGlobal('location', {hostname: 'tv.example.test'});
    vi.mocked(APIClient.get).mockReset();
});
afterEach(() => vi.unstubAllGlobals());

describe('Cloudflare Access identity detection', () => {
    it.each([
        '<!DOCTYPE html><html></html>',
        '<html>Not found</html>',
        null,
        '',
        [],
        {},
        {detail: 'Not Found'},
        {err: 'unrelated error'},
        {account_id: 'account'},
        {account_id: '', user_uuid: 'user'},
    ])('does not treat an ordinary response as Access: %j', async (data) => {
        vi.mocked(APIClient.get).mockResolvedValue({type: 'success', status: 200, headers: {}, data});
        const store = useUserStore();

        await store.fetchCloudflareIdentity();

        expect(store.cloudflare_identity).toBeNull();
        expect(store.is_cloudflare_logged_in).toBe(false);
    });

    it('does not treat an HTTP or network failure as Access', async () => {
        for (const status of [404, 502, NaN]) {
            vi.mocked(APIClient.get).mockResolvedValue({
                type: 'error', status, headers: {}, data: {detail: 'Request failed'}, error: new AxiosError(),
            });
            expect(await CloudflareZerotrust.fetchCloudflareZerotrustIdentity()).toBeNull();
        }
    });

    it('preserves the explicit unauthenticated Access response for reauthentication', async () => {
        vi.mocked(APIClient.get).mockResolvedValue({
            type: 'success', status: 200, headers: {}, data: {err: 'no app token set'},
        });
        const store = useUserStore();

        expect(await store.fetchCloudflareIdentity()).toBe(false);
        expect(store.is_cloudflare_logged_in).toBe(false);
    });

    it('skips Access discovery on the local development domain', async () => {
        vi.stubGlobal('location', {hostname: 'my.local.konomi.tv'});

        expect(await CloudflareZerotrust.fetchCloudflareZerotrustIdentity()).toBeNull();
        expect(APIClient.get).not.toHaveBeenCalled();
    });

    it('removes the obsolete persisted detection flag during settings normalization', () => {
        const settings = getNormalizedLocalClientSettings({is_cloudflare_zerotrust: true});

        expect(settings).not.toHaveProperty('is_cloudflare_zerotrust');
    });

    it('shows the watch logout link only after verifying the current identity and removes it on failed discovery', async () => {
        const wrapper = mount(WatchNavigation, {
            global: {
                plugins: [createPinia()],
                stubs: {RouterLink: {template: '<a><slot /></a>'}, Icon: true, VSpacer: true},
                mocks: {$route: {path: '/tv/watch/gr011'}},
                directives: {ripple: {}, 'ftooltip': {}},
            },
        });
        const store = useUserStore();
        const logout = () => wrapper.find('a[href="/cdn-cgi/access/logout"]');
        expect(logout().exists()).toBe(false);

        const identity = {account_id: 'account', user_uuid: 'user'};
        vi.mocked(APIClient.get).mockResolvedValue({type: 'success', status: 200, headers: {}, data: identity});
        await store.fetchCloudflareIdentity();
        await nextTick();
        expect(logout().exists()).toBe(true);

        vi.mocked(APIClient.get).mockResolvedValue({type: 'success', status: 200, headers: {}, data: {detail: 'Not Found'}});
        await store.fetchCloudflareIdentity();
        await nextTick();
        expect(logout().exists()).toBe(false);
        wrapper.unmount();
    });
});
