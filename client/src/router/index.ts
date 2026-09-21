

import { createRouter, createWebHistory } from 'vue-router';

import Message from '@/message';
import Channels from '@/services/Channels';
import RemoteControl, { type RemoteCommand } from '@/services/RemoteControl';
import useChannelsStore from '@/stores/ChannelsStore';
import useSettingsStore from '@/stores/SettingsStore';
import Utils from '@/utils';


// Vue Router v4
// ref: https://router.vuejs.org/guide/

const router = createRouter({

    // ルーティングのベース URL
    history: createWebHistory(import.meta.env.BASE_URL),

    // ルーティング設定
    routes: [
        {
            path: '/',
            redirect: '/tv/',
        },
        {
            path: '/tv/',
            name: 'TV Home',
            component: () => import('@/views/TV/Home.vue'),
        },
        {
            path: '/tv/watch/:display_channel_id',
            name: 'TV Watch',
            component: () => import('@/views/TV/Watch.vue'),
        },
        {
            path: '/tv/search',
            name: 'TV Search',
            component: () => import('@/views/TV/Search.vue'),
        },
        {
            path: '/videos/',
            name: 'Videos Home',
            component: () => import('@/views/Videos/Home.vue'),
        },
        {
            path: '/videos/search',
            name: 'Videos Search',
            component: () => import('@/views/Videos/Search.vue'),
        },
        {
            path: '/videos/programs',
            name: 'Videos Programs',
            component: () => import('@/views/Videos/Programs.vue'),
        },
        {
            path: '/videos/series/:series_id',
            redirect: to => `/series/${to.params.series_id}`,
        },
        {
            path: '/series/on-air/:series_id?',
            name: 'On Air Series',
            component: () => import('@/views/Series/OnAir.vue'),
        },
        {
            path: '/series/:series_id?',
            name: 'Series Home',
            component: () => import('@/views/Series/Home.vue'),
        },
        {
            path: '/videos/recording',
            name: 'Videos Recording',
            component: () => import('@/views/Videos/Recording.vue'),
        },
        {
            path: '/videos/watch/:video_id',
            name: 'Videos Watch',
            component: () => import('@/views/Videos/Watch.vue'),
        },
        {
            path: '/timetable/',
            name: 'TimeTable',
            component: () => import('@/views/TimeTable.vue'),
        },
        {
            path: '/reservations/',
            name: 'Reservations',
            component: () => import('@/views/Reservations/Home.vue'),
        },
        {
            path: '/reservations/all',
            name: 'Reservations All',
            component: () => import('@/views/Reservations/Reservations.vue'),
        },
        {
            path: '/mylist/',
            name: 'Mylist',
            component: () => import('@/views/Mylist.vue'),
        },
        {
            path: '/offline-videos/',
            name: 'Offline Videos',
            component: () => import('@/views/OfflineVideos.vue'),
        },
        {
            path: '/watched-history/',
            name: 'Watched History',
            component: () => import('@/views/WatchedHistory.vue'),
        },
        {
            path: '/offline-videos/',
            name: 'Offline Videos',
            component: () => import('@/views/OfflineVideos.vue'),
        },
        {
            path: '/mypage/',
            name: 'MyPage',
            component: () => import('@/views/MyPage.vue'),
        },
        {
            path: '/settings/',
            name: 'Settings Index',
            component: () => import('@/views/Settings/Index.vue'),
            beforeEnter: (to, from, next) => {
                // スマホ縦画面・スマホ横画面・タブレット縦画面では設定一覧画面を表示する（画面サイズの関係）
                if (Utils.isSmartphoneVertical() || Utils.isSmartphoneHorizontal() || Utils.isTabletVertical()) {
                    next();  // 通常通り遷移
                    return;
                }
                // それ以外の画面サイズでは全般設定にリダイレクト
                next({path: '/settings/general/'});
            }
        },
        {
            path: '/settings/general',
            name: 'Settings General',
            component: () => import('@/views/Settings/General.vue'),
        },
        {
            path: '/settings/quality',
            name: 'Settings Quality',
            component: () => import('@/views/Settings/Quality.vue'),
        },
        {
            path: '/settings/caption',
            name: 'Settings Caption',
            component: () => import('@/views/Settings/Caption.vue'),
        },
        {
            path: '/settings/data-broadcasting',
            name: 'Settings Data Broadcasting',
            component: () => import('@/views/Settings/DataBroadcasting.vue'),
        },
        {
            path: '/settings/capture',
            name: 'Settings Capture',
            component: () => import('@/views/Settings/Capture.vue'),
        },
        {
            path: '/settings/account',
            name: 'Settings Account',
            component: () => import('@/views/Settings/Account.vue'),
        },
        {
            path: '/settings/jikkyo',
            name: 'Settings Jikkyo',
            component: () => import('@/views/Settings/Jikkyo.vue'),
        },
        {
            path: '/settings/bangumi',
            name: 'Settings Bangumi',
            component: () => import('@/views/Settings/Bangumi.vue'),
        },
        {
            path: '/settings/twitter',
            name: 'Settings Twitter',
            component: () => import('@/views/Settings/Twitter.vue'),
        },
        {
            path: '/settings/server',
            name: 'Settings Server',
            component: () => import('@/views/Settings/Server.vue'),
        },
        {
            path: '/login/',
            name: 'Login',
            component: () => import('@/views/Login.vue'),
        },
        {
            path: '/pair/',
            name: 'Device Pairing',
            component: () => import('@/views/Pair.vue'),
        },
        {
            path: '/register/',
            name: 'Register',
            component: () => import('@/views/Register.vue'),
        },
        {
            path: '/:pathMatch(.*)*',
            name: 'NotFound',
            component: () => import('@/views/NotFound.vue'),
        },
    ],

    // ページ遷移時のスクロールの挙動の設定
    scrollBehavior(to, from, savedPosition) {
        if (savedPosition) {
            // 戻る/進むボタンが押されたときは保存されたスクロール位置を使う
            return savedPosition;
        } else if (to.name === 'Series Home' && from.name === 'Series Home') {
            // 同じシリーズ一覧上で展開状態だけを切り替える場合は、カードの画面内位置を維持する
            return false;
        } else if (to.name === 'On Air Series' && from.name === 'On Air Series') {
            // 放送中一覧でも、展開状態だけの切り替えではスクロール位置を変えない
            return false;
        } else {
            // それ以外は常に先頭にスクロールする
            return {top: 0, left: 0};
        }
    }
});

// ルーティングの変更時に View Transitions API を適用する
// ref: https://developer.mozilla.org/ja/docs/Web/API/View_Transitions_API
router.beforeResolve(async (to, from, next) => {
    // テレビが選択されている間は視聴ページをローカルで開かず、選択中の Komorebi へ再生対象だけを送る。
    const selectedDeviceId = useSettingsStore().settings.selected_remote_device_id;
    let remoteCommand: Extract<RemoteCommand, {type: 'OpenLive' | 'OpenRecording'}> | null = null;
    if (selectedDeviceId !== null && to.name === 'TV Watch' && typeof to.params.display_channel_id === 'string') {
        // 選択中テレビへ未対応のチャンネルを送信しない。元のカードとフォーカスを保持する。
        const channel = Object.values(useChannelsStore().channels_list).flat()
            .find(item => item.display_channel_id === to.params.display_channel_id)
            ?? await Channels.fetch(to.params.display_channel_id);
        if (channel === null || channel.capabilities.remote_playback === false) {
            Message.warning(channel === null ? 'チャンネル情報を確認できませんでした。再試行してください。' :
                'このチャンネルはテレビへの送信に対応していません。テレビ操作メニューで接続を解除するとブラウザで視聴できます。');
            next(false);
            return;
        }
        remoteCommand = {type: 'OpenLive', display_channel_id: to.params.display_channel_id};
    } else if (selectedDeviceId !== null && to.name === 'Videos Watch' && typeof to.params.video_id === 'string') {
        const recordedProgramId = Number(to.params.video_id);
        if (Number.isInteger(recordedProgramId)) {
            const seekQuery = Array.isArray(to.query.t) ? to.query.t[0] : to.query.t;
            const requestedSeekSeconds = typeof seekQuery === 'string' ? Number(seekQuery) : null;
            const positionSeconds = requestedSeekSeconds !== null && Number.isFinite(requestedSeekSeconds) &&
                requestedSeekSeconds >= 0
                ? requestedSeekSeconds
                : 0;
            remoteCommand = {
                type: 'OpenRecording',
                recorded_program_id: recordedProgramId,
                position_seconds: positionSeconds,
            };
        }
    }
    if (selectedDeviceId !== null && remoteCommand !== null) {
        const sent = await RemoteControl.sendOpenCommand(selectedDeviceId, remoteCommand);
        if (sent === true) {
            Message.success('テレビへ再生を送信しました。');
        }
        // 送信失敗でも視聴先をブラウザへ変えず、元のカードから再試行できるようにする。
        next(false);
        return;
    }

    // View Transition API を適用しないルートの prefix
    // to と from の両方のパスがこの prefix で始まる場合は View Transition API を適用しない
    const no_transition_routes = [
        '/tv/watch/',
        '/videos/watch/',
    ];
    if (document.startViewTransition && !no_transition_routes.some((route) => to.path.startsWith(route) && from.path.startsWith(route))) {
        document.startViewTransition(() => {
            next();
        });
    } else {
        next();
    }
});

export default router;
