import { beforeEach, describe, expect, it, vi } from 'vitest';

import type DPlayer from 'dplayer';

import LiveCommentManager from '@/services/player/managers/LiveCommentManager';


const mocks = vi.hoisted(() => ({
    channel: {type: 'IPTV', id: 'net-channel'},
    fetchUser: vi.fn(),
    fetchWebSocketInfo: vi.fn(),
    state: {live_comment_init_failed_message: null as string | null},
}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => ({channel: {current: mocks.channel}})}));
vi.mock('@/stores/PlayerStore', () => ({default: () => mocks.state}));
vi.mock('@/stores/UserStore', () => ({default: () => ({fetchUser: mocks.fetchUser})}));
vi.mock('@/stores/SettingsStore', () => ({default: () => ({settings: {}})}));
vi.mock('@/services/Channels', () => ({default: {fetchWebSocketInfo: mocks.fetchWebSocketInfo}}));
vi.mock('@/utils', () => ({default: {}, dayjs: vi.fn(), CommentUtils: {}}));

describe('ネット分類の実況接続', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.channel.type = 'IPTV';
        mocks.state.live_comment_init_failed_message = null;
        mocks.fetchWebSocketInfo.mockResolvedValue({watch_session_url: null});
    });

    it('供給元によらずネットではユーザー取得も実況 API も呼ばない', async () => {
        const manager = new LiveCommentManager({} as DPlayer);
        await manager.init();
        expect(mocks.fetchUser).not.toHaveBeenCalled();
        expect(mocks.fetchWebSocketInfo).not.toHaveBeenCalled();
        expect(mocks.state.live_comment_init_failed_message).toBeNull();
    });

    it('放送チャンネルでは既存の実況取得を維持する', async () => {
        mocks.channel.type = 'GR';
        const manager = new LiveCommentManager({} as DPlayer);
        await manager.init();
        expect(mocks.fetchUser).toHaveBeenCalledOnce();
        expect(mocks.fetchWebSocketInfo).toHaveBeenCalledWith('net-channel');
        expect(mocks.state.live_comment_init_failed_message).toContain('対応していません');
    });
});
