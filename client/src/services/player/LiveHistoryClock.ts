/** ライブ映像のメディア時刻を放送時刻へ対応づける。停止中の壁時計は視聴進度に加えない。 */
export default class LiveHistoryClock {
    private media_time: number | null = null;
    private picture_time: number | null = null;

    observe(media_time: number, wall_time: number, buffered_seconds: number, playing: boolean): number | null {
        if (!playing || !Number.isFinite(media_time) || !Number.isFinite(wall_time)) return null;
        const previous = this.media_time;
        // 初回およびストリームの時刻リセット時に、受信済み映像の末尾から遅延を差し引く。
        if (previous === null || media_time < previous) {
            this.media_time = media_time;
            this.picture_time = wall_time - Math.max(0, buffered_seconds);
            return null;
        }
        if (media_time <= previous) return null;
        this.media_time = media_time;
        this.picture_time = Math.min(wall_time, this.picture_time! + media_time - previous);
        return this.picture_time;
    }
}
