import Hls from 'hls.js';
import mpegts from 'mpegts.js';

import type PlayerManager from '@/services/player/PlayerManager';
import type DPlayer from 'dplayer';

import usePlayerStore from '@/stores/PlayerStore';


/** 放送イベントに依存しない HLS / MPEG-TS セッションの準備・再生・失敗を管理する。 */
export default class LiveSessionPlaybackManager implements PlayerManager {
    public readonly restart_required_when_quality_switched = false;
    private timeout: number | null = null;
    private destroyed = false;
    private failed = false;

    /**
     * @param player このセッションだけの DPlayer インスタンス
     * @param onFailure セッションを解放し、同じ画面に復旧操作を残す処理
     */
    constructor(private readonly player: DPlayer, private readonly onFailure: (reason: string) => void) {}

    /** メディアが実際に再生された時点で初めて準備中表示を解除する。 */
    private onPlaying = (): void => {
        if (this.destroyed || this.failed) return;
        if (this.timeout !== null) window.clearTimeout(this.timeout);
        this.timeout = null;
        const store = usePlayerStore();
        store.is_loading = false;
        store.is_video_buffering = false;
        store.is_background_display = false;
        store.live_stream_status = 'ONAir';
    };

    /** 初期化中と再生中の致命的エラーは、再試行可能な失敗として一度だけ通知する。 */
    private fail(reason: string): void {
        if (this.destroyed || this.failed) return;
        this.failed = true;
        this.onFailure(reason);
    }

    private onNativeError = (): void => {
        this.fail(`映像を読み込めませんでした。接続を確認して再試行してください。 [LIVE_MEDIA_${this.player.video.error?.code ?? 'UNKNOWN'}]`);
    };

    private onEnded = (): void => {
        this.fail('配信が終了しました。もう一度視聴するには再試行してください。 [LIVE_STREAM_ENDED]');
    };

    private onWaiting = (): void => {
        if (this.timeout !== null || this.destroyed || this.failed) return;
        this.timeout = window.setTimeout(() => {
            this.fail('配信から映像が届かなくなりました。接続を確認して再試行してください。 [LIVE_STALL_TIMEOUT]');
        }, 30000);
    };

    private onHLSError = (_event: string, data: {fatal: boolean; details: string}): void => {
        if (data.fatal) this.fail(`配信データを再生できませんでした。再試行してください。 [HLS_${data.details}]`);
    };

    private onTSError = (type: string, detail: string): void => {
        this.fail(`配信データを読み込めませんでした。接続を確認して再試行してください。 [MPEGTS_${type}: ${detail}]`);
    };

    /** セッション専用のメディアイベントを購読する。放送の SSE / PSI は使用しない。 */
    public async init(): Promise<void> {
        const store = usePlayerStore();
        store.is_loading = true;
        store.is_video_buffering = true;
        store.is_background_display = true;
        store.live_stream_status = 'Standby';
        this.player.video.addEventListener('playing', this.onPlaying);
        this.player.video.addEventListener('error', this.onNativeError);
        this.player.video.addEventListener('ended', this.onEnded);
        this.player.video.addEventListener('waiting', this.onWaiting);
        this.player.plugins.hls?.on(Hls.Events.ERROR, this.onHLSError);
        this.player.plugins.mpegts?.on(mpegts.Events.ERROR, this.onTSError);
        this.timeout = window.setTimeout(() => {
            this.fail('配信から映像が届かず、再生開始がタイムアウトしました。再試行してください。 [LIVE_START_TIMEOUT]');
        }, 30000);
        if (this.player.video.error !== null) this.onNativeError();
        else if (this.player.video.readyState >= HTMLMediaElement.HAVE_FUTURE_DATA && !this.player.video.paused) this.onPlaying();
    }

    /** 退出後に旧メディアから新しい画面の状態が上書きされないよう購読を終了する。 */
    public async destroy(): Promise<void> {
        this.destroyed = true;
        if (this.timeout !== null) window.clearTimeout(this.timeout);
        this.player.video.removeEventListener('playing', this.onPlaying);
        this.player.video.removeEventListener('error', this.onNativeError);
        this.player.video.removeEventListener('ended', this.onEnded);
        this.player.video.removeEventListener('waiting', this.onWaiting);
        this.player.plugins.hls?.off(Hls.Events.ERROR, this.onHLSError);
        this.player.plugins.mpegts?.off(mpegts.Events.ERROR, this.onTSError);
    }
}
