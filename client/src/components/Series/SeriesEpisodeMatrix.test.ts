import { describe, expect, it } from 'vitest';

import { collapseBatchEpisodeColumns } from '@/components/Series/SeriesEpisodeMatrix';

const slots = Array.from({length: 12}, (_, index) => ({key: `episode:${index + 1}`, label: `第${index + 1}話`}));

describe('一挙放送の表示列', () => {
    it('12話入りの一つの録画は一枚へまとめ、元の自然話数は残す', () => {
        const matrix = {slots, rows: [{programs: slots.map(() => ({id: 1}))}]};
        const display = collapseBatchEpisodeColumns(matrix);
        expect(display.slots.map(slot => slot.label)).toEqual(['第1–12話']);
        expect(display.rows[0].programs).toEqual([{id: 1}]);
        expect(matrix.slots).toHaveLength(12);
    });

    it('別局に単話録画があれば、比較する列をまとめない', () => {
        const display = collapseBatchEpisodeColumns({slots, rows: [
            {programs: slots.map(() => ({id: 1}))},
            {programs: slots.map((_, index) => ({id: index + 2}))},
        ]});
        expect(display.slots).toHaveLength(12);
    });

    it('欠番をまとめず、異なる録画・非連続話数を跨がない', () => {
        const display = collapseBatchEpisodeColumns({slots: [slots[0], slots[1], slots[2], slots[4]], rows: [
            {programs: [{id: 1}, null, {id: 2}, {id: 2}]},
        ]});
        expect(display.slots.map(slot => slot.label)).toEqual(['第1話', '第2話', '第3話', '第5話']);
    });
});
