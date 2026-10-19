import { describe, expect, it } from 'vitest';

import LiveHistoryClock from '@/services/player/LiveHistoryClock';


describe('ライブ映像の放送時刻', () => {
    it('受信バッファの遅延を引き、メディア時刻の進行だけを加算する', () => {
        const clock = new LiveHistoryClock();
        expect(clock.observe(100, 1000, 5, true)).toBeNull();
        expect(clock.observe(110, 1010, 5, true)).toBe(1005);
        // データ供給も止まった後の再開でも、停止していた壁時計の60秒を加算しない。
        expect(clock.observe(110, 1070, 5, false)).toBeNull();
        expect(clock.observe(111, 1071, 4, true)).toBe(1006);
    });

    it('再生停止・バッファリング・同じ位置の反復は進度として返さない', () => {
        const clock = new LiveHistoryClock();
        clock.observe(0, 1000, 3, false);
        expect(clock.observe(0, 1010, 3, true)).toBeNull();
        expect(clock.observe(1, 1011, 3, false)).toBeNull();
        expect(clock.observe(2, 1012, 3, true)).toBe(1009);
        expect(clock.observe(NaN, 1013, 3, true)).toBeNull();
    });

    it('画質切替などでメディア時刻がリセットされたら対応を取り直す', () => {
        const clock = new LiveHistoryClock();
        clock.observe(100, 1000, 5, true);
        clock.observe(110, 1010, 5, true);
        expect(clock.observe(0, 1020, 3, true)).toBeNull();
        expect(clock.observe(1, 1021, 3, true)).toBe(1018);
    });
});
