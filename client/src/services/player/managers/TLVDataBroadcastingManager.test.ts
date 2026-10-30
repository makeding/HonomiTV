import { beforeEach, describe, expect, it, vi } from 'vitest';

import TLVDataBroadcastingManager from './TLVDataBroadcastingManager';

import type DPlayer from 'dplayer';

const settings = vi.hoisted(() => ({enable_internet_access_from_data_broadcasting: false}));
vi.mock('dplayer', () => ({default: class {}}));
vi.mock('libaribhtml5', () => ({}));
vi.mock('@/services/APIClient', () => ({default: {}}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => ({})}));
vi.mock('@/stores/PlayerStore', () => ({default: () => ({})}));
vi.mock('@/stores/SettingsStore', () => ({default: () => ({settings})}));
vi.mock('@/utils', () => ({dayjs: vi.fn()}));

describe('URL-only data broadcasting network notice', () => {
    beforeEach(() => {
        settings.enable_internet_access_from_data_broadcasting = false;
        document.body.innerHTML = '<div class="watch-panel__remocon"><div class="remote-control-data-broadcasting"><button data-arib-key-code="20"></button></div></div>';
    });

    it('explains the setting on D before VFS readiness and permits retry after enabling it', async () => {
        const notice = vi.fn();
        const manager = new TLVDataBroadcastingManager({notice} as unknown as DPlayer, 'Live');
        const control = manager as unknown as {
            ready_application_key: string;
            applications: Map<string, unknown>;
            show_requested: boolean;
            initRemoconButtons(): void;
            loadManagedApplication(): Promise<void>;
            vfs: object;
            iframe: HTMLIFrameElement;
        };
        control.ready_application_key = 'online';
        control.applications.set('online', {key: 'online', broadband: true, state: {controlCode: 1}});
        control.initRemoconButtons();
        const load = vi.spyOn(control, 'loadManagedApplication').mockResolvedValue();
        const button = document.querySelector<HTMLButtonElement>('button')!;
        button.click();
        expect(notice).toHaveBeenCalledWith(expect.stringContaining('TLV_DATA_NETWORK_DISABLED'), 8000, undefined, '#FF6F6A');
        expect(notice.mock.calls[0][0]).toContain('データ放送からのインターネットアクセスを有効にする');
        expect(control.show_requested).toBe(false);
        expect(load).not.toHaveBeenCalled();

        settings.enable_internet_access_from_data_broadcasting = true;
        control.vfs = {};
        control.iframe = document.createElement('iframe');
        button.click();
        await Promise.resolve();
        expect(load).toHaveBeenCalledOnce();
        expect(notice).toHaveBeenCalledOnce();
    });

    it('keeps carousel loading available with Internet access disabled', () => {
        const notice = vi.fn();
        const manager = new TLVDataBroadcastingManager({notice} as unknown as DPlayer, 'Live');
        const control = manager as unknown as {
            ready_application_key: string;
            applications: Map<string, unknown>;
            show_requested: boolean;
            initRemoconButtons(): void;
        };
        control.ready_application_key = 'carousel';
        control.applications.set('carousel', {key: 'carousel', broadband: false, state: {controlCode: 2}});
        control.initRemoconButtons();
        document.querySelector<HTMLButtonElement>('button')!.click();
        expect(notice).not.toHaveBeenCalled();
        expect(control.show_requested).toBe(true);
    });
});
