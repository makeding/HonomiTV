<template>
    <div class="recorded-program-menu" :class="`recorded-program-menu--${variant.toLowerCase()}`">
        <v-menu location="bottom end" :close-on-content-click="true">
            <template v-slot:activator="{ props: menuProps }">
                <button v-ripple class="recorded-program-menu__button" type="button"
                    v-bind="menuProps" aria-label="録画番組の操作メニュー"
                    @click.prevent.stop @mousedown.prevent.stop>
                    <svg width="19px" height="19px" viewBox="0 0 16 16">
                        <path fill="currentColor" d="M9.5 13a1.5 1.5 0 1 1-3 0a1.5 1.5 0 0 1 3 0m0-5a1.5 1.5 0 1 1-3 0a1.5 1.5 0 0 1 3 0m0-5a1.5 1.5 0 1 1-3 0a1.5 1.5 0 0 1 3 0" />
                    </svg>
                </button>
            </template>
            <v-list density="compact" bg-color="background-lighten-1" class="recorded-program-menu__list">
                <v-list-item @click="showOfflineDownload = true"
                    :disabled="program.recorded_video.status === 'Recording'">
                    <template v-slot:prepend>
                        <Icon icon="fluent:cloud-arrow-down-20-regular" width="20px" height="20px" />
                    </template>
                    <v-list-item-title class="ml-3">
                        オフライン再生用に保存 ({{offlineMenuSizeLabel}})
                    </v-list-item-title>
                </v-list-item>
                <v-list-item :to="`/captures/recorded/${program.id}`">
                    <template v-slot:prepend>
                        <Icon icon="fluent:image-multiple-20-regular" width="20px" height="20px" />
                    </template>
                    <v-list-item-title class="ml-3">この番組のキャプチャを見る</v-list-item-title>
                </v-list-item>
                <v-list-item @click="showVideoInfo = true">
                    <template v-slot:prepend>
                        <svg width="20px" height="20px" viewBox="0 0 16 16">
                            <path fill="currentColor" d="M8.499 7.5a.5.5 0 1 0-1 0v3a.5.5 0 0 0 1 0zm.25-2a.749.749 0 1 1-1.499 0a.749.749 0 0 1 1.498 0M8 1a7 7 0 1 0 0 14A7 7 0 0 0 8 1M2 8a6 6 0 1 1 12 0A6 6 0 0 1 2 8" />
                        </svg>
                    </template>
                    <v-list-item-title class="ml-3">録画ファイル情報を表示</v-list-item-title>
                </v-list-item>
                <v-list-item @click="downloadVideo" :disabled="program.recorded_video.status === 'Recording'">
                    <template v-slot:prepend>
                        <Icon icon="fluent:arrow-download-24-regular" width="20px" height="20px" />
                    </template>
                    <v-list-item-title class="ml-3">
                        {{program.recorded_video.container_format === 'MMT/TLV'
                            ? '元の TLV をダウンロード'
                            : '録画ファイル本体をダウンロード'}}
                        ({{Utils.formatBytes(program.recorded_video.file_size)}})
                    </v-list-item-title>
                </v-list-item>
                <v-list-item @click="showReanalyzeModal"
                    v-ftooltip="'再生時に必要な録画ファイル情報や番組情報などを解析し直します'">
                    <template v-slot:prepend>
                        <Icon icon="fluent:book-arrow-clockwise-20-regular" width="20px" height="20px" />
                    </template>
                    <v-list-item-title class="ml-3">メタデータを再解析</v-list-item-title>
                </v-list-item>
                <v-list-item @click="regenerateThumbnail"
                    v-ftooltip="'サムネイルのみを再生成します（数分かかります） 変更を反映するにはブラウザキャッシュの削除が必要です'">
                    <template v-slot:prepend>
                        <Icon icon="fluent:image-arrow-counterclockwise-24-regular" width="20px" height="20px" />
                    </template>
                    <v-list-item-title class="ml-3">サムネイルを再生成</v-list-item-title>
                </v-list-item>
                <v-divider />
                <v-list-item class="recorded-program-menu__danger" @click="showDeleteConfirmation"
                    :disabled="program.recorded_video.status === 'Recording'">
                    <template v-slot:prepend>
                        <Icon icon="fluent:delete-20-regular" width="20px" height="20px" />
                    </template>
                    <v-list-item-title class="ml-3">録画ファイルを削除</v-list-item-title>
                </v-list-item>
            </v-list>
        </v-menu>
    </div>

    <RecordedFileInfoDialog :program="program" v-model:show="showVideoInfo" />
    <OfflineVideoDownloadDialog :program="program" v-model:show="showOfflineDownload" />

    <v-dialog v-model="showDeleteConfirmationDialog" max-width="750">
        <v-card>
            <v-card-title class="d-flex justify-center pt-6 font-weight-bold">
                本当に録画ファイルを削除しますか？
            </v-card-title>
            <v-card-text class="pt-2 pb-0">
                <div class="recorded-program-menu__file-path mb-4">{{program.recorded_video.file_path}}</div>
                <div class="text-error-lighten-1 font-weight-bold">
                    この録画ファイルに関連するすべてのデータ (サムネイル / .ts.program.txt / .ts.err を含む) が削除されます。<br>
                    元に戻すことはできません。本当に録画ファイルを削除しますか？
                </div>
            </v-card-text>
            <v-card-actions class="pt-4 px-6 pb-6">
                <v-spacer />
                <v-btn color="text" variant="text" @click="showDeleteConfirmationDialog = false">
                    <Icon icon="fluent:dismiss-16-filled" width="18px" height="18px" />
                    <span class="ml-1">キャンセル</span>
                </v-btn>
                <v-btn class="px-3" color="error" variant="flat" @click="deleteVideo">
                    <Icon icon="fluent:delete-16-regular" width="18px" height="18px" />
                    <span class="ml-1">録画ファイルを削除</span>
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>

    <v-dialog v-model="showReanalyzeConfirmation" max-width="650px" scrollable>
        <v-card>
            <v-card-title class="pt-6 px-6 pb-2">
                <Icon icon="fluent:book-arrow-clockwise-20-regular" width="22px" height="22px" />
                <span class="ml-3">メタデータを再解析</span>
            </v-card-title>
            <v-card-text class="px-6 pb-3">
                <div class="text-subtitle-1 font-weight-bold mb-3">{{program.title}}</div>
                <div class="recorded-program-menu__file-path mb-4">{{program.recorded_video.file_path}}</div>
                <div class="mb-4">
                    再生時に必要な録画ファイル情報や番組情報などを解析し直します。<br>
                    複数のチャンネルが含まれる録画ファイルの場合、特定のチャンネルを選択して解析できます。
                </div>
                <div v-if="availableChannels && availableChannels.length > 1" class="mb-4">
                    <div class="text-subtitle-2 mb-2">解析するチャンネルを選択してください:</div>
                    <v-radio-group v-model="selectedServiceID" hide-details>
                        <v-radio label="自動選択（推奨）" :value="null" />
                        <v-radio v-for="channel in availableChannels" :key="channel.service_id"
                            :label="`${channel.channel_name} (Service ID: ${channel.service_id})`"
                            :value="channel.service_id" />
                    </v-radio-group>
                </div>
                <div v-else-if="availableChannels && availableChannels.length === 1" class="mb-4">
                    <div class="text-subtitle-2">利用可能なチャンネル:</div>
                    <div>
                        {{availableChannels[0].channel_name}}
                        (Service ID: {{availableChannels[0].service_id}})
                    </div>
                </div>
                <div v-if="loadingChannels" class="d-flex align-center mb-4">
                    <v-progress-circular indeterminate size="20" />
                    <span class="ml-2">利用可能なチャンネルを取得中...</span>
                </div>
            </v-card-text>
            <v-card-actions class="pt-4 px-6 pb-6">
                <v-spacer />
                <v-btn variant="text" @click="cancelReanalyzeModal">キャンセル</v-btn>
                <v-btn color="secondary" variant="flat" @click="executeReanalyze"
                    :disabled="loadingChannels">
                    再解析を開始
                </v-btn>
            </v-card-actions>
        </v-card>
    </v-dialog>
</template>
<script lang="ts" setup>

import { computed, onBeforeUnmount, ref } from 'vue';

import OfflineVideoDownloadDialog from '@/components/Videos/Dialogs/OfflineVideoDownloadDialog.vue';
import RecordedFileInfoDialog from '@/components/Videos/Dialogs/RecordedFileInfoDialog.vue';
import Message from '@/message';
import OfflineVideos from '@/services/OfflineVideos';
import Videos, { type IRecordedProgram } from '@/services/Videos';
import useUserStore from '@/stores/UserStore';
import Utils, { PlayerUtils } from '@/utils';

const props = withDefaults(defineProps<{
    program: IRecordedProgram;
    variant?: 'List' | 'Thumbnail';
}>(), {
    variant: 'List',
});

const emit = defineEmits<{
    (e: 'deleted', id: number): void;
}>();

const showVideoInfo = ref(false);
const showOfflineDownload = ref(false);
const showDeleteConfirmationDialog = ref(false);
const showReanalyzeConfirmation = ref(false);
const availableChannels = ref<Array<{service_id: number, channel_name: string}> | null>(null);
const selectedServiceID = ref<number | null>(null);
const loadingChannels = ref(false);
let cancelChannelFetch = false;

const offlineMenuSizeLabel = computed(() => {
    return OfflineVideos.formatDefaultMenuSizeLabel(props.program, PlayerUtils.isHEVCVideoSupported());
});

const downloadVideo = () => {
    window.location.href = `${Utils.api_base_url}/videos/${props.program.id}/download`;
};

const showReanalyzeModal = async () => {
    cancelChannelFetch = true;
    showReanalyzeConfirmation.value = true;
    loadingChannels.value = true;
    selectedServiceID.value = null;
    cancelChannelFetch = false;
    try {
        const channels = await Videos.fetchVideoAvailableChannels(props.program.id);
        if (cancelChannelFetch === false) availableChannels.value = channels;
    } catch (error) {
        console.error('Failed to fetch available channels:', error);
        if (cancelChannelFetch === false) availableChannels.value = [];
    } finally {
        if (cancelChannelFetch === false) loadingChannels.value = false;
    }
};

const cancelReanalyzeModal = () => {
    cancelChannelFetch = true;
    loadingChannels.value = false;
    showReanalyzeConfirmation.value = false;
};

const executeReanalyze = async () => {
    showReanalyzeConfirmation.value = false;
    Message.success('メタデータの再解析を開始します。完了までしばらくお待ちください。');
    const result = await Videos.reanalyzeVideo(props.program.id, selectedServiceID.value ?? undefined);
    if (result === true) Message.success('メタデータの再解析が完了しました。');
};

const regenerateThumbnail = async () => {
    Message.success('サムネイルの再生成を開始しました。完了までしばらくお待ちください。');
    const result = await Videos.regenerateThumbnail(props.program.id);
    if (result === true) Message.success('サムネイルの再生成が完了しました。');
};

const showDeleteConfirmation = () => {
    const userStore = useUserStore();
    if (userStore.user === null || userStore.user.is_admin === false) {
        Message.warning('録画ファイルを削除するには管理者権限が必要です。\n管理者アカウントでログインし直してください。');
        return;
    }
    showDeleteConfirmationDialog.value = true;
};

const deleteVideo = async () => {
    showDeleteConfirmationDialog.value = false;
    Message.info('録画ファイルの削除を開始します。完了までしばらくお待ちください。');
    const result = await Videos.deleteVideo(props.program.id);
    if (result === true) {
        Message.success('録画ファイルを削除しました。');
        emit('deleted', props.program.id);
    }
};

onBeforeUnmount(() => {
    cancelChannelFetch = true;
});

</script>
<style lang="scss" scoped>

.recorded-program-menu {
    display: flex;
    align-items: center;
    justify-content: center;
    flex: 0 0 auto;
    cursor: pointer;

    &--list {
        position: absolute;
        top: 65%;
        right: 12px;
        transform: translateY(-50%);
        @include tablet-vertical { right: 6px; }
        @include smartphone-horizontal { right: 6px; }
        @include smartphone-vertical { right: 4px; }
    }

    &--thumbnail {
        position: absolute;
        top: 5px;
        right: 5px;
        z-index: 3;
        color: white;
        background: rgb(0 0 0 / 48%);
        border-radius: 50%;
        box-shadow: 0 1px 5px rgb(0 0 0 / 45%);
    }

    &__button {
        position: relative;
        display: flex;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        padding: 0;
        color: inherit;
        background: transparent;
        border: 0;
        border-radius: 50%;
        transition: color 0.15s ease;
        user-select: none;
        @include tablet-vertical { width: 28px; height: 28px; }
        @include smartphone-horizontal { width: 28px; height: 28px; }
        @include smartphone-vertical { width: 28px; height: 28px; }

        &::before {
            position: absolute;
            inset: 0;
            content: '';
            color: inherit;
            pointer-events: none;
            background-color: currentColor;
            border-radius: inherit;
            opacity: 0;
            transition: opacity 0.2s cubic-bezier(0.4, 0, 0.6, 1);
        }
        &:hover::before { opacity: 0.15; }
        @media (hover: none) { &:hover::before { opacity: 0; } }
    }

    &__list {
        :deep(.v-list-item-title) { font-size: 14px !important; text-autospace: normal; }
        :deep(.v-list-item) { min-height: 36px !important; }
    }

    &__danger { color: rgb(var(--v-theme-error)) !important; }

    &__file-path {
        padding: 12px;
        font-size: 14px;
        word-break: break-all;
        white-space: pre-wrap;
        background-color: rgb(var(--v-theme-background-lighten-1));
        border-radius: 4px;
    }
}

</style>
