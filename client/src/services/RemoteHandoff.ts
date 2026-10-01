import type { RouteLocationNormalizedLoaded, Router } from 'vue-router';

import Message from '@/message';
import RemoteControl, { type RemoteCommand } from '@/services/RemoteControl';
import useChannelsStore from '@/stores/ChannelsStore';
import usePlayerStore from '@/stores/PlayerStore';


/** 視聴画面でブラウザが再生中のコンテンツをテレビへ引き継ぐ (ハンドオフ) ときの結果 */
export type RemoteHandoffResult =
    // 視聴画面にいないため、引き継ぐコンテンツがない (テレビ操作メニューの通常の「テレビを選択する」動作に任せる)
    | 'NotWatching'
    // 現在のチャンネルがテレビへの送信に対応していない (メッセージは表示済み)
    | 'Unsupported'
    // テレビへの送信に失敗した (テレビがオフラインなど)。エラーメッセージは RemoteControl 側で表示済み
    | 'Failure'
    // 引き継ぎが完了し、ブラウザ側の再生を終了して一覧ページへ戻った
    | 'Success';


/**
 * 視聴画面 (TV Watch / Videos Watch) でブラウザが再生中のコンテンツを、指定したテレビへ引き継ぐ (ハンドオフ)。
 *
 * Chrome の Cast や Apple の Handoff と同じく、「いま見ているものをその context のまま別の画面へ移す」ことを責務とする。
 * ライブ視聴なら表示中のチャンネルを、録画視聴なら表示中の録画番組と現在の再生位置をテレビへ送信する。
 * 送信に成功したらブラウザ側の再生を視聴画面から離れることで終了させ、テレビ操作メニューのある一覧ページへ戻る。
 * 以後はヘッダーのテレビ操作メニューがリモコンとして機能する (どのページからも同じ)。
 *
 * Args:
 *     device_id (string): 引き継ぎ先の Komorebi (テレビ) の ID。
 *     route (RouteLocationNormalizedLoaded): 現在のルート。視聴画面かどうかと、再生対象の ID の取得に使う。
 *     router (Router): 引き継ぎ完了後に一覧ページへ戻るために使う。
 * Returns:
 *     RemoteHandoffResult: 引き継ぎの結果。'NotWatching' の場合はメニューの通常動作 (テレビ選択のみ) を継続させる。
 */
export async function handoffCurrentPlaybackToDevice(
    device_id: string,
    route: RouteLocationNormalizedLoaded,
    router: Router,
): Promise<RemoteHandoffResult> {

    // 視聴画面ごとに、引き継ぐコンテンツを表す Open コマンドを組み立てる
    // URL のパラメータを再生対象の一次情報とし、ストアは capability と再生位置の補完に使う
    let command: Extract<RemoteCommand, {type: 'OpenLive' | 'OpenRecording'}> | null = null;
    // 引き継ぎ完了後に戻る一覧ページ。テレビ操作メニュー (リモコン) が常に使えるページ
    let back_path = '/tv/';

    if (route.name === 'TV Watch' && typeof route.params.display_channel_id === 'string') {
        // チャンネルごとの capability を確認し、テレビへの送信に対応していないチャンネル (未対応 IPTV など) は引き継がない。
        // チャンネルストアは視聴画面の初期化時に必ず更新されているため、ここから直接参照できる
        const channel = useChannelsStore().channel.current;
        if (channel.capabilities.remote_playback === false) {
            Message.warning('このチャンネルはテレビへの送信に対応していません。テレビ操作メニューで接続を解除するとブラウザで視聴できます。');
            return 'Unsupported';
        }
        command = {type: 'OpenLive', display_channel_id: route.params.display_channel_id};

    } else if (route.name === 'Videos Watch' && typeof route.params.video_id === 'string' &&
        /^[1-9]\d*$/.test(route.params.video_id)) {
        // URL 上の録画番組 ID と、PlayerController が随時更新する最新の再生位置をそのまま送る。
        // 一時停止中でも timeupdate で更新済みの位置が残っているため、止めていた位置から再生を引き継げる。
        // 追っかけ再生中は数秒程度のズレが生じ得るが、テレビ側はキーフレーム単位で着地するため許容する
        command = {
            type: 'OpenRecording',
            recorded_program_id: Number(route.params.video_id),
            position_seconds: usePlayerStore().video_playback_position,
        };
        back_path = '/videos/';
    }

    // 視聴画面以外にいるときは引き継がない。テレビ操作メニューの通常動作 (操作対象テレビの選択) のみが行われる
    if (command === null) return 'NotWatching';

    // 送信に失敗した場合 (テレビがオフラインなど) はブラウザ側の再生を続けられるよう、視聴画面に留まる
    const sent = await RemoteControl.sendOpenCommand(device_id, command);
    if (sent === false) return 'Failure';

    // フルスクリーン中なら解除してから一覧ページへ戻る。
    // フルスクリーンは視聴画面全体 (document.body) に適用されるため、解除しないと遷移先の一覧ページまでフルスクリーンのままになってしまう。
    // Fullscreen API 非対応の環境 (iOS Safari など) では fullscreenElement も exitFullscreen も存在しないため、存在確認をしてから解除する
    if (document.fullscreenElement && typeof document.exitFullscreen === 'function') {
        await document.exitFullscreen().catch(() => undefined);
    }

    // 引き継ぎ完了。視聴画面を離れることでブラウザ側のストリーミングも破棄される
    Message.success('テレビへ再生を引き継ぎました。');
    await router.push({path: back_path});
    return 'Success';
}
