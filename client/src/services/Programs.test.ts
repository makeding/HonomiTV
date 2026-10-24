import { describe, expect, it, vi } from 'vitest';

import APIClient from '@/services/APIClient';
import { ILiveChannelDefault } from '@/services/Channels';
import Programs, { IProgramDefault } from '@/services/Programs';
import { dayjs } from '@/utils';

vi.mock('@/services/APIClient', () => ({default: {get: vi.fn(), showGenericError: vi.fn()}}));

describe('H ガイドの番組詳細取得', () => {
    it('画面のフィルターを付けず関連時刻を取得し、サブチャンネルも四項目で照合する', async () => {
        const program = {...IProgramDefault, reservation: null};
        const channel = {...ILiveChannelDefault};
        const startAt = dayjs(program.start_time).valueOf();
        const association = {network_id: 0, service_id: 0, event_id: 0, start_at: startAt};
        vi.mocked(APIClient.get).mockClear().mockResolvedValueOnce({type: 'success', status: 200, headers: {}, data: {
            channels: [{channel, programs: [{...program, start_time: '2000-01-02T00:00:00+09:00'}],
                subchannels: [{channel, programs: [program]}]}],
            date_range: {earliest: program.start_time, latest: program.end_time}, source_errors: {IPTV: null},
        }});
        expect(await Programs.fetchGGuideProgram([association, {...association, event_id: 1}])).toEqual({program, channel});
        const url = vi.mocked(APIClient.get).mock.calls[0][0];
        const params = new URLSearchParams(url.split('?')[1]);
        expect(dayjs(params.get('start_time')!).valueOf()).toBe(startAt);
        expect(dayjs(params.get('end_time')!).valueOf()).toBe(startAt + 1000);
        expect([...params.keys()].sort()).toEqual(['end_time', 'start_time']);
        expect(APIClient.get).toHaveBeenCalledTimes(1);
    });

    it('同じ EID でも開始時刻が異なる番組を開かず、同時刻の問い合わせを重複しない', async () => {
        const program = {...IProgramDefault, reservation: null};
        const startAt = dayjs(program.start_time).valueOf() - 1000;
        const association = {network_id: 0, service_id: 0, event_id: 0, start_at: startAt};
        vi.mocked(APIClient.get).mockClear().mockResolvedValueOnce({type: 'success', status: 200, headers: {}, data: {
            channels: [{channel: ILiveChannelDefault, programs: [program], subchannels: null}],
            date_range: {earliest: program.start_time, latest: program.end_time}, source_errors: {IPTV: null},
        }});
        expect(await Programs.fetchGGuideProgram([association, {...association, event_id: 1}])).toBeNull();
        expect(APIClient.get).toHaveBeenCalledTimes(1);
    });
});
