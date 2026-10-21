import { afterEach, describe, expect, it, vi } from 'vitest';

import { PlayerUtils } from '@/utils/PlayerUtils';


describe('ネットワーク回線に応じた画質の自動選択', () => {

    afterEach(() => vi.unstubAllGlobals());

    it.each([
        {type: 'wifi', expected: 'Wi-Fi'},
        {type: 'ethernet', expected: 'Wi-Fi'},
        {type: 'cellular', expected: 'Cellular'},
        {type: 'unknown', expected: null},
    ])('$type を $expected として扱う', ({type, expected}) => {
        vi.stubGlobal('navigator', {connection: {type}});

        expect(PlayerUtils.getNetworkCircuitType()).toBe(expected);
    });

    it.each(['slow-2g', '2g', '3g', '4g'])('実効速度 %s から不明な回線をモバイル回線と推測しない', (effectiveType) => {
        vi.stubGlobal('navigator', {connection: {type: 'unknown', effectiveType}});

        expect(PlayerUtils.getNetworkCircuitType()).toBeNull();
    });

    it('回線種別を公開しないブラウザでは不明のまま扱う', () => {
        vi.stubGlobal('navigator', {connection: {effectiveType: '4g'}});
        vi.spyOn(console, 'warn').mockImplementation(() => {});

        expect(PlayerUtils.getNetworkCircuitType()).toBeNull();
    });

    it('Network Information API がなくてもモバイル回線扱いにしない', () => {
        vi.stubGlobal('navigator', {});
        vi.spyOn(console, 'warn').mockImplementation(() => {});

        expect(PlayerUtils.getNetworkCircuitType()).toBeNull();
    });
});
