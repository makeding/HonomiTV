import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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
        let iptv_response: ILiveChannelsResponse = createResponse([jellyfinChannel], null);
        const fetch = vi.spyOn(Channels, 'fetchAllChannels').mockImplementation(async (source) => {
            return source === 'Jellyfin' ? iptv_response : createResponse([], null);
        });
        const store = useChannelsStore();
        const settings = useSettingsStore();

        await store.update(true);
        settings.settings.pinned_channel_ids = ['jellyfin-1'];
        iptv_response = createResponse([], 'Jellyfin is unavailable');
        await store.update(true);
        expect(store.channels_list.IPTV.map((channel) => channel.id)).toEqual(['jellyfin-1']);
        expect(store.source_errors.IPTV).toBe('Jellyfin is unavailable');

        iptv_response = createResponse([], null);
        await store.update(true);
        expect(settings.settings.pinned_channel_ids).toEqual(['jellyfin-1']);
        expect(fetch).toHaveBeenCalledTimes(6);
    });

    it('never treats response metadata as a channel list in getters', () => {
        const store = useChannelsStore();
        store.is_channels_list_initial_updated = true;
        store.channels_list = {
            ...createResponse([jellyfinChannel], null),
            // Simulate a malformed assignment from an API response to prove getters only enumerate channel keys.
            source_errors: {IPTV: 'metadata'},
        } as unknown as typeof store.channels_list;
        store.display_channel_id = 'jellyfin-1';

        expect(store.channels_list_with_pinned.get('ネット')?.[0].id).toBe('jellyfin-1');
        expect(store.channel.current.id).toBe('jellyfin-1');
    });

    it('renders broadcast channels while the network request remains pending', async () => {
        let resolveIPTV!: (response: ILiveChannelsResponse) => void;
        const pending = new Promise<ILiveChannelsResponse>(resolve => { resolveIPTV = resolve; });
        const broadcast = {...jellyfinChannel, id: 'broadcast', display_channel_id: 'gr011', type: 'GR' as const};
        vi.spyOn(Channels, 'fetchAllChannels').mockImplementation(async source =>
            source === 'Jellyfin' ? pending : {...createResponse([], null), GR: [broadcast]});
        const store = useChannelsStore();
        try {
            await store.update(true);
            expect(store.is_iptv_loading).toBe(true);
            expect(store.channels_list_with_pinned.get('地デジ')?.[0].id).toBe('broadcast');
        } finally {
            resolveIPTV(createResponse([], null));
            await store.updateIPTV();
        }
    });

    it('renders network channels while the broadcast request remains pending', async () => {
        let resolveBroadcast!: (response: ILiveChannelsResponse) => void;
        const pending = new Promise<ILiveChannelsResponse>(resolve => { resolveBroadcast = resolve; });
        vi.spyOn(Channels, 'fetchAllChannels').mockImplementation(async source =>
            source === 'Broadcast' ? pending : createResponse([jellyfinChannel], null));
        const store = useChannelsStore();
        const update = store.update(true);
        try {
            await store.updateIPTV();
            expect(store.is_channels_list_initial_updated).toBe(false);
            expect(store.channels_list_with_pinned.get('ネット')?.[0].id).toBe('jellyfin-1');
            store.display_channel_id = 'jellyfin-1';
            expect(store.channel.current.id).toBe('jellyfin-1');
        } finally {
            resolveBroadcast(createResponse([], null));
            await update;
        }
    });
});
