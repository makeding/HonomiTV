<template>
    <v-menu v-if="isLoggedIn" v-model="isOpen"
        :target="remoteDeviceActivatorElement ?? undefined" location="bottom end" :close-on-content-click="false"
        :persistent="isPinned" no-click-animation
        :offset="8" transition="fade-transition">
        <v-list class="remote-device-menu" density="compact" elevation="8" bg-color="background-lighten-1">
            <v-list-item class="remote-device-menu__header" title="テレビで再生">
                <template #append>
                    <v-btn icon size="small" variant="text" :color="isPinned ? 'primary' : undefined"
                        :aria-label="isPinned ? 'テレビ操作メニューの固定を解除' : 'テレビ操作メニューを固定'" @click="togglePinned">
                        <Icon :icon="isPinned ? 'fluent:pin-20-filled' : 'fluent:pin-20-regular'" width="20px" />
                    </v-btn>
                    <v-btn icon size="small" variant="text" aria-label="テレビ一覧を更新" :loading="isLoading" @click="refreshDevices()">
                        <Icon icon="fluent:arrow-clockwise-20-regular" width="21px" />
                    </v-btn>
                </template>
            </v-list-item>

            <v-progress-linear v-if="isLoading" color="primary" indeterminate />
            <template v-else-if="devices.length > 0">
                <v-list-item v-for="device in devices" :key="device.device_id" :active="device.device_id === selectedDeviceId"
                    color="primary" @click="selectDevice(device)">
                    <template #prepend>
                        <Icon icon="fluent:tv-20-regular" width="23px" class="mr-3" />
                    </template>
                    <v-list-item-title>{{ device.device_name }}</v-list-item-title>
                    <v-list-item-subtitle>オンライン</v-list-item-subtitle>
                    <template #append>
                        <Icon v-if="device.device_id === selectedDeviceId" icon="fluent:checkmark-20-filled" width="21px" />
                    </template>
                </v-list-item>
            </template>
            <v-list-item v-else lines="two">
                <template #prepend>
                    <Icon icon="fluent:tv-off-20-regular" width="23px" class="mr-3" />
                </template>
                <v-list-item-title>オンラインのテレビがありません</v-list-item-title>
                <v-list-item-subtitle>Komorebi を起動してペアリングしてください</v-list-item-subtitle>
            </v-list-item>

            <template v-if="selectedDevice !== null && selectedPlaybackState.content_type !== 'Idle'">
                <v-divider class="my-2" />
                <div class="remote-device-menu__now-playing">
                    <div v-if="selectedPlaybackState.artwork_url" class="remote-device-menu__artwork">
                        <img :src="selectedPlaybackState.artwork_url" alt="" />
                    </div>
                    <div class="remote-device-menu__media-info">
                        <div class="remote-device-menu__section-title">
                            {{ selectedPlaybackState.content_type === 'Live' ? 'ライブ再生中' : '録画番組を再生中' }}
                        </div>
                        <div v-if="selectedPlaybackState.title" class="remote-device-menu__media-title">
                            {{ selectedPlaybackState.title }}
                        </div>
                        <div v-if="selectedPlaybackState.subtitle" class="remote-device-menu__media-subtitle">
                            {{ selectedPlaybackState.subtitle }}
                        </div>
                    </div>
                </div>
                <div v-if="isSeekBarAvailable" class="remote-device-menu__seek-bar" :style="{'--remote-cm-track': cmTrackGradient}">
                    <v-slider :model-value="seekBarPositionSeconds" :min="0" :max="totalDurationSeconds" :step="1"
                        color="primary" hide-details density="compact" aria-label="再生位置"
                        @start="onSeekBarScrubStart" @update:model-value="onSeekBarScrubUpdate" @end="onSeekBarScrubEnd" />
                    <div class="remote-device-menu__seek-bar-times">
                        <span>{{ formatPlaybackTime(seekBarPositionSeconds) }}</span>
                        <span>{{ selectedPlaybackState.is_chase_playback === true ? '録画中' : formatPlaybackTime(totalDurationSeconds) }}</span>
                    </div>
                </div>
                <div class="remote-device-menu__controls">
                    <v-btn icon size="small" variant="text" :disabled="canSeek === false || hasChapters === false"
                        aria-label="前のチャプターへ" @click="sendControl({type: 'SkipChapter', direction: 'Previous'})">
                        <Icon icon="fluent:previous-20-filled" width="20px" />
                    </v-btn>
                    <v-btn icon size="small" variant="text" :disabled="canSeek === false" aria-label="10秒戻る"
                        @click="sendControl({type: 'SeekRelative', delta_seconds: -10})">
                        <Icon icon="fluent:arrow-counterclockwise-20-regular" width="22px" />
                    </v-btn>
                    <v-btn icon size="small" variant="text" :aria-label="selectedPlaybackState.is_playing ? '一時停止' : '再生'"
                        @click="sendControl({type: selectedPlaybackState.is_playing ? 'Pause' : 'Play'})">
                        <Icon :icon="selectedPlaybackState.is_playing ? 'fluent:pause-20-filled' : 'fluent:play-20-filled'" width="24px" />
                    </v-btn>
                    <v-btn icon size="small" variant="text" :disabled="canSeek === false" aria-label="10秒進む"
                        @click="sendControl({type: 'SeekRelative', delta_seconds: 10})">
                        <Icon icon="fluent:arrow-clockwise-20-regular" width="22px" />
                    </v-btn>
                    <v-btn icon size="small" variant="text" :disabled="canSeek === false || hasChapters === false"
                        aria-label="次のチャプターへ" @click="sendControl({type: 'SkipChapter', direction: 'Next'})">
                        <Icon icon="fluent:next-20-filled" width="20px" />
                    </v-btn>
                    <v-btn icon size="small" variant="text" aria-label="停止" @click="sendControl({type: 'Stop'})">
                        <Icon icon="fluent:stop-20-filled" width="22px" />
                    </v-btn>
                </div>
                <div v-if="cmSkipMode !== null" class="remote-device-menu__cm-skip">
                    <div class="remote-device-menu__section-title">CM スキップ</div>
                    <div class="remote-device-menu__cm-skip-row">
                        <v-btn-toggle :model-value="cmSkipMode" density="compact" variant="outlined" divided
                            color="primary" mandatory class="remote-device-menu__cm-skip-modes"
                            @update:model-value="onCMSkipModeSelect">
                            <v-btn v-for="item in CM_SKIP_MODE_LABELS" :key="item.mode" :value="item.mode" size="small">
                                {{ item.label }}
                            </v-btn>
                        </v-btn-toggle>
                        <v-btn size="small" variant="tonal" color="primary" :disabled="currentCMSection === null"
                            @click="sendControl({type: 'SkipCM'})">
                            本編へ
                        </v-btn>
                    </div>
                    <div v-if="hasChapters === false" class="remote-device-menu__hint">
                        この録画には CM 区間の判定結果がありません。
                    </div>
                </div>
            </template>

            <template v-if="selectedDevice !== null">
                <v-divider class="my-2" />
                <div class="remote-device-menu__volume">
                    <div class="remote-device-menu__section-title">音量</div>
                    <div class="remote-device-menu__controls">
                        <v-btn icon size="small" variant="text" :disabled="selectedPlaybackState.can_adjust_volume === false" aria-label="音量を下げる"
                            @click="sendControl({type: 'VolumeDown'})">
                            <Icon icon="fluent:speaker-1-20-filled" width="22px" />
                        </v-btn>
                        <v-btn icon size="small" variant="text" :disabled="selectedPlaybackState.can_adjust_volume === false" aria-label="ミュートを切り替える"
                            @click="sendControl({type: 'VolumeMute'})">
                            <Icon icon="fluent:speaker-mute-20-filled" width="22px" />
                        </v-btn>
                        <v-btn icon size="small" variant="text" :disabled="selectedPlaybackState.can_adjust_volume === false" aria-label="音量を上げる"
                            @click="sendControl({type: 'VolumeUp'})">
                            <Icon icon="fluent:speaker-2-20-filled" width="22px" />
                        </v-btn>
                    </div>
                    <div v-if="selectedPlaybackState.can_adjust_volume === false" class="remote-device-menu__volume-unavailable">
                        テレビが固定音量として報告しています。テレビまたはオーディオ機器側で音量を操作してください。
                    </div>
                </div>
            </template>

            <template v-if="selectedDeviceId !== null">
                <v-divider class="my-2" />
                <v-list-item title="接続を解除" @click="disconnect">
                    <template #prepend>
                        <Icon icon="fluent:link-dismiss-20-regular" width="22px" class="mr-3" />
                    </template>
                </v-list-item>
            </template>
        </v-list>
    </v-menu>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, ref, watch } from 'vue';

import RemoteControl, {
    type IRemoteDevice,
    type IRemotePlaybackState,
    type RemoteCMSkipMode,
    type RemoteCommand,
} from '@/services/RemoteControl';
import {
    remoteDeviceActivatorElement,
    remoteDeviceMenuOpenRequest,
    selectedRemoteDeviceName,
} from '@/services/RemoteControlUI';
import useSettingsStore from '@/stores/SettingsStore';
import Utils from '@/utils';

// 再生位置を補間するための更新間隔。テレビ側の State 送信は 5 秒周期なので、
// この間隔で基点からの経過時間を足し込んで進捗バーを滑らかに進める
const PLAYBACK_POSITION_INTERPOLATION_INTERVAL_MS = 250;
// シーク要求を送ってからテレビ側が新しい位置を報告するまで、楽観的に表示を保持する上限
const PENDING_SEEK_TIMEOUT_MS = 5000;
// シーク先と報告された位置がこの秒数以内なら、要求が反映されたとみなして楽観的表示をやめる。
// TS のキーフレーム単位でしか着地できないため、完全一致では待ち続けてしまう
const PENDING_SEEK_SETTLED_TOLERANCE_SECONDS = 5;

const CM_SKIP_MODE_LABELS: {mode: RemoteCMSkipMode; label: string;}[] = [
    {mode: 'Off', label: 'オフ'},
    {mode: 'Manual', label: '手動'},
    {mode: 'Auto', label: '自動'},
];

const settingsStore = useSettingsStore();
const isOpen = ref(false);
const isLoading = ref(false);
const devices = ref<IRemoteDevice[]>([]);
const isLoggedIn = computed(() => Utils.getAccessToken() !== null);
const selectedDeviceId = computed(() => settingsStore.settings.selected_remote_device_id);
const isPinned = computed(() => settingsStore.settings.remote_control_menu_pinned);
const selectedDevice = computed(() => devices.value.find((device) => device.device_id === selectedDeviceId.value) ?? null);
const selectedDeviceName = computed(() => selectedDevice.value?.device_name ?? null);
const selectedPlaybackState = computed<IRemotePlaybackState>(() => {
    return selectedDevice.value?.state as unknown as IRemotePlaybackState ?? {content_type: 'Idle'};
});

// ***** 進捗バー *****

// 補間の基点。テレビから届いた再生位置と、それを受け取ったブラウザ側の時刻を対にして保持する。
// 時刻の比較をブラウザ内で完結させることで、テレビとブラウザの時計のズレを気にせずに済む
const playbackPositionAnchor = ref<{state_sequence: number; position_seconds: number; measured_at: number;} | null>(null);
// 補間を再計算させるためだけの時計。PLAYBACK_POSITION_INTERPOLATION_INTERVAL_MS ごとに進む
const interpolationClock = ref(0);
// ドラッグ中はテレビからの報告を無視し、つまみを指に追従させる
const isScrubbing = ref(false);
const scrubPositionSeconds = ref(0);
// シーク要求の送信直後は、テレビが新しい位置を報告するまで要求した位置を表示し続ける。
// そうしないと補間が古い位置から進み続け、つまみが一度戻ってから飛ぶように見えてしまう
const pendingSeek = ref<{position_seconds: number; requested_at: number;} | null>(null);
let interpolationTimer: number | null = null;

const totalDurationSeconds = computed(() => selectedPlaybackState.value.duration_seconds ?? 0);
const chapters = computed(() => selectedPlaybackState.value.chapters ?? []);
const hasChapters = computed(() => chapters.value.length > 0);
const cmSkipMode = computed(() => selectedPlaybackState.value.cm_skip_mode ?? null);
const canSeek = computed(() => selectedPlaybackState.value.can_seek === true);
const isSeekBarAvailable = computed(() => canSeek.value === true && totalDurationSeconds.value > 0);

/** 補間を効かせた、いま表示すべき再生位置 */
const interpolatedPositionSeconds = computed(() => {
    const state = selectedPlaybackState.value;
    const anchor = playbackPositionAnchor.value;
    if (anchor === null) return state.position_seconds ?? 0;
    // 一時停止中は位置が進まないので基点をそのまま使う
    if (state.is_playing !== true) return anchor.position_seconds;
    // interpolationClock を参照することで、タイマーが進むたびにこの computed が再評価される
    const elapsedSeconds = Math.max(0, (interpolationClock.value - anchor.measured_at) / 1000);
    return anchor.position_seconds + (elapsedSeconds * (state.playback_rate ?? 1));
});

/** 進捗バーのつまみの位置。ドラッグ中とシーク要求中はそちらを優先する */
const seekBarPositionSeconds = computed(() => {
    if (isScrubbing.value === true) return scrubPositionSeconds.value;
    if (pendingSeek.value !== null) return pendingSeek.value.position_seconds;
    const duration = totalDurationSeconds.value;
    return Math.min(Math.max(interpolatedPositionSeconds.value, 0), duration > 0 ? duration : Number.MAX_SAFE_INTEGER);
});

/** つまみがいま CM 区間の中にあるか。CM スキップボタンの有効・無効はこれで決まる */
const currentCMSection = computed(() => {
    const position = seekBarPositionSeconds.value;
    return chapters.value.find((chapter) =>
        chapter.is_cm && position >= chapter.start_seconds && position < chapter.end_seconds) ?? null;
});

/**
 * CM 区間を進捗バーのトラックへ塗り分けるための linear-gradient。
 *
 * Vuetify の v-slider はトラックを分割できないため、トラックの background-image として重ねている。
 * オーバーレイ要素を重ねる方式と違い、トラックの実寸を自前で計算せずに済む。
 */
const cmTrackGradient = computed<string>(() => {
    const duration = totalDurationSeconds.value;
    const cmSections = chapters.value.filter((chapter) => chapter.is_cm);
    if (duration <= 0 || cmSections.length === 0) return 'none';
    const colorStops: string[] = [];
    let cursorPercent = 0;
    // 区間は開始時刻順に並んでいる前提だが、念のため並べ替えてから重ならないように詰めていく
    for (const section of [...cmSections].sort((a, b) => a.start_seconds - b.start_seconds)) {
        const startPercent = Math.min(Math.max((section.start_seconds / duration) * 100, 0), 100);
        const endPercent = Math.min(Math.max((section.end_seconds / duration) * 100, 0), 100);
        if (endPercent <= Math.max(startPercent, cursorPercent)) continue;
        const clampedStart = Math.max(startPercent, cursorPercent);
        colorStops.push(`transparent ${cursorPercent}%`, `transparent ${clampedStart}%`);
        colorStops.push(`rgb(var(--v-theme-error)) ${clampedStart}%`, `rgb(var(--v-theme-error)) ${endPercent}%`);
        cursorPercent = endPercent;
    }
    if (colorStops.length === 0) return 'none';
    colorStops.push(`transparent ${cursorPercent}%`, 'transparent 100%');
    return `linear-gradient(to right, ${colorStops.join(', ')})`;
});

/** 再生位置と長さの表示。1 時間以上の録画では h:mm:ss、それ未満は m:ss にする */
function formatPlaybackTime(seconds: number): string {
    const totalSeconds = Math.max(0, Math.floor(seconds));
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const remainingSeconds = totalSeconds % 60;
    if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${remainingSeconds.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${remainingSeconds.toString().padStart(2, '0')}`;
}

function onCMSkipModeSelect(mode: RemoteCMSkipMode | null | undefined): void {
    // mandatory を付けていても選択解除が飛んでくる経路があるため、未選択はそのまま無視する。
    // 同じモードを選び直したときも送らない (テレビ側でトーストが無駄に出るため)
    if (mode === null || mode === undefined || mode === cmSkipMode.value) return;
    void sendControl({type: 'SetCMSkipMode', mode});
}

function onSeekBarScrubStart(): void {
    isScrubbing.value = true;
    scrubPositionSeconds.value = seekBarPositionSeconds.value;
}

function onSeekBarScrubUpdate(position: number): void {
    scrubPositionSeconds.value = position;
}

async function onSeekBarScrubEnd(position: number): Promise<void> {
    isScrubbing.value = false;
    // 要求が反映されるまでの見た目を安定させるため、送信の成否に関わらず先に楽観的な位置を立てる
    pendingSeek.value = {position_seconds: position, requested_at: window.performance.now()};
    const succeeded = await sendControl({type: 'SeekTo', position_seconds: position});
    // 送信自体に失敗したときは楽観的表示を続ける意味がないため、すぐテレビの報告へ戻す
    if (succeeded === false) pendingSeek.value = null;
}

let unsubscribeDevices: (() => void) | null = null;
let reconnectTimer: number | null = null;
let refreshInProgress = false;
let isAwaitingDeviceSnapshot = false;

async function refreshDevices(): Promise<void> {
    if (refreshInProgress === true) return;
    refreshInProgress = true;
    isLoading.value = devices.value.length === 0;
    try {
        const fetchedDevices = await RemoteControl.fetchDevices();
        // 通信失敗時は直前の成功結果を維持し、一瞬だけ「オフライン」に切り替わるのを防ぐ。
        if (fetchedDevices === null) return;
        devices.value = fetchedDevices;
        isAwaitingDeviceSnapshot = false;
    } finally {
        isLoading.value = isAwaitingDeviceSnapshot && devices.value.length === 0;
        refreshInProgress = false;
    }
}

function disconnectDeviceSubscription(): void {
    if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
    }
    unsubscribeDevices?.();
    unsubscribeDevices = null;
    isAwaitingDeviceSnapshot = false;
    isLoading.value = false;
}

function connectDeviceSubscription(): void {
    disconnectDeviceSubscription();
    isAwaitingDeviceSnapshot = devices.value.length === 0;
    isLoading.value = devices.value.length === 0;
    unsubscribeDevices = RemoteControl.subscribeDevices((fetchedDevices) => {
        devices.value = fetchedDevices;
        isAwaitingDeviceSnapshot = false;
        isLoading.value = false;
    }, () => {
        unsubscribeDevices = null;
        // 一時的な切断時だけ3秒後に同じユーザーの部屋へ入り直す。
        if (isOpen.value === true && remoteDeviceActivatorElement.value !== null) {
            isLoading.value = isAwaitingDeviceSnapshot && devices.value.length === 0;
            reconnectTimer = window.setTimeout(connectDeviceSubscription, 3_000);
        } else {
            isAwaitingDeviceSnapshot = false;
            isLoading.value = false;
        }
    });
}

function selectDevice(device: IRemoteDevice): void {
    settingsStore.settings.selected_remote_device_id = device.device_id;
}

async function sendControl(command: Exclude<RemoteCommand, {type: 'OpenLive' | 'OpenRecording'}>): Promise<boolean> {
    if (selectedDeviceId.value === null) return false;
    return await RemoteControl.sendCommand(selectedDeviceId.value, command);
}

function disconnect(): void {
    settingsStore.settings.selected_remote_device_id = null;
    isOpen.value = false;
}

function togglePinned(): void {
    settingsStore.settings.remote_control_menu_pinned = !isPinned.value;
}

function stopPlaybackPositionInterpolation(): void {
    if (interpolationTimer !== null) {
        window.clearInterval(interpolationTimer);
        interpolationTimer = null;
    }
    playbackPositionAnchor.value = null;
    pendingSeek.value = null;
    isScrubbing.value = false;
}

function startPlaybackPositionInterpolation(): void {
    stopPlaybackPositionInterpolation();
    interpolationClock.value = window.performance.now();
    interpolationTimer = window.setInterval(() => {
        interpolationClock.value = window.performance.now();
        // シーク要求がいつまでも反映されない (テレビ側が拒否した・取りこぼした) 場合に備えて必ず解除する
        const pending = pendingSeek.value;
        if (pending !== null && (interpolationClock.value - pending.requested_at) > PENDING_SEEK_TIMEOUT_MS) {
            pendingSeek.value = null;
        }
    }, PLAYBACK_POSITION_INTERPOLATION_INTERVAL_MS);
}

// テレビから新しい再生状態が届くたびに補間の基点を取り直す。
watch(selectedPlaybackState, (state) => {
    const positionSeconds = state.position_seconds;
    if (positionSeconds === undefined) {
        playbackPositionAnchor.value = null;
        return;
    }
    // サーバーはデバイスの接続・切断でも同じ state を再ブロードキャストする。
    // state_sequence が変わっていなければ中身は同じなので、基点を取り直すと進捗バーが巻き戻ってしまう
    const stateSequence = state.state_sequence ?? null;
    const anchor = playbackPositionAnchor.value;
    if (stateSequence !== null && anchor !== null && anchor.state_sequence === stateSequence) return;
    playbackPositionAnchor.value = {
        state_sequence: stateSequence ?? -1,
        position_seconds: positionSeconds,
        measured_at: window.performance.now(),
    };
    // 要求した位置の近くまで進んでいれば、シークは反映されたとみなして楽観的表示をやめる
    const pending = pendingSeek.value;
    if (pending !== null && Math.abs(positionSeconds - pending.position_seconds) <= PENDING_SEEK_SETTLED_TOLERANCE_SECONDS) {
        pendingSeek.value = null;
    }
}, {deep: true});

// 選択中のテレビを切り替えたときは、前のテレビの再生位置を引きずらないように基点を捨てる。
watch(selectedDeviceId, () => {
    playbackPositionAnchor.value = null;
    pendingSeek.value = null;
    isScrubbing.value = false;
});

onBeforeUnmount(() => {
    disconnectDeviceSubscription();
    stopPlaybackPositionInterpolation();
});

watch(selectedDeviceName, (deviceName) => {
    selectedRemoteDeviceName.value = deviceName;
}, {immediate: true});

// 投影ボタンから isOpen を直接変更した場合も含め、メニューの実際の開閉状態へ購読寿命を一致させる。
watch(isOpen, (visible) => {
    if (visible) {
        connectDeviceSubscription();
        // 初回表示では WebSocket の参加と同時に現在のスナップショットも取得し、手動更新を不要にする。
        void refreshDevices();
        // 進捗バーの補間もメニューが開いている間だけ動かす。閉じている間は誰も見ていない
        startPlaybackPositionInterpolation();
    } else {
        disconnectDeviceSubscription();
        stopPlaybackPositionInterpolation();
    }
});

watch(remoteDeviceMenuOpenRequest, () => {
    if (remoteDeviceActivatorElement.value !== null) isOpen.value = true;
});
</script>

<style scoped lang="scss">
.remote-device-menu {
    width: min(320px, calc(100vw - 24px));
    padding: 8px;

    &__header {
        min-height: 46px;
    }

    &__section-title {
        margin-bottom: 4px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 12px;
    }

    &__now-playing {
        display: flex;
        gap: 12px;
        align-items: center;
        padding: 4px 12px 10px;
    }

    &__artwork {
        width: 96px;
        height: 54px;
        overflow: hidden;
        flex: 0 0 auto;
        border-radius: 4px;
        background: rgb(var(--v-theme-background));

        img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
    }

    &__media-info {
        min-width: 0;
    }

    &__media-title,
    &__media-subtitle {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    &__media-title {
        font-size: 14px;
        font-weight: 500;
    }

    &__media-subtitle {
        margin-top: 2px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 12px;
    }

    &__seek-bar {
        padding: 0 16px;

        // CM 区間はトラック上に重ねた擬似要素へ linear-gradient で描く。
        // v-slider のトラックは分割できず、再生済みを塗る __fill が __background を覆ってしまうため、
        // その両方より後ろ (= 上) に来る ::after へ描いて再生位置に関わらず CM 区間が見えるようにする。
        // 位置指定を __background と揃えているので、トラックの実寸を自前で計算する必要はない。
        :deep(.v-slider-track)::after {
            content: '';
            position: absolute;
            inset-inline-start: 0;
            width: 100%;
            height: var(--v-slider-track-size);
            border-radius: inherit;
            background-image: var(--remote-cm-track);
            pointer-events: none;
        }
    }

    &__seek-bar-times {
        display: flex;
        justify-content: space-between;
        margin-top: -2px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 11px;
        font-variant-numeric: tabular-nums;
    }

    &__controls {
        display: flex;
        align-items: center;
        justify-content: space-evenly;
        padding: 0 12px 6px;
    }

    &__cm-skip {
        padding: 2px 12px 8px;
    }

    &__cm-skip-row {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: space-between;
    }

    &__cm-skip-modes {
        height: 30px;
    }

    &__hint {
        margin-top: 6px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 11px;
    }

    &__volume {
        padding-top: 4px;
    }

    &__volume-unavailable {
        padding: 0 12px 8px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 12px;
    }
}
</style>
