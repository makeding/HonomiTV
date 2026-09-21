import APIClient from '@/services/APIClient';


/** 供給元に依存しないライブ再生セッション。URL は必ずサーバーの代理経路。 */
export interface ILivePlaybackSession {
    id: string;
    stream_url: string;
    stream_type: 'hls' | 'mpegts';
}


/**
 * 一つのプレイヤーが所有するセッションの寿命を管理する。
 * 開始要求をキャンセルすると応答中の ID を失うため、応答を受け取り次第必ず解放する。
 */
export default class LivePlaybackSession {
    private generation = 0;
    private session: ILivePlaybackSession | null = null;
    private onPageHide = (): void => { void this.close(); };

    /**
     * 共通ライブ API からセッションを取得する。
     * @param channelID 安定したチャンネル ID
     * @returns 有効なセッション。待機中に破棄された場合は null
     */
    async open(channelID: string): Promise<ILivePlaybackSession | null> {
        const generation = ++this.generation;
        window.addEventListener('pagehide', this.onPageHide);
        const response = await APIClient.post<ILivePlaybackSession>('/streams/live/sessions', {channel_id: channelID});
        if (response.type === 'error') {
            if (generation !== this.generation) return null;
            const reason = typeof response.data.detail === 'string' ? response.data.detail : 'サーバーの応答が不正です。';
            throw new Error(`${reason} [LIVE_SESSION_OPEN_FAILED]`);
        }
        if (generation !== this.generation) {
            await this.release(response.data.id);
            return null;
        }
        this.session = response.data;
        return response.data;
    }

    /** 保留中の開始を無効化し、このプレイヤーが所有するセッションだけを閉じる。 */
    async close(): Promise<void> {
        ++this.generation;
        window.removeEventListener('pagehide', this.onPageHide);
        const session = this.session;
        this.session = null;
        if (session !== null) await this.release(session.id);
    }

    /**
     * 解放失敗はサーバー側の期限回収へ委ねる。供給元への終了再試行もサーバーが担当する。
     * @param sessionID このプレイヤーに発行されたセッション ID
     */
    private async release(sessionID: string): Promise<void> {
        const response = await APIClient.delete(`/streams/live/sessions/${encodeURIComponent(sessionID)}`, {
            // ページ更新時にも終了要求を送る。到達しない場合はサーバーの期限回収が補完する。
            adapter: 'fetch', fetchOptions: {keepalive: true}, timeout: 3000,
        });
        if (response.type === 'error') console.warn('[LivePlaybackSession] Failed to release session.');
    }
}
