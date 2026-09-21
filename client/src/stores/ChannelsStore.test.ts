import { beforeEach, describe, expect, it, vi } from 'vitest';
import { createPinia, setActivePinia } from 'pinia';

import Channels, { ILiveChannel, ILiveChannelsResponse } from '@/services/Channels';
import useChannelsStore from '@/stores/ChannelsStore';
import useSettingsStore from '@/stores/SettingsStore';

const createResponse = (iptv: ILiveChannel[], error: string | null): ILiveChannelsResponse => ({
    GR: [], BS: [], CS: [], CATV: [], SKY: [], BS4K: [], IPTV: iptv,
    source_errors: {IPTV: error},
});

const jellyfinChannel: ILiveChannel = {
    id: 'jellyfin-1', display_channel_id: 'jellyfin-1', network_id: null, service_id: null,
    transport_stream_id: null, remocon_id: null, channel_number: '---', type: 'IPTV', name: 'Test IPTV',
    terrestrial_regions: null, jikkyo_force: null, is_subchannel: false, is_radiochannel: false, is_watchable: true,
    source: 'Jellyfin', capabilities: {live_stream: true, live_stream_session: true, data_broadcasting: false, recording: false, remote_playback: false},
    is_display: true, viewer_count: null, program_present: null, program_following: null,
};

describe('ChannelsStore network source recovery', () => {
    beforeEach(() => setActivePinia(createPinia()));

    it('keeps cached network cards on source failure and keeps their pins when disabled or empty', async () => {
        const fetch = vi.spyOn(Channels, 'fetchAllChannels')
            .mockResolvedValueOnce(createResponse([jellyfinChannel], null))
            .mockResolvedValueOnce(createResponse([], 'Jellyfin is unavailable'))
            .mockResolvedValueOnce(createResponse([], null));
        const store = useChannelsStore();
        const settings = useSettingsStore();

        await store.update(true);
        settings.settings.pinned_channel_ids = ['jellyfin-1'];
        await store.update(true);
        expect(store.channels_list.IPTV.map((channel) => channel.id)).toEqual(['jellyfin-1']);
        expect(store.source_errors.IPTV).toBe('Jellyfin is unavailable');

        await store.update(true);
        expect(settings.settings.pinned_channel_ids).toEqual(['jellyfin-1']);
        expect(fetch).toHaveBeenCalledTimes(3);
    });
});
