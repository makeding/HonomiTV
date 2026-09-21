import { describe, expect, it } from 'vitest';

import { ChannelUtils } from '@/utils/ChannelUtils';

describe('ChannelUtils.getChannelType', () => {
    it('recognizes the stable Jellyfin display ID without a broadcast-number pattern', () => {
        expect(ChannelUtils.getChannelType('jellyfin-9f4e6c')).toBe('IPTV');
    });

    it('keeps broadcast display IDs classified by their existing prefixes', () => {
        expect(ChannelUtils.getChannelType('gr011')).toBe('GR');
        expect(ChannelUtils.getChannelType('bs4k101')).toBe('BS4K');
    });
});
