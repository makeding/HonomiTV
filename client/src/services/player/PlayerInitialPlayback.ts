import type DPlayer from 'dplayer';


/**
 * 録画再生の初期位置を DPlayer の UI と再生状態へ一度に反映する。
 * 初期 seek 自身は無通知にし、同時に表示された別用途の notice は保持する。
 */
export function applyInitialPlaybackPosition(options: {
    player: DPlayer;
    playbackPositionSeconds: number;
    recordedDurationSeconds: number;
    recordingStartMarginSeconds: number;
    suppressResumeNotice?: boolean;
}): void {
    // seek() 後に hideNotice() すると、非同期で先に表示された TLV 障害通知まで消えてしまう。
    // DPlayer の第2引数で、この初期 seek が生成する通知だけを最初から抑止する。
    options.player.seek(options.playbackPositionSeconds, true);
    options.player.bar.set(
        'played',
        options.playbackPositionSeconds / options.recordedDurationSeconds,
        'width',
    );

    // 視聴履歴から明確に途中再開する場合だけ、TLV 障害通知より新しいユーザー操作結果として表示する。
    if (options.suppressResumeNotice !== true &&
        options.playbackPositionSeconds > options.recordingStartMarginSeconds + 2) {
        options.player.notice('前回視聴した続きから再生します');
    }
    options.player.play();
}
