<template>
    <Watch :playback_mode="'Video'" />
</template>
<script lang="ts">

import { mapStores } from 'pinia';
import { defineComponent } from 'vue';

import Watch from '@/components/Watch/Watch.vue';
import OfflineVideos from '@/services/OfflineVideos';
import PlayerController from '@/services/player/PlayerController';
import Videos from '@/services/Videos';
import usePlayerStore from '@/stores/PlayerStore';
import useSettingsStore from '@/stores/SettingsStore';

// PlayerController のインスタンス
// data() 内に記述すると再帰的にリアクティブ化され重くなる上リアクティブにする必要自体がないので、グローバル変数にしている
let player_controller: PlayerController | null = null;
// 追いかけ再生中の録画時長などを更新するタイマー
let recording_metadata_refresh_timer: number | null = null;
const RECORDING_METADATA_REFRESH_INTERVAL = 30 * 1000;
let playback_session_generation = 0;

export default defineComponent({
    name: 'Video-Watch',
    components: {
        Watch,
    },
    computed: {
        ...mapStores(usePlayerStore, useSettingsStore),
    },
    // 開始時に実行
    created() {

        // 下記以外の視聴画面の開始処理は Watch コンポーネントの方で自動的に行われる

        // 再生セッションを初期化
        this.init();
    },
    // チャンネル切り替え時に実行
    // コンポーネント（インスタンス）は再利用される
    // ref: https://v3.router.vuejs.org/ja/guide/advanced/navigation-guards.html#%E3%83%AB%E3%83%BC%E3%83%88%E5%8D%98%E4%BD%8D%E3%82%AB%E3%82%99%E3%83%BC%E3%83%88%E3%82%99
    beforeRouteUpdate(to, from, next) {

        // 前の再生セッションを破棄して終了し、完了を待ってから再度初期化する
        const destroy_promise = this.destroy();
        destroy_promise.then(() => this.init());

        // 次のルートに置き換え
        next();
    },
    // 終了前に実行
    beforeUnmount() {

        // destroy() を実行
        // 別のページへ遷移するため、DPlayer のインスタンスを確実に破棄する
        // さもなければ、ブラウザがリロードされるまでバックグラウンドで永遠に再生され続けてしまう
        this.destroy();

        // 上記以外の視聴画面の終了処理は Watch コンポーネントの方で自動的に行われる
    },
    methods: {

        // 再生セッションを初期化する
        async init() {

            // 破棄済みセッションの遅延レスポンスが、同じ録画を開き直した新しいセッションへ混ざらないよう世代を分ける。
            const session_generation = ++playback_session_generation;

            // URL 上の録画番組 ID が未定義なら実行しない (フェイルセーフ)
            // 基本あり得ないはずだが、念のため
            if (this.$route.params.video_id === undefined) {
                this.$router.push({path: '/not-found/'});
                return;
            }

            const video_id_text = this.$route.params.video_id;
            if (typeof video_id_text !== 'string' || /^[1-9]\d*$/.test(video_id_text) === false) {
                this.$router.push({path: '/not-found/'});
                return;
            }
            const video_id = Number(video_id_text);

            // オフライン保存ページから開いた場合は保存時点の番組情報を使い、通常再生の通信失敗時だけ保存版へ切り替える
            let offline_video = this.$route.query.source === 'offline' ? await OfflineVideos.getVideo(video_id) : null;
            let recorded_program = offline_video?.program ?? null;
            if (recorded_program === null) {
                try {
                    recorded_program = await Videos.fetchVideo(video_id);
                } catch (error) {
                    // 一時的なサーバーエラーや通信断では保存版へフォールバックし、保存版もなければ現在のページに留まる
                    offline_video = await OfflineVideos.getVideo(video_id);
                    recorded_program = offline_video?.program ?? null;
                    if (recorded_program === null) {
                        console.warn('[Video-Watch] Failed to fetch recorded program. Keep current route.', error);
                        return;
                    }
                }
            }
            if (recorded_program === null && offline_video === null) {
                offline_video = await OfflineVideos.getVideo(video_id);
                recorded_program = offline_video?.program ?? null;
            }
            if (recorded_program === null) {
                this.$router.push({path: '/not-found/'});
                return;
            }
            // 取得待ちの間に破棄・切り替えられた旧セッションは、現在の番組情報を上書きしない。
            if (session_generation !== playback_session_generation) return;
            this.playerStore.recorded_program = recorded_program;
            this.playerStore.is_offline_playback = offline_video !== null;
            this.playerStore.offline_video = offline_video;

            // Series のキーフレームプレビューなどから秒数が明示された場合は、視聴履歴より優先して再生を開始する。
            // 不正な値は無視し、従来通り PlayerController 側の視聴履歴・録画開始マージンの順で復帰させる。
            const seek_query = Array.isArray(this.$route.query.t) ? this.$route.query.t[0] : this.$route.query.t;
            const requested_seek_seconds = typeof seek_query === 'string' ? Number(seek_query) : null;
            const seek_seconds = requested_seek_seconds !== null && Number.isFinite(requested_seek_seconds) &&
                requested_seek_seconds >= 0
                ? Math.min(requested_seek_seconds, recorded_program.recorded_video.duration)
                : null;

            // PlayerController を初期化
            player_controller = new PlayerController('Video');
            await player_controller.init({
                default_quality: null,
                playback_rate: null,
                seek_seconds,
            });

            // PlayerController 初期化中に破棄された場合は、旧セッションの定期更新を後から作らない。
            if (session_generation !== playback_session_generation) return;

            // HLS の再生位置は維持したまま、録画中だけ詳細 API の実メディア時長をストアへ反映する。
            if (recorded_program.recorded_video.status === 'Recording' && offline_video === null) {
                let is_metadata_refresh_in_flight = false;
                recording_metadata_refresh_timer = window.setInterval(async () => {
                    if (is_metadata_refresh_in_flight === true) return;
                    is_metadata_refresh_in_flight = true;
                    try {
                        const refreshed_program = await Videos.fetchVideo(video_id);
                        if (
                            refreshed_program === null ||
                            session_generation !== playback_session_generation ||
                            this.playerStore.recorded_program.id !== video_id
                        ) return;
                        this.playerStore.recorded_program = refreshed_program;
                        if (refreshed_program.recorded_video.status !== 'Recording' && recording_metadata_refresh_timer !== null) {
                            window.clearInterval(recording_metadata_refresh_timer);
                            recording_metadata_refresh_timer = null;
                        }
                    } catch (error) {
                        // 一時的な取得失敗では再生を止めず、次の定期更新で再試行する。
                        console.warn('[Video-Watch] Failed to refresh recording metadata.', error);
                    } finally {
                        is_metadata_refresh_in_flight = false;
                    }
                }, RECORDING_METADATA_REFRESH_INTERVAL);
            }
        },

        // 再生セッションを破棄する
        // 再生する録画番組を切り替える際にも実行される
        async destroy() {

            // 実行中のメタデータ取得結果も含め、これ以前のセッションからの更新を無効化する。
            playback_session_generation += 1;

            if (recording_metadata_refresh_timer !== null) {
                window.clearInterval(recording_metadata_refresh_timer);
                recording_metadata_refresh_timer = null;
            }

            // PlayerController を破棄
            if (player_controller !== null) {
                await player_controller.destroy();
                player_controller = null;
            }
        }
    }
});

</script>
