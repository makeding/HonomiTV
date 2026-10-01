import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
    send: vi.fn(),
    push: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    channels: {channel: {current: {capabilities: {remote_playback: true}}}},
    player: {video_playback_position: 0},
}));
vi.mock('@/message', () => ({default: {success: mocks.success, warning: mocks.warning}}));
vi.mock('@/services/RemoteControl', () => ({default: {sendOpenCommand: mocks.send}}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => mocks.channels}));
vi.mock('@/stores/PlayerStore', () => ({default: () => mocks.player}));

import { handoffCurrentPlaybackToDevice } from '@/services/RemoteHandoff';

// ルートとルーターの最小限のモック。実物は画面遷移を伴うため、判定に必要な形だけを再現する
const router = {push: mocks.push} as any;
const route = (name: string | null, params: Record<string, string> = {}): any => ({name, params});

describe('RemoteHandoff 視聴画面からのテレビへの再生引き継ぎ', () => {
    beforeEach(() => {
        vi.clearAllMocks();
        mocks.send.mockResolvedValue(true);
        mocks.push.mockResolvedValue(undefined);
        mocks.channels.channel.current = {capabilities: {remote_playback: true}};
        mocks.player.video_playback_position = 0;
    });

    it('ライブ視聴画面では表示中のチャンネルを送信し、一覧ページへ戻る', async () => {
        const result = await handoffCurrentPlaybackToDevice('tv', route('TV Watch', {display_channel_id: 'gr011'}), router);
        expect(result).toBe('Success');
        expect(mocks.send).toHaveBeenCalledWith('tv', {type: 'OpenLive', display_channel_id: 'gr011'});
        expect(mocks.push).toHaveBeenCalledWith({path: '/tv/'});
        expect(mocks.success).toHaveBeenCalledWith('テレビへ再生を引き継ぎました。');
    });

    it('録画視聴画面では表示中の録画番組と現在の再生位置を送信し、一覧ページへ戻る', async () => {
        mocks.player.video_playback_position = 123.5;
        const result = await handoffCurrentPlaybackToDevice('tv', route('Videos Watch', {video_id: '45'}), router);
        expect(result).toBe('Success');
        expect(mocks.send).toHaveBeenCalledWith('tv', {type: 'OpenRecording', recorded_program_id: 45, position_seconds: 123.5});
        expect(mocks.push).toHaveBeenCalledWith({path: '/videos/'});
        expect(mocks.success).toHaveBeenCalledWith('テレビへ再生を引き継ぎました。');
    });

    it('テレビへの送信に対応していないチャンネルは送信せず、視聴画面に留まる', async () => {
        mocks.channels.channel.current = {capabilities: {remote_playback: false}};
        const result = await handoffCurrentPlaybackToDevice('tv', route('TV Watch', {display_channel_id: 'gr011'}), router);
        expect(result).toBe('Unsupported');
        expect(mocks.send).not.toHaveBeenCalled();
        expect(mocks.push).not.toHaveBeenCalled();
        expect(mocks.warning).toHaveBeenCalledTimes(1);
    });

    it('送信に失敗したときは視聴画面に留まり、成功メッセージは表示しない', async () => {
        mocks.send.mockResolvedValue(false);
        const result = await handoffCurrentPlaybackToDevice('tv', route('Videos Watch', {video_id: '45'}), router);
        expect(result).toBe('Failure');
        expect(mocks.push).not.toHaveBeenCalled();
        expect(mocks.success).not.toHaveBeenCalled();
    });

    it('視聴画面以外では何も送信せず、メニューの通常の選択動作に任せる', async () => {
        const result = await handoffCurrentPlaybackToDevice('tv', route('TV Home'), router);
        expect(result).toBe('NotWatching');
        expect(mocks.send).not.toHaveBeenCalled();
        expect(mocks.push).not.toHaveBeenCalled();
    });

    it('録画番組 ID が不正な URL では引き継がない', async () => {
        expect(await handoffCurrentPlaybackToDevice('tv', route('Videos Watch', {video_id: '0'}), router)).toBe('NotWatching');
        expect(await handoffCurrentPlaybackToDevice('tv', route('Videos Watch', {video_id: 'abc'}), router)).toBe('NotWatching');
        expect(mocks.send).not.toHaveBeenCalled();
    });
});
