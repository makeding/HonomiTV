import { beforeEach, describe, expect, it, vi } from 'vitest';

import APIClient from '@/services/APIClient';
import LivePlaybackSession, { type ILivePlaybackSession } from '@/services/player/LivePlaybackSession';


vi.mock('@/services/APIClient', () => ({default: {post: vi.fn(), delete: vi.fn()}}));

const session = (id: string): ILivePlaybackSession => ({
    id, stream_url: `/api/streams/live/sessions/${id}/playlist`, stream_type: 'hls',
});
const success = (id: string) => ({type: 'success' as const, status: 200, headers: {}, data: session(id)});

describe('共通ライブセッションの所有権', () => {
    beforeEach(() => {
        vi.mocked(APIClient.post).mockReset();
        vi.mocked(APIClient.delete).mockReset();
        vi.mocked(APIClient.delete).mockResolvedValue({type: 'success', status: 204, headers: {}, data: null});
    });

    it('終了済みの画面へ届いた開始応答を閉じ、再生には渡さない', async () => {
        let respond!: (response: ReturnType<typeof success>) => void;
        vi.mocked(APIClient.post).mockImplementation(() => new Promise(resolve => {respond = resolve;}));
        const owner = new LivePlaybackSession();
        const pending = owner.open('jellyfin-a');
        await owner.close();
        respond(success('old'));
        expect(await pending).toBeNull();
        expect(APIClient.delete).toHaveBeenCalledWith('/streams/live/sessions/old', expect.objectContaining({fetchOptions: {keepalive: true}}));
    });

    it('高速切り替えでは新しいチャンネルのセッションを旧画面が閉じない', async () => {
        let respond!: (response: ReturnType<typeof success>) => void;
        vi.mocked(APIClient.post).mockImplementationOnce(() => new Promise(resolve => {respond = resolve;}));
        vi.mocked(APIClient.post).mockResolvedValueOnce(success('new'));
        const oldOwner = new LivePlaybackSession();
        const newOwner = new LivePlaybackSession();
        const pending = oldOwner.open('jellyfin-a');
        await oldOwner.close();
        expect(await newOwner.open('jellyfin-b')).toEqual(session('new'));
        respond(success('old'));
        expect(await pending).toBeNull();
        expect(APIClient.delete).toHaveBeenCalledTimes(1);
        await newOwner.close();
        expect(APIClient.delete).toHaveBeenLastCalledWith('/streams/live/sessions/new', expect.any(Object));
    });

    it('終了を重ねても上流の解放要求を重複させない', async () => {
        vi.mocked(APIClient.post).mockResolvedValue(success('active'));
        const owner = new LivePlaybackSession();
        await owner.open('jellyfin-a');
        await Promise.all([owner.close(), owner.close()]);
        expect(APIClient.delete).toHaveBeenCalledTimes(1);
        expect(APIClient.post).toHaveBeenCalledWith('/streams/live/sessions', {channel_id: 'jellyfin-a'});
    });

    it('失敗理由を保ち、失敗した開始から架空の終了要求を送らない', async () => {
        vi.mocked(APIClient.post).mockResolvedValue({type: 'error', status: 502, headers: {},
            data: {detail: 'Authentication failed [IPTV_AUTH]'}, error: {} as never});
        const owner = new LivePlaybackSession();
        await expect(owner.open('jellyfin-a')).rejects.toThrow('Authentication failed [IPTV_AUTH]');
        await owner.close();
        expect(APIClient.delete).not.toHaveBeenCalled();
    });

    it('ページ更新でも keepalive でセッションの解放を送る', async () => {
        vi.mocked(APIClient.post).mockResolvedValue(success('refresh'));
        const owner = new LivePlaybackSession();
        await owner.open('jellyfin-a');
        window.dispatchEvent(new Event('pagehide'));
        expect(APIClient.delete).toHaveBeenCalledWith('/streams/live/sessions/refresh', expect.objectContaining({
            adapter: 'fetch', fetchOptions: {keepalive: true},
        }));
        await owner.close();
        expect(APIClient.delete).toHaveBeenCalledTimes(1);
    });
});
