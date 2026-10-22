<template>
    <component :is="rootTag" v-ripple class="recorded-program"
        v-bind="rootBindings"
        :class="{
            'recorded-program--recording': program.recorded_video.status === 'Recording',
            'recorded-program--failed': program.recorded_video.status === 'AnalysisFailed',
            'recorded-program--offline': forOffline,
            'recorded-program--offline-blocked': isOfflineInteractionBlocked,
            'recorded-program--offline-job-failed': isOfflineJobFailed,
        }">
        <div class="recorded-program__container">
            <div class="recorded-program__thumbnail">
                <img class="recorded-program__thumbnail-image" loading="lazy" decoding="async"
                    :src="offlineThumbnailURL" @error="onOfflineThumbnailError">
                <div class="recorded-program__thumbnail-duration">{{ProgramUtils.getProgramDuration(program)}}</div>
                <div v-if="program.recorded_video.status === 'Recording'" class="recorded-program__thumbnail-status recorded-program__thumbnail-status--recording">
                    <div class="recorded-program__thumbnail-status-dot"></div>
                    追いかけ再生
                </div>
                <div v-else-if="isOfflineJobActive" class="recorded-program__thumbnail-status recorded-program__thumbnail-status--downloading">
                    <Icon icon="fluent:arrow-download-16-filled" width="13px" height="13px" />
                    {{offlineDownloadStateLabel}}
                </div>
                <div v-else-if="isOfflineJobFailed" class="recorded-program__thumbnail-status recorded-program__thumbnail-status--failed">
                    <Icon icon="fluent:error-circle-12-regular" width="15px" height="15px" />
                    保存失敗
                </div>
                <div v-else-if="program.recorded_video.status === 'AnalysisFailed'" class="recorded-program__thumbnail-status recorded-program__thumbnail-status--failed">
                    <Icon icon="fluent:error-circle-12-regular" width="15px" height="15px" />
                    メタデータ解析失敗
                </div>
                <div v-else-if="program.is_partially_recorded" class="recorded-program__thumbnail-status recorded-program__thumbnail-status--partial">
                    ⚠️ 一部のみ録画
                </div>
                <div v-if="watchHistory" class="recorded-program__thumbnail-progress">
                    <div class="recorded-program__thumbnail-progress-bar"
                        :style="`width: ${(watchHistory.last_playback_position / program.recorded_video.duration) * 100}%`">
                    </div>
                </div>
            </div>
            <div class="recorded-program__content">
                <div class="recorded-program__content-header">
                    <div class="recorded-program__content-title"
                        v-html="ProgramUtils.decorateProgramInfo(program, 'title')"></div>
                    <div v-if="offlineQualityLabel !== null || offlineSizeLabel !== null"
                        class="recorded-program__content-chips">
                        <v-chip v-if="offlineQualityLabel !== null"
                            class="recorded-program__quality-chip recorded-program__quality-chip--resolution"
                            color="info" size="small" variant="tonal">
                            {{offlineQualityLabel}}
                        </v-chip>
                        <v-chip v-if="offlineSizeLabel !== null"
                            class="recorded-program__quality-chip recorded-program__quality-chip--size"
                            color="info" size="small" variant="tonal">
                            {{offlineSizeLabel}}
                        </v-chip>
                    </div>
                </div>
                <div class="recorded-program__content-meta">
                    <div class="recorded-program__content-meta-broadcaster" v-if="program.channel">
                        <div class="recorded-program__content-meta-broadcaster-icon">
                            <div class="ch-sprite" :chid="program.channel.id">
                                <img loading="lazy" :src="`${Utils.api_base_url}/channels/${program.channel.id}/logo`">
                            </div>
                        </div>
                        <span class="recorded-program__content-meta-broadcaster-name">Ch: {{program.channel.channel_number}} {{program.channel.name}}</span>
                    </div>
                    <div class="recorded-program__content-meta-broadcaster" v-else>
                        <span class="recorded-program__content-meta-broadcaster-name">チャンネル情報なし</span>
                    </div>
                    <div class="recorded-program__content-meta-time">{{ProgramUtils.getProgramTime(program)}}</div>
                </div>
                <div v-if="isOfflineJobFailed && offlineDownloadJob?.error" class="recorded-program__content-error">
                    {{offlineDownloadJob.error}}
                </div>
                <div v-else class="recorded-program__content-description"
                    v-html="ProgramUtils.decorateProgramInfo(program, 'description')"></div>
            </div>
            <div v-if="!forWatchedHistory && !forOffline" v-ripple class="recorded-program__mylist"
                :class="{'recorded-program__mylist--highlight': isInMylist && !forMylist}"
                v-ftooltip="isInMylist ? 'マイリストから削除する' : 'マイリストに追加する'"
                @click.prevent.stop="toggleMylist"
                @mousedown.prevent.stop="">
                <template v-if="forMylist">
                    <svg width="22px" height="22px" viewBox="0 0 16 16">
                        <path fill="currentColor" d="M7 3h2a1 1 0 0 0-2 0M6 3a2 2 0 1 1 4 0h4a.5.5 0 0 1 0 1h-.564l-1.205 8.838A2.5 2.5 0 0 1 9.754 15H6.246a2.5 2.5 0 0 1-2.477-2.162L2.564 4H2a.5.5 0 0 1 0-1zm1 3.5a.5.5 0 0 0-1 0v5a.5.5 0 0 0 1 0zM9.5 6a.5.5 0 0 0-.5.5v5a.5.5 0 0 0 1 0v-5a.5.5 0 0 0-.5-.5"></path>
                    </svg>
                </template>
                <template v-else>
                    <svg v-if="isInMylist" width="22px" height="22px" viewBox="0 0 16 16">
                        <path fill="currentColor" d="M14.046 3.486a.75.75 0 0 1-.032 1.06l-7.93 7.474a.85.85 0 0 1-1.188-.022l-2.68-2.72a.75.75 0 1 1 1.068-1.053l2.234 2.267l7.468-7.038a.75.75 0 0 1 1.06.032"></path>
                    </svg>
                    <svg v-else width="22px" height="22px" viewBox="0 0 15.2 15.2">
                        <path fill="currentColor" d="M8 2.5a.5.5 0 0 0-1 0V7H2.5a.5.5 0 0 0 0 1H7v4.5a.5.5 0 0 0 1 0V8h4.5a.5.5 0 0 0 0-1H8z"></path>
                    </svg>
                </template>
            </div>
            <div v-if="forOffline && isOfflineJobActive" v-ripple class="recorded-program__mylist"
                role="button" tabindex="0" aria-label="オフライン保存をキャンセルする"
                v-ftooltip="'キャンセル'"
                @click.prevent.stop="cancelOfflineDownload"
                @keydown.enter.prevent.stop="cancelOfflineDownload"
                @keydown.space.prevent.stop="cancelOfflineDownload"
                @mousedown.prevent.stop="">
                <Icon icon="fluent:dismiss-16-regular" width="22px" height="22px" />
            </div>
            <div v-else-if="forOffline && isOfflineJobFailed" v-ripple class="recorded-program__mylist"
                role="button" tabindex="0" aria-label="失敗した保存ジョブを閉じる"
                v-ftooltip="'閉じる'"
                @click.prevent.stop="dismissOfflineDownload"
                @keydown.enter.prevent.stop="dismissOfflineDownload"
                @keydown.space.prevent.stop="dismissOfflineDownload"
                @mousedown.prevent.stop="">
                <Icon icon="fluent:dismiss-16-regular" width="22px" height="22px" />
            </div>
            <div v-if="forWatchedHistory" v-ripple class="recorded-program__mylist"
                v-ftooltip="'視聴履歴から削除する'"
                @click.prevent.stop="removeFromWatchedHistory"
                @mousedown.prevent.stop="">
                <svg width="22px" height="22px" viewBox="0 0 16 16">
                    <path fill="currentColor" d="M7 3h2a1 1 0 0 0-2 0M6 3a2 2 0 1 1 4 0h4a.5.5 0 0 1 0 1h-.564l-1.205 8.838A2.5 2.5 0 0 1 9.754 15H6.246a2.5 2.5 0 0 1-2.477-2.162L2.564 4H2a.5.5 0 0 1 0-1zm1 3.5a.5.5 0 0 0-1 0v5a.5.5 0 0 0 1 0zM9.5 6a.5.5 0 0 0-.5.5v5a.5.5 0 0 0 1 0v-5a.5.5 0 0 0-.5-.5"></path>
                </svg>
            </div>
            <div v-if="forOffline && offlineVideo !== null && isOfflineJobActive === false && isOfflineJobFailed === false" v-ripple class="recorded-program__mylist"
                v-ftooltip="'オフライン保存を削除する'"
                @click.prevent.stop="deleteOfflineVideo"
                @mousedown.prevent.stop="">
                <svg width="22px" height="22px" viewBox="0 0 16 16">
                    <path fill="currentColor" d="M7 3h2a1 1 0 0 0-2 0M6 3a2 2 0 1 1 4 0h4a.5.5 0 0 1 0 1h-.564l-1.205 8.838A2.5 2.5 0 0 1 9.754 15H6.246a2.5 2.5 0 0 1-2.477-2.162L2.564 4H2a.5.5 0 0 1 0-1zm1 3.5a.5.5 0 0 0-1 0v5a.5.5 0 0 0 1 0zM9.5 6a.5.5 0 0 0-.5.5v5a.5.5 0 0 0 1 0v-5a.5.5 0 0 0-.5-.5"></path>
                </svg>
            </div>
            <RecordedProgramMenu v-if="!forOffline || offlineVideo !== null"
                :program="program" @deleted="emit('deleted', $event)" />
        </div>
        <div v-if="displayedOfflineDownloadProgress !== null" class="recorded-program__offline-progress">
            <div class="recorded-program__offline-progress-bar"
                :style="`width: ${displayedOfflineDownloadProgress}%`">
            </div>
        </div>
    </component>
    <!-- オフライン保存削除確認ダイアログ -->
    <v-dialog v-model="showOfflineDeleteConfirmation" max-width="715">
        <v-card>
            <v-card-title class="d-flex justify-center pt-6 font-weight-bold">
                オフライン保存を削除しますか？
            </v-card-title>
            <v-card-text class="pt-2 pb-0">
                <div class="mb-4">
                    <div class="text-h6 text-text mb-2"
                        v-html="ProgramUtils.decorateProgramInfo(program, 'title')"></div>
                    <div class="text-body-2 text-text-darken-1">
                        {{ProgramUtils.getProgramTime(program)}}
                    </div>
                </div>
                <v-alert color="info" variant="tonal">
                    端末に保存したオフライン再生用データだけを削除します。<br>
                    サーバー上の録画ファイルは削除されません。
                </v-alert>
            </v-card-text>
            <v-card-actions class="pt-4 px-6 pb-6">
                <v-spacer />
                <v-btn color="text" variant="text" @click="showOfflineDeleteConfirmation = false">
                    <Icon icon="fluent:dismiss-16-filled" width="18px" height="18px" />
                    <span class="ml-1">キャンセル</span>
                </v-btn>
                <v-btn class="px-3" color="error" variant="flat" :loading="isDeletingOfflineVideo" @click="confirmDeleteOfflineVideo">
                    <Icon icon="fluent:delete-16-regular" width="18px" height="18px" />
                    <span class="ml-1">オフライン保存を削除</span>
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>

</template>
<script lang="ts" setup>

import { ref, computed, watch } from 'vue';

import RecordedProgramMenu from '@/components/Videos/RecordedProgramMenu.vue';
import Message from '@/message';
import OfflineVideos, { type IOfflineDownloadJob, type IOfflineVideo } from '@/services/OfflineVideos';
import { type IRecordedProgram } from '@/services/Videos';
import useSettingsStore from '@/stores/SettingsStore';
import Utils, { ProgramUtils } from '@/utils';

// Props
const props = withDefaults(defineProps<{
    program: IRecordedProgram;
    forMylist?: boolean;
    forWatchedHistory?: boolean;
    forOffline?: boolean;
    offlineVideo?: IOfflineVideo | null;
    offlineDownloadJob?: IOfflineDownloadJob | null;
}>(), {
    forMylist: false,
    forWatchedHistory: false,
    forOffline: false,
    offlineVideo: null,
    offlineDownloadJob: null,
});

// Emits
const emit = defineEmits<{
    (e: 'deleted', id: number): void;
    (e: 'cancelOfflineJob', jobID: string): void;
    (e: 'dismissOfflineJob', jobID: string): void;
}>();

// オフライン保存削除確認ダイアログの表示状態
const showOfflineDeleteConfirmation = ref(false);
// オフライン保存の削除中は確定ボタンの二重操作を防ぐ
const isDeletingOfflineVideo = ref(false);

// マイリストに追加/削除
const settingsStore = useSettingsStore();
const toggleMylist = () => {
    // マイリストに追加されているか確認
    const isInMylist = settingsStore.settings.mylist.some(item => {
        return item.type === 'RecordedProgram' && item.id === props.program.id;
    });

    if (isInMylist) {
        // マイリストから削除
        settingsStore.settings.mylist = settingsStore.settings.mylist.filter(item => {
            return !(item.type === 'RecordedProgram' && item.id === props.program.id);
        });
        Message.show('マイリストから削除しました。');
    } else {
        // マイリストに追加
        settingsStore.settings.mylist.push({
            type: 'RecordedProgram',
            id: props.program.id,
            created_at: Utils.time(),  // 秒単位
        });
        Message.success('マイリストに追加しました。');
    }
};

// マイリストに追加されているか確認
const isInMylist = computed(() => {
    return settingsStore.settings.mylist.some(item => item.type === 'RecordedProgram' && item.id === props.program.id);
});

// 視聴履歴を取得
const watchHistory = computed(() => {
    return settingsStore.settings.watched_history.find(history => history.video_id === props.program.id);
});

// 視聴履歴から削除
const removeFromWatchedHistory = () => {
    settingsStore.settings.watched_history = settingsStore.settings.watched_history.filter(history => {
        return history.video_id !== props.program.id;
    });
    Message.show('視聴履歴から削除しました。');
};

// オフライン保存サムネイルの読み込み失敗時はサーバー側サムネイルへ切り替える
const shouldUseServerThumbnail = ref(false);
// 録画中カードは同じ URL のままなので、一覧の定期更新ごとにだけ再検証する世代番号を保持する
const recordingThumbnailRevision = ref(0);
watch(() => [props.offlineVideo?.generation_id, props.program.id], () => {
    shouldUseServerThumbnail.value = false;
});
watch(() => props.program, () => {
    if (props.program.recorded_video.status === 'Recording') {
        recordingThumbnailRevision.value += 1;
    }
});
const offlineThumbnailURL = computed(() => {
    if (props.offlineVideo === null || shouldUseServerThumbnail.value === true) {
        const baseURL = `${Utils.api_base_url}/videos/${props.program.id}/thumbnail`;
        if (props.program.recorded_video.status === 'Recording') {
            return `${baseURL}?recording_revision=${recordingThumbnailRevision.value}`;
        }
        return baseURL;
    }
    return OfflineVideos.getAssetURL(props.offlineVideo, 'thumbnail.webp');
});
const onOfflineThumbnailError = () => {
    if (props.offlineVideo !== null) {
        shouldUseServerThumbnail.value = true;
    }
};

// 実行中の保存ジョブかどうか
const isOfflineJobActive = computed(() => {
    if (props.offlineDownloadJob === null) return false;
    return ['Waiting', 'Downloading', 'Finalizing'].includes(props.offlineDownloadJob.state);
});

// 失敗した保存ジョブかどうか
const isOfflineJobFailed = computed(() => props.offlineDownloadJob?.state === 'Failed');

// 保存済みデータがなく、保存ジョブだけが存在する場合は再生リンクを無効化する
const isOfflineInteractionBlocked = computed(() => {
    if (props.forOffline !== true) return false;
    if (props.offlineVideo !== null) return false;
    return isOfflineJobActive.value === true || isOfflineJobFailed.value === true;
});

// オフライン保存ページのルート要素 (保存ジョブ実行中は div へ切り替える)
const rootTag = computed(() => isOfflineInteractionBlocked.value === true ? 'div' : 'router-link');

// router-link 利用時だけ遷移先を渡す
const rootBindings = computed(() => {
    if (rootTag.value !== 'router-link') return {};
    return {
        to: props.program.recorded_video.status !== 'AnalysisFailed'
            ? (props.forOffline ? `/videos/watch/${props.program.id}?source=offline` : `/videos/watch/${props.program.id}`)
            : { path: '' },
    };
});

// 保存ジョブの状態ラベル
const offlineDownloadStateLabel = computed(() => {
    if (props.offlineDownloadJob === null) return '';
    return {
        Waiting: '待機中',
        Downloading: 'ダウンロード中',
        Finalizing: '保存処理中',
        Completed: '完了',
        Failed: '失敗',
        Cancelled: 'キャンセル済み',
    }[props.offlineDownloadJob.state];
});

// 保存ジョブまたは保存済み動画から表示する画質ラベル
const offlineQualityLabel = computed(() => {
    if (props.forOffline !== true) return null;
    const quality = props.offlineDownloadJob?.quality ?? props.offlineVideo?.quality ?? null;
    if (quality === null) return null;
    return OfflineVideos.formatQualityLabel(quality);
});

// 保存ジョブまたは保存済み動画から表示する容量ラベル
const offlineSizeLabel = computed(() => {
    if (props.forOffline !== true) return null;

    // 実行中ジョブは選択画質の平均ビットレートから見積もりを表示する
    if (isOfflineJobActive.value === true && props.offlineDownloadJob !== null) {
        return OfflineVideos.formatOfflineSize(
            OfflineVideos.estimateDisplaySizeBytes(
                props.program.recorded_video.duration,
                props.offlineDownloadJob.quality,
                props.program.recorded_video.file_size,
            ),
            true,
        );
    }

    // 保存完了後は実測サイズを確定値として表示する
    if (props.offlineVideo !== null) {
        return OfflineVideos.formatOfflineSize(props.offlineVideo.size_bytes, false);
    }

    // 失敗ジョブは最後に試行した画質の見積もりを表示する
    if (props.offlineDownloadJob !== null) {
        return OfflineVideos.formatOfflineSize(
            OfflineVideos.estimateDisplaySizeBytes(
                props.program.recorded_video.duration,
                props.offlineDownloadJob.quality,
                props.program.recorded_video.file_size,
            ),
            true,
        );
    }

    return null;
});


// 保存ジョブの進捗率 (0〜99)。完了確定前は 100% にしない
const offlineDownloadProgress = computed(() => {
    if (isOfflineJobActive.value === false || props.offlineDownloadJob === null) return null;
    if (props.offlineDownloadJob.state === 'Finalizing') return 99;
    // ジョブ開始時に固定した estimated_size_bytes は表示のたびに再見積もりする
    const estimatedSizeBytes = OfflineVideos.estimateJobSizeBytes(
        props.program.recorded_video.duration,
        props.offlineDownloadJob.quality,
        props.program.recorded_video.file_size,
    );
    if (estimatedSizeBytes === null || estimatedSizeBytes <= 0) return 0;
    return Math.min(99, (props.offlineDownloadJob.downloaded_bytes / estimatedSizeBytes) * 100);
});

// 進捗更新のたびに 0% へ戻ってから伸び直す見え方を避けるため、表示値は単調増加だけを反映する
const displayedOfflineDownloadProgress = ref<number | null>(null);
watch(
    () => [props.offlineDownloadJob?.job_id, offlineDownloadProgress.value] as const,
    ([jobID, progress]) => {
        if (progress === null || jobID === undefined) {
            displayedOfflineDownloadProgress.value = null;
            return;
        }
        const currentProgress = displayedOfflineDownloadProgress.value;
        if (currentProgress === null || progress >= currentProgress) {
            displayedOfflineDownloadProgress.value = progress;
        }
    },
    { immediate: true },
);

// 実行中の保存ジョブをキャンセルする
const cancelOfflineDownload = () => {
    if (props.offlineDownloadJob === null) return;
    emit('cancelOfflineJob', props.offlineDownloadJob.job_id);
};

// 失敗した保存ジョブを一覧から消す
const dismissOfflineDownload = () => {
    if (props.offlineDownloadJob === null) return;
    emit('dismissOfflineJob', props.offlineDownloadJob.job_id);
};

// 端末内の保存データだけを削除することを、専用ダイアログで確認
const deleteOfflineVideo = () => {
    if (props.offlineVideo === null) return;
    showOfflineDeleteConfirmation.value = true;
};

// 確認後にオフライン保存を削除
const confirmDeleteOfflineVideo = async (): Promise<void> => {
    if (props.offlineVideo === null || isDeletingOfflineVideo.value === true) return;
    isDeletingOfflineVideo.value = true;
    try {
        await OfflineVideos.deleteVideo(props.program.id);
    } catch (error) {
        Message.error(error instanceof Error ? error.message : 'オフライン保存を削除できませんでした。');
        return;
    } finally {
        isDeletingOfflineVideo.value = false;
    }
    showOfflineDeleteConfirmation.value = false;
    emit('deleted', props.program.id);
    Message.success('オフライン保存を削除しました。');
};


</script>
<style lang="scss" scoped>

.recorded-program {
    display: flex;
    position: relative;
    width: 100%;
    min-width: 0;  // 一覧側の横幅が狭いときも、カード自身が親要素を押し広げないようにする
    max-width: 100%;
    height: 125px;
    padding: 0px 16px;
    color: rgb(var(--v-theme-text));
    background: rgb(var(--v-theme-background-lighten-1));
    transition: background-color 0.15s;
    text-decoration: none;
    user-select: none;
    box-sizing: border-box;
    cursor: pointer;
    content-visibility: auto;
    contain-intrinsic-height: auto 125px;
    @include smartphone-vertical {
        height: auto;
        padding: 0px 9px;
        contain-intrinsic-height: auto 115px;
    }

    &:hover {
        background: rgb(var(--v-theme-background-lighten-2));
    }
    // タッチデバイスで hover を無効にする
    @media (hover: none) {
        &:hover {
            background: rgb(var(--v-theme-background-lighten-1));
        }
    }

    &__container {
        display: flex;
        align-items: center;
        width: 100%;
        min-width: 0;  // サムネイルと本文を同じ行に収め、長い番組名は本文側の省略表示に任せる
        height: 100%;
        padding: 12px 0px;
        @include smartphone-vertical {
            padding: 8px 0px;
        }
    }

    &__thumbnail {
        display: flex;
        align-items: center;
        flex-shrink: 0;
        aspect-ratio: 16 / 9;
        height: 100%;
        border-radius: 4px;
        overflow: hidden;
        position: relative;
        @include smartphone-vertical {
            width: 120px;
            height: auto;
            aspect-ratio: 3 / 2;
        }

        &-image {
            width: 100%;
            border-radius: 4px;
            aspect-ratio: 16 / 9;
            object-fit: cover;
            @include smartphone-vertical {
                aspect-ratio: 3 / 2;
            }
        }

        &-duration {
            position: absolute;
            right: 4px;
            bottom: 4px;
            padding: 3px 4px;
            border-radius: 2px;
            background: rgba(0, 0, 0, 0.7);
            color: #fff;
            font-size: 11px;
            line-height: 1;
            @include smartphone-vertical {
                font-size: 10.5px;
            }
        }

        &-status {
            display: flex;
            align-items: center;
            gap: 4px;
            position: absolute;
            top: 4px;
            right: 4px;
            padding: 4px 6px;
            border-radius: 2px;
            font-size: 10.5px;
            font-weight: 700;
            line-height: 1;
            background: rgba(var(--v-theme-background-lighten-1), 0.9);
            color: rgba(255, 255, 255, 0.85);

            &--failed {
                gap: 3px;
                svg {
                    color: rgb(var(--v-theme-error));
                }
            }

            &--downloading {
                gap: 3px;
                color: rgb(var(--v-theme-text));
            }

            &-dot {
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: #ff4444;
                animation: blink 1.5s infinite;
            }
        }

        &-progress {
            position: absolute;
            left: 0;
            right: 0;
            bottom: 0;
            height: 3px;
            background: rgba(0, 0, 0, 0.6);

            &-bar {
                height: 100%;
                background: rgb(var(--v-theme-secondary-lighten-1));
                transition: width 0.2s ease;
            }
        }
    }

    &__content {
        display: flex;
        flex-direction: column;
        justify-content: center;
        flex-grow: 1;
        min-width: 0;  // magic!
        margin-left: 16px;
        margin-right: 40px;
        @include tablet-vertical {
            margin-right: 16px;
        }
        @include smartphone-horizontal {
            margin-right: 20px;
        }
        @include smartphone-vertical {
            margin-left: 12px;
            margin-right: 0px;
        }

        &-header {
            display: flex;
            align-items: center;
            gap: 8px;
            min-width: 0;
            @include tablet-vertical {
                align-items: flex-start;
            }
        }

        &-chips {
            display: flex;
            flex-shrink: 0;
            align-items: center;
            gap: 4px;
        }

        &-title {
            min-width: 0;
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
            font-size: 17px;
            font-weight: 600;
            font-feature-settings: "palt" 1;  // 文字詰め
            letter-spacing: 0.07em;  // 字間を少し空ける
            @include tablet-vertical {
                display: -webkit-box;
                font-size: 15px;
                line-height: 1.4;
                white-space: normal;
                -webkit-line-clamp: 2;  // 2行までに制限
                -webkit-box-orient: vertical;
            }
            @include smartphone-horizontal {
                font-size: 14px;
            }
            @include smartphone-vertical {
                display: -webkit-box;
                margin-right: 0;
                font-size: 13px;
                line-height: 1.4;
                white-space: normal;
                -webkit-line-clamp: 2;  // 2行までに制限
                -webkit-box-orient: vertical;
            }
        }

        &-meta {
            display: flex;
            align-items: center;
            margin-top: 4px;
            font-size: 13.8px;
            @include tablet-vertical {
                flex-wrap: wrap;
            }
            @include smartphone-horizontal {
                margin-top: 6px;
                flex-direction: column;
                align-items: flex-start;
            }
            @include smartphone-vertical {
                flex-direction: column;
                align-items: flex-start;
                margin-top: 4px;
                font-size: 12px;
            }

            &-broadcaster {
                display: flex;
                align-items: center;
                min-width: 0;  // magic!

                &-icon {
                    flex-shrink: 0;
                    --ch-sprite-width: 28;
                    --ch-sprite-height: 16;
                    --ch-sprite-border-radius: 2;
                    width: calc(var(--ch-sprite-width) * 1px);
                    height: calc(var(--ch-sprite-height) * 1px);
                    margin-right: 10px;
                    border-radius: calc(var(--ch-sprite-border-radius) * 1px);
                    // 読み込まれるまでのアイコンの背景
                    background: linear-gradient(150deg, rgb(var(--v-theme-gray)), rgb(var(--v-theme-background-lighten-2)));
                    object-fit: cover;
                    @include smartphone-horizontal {
                        margin-right: 8px;
                    }
                    @include smartphone-vertical {
                        margin-right: 4px;
                        --ch-sprite-width: 24;
                        --ch-sprite-height: 14;
                    }
                }

                &-name {
                    color: rgb(var(--v-theme-text-darken-1));
                    overflow: hidden;
                    white-space: nowrap;
                    text-overflow: ellipsis;
                    @include tablet-vertical {
                        font-size: 13px;
                    }
                    @include smartphone-horizontal {
                        font-size: 13px;
                    }
                    @include smartphone-vertical {
                        margin-left: 4px;
                        font-size: 11.5px;
                    }
                }
            }

            &-time {
                display: inline-block;
                flex-shrink: 0;
                margin-left: auto;
                color: rgb(var(--v-theme-text-darken-1));
                height: 16px;
                line-height: 15.5px;
                @include desktop {
                    min-width: 236.5px;
                }
                @include tablet-horizontal {
                    min-width: 236.5px;
                }
                @include tablet-vertical {
                    margin-top: 2px;
                    margin-left: 0px;
                    font-size: 12px;
                }
                @include smartphone-horizontal {
                    margin-top: 2px;
                    margin-left: 0px;
                    font-size: 12px;
                }
                @include smartphone-vertical {
                    margin-top: 1px;
                    margin-left: 0px;
                    font-size: 11px;
                }
            }
        }

        &-description {
            display: -webkit-box;
            margin-top: 6px;
            color: rgb(var(--v-theme-text-darken-1));
            font-size: 11.5px;
            line-height: 1.55;
            overflow-wrap: break-word;
            font-feature-settings: "palt" 1;  // 文字詰め
            letter-spacing: 0.07em;  // 字間を少し空ける
            overflow: hidden;
            -webkit-line-clamp: 2;  // 2行までに制限
            -webkit-box-orient: vertical;
            @include tablet-vertical {
                display: block;
                margin-top: 3.5px;
                font-size: 11px;
                overflow-wrap: normal;
                white-space: nowrap;
                text-overflow: ellipsis;
            }
            @include smartphone-horizontal {
                margin-top: 3.5px;
                font-size: 11px;
            }
            @include smartphone-vertical {
                margin-top: 2.5px;
                margin-right: 12px;
                font-size: 10px;
                line-height: 1.45;
            }
        }

        &-error {
            display: -webkit-box;
            margin-top: 6px;
            color: rgb(var(--v-theme-error));
            font-size: 11.5px;
            line-height: 1.55;
            overflow: hidden;
            -webkit-line-clamp: 2;
            -webkit-box-orient: vertical;
            @include smartphone-vertical {
                margin-top: 2.5px;
                margin-right: 12px;
                font-size: 10px;
                line-height: 1.45;
            }
        }
    }

    &__quality-chip {
        padding: 0px 9px;
        flex-shrink: 0;
        min-width: 0;
        font-size: 12px !important;
        font-weight: 500;
        text-autospace: normal;

        // スマホでは解像度より容量の方が重要なので、720p などの解像度表示は非表示にする
        &--resolution {
            @include smartphone-horizontal {
                display: none !important;
            }
            @include smartphone-vertical {
                display: none !important;
            }
        }

        :deep(.v-chip) {
            height: 22px !important;
            padding: 0 6px !important;
            font-weight: 500 !important;
        }

        :deep(.v-chip__content) {
            overflow: hidden;
            white-space: nowrap;
            text-overflow: ellipsis;
        }

        @include tablet-vertical {
            :deep(.v-chip) {
                height: 22px !important;
                padding: 0 6px !important;
                font-size: 11px !important;
            }
        }
        @include smartphone-horizontal {
            padding: 0 6px;
            height: 18px !important;
            min-height: 18px !important;
            font-size: 10px !important;

            :deep(.v-chip) {
                height: 18px !important;
                padding: 0 6px !important;
                font-size: 10px !important;
            }
        }
        @include smartphone-vertical {
            padding: 0 6px;
            height: 18px !important;
            min-height: 18px !important;
            font-size: 10px !important;

            :deep(.v-chip) {
                height: 18px !important;
                padding: 0 6px !important;
                font-size: 10px !important;
            }
        }
    }

    &__offline-progress {
        position: absolute;
        left: 0;
        right: 0;
        bottom: 0;
        height: 3px;
        background: rgba(0, 0, 0, 0.35);

        &-bar {
            height: 100%;
            background: rgb(var(--v-theme-secondary-lighten-1));
            transition: width 0.2s ease;
        }
    }

    &__mylist {
        display: flex;
        align-items: center;
        justify-content: center;
        flex-shrink: 0;
        position: absolute;
        top: 38%;
        right: 12px;
        transform: translateY(-50%);
        width: 32px;
        height: 32px;
        color: rgb(var(--v-theme-text-darken-1));
        border-radius: 50%;
        transition: color 0.15s ease, background-color 0.15s ease;
        user-select: none;
        cursor: pointer;
        @include tablet-vertical {
            right: 6px;
            width: 28px;
            height: 28px;
            svg {
                width: 18px;
                height: 18px;
            }
        }
        @include smartphone-horizontal {
            right: 6px;
            width: 28px;
            height: 28px;
            svg {
                width: 18px;
                height: 18px;
            }
        }
        @include smartphone-vertical {
            right: 4px;
            width: 28px;
            height: 28px;
            svg {
                width: 18px;
                height: 18px;
            }
        }

        &:before {
            content: "";
            position: absolute;
            top: 0;
            left: 0;
            right: 0;
            bottom: 0;
            border-radius: inherit;
            background-color: currentColor;
            color: inherit;
            opacity: 0;
            transition: opacity 0.2s cubic-bezier(0.4, 0, 0.6, 1);
            pointer-events: none;
        }
        &:hover {
            color: rgb(var(--v-theme-text));
            &:before {
                opacity: 0.15;
            }
        }
        // タッチデバイスで hover を無効にする
        @media (hover: none) {
            &:hover {
                &:before {
                    opacity: 0;
                }
            }
        }

        &--highlight {
            color: rgb(var(--v-theme-primary));
            &:hover {
                color: rgb(var(--v-theme-primary));
            }
        }
    }

    &--offline {
        .recorded-program__content-header {
            @include smartphone-vertical {
                // 右上の画質/容量チップはカード全体ではなく、本文列の右端へそろえる
                position: relative;
            }
        }

        .recorded-program__content-title {
            flex-grow: 1;
            margin-right: 12px;

            @include smartphone-vertical {
                margin-right: 0px;
                padding-right: 72px;  // 容量チップの表示幅分（解像度チップは非表示）
            }
        }

        .recorded-program__content-chips {
            margin-right: -1.5px;  // 錯視対策

            @include smartphone-vertical {
                position: absolute;
                top: 0px;
                right: 1.5px;
            }
        }
    }

    &--failed, &--offline-blocked {
        pointer-events: none;
        &:hover {
            background: rgb(var(--v-theme-background-lighten-1));
        }
        .recorded-program__thumbnail-image,
        .recorded-program__thumbnail-duration,
        .recorded-program__content {
            opacity: 0.65;
        }
        .recorded-program__mylist,
        :deep(.recorded-program-menu) {
            pointer-events: auto;
        }
    }

    &--offline-job-failed {
        &:hover {
            background: rgb(var(--v-theme-background-lighten-1));
        }
    }

    // ダウンロード中・失敗時はドロップダウンを出さず、右端ボタンだけ縦中央へ寄せる
    &--offline-blocked, &--offline-job-failed {
        .recorded-program__mylist {
            top: 49%;
        }
    }
}

.video-info {
    &__item {
        display: flex;
        margin-top: 8px;

        &-label {
            flex-shrink: 0;
            width: 140px;
            color: rgb(var(--v-theme-text-darken-1));
            font-size: 14px;
        }

        &-value {
            flex-grow: 1;
            font-size: 14px;
            word-break: break-word;
        }
    }
}

@keyframes blink {
    0% { opacity: 0; }
    50% { opacity: 1; }
    100% { opacity: 0; }
}

</style>
