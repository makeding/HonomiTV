import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    guard: null as any,
    settings: {selected_remote_device_id: 'tv' as string | null},
    channels: {} as Record<string, any[]>,
    fetch: vi.fn(), send: vi.fn(), success: vi.fn(), warning: vi.fn(),
}));
vi.mock('vue-router', () => ({
    createWebHistory: vi.fn(),
    createRouter: () => ({beforeResolve: (guard: any) => { mocks.guard = guard; }}),
}));
vi.mock('@/message', () => ({default: {success: mocks.success, warning: mocks.warning}}));
vi.mock('@/services/Channels', () => ({default: {fetch: mocks.fetch}}));
vi.mock('@/services/RemoteControl', () => ({default: {sendOpenCommand: mocks.send}}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => ({channels_list: mocks.channels})}));
vi.mock('@/stores/SettingsStore', () => ({default: () => ({settings: mocks.settings})}));
vi.mock('@/utils', () => ({default: {}}));
import '@/router/index';

const channel = {display_channel_id: 'jellyfin-channel', capabilities: {remote_playback: true}};
const destination = {name: 'TV Watch', path: '/tv/watch/jellyfin-channel', params: {display_channel_id: 'jellyfin-channel'}};

describe('remote playback navigation', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.settings.selected_remote_device_id = 'tv';
        mocks.channels = {IPTV: [channel]};
        mocks.fetch.mockResolvedValue(channel);
        mocks.send.mockResolvedValue(true);
    });

    it.each(['/tv/', '/timetable/'])('keeps %s in place after sending Jellyfin unchanged', async path => {
        const next = vi.fn();
        await mocks.guard(destination, {path}, next);
        expect(mocks.send).toHaveBeenCalledWith('tv', {type: 'OpenLive', display_channel_id: channel.display_channel_id});
        expect(next.mock.calls).toEqual([[false]]);
        expect(mocks.success).toHaveBeenCalledWith('テレビへ再生を送信しました。');
        expect(mocks.fetch).not.toHaveBeenCalled();
    });

    it('fetches an uncached channel before forwarding', async () => {
        mocks.channels = {};
        await mocks.guard(destination, {path: '/timetable/'}, vi.fn());
        expect(mocks.fetch).toHaveBeenCalledWith(channel.display_channel_id);
        expect(mocks.send).toHaveBeenCalledTimes(1);
    });

    it('does not start local playback when the television is offline', async () => {
        mocks.send.mockResolvedValue(false);
        const next = vi.fn();
        await mocks.guard(destination, {path: '/tv/'}, next);
        expect(next.mock.calls).toEqual([[false]]);
        expect(mocks.success).not.toHaveBeenCalled();
    });

    it.each([null, {...channel, capabilities: {remote_playback: false}}])('blocks missing or unsupported channels', async value => {
        mocks.channels = {};
        mocks.fetch.mockResolvedValue(value);
        const next = vi.fn();
        await mocks.guard(destination, {path: '/tv/'}, next);
        expect(next.mock.calls).toEqual([[false]]);
        expect(mocks.send).not.toHaveBeenCalled();
        expect(mocks.warning).toHaveBeenCalledTimes(1);
    });

    it('keeps local navigation when no television is selected', async () => {
        mocks.settings.selected_remote_device_id = null;
        const next = vi.fn();
        await mocks.guard(destination, {path: '/tv/'}, next);
        expect(next.mock.calls).toEqual([[]]);
        expect(mocks.send).not.toHaveBeenCalled();
    });
});
