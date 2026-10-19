import type DPlayer from 'dplayer';

import LiveHistoryClock from '@/services/player/LiveHistoryClock';
import PlayerManager from '@/services/player/PlayerManager';
import useChannelsStore from '@/stores/ChannelsStore';
import useSettingsStore from '@/stores/SettingsStore';
import Utils from '@/utils';


/** ライブの映像時刻を既存の設定同期へ添え、録画の視聴履歴として保存する。 */
export default class LiveHistoryManager implements PlayerManager {
    public readonly restart_required_when_quality_switched = false;
    private cleanup: (() => void) | null = null;

    constructor(private readonly player: DPlayer) {}

    public async init(): Promise<void> {
        this.cleanup?.();
        const settings_store = useSettingsStore();
        const channel_id = useChannelsStore().channel.current.id;
        const owner_token = Utils.getAccessToken();
        let video = this.player.video;
        let clock = new LiveHistoryClock();
        let last_picture_time: number | null = null;
        let last_sent_at = 0;
        // 通信中の更新は最新の一件にまとめ、回線断でも保存待ちを無制限に積まない。
        let saving = false;
        let pending_picture_time: number | null = null;
        const save = () => {
            if (last_picture_time === null || owner_token === null || settings_store.settings.sync_settings === false) return;
            pending_picture_time = last_picture_time;
            if (saving) return;
            saving = true;
            void (async () => {
                while (pending_picture_time !== null) {
                    const playback_time = pending_picture_time;
                    pending_picture_time = null;
                    if (Utils.getAccessToken() !== owner_token || settings_store.settings.sync_settings === false) break;
                    // 既存の設定同期へ添え、履歴の読み戻しも既存の設定 pull に任せる。
                    // 応答をストアへ二重反映すると $subscribe がもう一度 PUT を送ってしまう。
                    await settings_store.syncClientSettingsToServer(false, {channel_id, playback_time});
                }
            })().catch(error => console.warn('[LiveHistoryManager] Failed to save live history.', error))
                .finally(() => { saving = false; });
        };
        const observe = () => {
            const current_video = this.player?.video;
            if (!current_video) return;
            if (video !== current_video) {
                video = current_video;
                clock = new LiveHistoryClock();
            }
            const buffered_end = video.buffered.length > 0 ? video.buffered.end(video.buffered.length - 1) : video.currentTime;
            const picture_time = clock.observe(video.currentTime, Utils.time(), buffered_end - video.currentTime,
                !video.paused && !video.ended && video.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA);
            if (picture_time === null) return;
            last_picture_time = picture_time;
            const now = Utils.time();
            if (now - last_sent_at >= 10) {
                last_sent_at = now;
                save();
            }
        };
        const on_hidden = () => { if (document.visibilityState === 'hidden') save(); };
        this.player.on('timeupdate', observe);
        this.player.on('pause', save);
        window.addEventListener('pagehide', save);
        document.addEventListener('visibilitychange', on_hidden);
        this.cleanup = () => {
            this.player.off('timeupdate', observe);
            this.player.off('pause', save);
            save();
            window.removeEventListener('pagehide', save);
            document.removeEventListener('visibilitychange', on_hidden);
        };
    }

    public async destroy(): Promise<void> {
        this.cleanup?.();
        this.cleanup = null;
    }
}
