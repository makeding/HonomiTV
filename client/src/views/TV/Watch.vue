<template>
    <Watch :playback_mode="'Live'" />
</template>
<script lang="ts">

import { mapStores } from 'pinia';
import { defineComponent } from 'vue';

import Watch from '@/components/Watch/Watch.vue';
import PlayerController from '@/services/player/PlayerController';
import useChannelsStore from '@/stores/ChannelsStore';
import usePlayerStore from '@/stores/PlayerStore';
import useSettingsStore from '@/stores/SettingsStore';
import Utils, { dayjs } from '@/utils';

// PlayerController のインスタンス
// data() 内に記述すると再帰的にリアクティブ化され重くなる上リアクティブにする必要自体がないので、グローバル変数にしている
let player_controller: PlayerController | null = null;
// 連続切り替えでも旧プレイヤーの破棄完了を共有し、新しい DOM を旧処理が消さない。
let player_destruction: Promise<void> = Promise.resolve();

export default defineComponent({
    name: 'TV-Watch',
    components: {
        Watch,
    },
    data() {
        return {
            // インターバル ID
            // ページ遷移時に setInterval(), setTimeout() の実行を止めるのに使う
            // setInterval(), setTimeout() の返り値を登録する
            interval_ids: [] as number[],
            // 退出・切り替えのたびに更新し、古い更新応答でプレイヤーを作らない。
            playback_generation: 0,
            is_leaving: false,
        };
    },
    computed: {
        ...mapStores(useChannelsStore, usePlayerStore, useSettingsStore),
    },
    // 開始時に実行
    created() {

        // 下記以外の視聴画面の開始処理は Watch コンポーネントの方で自動的に行われる

        // チャンネル ID をセット
        this.channelsStore.display_channel_id = this.$route.params.display_channel_id as string;
        this.playerStore.event_emitter.on('LivePlaybackRetry', this.retryPlayback);

        // 再生セッションを初期化
        this.init();
    },
    // チャンネル切り替え時に実行
    // コンポーネント（インスタンス）は再利用される
    // ref: https://v3.router.vuejs.org/ja/guide/advanced/navigation-guards.html#%E3%83%AB%E3%83%BC%E3%83%88%E5%8D%98%E4%BD%8D%E3%82%AB%E3%82%99%E3%83%BC%E3%83%88%E3%82%99
    beforeRouteUpdate(to, from, next) {

        // 前の再生セッションを破棄して終了する
        // このとき this.interval_ids に登録された setTimeout がキャンセルされるため、
        // 後述の 0.5 秒の間にザッピングにより他のチャンネルに切り替えた場合は this.init() は実行されない
        const destroy_promise = this.destroy();
        const generation = this.playback_generation;
        const initializeIfCurrent = () => {
            if (generation === this.playback_generation && this.is_leaving === false) return this.init();
        };

        // チャンネル ID を次のチャンネルのものに切り替える
        this.channelsStore.display_channel_id = to.params.display_channel_id as string;

        (async () => {

            // ザッピング（「前/次のチャンネル」ボタン or 上下キーショートカット）によるチャンネル移動時のみ、
            // 0.5秒だけ待ってから新しい再生セッションを初期化する
            // 連続してチャンネルを切り替えた際に毎回再生処理を開始しないように猶予を設ける
            if (this.playerStore.is_zapping === true) {
                this.playerStore.is_zapping = false;
                this.interval_ids.push(window.setTimeout(() => {
                    destroy_promise.then(initializeIfCurrent);  // destroy() の実行完了を待ってから初期化する
                }, 0.5 * 1000));

            // 通常のチャンネル移動時は、すぐに再生セッションを初期化する
            } else {
                destroy_promise.then(initializeIfCurrent);  // destroy() の実行完了を待ってから初期化する
            }
        })();

        // 次のルートに置き換え
        next();
    },
    // 終了前に実行
    beforeUnmount() {
        this.is_leaving = true;
        this.playerStore.event_emitter.off('LivePlaybackRetry', this.retryPlayback);

        // destroy() を実行
        // 別のページへ遷移するため、DPlayer のインスタンスを確実に破棄する
        // さもなければ、ブラウザがリロードされるまでバックグラウンドで永遠に再生され続けてしまう
        this.destroy();

        // このページから離れるので、チャンネル ID を gr000 (ダミー値) に戻す
        this.channelsStore.display_channel_id = 'gr000';

        // 上記以外の視聴画面の終了処理は Watch コンポーネントの方で自動的に行われる
    },
    methods: {

        // 初期化失敗で DPlayer が存在しない場合も、画面自身が新しい再生を開始する。
        async retryPlayback() {
            if (this.playerStore.live_playback_error === null) return;
            this.playerStore.live_playback_recovering = true;
            this.playerStore.live_playback_error = null;
            const destruction = this.destroy();
            const generation = this.playback_generation;
            await destruction;
            if (this.is_leaving === false && generation === this.playback_generation) await this.init(true);
        },

        // 再生セッションを初期化する
        async init(force = false) {
            const generation = ++this.playback_generation;
            const channel_id = this.channelsStore.display_channel_id;
            await player_destruction;
            if (generation !== this.playback_generation || this.is_leaving) return;
            if (force === false) this.playerStore.live_playback_recovering = false;
            this.playerStore.live_playback_error = null;
            this.playerStore.is_loading = true;
            this.playerStore.is_video_buffering = true;

            // 00秒までの残り秒数を取得
            // 現在 16:01:34 なら 26 (秒) になる
            const residue_second = 60 - dayjs().second();

            // 00秒になるまで待ってから実行するタイマー
            // 番組は基本1分単位で組まれているため、20秒や45秒など中途半端な秒数で更新してしまうと番組情報の反映が遅れてしまう
            this.interval_ids.push(window.setTimeout(() => {

                // この時点で00秒なので、チャンネル情報を更新
                this.channelsStore.update(true);

                // 以降、30秒おきにチャンネル情報を更新
                this.interval_ids.push(window.setInterval(() => {
                    this.channelsStore.update(true);
                }, 30 * 1000));

            }, residue_second * 1000));

            // チャンネル情報を更新 (初回)
            await this.channelsStore.update(force, channel_id.startsWith('jellyfin-') ? 'Jellyfin' : 'Broadcast');
            if (generation !== this.playback_generation || this.is_leaving ||
                this.channelsStore.display_channel_id !== channel_id) return;

            // URL 上のチャンネル ID が未定義なら実行しない (フェイルセーフ)
            // 基本あり得ないはずだが、念のため
            if (this.$route.params.display_channel_id === undefined) {
                this.$router.push({path: '/not-found/'});
                return;
            }

            // もしこの時点でチャンネル名が「チャンネル情報取得エラー」の場合、
            // URL で指定された display_channel_id に紐づくチャンネル情報がないことを示しているので、404 ページにリダイレクト
            if (this.channelsStore.channel.current.name === 'チャンネル情報取得エラー') {
                // 一時的な供給元の障害を 404 として扱わず、同じ視聴枠に復旧手段を残す。
                if (channel_id.startsWith('jellyfin-')) {
                    const reason = this.channelsStore.source_errors.IPTV ?? '接続を確認してください。';
                    this.playerStore.live_playback_error = `チャンネル情報を取得できませんでした。${reason} 再試行してください。 [LIVE_CHANNEL_UNAVAILABLE]`;
                    this.playerStore.live_playback_recovering = false;
                    this.playerStore.is_loading = false;
                    this.playerStore.is_video_buffering = false;
                    return;
                }
                await Utils.sleep(3);  // 3秒待機
                if (generation !== this.playback_generation || this.is_leaving) return;
                this.$router.push({path: '/not-found/'});
                return;
            }

            // PlayerController を初期化
            const controller = new PlayerController('Live');
            player_controller = controller;
            await controller.init();
        },

        // 再生セッションを破棄する
        // チャンネルを切り替える際に実行される
        async destroy() {
            ++this.playback_generation;

            // clearInterval() ですべての setInterval(), setTimeout() の実行を止める
            // clearInterval() と clearTimeout() は中身共通なので問題ない
            for (const interval_id of this.interval_ids) {
                window.clearInterval(interval_id);
            }

            // interval_ids をクリア
            this.interval_ids = [];

            // PlayerController を破棄
            if (player_controller !== null) {
                const controller = player_controller;
                player_controller = null;
                player_destruction = player_destruction.then(() => controller.dispose());
            }
            await player_destruction;
        }
    }
});

</script>
