import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ILiveChannelDefault } from '@/services/Channels';
import Programs, { ITimeTable } from '@/services/Programs';
import useSettingsStore from '@/stores/SettingsStore';
import useTimeTableStore from '@/stores/TimeTableStore';

const response: ITimeTable = {
    channels: ['GR', 'BS', 'CS', 'BS4K', 'IPTV'].map(type => ({
        channel: {...ILiveChannelDefault, id: type, type: type as typeof ILiveChannelDefault.type},
        programs: [],
        subchannels: null,
    })),
    date_range: {earliest: '2026-09-22T04:00:00+09:00', latest: '2026-09-23T04:00:00+09:00'},
    source_errors: {IPTV: null},
};

describe('TimeTableStore channel selection', () => {
    beforeEach(() => setActivePinia(createPinia()));

    it('filters a mixed response for both the header and program grid', async () => {
        vi.spyOn(Programs, 'fetchTimeTable').mockResolvedValue(response);
        const store = useTimeTableStore();
        await store.changeChannelType('ネット');
        expect(store.channels_data.map(row => row.channel.id)).toEqual(['IPTV']);
        await store.changeChannelType('BS4K');
        expect(store.channels_data.map(row => row.channel.id)).toEqual(['BS4K']);
    });

    it('keeps the selected filter during pending and failed requests', async () => {
        const fetch = vi.spyOn(Programs, 'fetchTimeTable').mockResolvedValue(response);
        const store = useTimeTableStore();
        await store.changeChannelType('地デジ');
        let resolve!: (value: null) => void;
        fetch.mockReturnValueOnce(new Promise(done => { resolve = done; }));
        const pending = store.changeChannelType('ネット');
        expect(store.channels_data.map(row => row.channel.id)).toEqual(['IPTV']);
        resolve(null);
        await pending;
        expect(store.channels_data.map(row => row.channel.id)).toEqual(['IPTV']);
    });

    it('limits mixed pins to selected IDs in pin order and shows empty pins honestly', async () => {
        vi.spyOn(Programs, 'fetchTimeTable').mockResolvedValue(response);
        const store = useTimeTableStore();
        const settings = useSettingsStore();
        settings.settings.pinned_channel_ids = ['IPTV', 'GR'];
        await store.changeChannelType('ピン留め');
        expect(store.channels_data.map(row => row.channel.id)).toEqual(['IPTV', 'GR']);
        settings.settings.pinned_channel_ids = [];
        expect(store.channels_data).toEqual([]);
        store.reset();
        expect(store.channels_data).toEqual([]);
    });
});
