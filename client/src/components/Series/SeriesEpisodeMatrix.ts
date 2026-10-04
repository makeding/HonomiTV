interface IEpisodeSlot {
    key: string;
    label: string;
}

// 全局で同じ録画が連続する列だけを束ねる。別局の単話録画があれば比較用の列を維持する。
// 自然話数の行列は統計用に残し、表示行列だけで一挙放送の同一ファイルを重複表示しない。
export const collapseBatchEpisodeColumns = <T extends {id: number}, R extends {programs: Array<T | null>}>(
    matrix: {slots: IEpisodeSlot[]; rows: R[]},
): {slots: IEpisodeSlot[]; rows: R[]} => {
    const groups: Array<{start: number; end: number}> = [];
    for (let index = 0; index < matrix.slots.length; index++) {
        const previous = groups[groups.length - 1];
        const number = /^episode:\d+$/.test(matrix.slots[index].key)
            ? Number(matrix.slots[index].key.slice('episode:'.length)) : null;
        const previousNumber = previous && /^episode:\d+$/.test(matrix.slots[previous.end].key)
            ? Number(matrix.slots[previous.end].key.slice('episode:'.length)) : null;
        // 空の欠番列や特別編を束ねず、全局の視聴先が変わらない連続自然話数だけをまとめる。
        if (previous && number !== null && previousNumber !== null && number === previousNumber + 1 &&
            matrix.rows.some(row => row.programs[index] !== null) &&
            matrix.rows.every(row => row.programs[index]?.id === row.programs[previous.end]?.id)) {
            previous.end = index;
        } else {
            groups.push({start: index, end: index});
        }
    }
    return {
        slots: groups.map(({start, end}) => start === end ? matrix.slots[start] : {
            key: `${matrix.slots[start].key}-${matrix.slots[end].key}`,
            label: `第${matrix.slots[start].key.slice('episode:'.length)}–${matrix.slots[end].key.slice('episode:'.length)}話`,
        }),
        rows: matrix.rows.map(row => ({...row, programs: groups.map(group => row.programs[group.start])})),
    };
};
