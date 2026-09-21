import { beforeEach, describe, expect, it, vi } from 'vitest';

import APIClient from '@/services/APIClient';
import Channels from '@/services/Channels';

vi.mock('@/services/APIClient', () => ({default: {get: vi.fn(), showGenericError: vi.fn()}}));

describe('channel list wire contract', () => {
    beforeEach(() => vi.clearAllMocks());

    it('reads source failures from headers without putting metadata in the wire channel groups', async () => {
        const body = {GR: [], BS: [], CS: [], CATV: [], SKY: [], BS4K: [], IPTV: []};
        vi.mocked(APIClient.get).mockResolvedValue({type: 'success', status: 200, data: body,
            headers: {'x-channel-source-errors': JSON.stringify({IPTV: '認証に失敗しました。'})}});

        const result = await Channels.fetchAllChannels('Jellyfin');
        expect(result?.source_errors.IPTV).toBe('認証に失敗しました。');
        expect(Object.values(body).every(Array.isArray)).toBe(true);
        expect(APIClient.get).toHaveBeenCalledWith('/channels?source=Jellyfin');
    });

    it('accepts a channel response with no source failure header', async () => {
        vi.mocked(APIClient.get).mockResolvedValue({type: 'success', status: 200,
            data: {GR: [], BS: [], CS: [], CATV: [], SKY: [], BS4K: [], IPTV: []}, headers: {}});
        expect((await Channels.fetchAllChannels())?.source_errors.IPTV).toBeNull();
    });
});
