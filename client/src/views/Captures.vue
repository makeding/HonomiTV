<template>
    <div class="route-container">
        <HeaderBar />
        <main>
            <Navigation />
            <div class="captures-container-wrapper">
                <SPHeaderBar />
                <div class="captures-container">
                    <Breadcrumbs :crumbs="breadcrumbs" />
                    <!-- ヘッダー: タイトル・件数・ソート -->
                    <div class="captures__header">
                        <h2 class="captures__title">
                            <span class="captures__title-text">{{ pageTitle }}</span>
                            <div class="captures__title-count">{{ (isLoading || loadError) && captures.length === 0 ? '—' : total }}件</div>
                            <!-- フォルダ表示時: 名前変更・削除ボタン -->
                            <template v-if="activeFolderId !== null && folders.some(folder => folder.id === activeFolderId)">
                                <v-btn class="captures__title-action ml-2" variant="flat" color="background-lighten-2"
                                    size="small" aria-label="フォルダ名を変更" @click="openRenameFolderDialogForActive()">
                                    <Icon icon="fluent:rename-20-filled" width="18px" />
                                </v-btn>
                                <v-btn class="captures__title-action ml-1" variant="flat" color="background-lighten-2"
                                    size="small" aria-label="フォルダを削除" @click="openDeleteFolderDialogForActive()">
                                    <Icon icon="fluent:delete-20-filled" width="18px" />
                                </v-btn>
                            </template>
                        </h2>
                        <div class="captures__actions">
                            <v-btn v-if="userStore.is_logged_in" variant="text" :disabled="isLoading || isMutating || captures.length === 0"
                                @click="isSelectionMode ? exitSelectionMode() : isSelectionMode = true">{{ isSelectionMode ? '選択解除' : '選択' }}</v-btn>
                            <v-select class="captures__sort" color="primary" bg-color="background-lighten-1"
                                variant="solo" density="comfortable" hide-details
                                :items="sortOptions" v-model="sortOrder"
                                @update:model-value="updateSortOrder">
                            </v-select>
                        </div>
                    </div>
                    <!-- フォルダナビゲーション -->
                    <div class="captures__filters" v-if="!isSearchMode">
                        <router-link v-if="!userStore.is_logged_in" to="/login/">ログインしてフォルダで整理</router-link>
                        <!-- フォルダナビゲーションチップ -->
                        <div class="captures__folder-chips">
                            <v-chip class="captures__chip" variant="elevated"
                                :color="activeFolderId === null ? 'primary' : 'background-lighten-2'"
                                @click="navigateToFolder(null)">
                                すべて
                            </v-chip>
                            <v-chip class="captures__chip" variant="elevated"
                                v-for="folder in folders" :key="folder.id"
                                :color="activeFolderId === folder.id ? 'primary' : 'background-lighten-2'"
                                @click="navigateToFolder(folder.id)"
                                @contextmenu.prevent="openFolderMenu($event, folder)">
                                <Icon icon="fluent:folder-20-filled" width="16px" class="mr-1" />
                                {{ folder.name }}
                                <span class="captures__chip-count">{{ folder.capture_count }}</span>
                            </v-chip>
                            <v-chip v-if="userStore.is_logged_in" class="captures__chip captures__chip--add" variant="tonal" color="primary"
                                @click="openCreateFolderDialog()">
                                <Icon icon="fluent:add-20-filled" width="16px" class="mr-1" />
                                フォルダ作成
                            </v-chip>
                        </div>
                    </div>
                    <div class="captures__status" aria-live="polite">
                        <v-progress-linear v-if="isLoading" indeterminate color="primary" aria-label="読み込み中" />
                        <v-alert v-if="loadError" type="error" variant="tonal" density="compact">
                            キャプチャ一覧を取得できませんでした。{{ loadError }}
                            <v-btn variant="text" :disabled="isLoading" @click="fetchCaptures">再試行</v-btn>
                        </v-alert>
                    </div>
                    <v-alert v-if="folderError" type="error" variant="tonal" density="compact">{{ folderError }}
                        <v-btn variant="text" :disabled="isMutating" @click="fetchFolders">再試行</v-btn>
                    </v-alert>
                    <!-- キャプチャグリッド (ローディング中・コンテンツ・空状態をすべて内包) -->
                    <div class="captures__grid" ref="gridElement" :style="{minHeight: `${gridMinHeight}px`}" :aria-busy="isLoading"
                        :class="{
                            'captures__grid--loading': isLoading,
                            'captures__grid--empty': total === 0 && !isLoading && !loadError,
                        }">
                        <!-- 空状態 -->
                        <div class="captures__empty"
                            :class="{'captures__empty--show': total === 0 && !isLoading && !loadError}">
                            <div class="captures__empty-content">
                                <Icon class="captures__empty-icon" :icon="emptyIcon" width="54px" height="54px" />
                                <h2>{{ emptyMessage }}</h2>
                                <div class="captures__empty-submessage" v-if="emptySubmessage">
                                    {{ emptySubmessage }}
                                </div>
                            </div>
                        </div>
                        <!-- グリッドコンテンツ -->
                        <div class="captures__grid-content">
                            <div class="capture-card" v-for="capture in captures" :key="capture.id" role="button" tabindex="0"
                                :aria-label="capture.capture_metadata?.title || capture.filename"
                                @keydown.enter.prevent="onCardClick(capture)" @keydown.space.prevent="onCardClick(capture)"
                                :class="{'capture-card--selected': selectedCaptureIds.has(capture.id)}"
                                @click="onCardClick(capture)"
                                @contextmenu.prevent="onCardContextMenu(capture)">
                                <!-- 選択モード時のチェックボックス -->
                                <div class="capture-card__checkbox" v-if="isSelectionMode"
                                    @click.stop="" @keydown.stop="">
                                    <v-checkbox-btn density="compact" color="primary"
                                        :model-value="selectedCaptureIds.has(capture.id)"
                                        @update:model-value="toggleSelection(capture)" />
                                </div>
                                <CaptureImage class="capture-card__image" :src="getCaptureThumbURL(capture)" lazy
                                    :alt="capture.capture_metadata?.title ?? capture.filename" />
                                <div class="capture-card__overlay">
                                    <span class="capture-card__program-title" v-if="capture.capture_metadata">
                                        <span v-html="ProgramUtils.decorateProgramInfo({...IProgramDefault, title: capture.capture_metadata?.title ?? capture.filename}, 'title')"></span>
                                    </span>
                                    <span class="capture-card__date">{{ formatDate(capture.file_modified_at) }}</span>
                                </div>
                            </div>
                        </div>
                    </div>
                    <!-- 選択モードバー -->
                    <v-slide-y-reverse-transition>
                        <div class="captures__selection-bar" v-if="isSelectionMode">
                            <v-alert v-if="actionError" type="error" density="compact">{{ actionError }}</v-alert>
                            <span class="captures__selection-count">{{ selectedCaptureIds.size }}件選択中</span>
                            <v-btn variant="flat" color="primary" size="small" :disabled="selectedCaptureIds.size === 0 || isMutating" @click="openFolderSelectDialog()">
                                <Icon icon="fluent:folder-add-20-filled" width="18px" class="mr-1" />
                                フォルダに追加
                            </v-btn>
                            <v-btn v-if="activeFolderId !== null && folders.some(folder => folder.id === activeFolderId)" variant="flat" color="warning" size="small"
                                :disabled="selectedCaptureIds.size === 0 || isMutating" :loading="isMutating" @click="removeSelectedFromFolder()">
                                <Icon icon="fluent:folder-dismiss-20-filled" width="18px" class="mr-1" />
                                フォルダから削除
                            </v-btn>
                            <v-spacer />
                            <v-btn variant="text" size="small" :disabled="isMutating" @click="exitSelectionMode()">キャンセル</v-btn>
                        </div>
                    </v-slide-y-reverse-transition>
                    <!-- ページネーション -->
                    <div class="captures__pagination">
                        <v-pagination v-model="currentPage" active-color="primary" density="comfortable"
                            :length="Math.max(1, Math.ceil(total / 36))" :disabled="isLoading" :total-visible="Utils.isSmartphoneVertical() ? 5 : 7"
                            @update:model-value="updatePage">
                        </v-pagination>
                    </div>
                </div>
            </div>
        </main>
        <!-- ライトボックスダイアログ -->
        <v-dialog content-class="captures-lightbox-dialog" max-width="980" transition="slide-y-transition"
            v-model="lightboxOpen">
            <div class="captures-lightbox" v-if="lightboxCapture">
                <CaptureImage class="captures-lightbox__image"
                    :src="getCaptureFullURL(lightboxCapture)"
                    :alt="lightboxCapture.capture_metadata?.title ?? lightboxCapture.filename" />

                <div class="captures-lightbox__info">
                    <div class="captures-lightbox__metadata">
                        <span class="captures-lightbox__program-title" v-if="lightboxCapture.capture_metadata">
                            <span v-html="ProgramUtils.decorateProgramInfo({...IProgramDefault, title: lightboxCapture.capture_metadata?.title ?? lightboxCapture.filename}, 'title')"></span>
                        </span>
                        <span class="captures-lightbox__filename">{{ lightboxCapture.filename }}</span>
                        <div class="captures-lightbox__details">
                            <span>{{ lightboxCapture.image_width }}x{{ lightboxCapture.image_height }}</span>
                            <span>{{ formatFileSize(lightboxCapture.file_size) }}</span>
                            <span>{{ formatDateTime(lightboxCapture.file_modified_at) }}</span>
                            <span v-if="lightboxCapture.capture_metadata?.is_caption_composited"
                                class="captures-lightbox__badge">字幕あり</span>
                            <span v-if="lightboxCapture.capture_metadata?.is_comment_composited"
                                class="captures-lightbox__badge">コメントあり</span>
                        </div>
                    </div>
                    <div class="captures-lightbox__actions">
                <a v-ripple class="captures-lightbox__download"
                    :href="CapturesService.getCaptureImageURL(lightboxCapture.id, false, true)" aria-label="原画像をダウンロード"
                    :download="lightboxCapture.filename">
                    <Icon icon="fa6-solid:download" width="22px" />
                </a>
                        <v-btn variant="text" size="small" @click="lightboxOpen = false">閉じる</v-btn>
                        <v-btn v-if="userStore.is_logged_in" variant="flat" color="primary" size="small"
                            @click="openFolderSelectDialogForSingle(lightboxCapture)">
                            <Icon icon="fluent:folder-add-20-filled" width="18px" class="mr-1" />
                            フォルダに追加
                        </v-btn>
                        <v-btn v-if="userStore.user?.is_admin" class="captures-lightbox__delete" variant="flat" color="error" size="small"
                            @click="confirmDelete()">
                            <Icon icon="fluent:delete-20-regular" width="18px" class="mr-1" />
                            削除
                        </v-btn>
                    </div>
                </div>
            </div>
        </v-dialog>
        <!-- 削除確認ダイアログ -->
        <v-dialog v-model="deleteConfirmOpen" max-width="450" persistent>
            <v-card>
                <v-card-title class="text-h6">キャプチャの削除</v-card-title>
                <v-card-text>
                    このキャプチャを削除しますか？<br>
                    この操作は取り消せません。
                </v-card-text>
                <v-alert v-if="actionError" type="error" variant="tonal" density="compact" class="mx-4">{{ actionError }}</v-alert>
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" :disabled="isMutating" @click="deleteConfirmOpen = false">キャンセル</v-btn>
                    <v-btn variant="flat" color="error" :loading="isMutating" @click="executeDelete()">削除</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
        <!-- フォルダ作成ダイアログ -->
        <v-dialog v-model="createFolderDialogOpen" max-width="450" persistent>
            <v-card>
                <v-card-title class="text-h6">フォルダ作成</v-card-title>
                <v-card-text>
                    <v-text-field v-model="newFolderName" label="フォルダ名" variant="outlined"
                        density="comfortable" hide-details autofocus
                        @keydown.enter="executeCreateFolder()" />
                </v-card-text>
                <v-alert v-if="actionError" type="error" variant="tonal" density="compact" class="mx-4">{{ actionError }}</v-alert>
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" :disabled="isMutating" @click="createFolderDialogOpen = false">キャンセル</v-btn>
                    <v-btn variant="flat" color="primary" :loading="isMutating"
                        :disabled="isMutating || !newFolderName.trim()" @click="executeCreateFolder()">作成</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
        <!-- フォルダ名前変更ダイアログ -->
        <v-dialog v-model="renameFolderDialogOpen" max-width="450" persistent>
            <v-card>
                <v-card-title class="text-h6">フォルダ名変更</v-card-title>
                <v-card-text>
                    <v-text-field v-model="renameFolderName" label="フォルダ名" variant="outlined"
                        density="comfortable" hide-details autofocus
                        @keydown.enter="executeRenameFolder()" />
                </v-card-text>
                <v-alert v-if="actionError" type="error" variant="tonal" density="compact" class="mx-4">{{ actionError }}</v-alert>
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" :disabled="isMutating" @click="renameFolderDialogOpen = false">キャンセル</v-btn>
                    <v-btn variant="flat" color="primary" :loading="isMutating"
                        :disabled="isMutating || !renameFolderName.trim()" @click="executeRenameFolder()">変更</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
        <!-- フォルダ削除確認ダイアログ -->
        <v-dialog v-model="deleteFolderDialogOpen" max-width="450" persistent>
            <v-card>
                <v-card-title class="text-h6">フォルダの削除</v-card-title>
                <v-card-text>
                    フォルダ「{{ targetFolder?.name }}」を削除しますか？<br>
                    フォルダ内のキャプチャ画像は削除されません。
                </v-card-text>
                <v-alert v-if="actionError" type="error" variant="tonal" density="compact" class="mx-4">{{ actionError }}</v-alert>
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" :disabled="isMutating" @click="deleteFolderDialogOpen = false">キャンセル</v-btn>
                    <v-btn variant="flat" color="error" :loading="isMutating"
                        @click="executeDeleteFolder()">削除</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
        <!-- フォルダ選択ダイアログ (キャプチャをフォルダに追加する用) -->
        <v-dialog v-model="folderSelectDialogOpen" max-width="450" :persistent="isMutating">
            <v-card>
                <v-card-title class="text-h6">フォルダに追加</v-card-title>
                <v-card-text class="pb-0">
                    <v-list v-if="folders.length > 0" density="compact">
                        <v-list-item v-for="folder in folders" :key="folder.id" :disabled="isMutating"
                            @click="addCapturesToSelectedFolder(folder.id)">
                            <template #prepend>
                                <Icon icon="fluent:folder-20-filled" width="20px" class="mr-2" />
                            </template>
                            <v-list-item-title>{{ folder.name }}</v-list-item-title>
                            <v-list-item-subtitle>{{ folder.capture_count }}件</v-list-item-subtitle>
                        </v-list-item>
                    </v-list>
                    <div v-else class="text-center py-4" style="color: rgb(var(--v-theme-text-darken-1));">
                        フォルダがありません。先にフォルダを作成してください。
                    </div>
                </v-card-text>
                <v-alert v-if="actionError" type="error" variant="tonal" density="compact" class="mx-4">{{ actionError }}</v-alert>
                <v-card-actions>
                    <v-spacer />
                    <v-btn variant="text" :disabled="isMutating" @click="folderSelectDialogOpen = false">閉じる</v-btn>
                </v-card-actions>
            </v-card>
        </v-dialog>
        <!-- フォルダ右クリックメニュー -->
        <v-menu v-model="folderContextMenuOpen" :style="{ left: folderMenuX + 'px', top: folderMenuY + 'px' }"
            location="start top" absolute>
            <v-list density="compact">
                <v-list-item @click="openRenameFolderDialog()">
                    <template #prepend>
                        <Icon icon="fluent:rename-20-filled" width="20px" class="mr-2" />
                    </template>
                    <v-list-item-title>名前を変更</v-list-item-title>
                </v-list-item>
                <v-list-item @click="openDeleteFolderDialog()">
                    <template #prepend>
                        <Icon icon="fluent:delete-20-filled" width="20px" class="mr-2" />
                    </template>
                    <v-list-item-title>フォルダを削除</v-list-item-title>
                </v-list-item>
            </v-list>
        </v-menu>
    </div>
</template>
<script lang="ts" setup>

import { computed, onMounted, onBeforeUnmount, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import CaptureImage from '@/components/CaptureImage.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import Message from '@/message';
import CapturesService, { ICapture, ICaptureFolder } from '@/services/Captures';
import { IProgramDefault } from '@/services/Programs';
import useUserStore from '@/stores/UserStore';
import Utils, { dayjs } from '@/utils';
import { ProgramUtils } from '@/utils/ProgramUtils';

// ルーター
const route = useRoute();
const router = useRouter();
const userStore = useUserStore();

// ==================== キャプチャ一覧の状態 ====================

// キャプチャのリスト
const captures = ref<ICapture[]>([]);
const total = ref(0);
const isLoading = ref(true);
const loadError = ref('');
const folderError = ref('');
const actionError = ref('');
const gridElement = ref<HTMLElement | null>(null);
const gridMinHeight = ref(260);
let requestVersion = 0;
let viewEpoch = 0;
let disposed = false;
const isMutating = ref(false);

// 現在のページ番号
const currentPage = ref(1);

// ソート順
const sortOrder = ref<'desc' | 'asc'>('desc');
const sortOptions = [
    { title: '新しい順', value: 'desc' as const },
    { title: '古い順', value: 'asc' as const },
];

// ==================== 検索の状態 ====================

// 検索モードかどうか (URL パスが /captures/search の場合)
const isSearchMode = computed(() => route.path === '/captures/search');

// 検索クエリ (URL の ?query= から取得)
const searchQuery = computed(() => {
    return (route.query.query as string) || '';
});

// ==================== フォルダの状態 ====================

// フォルダ一覧
const folders = ref<ICaptureFolder[]>([]);

// 現在表示中のフォルダ ID (null = すべてのキャプチャ)
const activeFolderId = computed<number | null>(() => {
    const folderId = route.params.folder_id as string;
    return folderId ? parseInt(folderId) : null;
});

// フォルダ操作対象
const targetFolder = ref<ICaptureFolder | null>(null);

// フォルダ作成ダイアログ
const createFolderDialogOpen = ref(false);
const newFolderName = ref('');


// フォルダ名前変更ダイアログ
const renameFolderDialogOpen = ref(false);
const renameFolderName = ref('');


// フォルダ削除ダイアログ
const deleteFolderDialogOpen = ref(false);


// フォルダ選択ダイアログ (キャプチャ追加用)
const folderSelectDialogOpen = ref(false);
// フォルダ追加対象のファイル名リスト (選択モードまたはライトボックスから設定される)
const folderAddTargetIds = ref<string[]>([]);

// フォルダ右クリックメニュー
const folderContextMenuOpen = ref(false);
const folderMenuX = ref(0);
const folderMenuY = ref(0);

// ==================== 選択モードの状態 ====================

// 選択モードかどうか
const isSelectionMode = ref(false);

// 選択されたキャプチャのファイル名セット
const selectedCaptureIds = ref<Set<string>>(new Set());

// ==================== ライトボックスの状態 ====================

const lightboxOpen = ref(false);
const lightboxCapture = ref<ICapture | null>(null);

// ==================== 削除の状態 ====================

const deleteConfirmOpen = ref(false);


// ==================== 算出プロパティ ====================

// パンくずリスト
const breadcrumbs = computed(() => {
    const crumbs: {name: string; path: string; disabled?: boolean}[] = [
        { name: 'ホーム', path: '/' },
        { name: 'キャプチャ', path: '/captures/' },
    ];
    if (isSearchMode.value) {
        crumbs.push({ name: '検索結果', path: '', disabled: true });
    } else if (activeFolderId.value !== null) {
        const folder = folders.value.find(f => f.id === activeFolderId.value);
        crumbs.push({ name: folder?.name ?? 'フォルダ', path: '', disabled: true });
    } else {
        // 最後のアイテムは disabled
        crumbs[crumbs.length - 1] = { ...crumbs[crumbs.length - 1], disabled: true };
    }
    return crumbs;
});

// ページタイトル
const pageTitle = computed(() => {
    if (isSearchMode.value) {
        return `「${searchQuery.value}」の検索結果`;
    }
    if (activeFolderId.value !== null) {
        const folder = folders.value.find(f => f.id === activeFolderId.value);
        return folder?.name ?? 'フォルダ';
    }
    return 'キャプチャ';
});

// 空状態のアイコン
const emptyIcon = computed(() => {
    if (isSearchMode.value) return 'fluent:search-20-regular';
    if (activeFolderId.value !== null) return 'fluent:folder-open-20-regular';
    return 'fluent:image-multiple-24-regular';
});

// 空状態のメッセージ
const emptyMessage = computed(() => {
    if (isSearchMode.value) return '検索結果が見つかりません。';
    if (activeFolderId.value !== null) return 'このフォルダにはキャプチャがありません。';
    return 'まだキャプチャがありません。';
});

// 空状態のサブメッセージ
const emptySubmessage = computed(() => {
    if (isSearchMode.value) return '別のキーワードで検索してみてください。チャンネル名でも検索できます。';
    if (activeFolderId.value !== null) return '一覧の「選択」からキャプチャを選び、フォルダに追加できます。';
    return 'キャプチャ設定で保存先を「KonomiTV サーバーにアップロード」にすると、ここに表示されます。';
});

// ==================== データ取得 ====================

// キャプチャ一覧を取得する
const fetchCaptures = async () => {
    const version = ++requestVersion;
    gridMinHeight.value = Math.max(260, gridElement.value?.clientHeight ?? 0);
    isLoading.value = true;
    loadError.value = '';
    try {
        const result = activeFolderId.value !== null
            ? await CapturesService.fetchFolderCaptures(activeFolderId.value, sortOrder.value, currentPage.value)
            : await CapturesService.fetchCaptures(sortOrder.value, currentPage.value, searchQuery.value || undefined);
        // 移動前の応答やアンマウント後の応答で現在の一覧を上書きしない。
        if (version !== requestVersion || disposed) return;
        const lastPage = Math.max(1, Math.ceil(result.total / 36));
        if (currentPage.value > lastPage) {
            await updatePage(lastPage);
            return;
        }
        captures.value = result.captures;
        total.value = result.total;
    } catch (error) {
        if (version === requestVersion && !disposed) loadError.value = error instanceof Error ? error.message : '一覧を取得できません。再試行してください。';
    } finally {
        if (version === requestVersion && !disposed) isLoading.value = false;
    }
};

// ログインしているユーザーの個人フォルダのみを取得する。
const fetchFolders = async () => {
    if (!userStore.is_logged_in) { folders.value = []; return; }
    folderError.value = '';
    try {
        const result = await CapturesService.fetchFolders();
        if (!disposed) folders.value = result.folders;
    } catch (error) {
        if (!disposed) folderError.value = error instanceof Error ? error.message : 'フォルダを取得できません。再試行してください。';
    }
};

// 送信中の二重操作を防ぎ、失敗理由と入力を同じダイアログに保持する。
const runMutation = async (action: () => Promise<void>) => {
    if (isMutating.value) return;
    const epoch = viewEpoch;
    isMutating.value = true;
    actionError.value = '';
    try { await action(); }
    catch (error) { if (!disposed && epoch === viewEpoch) actionError.value = error instanceof Error ? error.message : '操作に失敗しました。再試行してください。'; }
    finally { isMutating.value = false; }
};

// ==================== URL / ナビゲーション ====================

// サムネイル画像の URL を取得する
const getCaptureThumbURL = (capture: ICapture): string => {
    return CapturesService.getCaptureImageURL(capture.id, true);
};

// フルサイズ画像の URL を取得する
const getCaptureFullURL = (capture: ICapture): string => {
    return CapturesService.getCaptureImageURL(capture.id, false);
};

// フォルダに移動する
const navigateToFolder = (folderId: number | null) => {
    exitSelectionMode();
    if (folderId === null) {
        router.push('/captures/');
    } else {
        router.push(`/captures/folders/${folderId}`);
    }
};

// ==================== カードクリック / 選択モード ====================

// カードクリック時の処理 (選択モードかどうかで分岐)
const onCardClick = (capture: ICapture) => {
    if (isSelectionMode.value) {
        toggleSelection(capture);
    } else {
        openLightbox(capture);
    }
};

// カード右クリック → 選択モードに入る
const onCardContextMenu = (capture: ICapture) => {
    if (!userStore.is_logged_in) return;
    if (!isSelectionMode.value) {
        isSelectionMode.value = true;
        selectedCaptureIds.value = new Set();
    }
    // トグル選択
    toggleSelection(capture);
};

// 選択のトグル
const toggleSelection = (capture: ICapture) => {
    const newSet = new Set(selectedCaptureIds.value);
    if (newSet.has(capture.id)) {
        newSet.delete(capture.id);
    } else {
        newSet.add(capture.id);
    }
    selectedCaptureIds.value = newSet;
    // 選択が0件になったら選択モードを終了
    if (newSet.size === 0) {
        isSelectionMode.value = false;
    }
};

// 選択モードを終了する
const exitSelectionMode = () => {
    isSelectionMode.value = false;
    selectedCaptureIds.value = new Set();
};

// ==================== ライトボックス ====================

// ライトボックスを開く
const openLightbox = (capture: ICapture) => {
    actionError.value = '';
    lightboxCapture.value = capture;
    lightboxOpen.value = true;
};

// ==================== 削除 ====================

// 削除確認ダイアログを表示する
const confirmDelete = () => {
    deleteConfirmOpen.value = true;
};

// 削除を実行する
const executeDelete = async () => runMutation(async () => {
    const epoch = viewEpoch;
    if (!lightboxCapture.value) return;

    const success = await CapturesService.deleteCapture(lightboxCapture.value.id);

    if (disposed || epoch !== viewEpoch) return;
    if (success) {
        Message.success('キャプチャを削除しました。');
        deleteConfirmOpen.value = false;
        lightboxOpen.value = false;
        lightboxCapture.value = null;
        // 一覧を再取得する
        await fetchCaptures();
        await fetchFolders();
    }
});

// ==================== フォルダ操作 ====================

// フォルダ作成ダイアログを開く
const openCreateFolderDialog = () => {
    newFolderName.value = '';
    createFolderDialogOpen.value = true;
};

// フォルダ作成を実行する
const executeCreateFolder = async () => runMutation(async () => {
    const epoch = viewEpoch;
    if (!newFolderName.value.trim()) return;

    const result = await CapturesService.createFolder(newFolderName.value.trim());

    if (disposed || epoch !== viewEpoch) return;
    if (result) {
        Message.success(`フォルダ「${result.name}」を作成しました。`);
        createFolderDialogOpen.value = false;
        await fetchFolders();
    }
});

// フォルダ右クリックメニューを開く
const openFolderMenu = (event: MouseEvent, folder: ICaptureFolder) => {
    targetFolder.value = folder;
    folderMenuX.value = event.clientX;
    folderMenuY.value = event.clientY;
    folderContextMenuOpen.value = true;
};

// フォルダ名前変更ダイアログを開く
const openRenameFolderDialog = () => {
    if (!targetFolder.value) return;
    renameFolderName.value = targetFolder.value.name;
    renameFolderDialogOpen.value = true;
};

// フォルダ名前変更を実行する
const executeRenameFolder = async () => runMutation(async () => {
    const epoch = viewEpoch;
    if (!targetFolder.value || !renameFolderName.value.trim()) return;

    const result = await CapturesService.updateFolder(targetFolder.value.id, {
        name: renameFolderName.value.trim(),
    });

    if (disposed || epoch !== viewEpoch) return;
    if (result) {
        Message.success(`フォルダ名を「${result.name}」に変更しました。`);
        renameFolderDialogOpen.value = false;
        await fetchFolders();
    }
});

// フォルダ削除ダイアログを開く
const openDeleteFolderDialog = () => {
    if (!targetFolder.value) return;
    deleteFolderDialogOpen.value = true;
};

// ヘッダーボタンから名前変更ダイアログを開く (アクティブなフォルダを対象にする)
const openRenameFolderDialogForActive = () => {
    const folder = folders.value.find(f => f.id === activeFolderId.value);
    if (!folder) return;
    targetFolder.value = folder;
    openRenameFolderDialog();
};

// ヘッダーボタンから削除ダイアログを開く (アクティブなフォルダを対象にする)
const openDeleteFolderDialogForActive = () => {
    const folder = folders.value.find(f => f.id === activeFolderId.value);
    if (!folder) return;
    targetFolder.value = folder;
    openDeleteFolderDialog();
};

// フォルダ削除を実行する
const executeDeleteFolder = async () => runMutation(async () => {
    const epoch = viewEpoch;
    if (!targetFolder.value) return;

    const success = await CapturesService.deleteFolder(targetFolder.value.id);

    if (disposed || epoch !== viewEpoch) return;
    if (success) {
        Message.success(`フォルダ「${targetFolder.value.name}」を削除しました。`);
        deleteFolderDialogOpen.value = false;
        // 削除したフォルダを表示中だった場合は全体一覧に戻る
        if (activeFolderId.value === targetFolder.value.id) {
            router.push('/captures/');
        }
        await fetchFolders();
    }
});

// フォルダ選択ダイアログを開く (複数選択モードから)
const openFolderSelectDialog = () => {
    folderAddTargetIds.value = Array.from(selectedCaptureIds.value);
    folderSelectDialogOpen.value = true;
};

// フォルダ選択ダイアログを開く (ライトボックスから単一キャプチャ)
const openFolderSelectDialogForSingle = (capture: ICapture) => {
    folderAddTargetIds.value = [capture.id];
    folderSelectDialogOpen.value = true;
};

// 選択されたフォルダにキャプチャを追加する
const addCapturesToSelectedFolder = async (folderId: number) => runMutation(async () => {
    const epoch = viewEpoch;
    const captureIds = folderAddTargetIds.value;
    if (captureIds.length === 0) return;

    const success = await CapturesService.addCapturesToFolder(folderId, captureIds);
    if (disposed || epoch !== viewEpoch) return;
    if (success) {
        const folder = folders.value.find(f => f.id === folderId);
        Message.success(`${captureIds.length}件のキャプチャを「${folder?.name ?? 'フォルダ'}」に追加しました。`);
        folderSelectDialogOpen.value = false;
        exitSelectionMode();
        // フォルダ一覧を更新 (capture_count が変わるため)
        await fetchFolders();
    }
});

// 選択中のキャプチャを現在のフォルダから削除する
const removeSelectedFromFolder = async () => runMutation(async () => {
    const epoch = viewEpoch;
    if (activeFolderId.value === null) return;
    const captureIds = Array.from(selectedCaptureIds.value);
    if (captureIds.length === 0) return;

    const success = await CapturesService.removeCapturesFromFolder(activeFolderId.value, captureIds);
    if (disposed || epoch !== viewEpoch) return;
    if (success) {
        Message.success(`${captureIds.length}件のキャプチャをフォルダから削除しました。`);
        exitSelectionMode();
        await fetchCaptures();
        await fetchFolders();
    }
});

// ==================== フォーマッタ ====================

// 日付をフォーマットする (グリッドカード用)
const formatDate = (dateStr: string): string => {
    return dayjs(dateStr).format('YYYY/MM/DD HH:mm');
};

// 日時をフォーマットする (ライトボックス用)
const formatDateTime = (dateStr: string): string => {
    return dayjs(dateStr).format('YYYY/MM/DD HH:mm:ss');
};

// ファイルサイズをフォーマットする
const formatFileSize = (bytes: number): string => {
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

// ==================== ページ / ソート更新 ====================

// ページを更新する
const updatePage = async (page: number) => {
    currentPage.value = page;
    isLoading.value = true;
    await router.replace({
        query: {
            ...route.query,
            page: page.toString(),
        },
    });
};

// ソート順を更新する
const updateSortOrder = async () => {
    currentPage.value = 1;
    isLoading.value = true;
    await router.replace({
        query: {
            ...route.query,
            order: sortOrder.value,
            page: '1',
        },
    });
};

// ==================== ルート変更の監視 ====================

const syncRoute = () => {
    const page = Number(route.query.page ?? 1);
    currentPage.value = Number.isSafeInteger(page) && page > 0 ? page : 1;
    sortOrder.value = route.query.order === 'asc' ? 'asc' : 'desc';
};
watch(() => route.fullPath, async () => {
    viewEpoch++;
    requestVersion++;
    // 別の検索・フォルダ・ページの画像を、新しい見出しの結果として残さない。
    captures.value = [];
    total.value = 0;
    exitSelectionMode();
    lightboxOpen.value = false;
    deleteConfirmOpen.value = false;
    createFolderDialogOpen.value = false;
    renameFolderDialogOpen.value = false;
    deleteFolderDialogOpen.value = false;
    folderSelectDialogOpen.value = false;
    actionError.value = '';
    syncRoute();
    await fetchCaptures();
});
watch([deleteConfirmOpen, createFolderDialogOpen, renameFolderDialogOpen, deleteFolderDialogOpen, folderSelectDialogOpen], () => {
    if (!isMutating.value) actionError.value = '';
});
onMounted(async () => {
    syncRoute();
    // 共有画像一覧は認証状態の取得を待たずに表示する。
    void fetchCaptures();
    await userStore.fetchUser();
    if (!disposed) await fetchFolders();
});
onBeforeUnmount(() => { disposed = true; requestVersion++; viewEpoch++; });

</script>
<style lang="scss" scoped>

.captures-container-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;
    @include smartphone-vertical {
        padding-top: 10px !important;
    }
}

.captures-container {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    padding: 20px;
    margin: 0 auto;
    min-width: 0;
    max-width: 1000px;
    @include smartphone-horizontal {
        padding: 16px 20px !important;
    }
    @include smartphone-horizontal-short {
        padding: 16px 16px !important;
    }
    @include smartphone-vertical {
        padding: 16px 8px !important;
        padding-top: 8px !important;
    }
}

// ヘッダー: タイトル・件数・ソート (RecordedProgramList のヘッダーパターンに準拠)
.captures__header {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    align-items: center;
    @include smartphone-vertical {
        padding: 0px 8px;
    }
}

.captures__title {
    display: flex;
    flex-wrap: wrap;
    overflow-wrap: anywhere;
    min-width: 0;
    align-items: center;
    position: relative;
    font-size: 24px;
    font-weight: 700;
    padding-top: 8px;
    padding-bottom: 20px;
    @include smartphone-vertical {
        font-size: 22px;
        padding-bottom: 16px;
    }

    &-count {
        display: flex;
        align-items: center;
        flex-shrink: 0;
        padding-top: 8px;
        margin-left: 12px;
        font-size: 14px;
        font-weight: 400;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &-action {
        min-width: 36px !important;
        width: 36px;
        height: 36px;
        padding: 0 !important;
        border-radius: 8px;
    }
}

.captures__actions {
    display: flex;
    align-items: center;
    margin-left: auto;
    :deep(.v-field) {
        padding-right: 4px !important;
    }
    :deep(.v-field__input) {
        padding-left: 12px !important;
        padding-right: 0px !important;
    }
}

.captures__sort {
    width: 103px;
    :deep(.v-field__input) {
        font-size: 14px !important;
        padding-top: 6px !important;
        padding-bottom: 6px !important;
        min-height: unset !important;
    }
}

// チャンネルフィルタ & フォルダナビゲーション
.captures__filters {
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin-bottom: 12px;
    @include smartphone-vertical {
        padding: 0px 8px;
        margin-bottom: 8px;
    }
}

.captures__folder-chips {
    display: flex;
    flex-wrap: wrap;
    gap: 6px;
}

.captures__chip {
    cursor: pointer;
    font-size: 13px;
    &--add {
        opacity: 0.8;
    }
}

.captures__chip-count {
    margin-left: 4px;
    font-size: 11px;
    opacity: 0.7;
}

.captures__status {
    min-height: 28px;
    padding-bottom: 8px;
}

// キャプチャグリッド
// display: block を使うことで、子要素の CSS Grid レイアウトが確実に親の全幅で展開されるようにする
// (空状態の .captures__empty は position: absolute なので block レイアウトでも問題なし)
.captures__grid {
    position: relative;
    width: 100%;
    background: rgb(var(--v-theme-background-lighten-1));
    border-radius: 8px;
    overflow: hidden;

    &--empty {
        min-height: 200px;
    }
}

.captures__grid-content {
    display: grid;
    // デフォルトは4列 (デスクトップ・タブレット横・タブレット縦すべて)
    // smartphone-vertical のみ2列に切り替える
    grid-template-columns: repeat(4, 1fr);
    gap: 4px;
    padding: 4px;
    width: 100%;
    transition: visibility 0.2s ease, opacity 0.2s ease;
    @include smartphone-horizontal {
        grid-template-columns: repeat(3, 1fr);
    }
    @include smartphone-horizontal-short {
        grid-template-columns: repeat(3, 1fr);
    }
    @include smartphone-vertical {
        grid-template-columns: repeat(2, 1fr);
    }
}

// キャプチャカード
.capture-card {
    position: relative;
    aspect-ratio: 16 / 9;
    border-radius: 7px;
    overflow: hidden;
    cursor: pointer;
    // グリッドアイテムがグリッドセルの幅を超えないようにする
    min-width: 0;
    // 読み込まれるまでのキャプチャの背景 (既存のキャプチャコンポーネントと同じグラデーション)
    background: linear-gradient(150deg, rgb(var(--v-theme-gray)), rgb(var(--v-theme-background-lighten-2)));
    // content-visibility: auto は最終行のグリッドレイアウトを壊すことがあるため使用しない
    transition: opacity 0.15s ease;

    &:hover {
        opacity: 0.85;
    }

    &--selected {
        outline: 3px solid rgb(var(--v-theme-primary));
        outline-offset: -3px;
    }

    &__checkbox {
        position: absolute;
        top: 4px;
        left: 4px;
        z-index: 2;
        background: rgba(0, 0, 0, 0.5);
        border-radius: 4px;
    }

    &__image {
        display: block;
        width: 100%;
        height: 100%;
        object-fit: cover;
    }

    &__overlay {
        position: absolute;
        bottom: 0;
        left: 0;
        right: 0;
        padding: 24px 8px 6px;
        background: linear-gradient(transparent, rgba(0, 0, 0, 0.75));
        display: flex;
        flex-direction: column;
        gap: 1px;
    }

    &__program-title {
        font-size: 12px;
        font-weight: bold;
        color: #ffffff;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        @include smartphone-vertical {
            font-size: 11px;
        }
    }

    &__date {
        font-size: 11px;
        color: rgba(255, 255, 255, 0.8);
        @include smartphone-vertical {
            font-size: 10px;
        }
    }
}

// 空状態 (RecordedProgramList の __empty パターンに準拠)
.captures__empty {
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    display: flex;
    justify-content: center;
    align-items: center;
    padding-top: 28px;
    padding-bottom: 40px;
    flex-grow: 1;
    visibility: hidden;
    opacity: 0;
    transition: visibility 0.2s ease, opacity 0.2s ease;

    &--show {
        visibility: visible;
        opacity: 1;
    }

    &-content {
        text-align: center;

        .captures__empty-icon {
            color: rgb(var(--v-theme-text-darken-1));
        }

        h2 {
            font-size: 21px;
            @include tablet-vertical {
                font-size: 19px !important;
            }
            @include smartphone-horizontal {
                font-size: 19px !important;
            }
            @include smartphone-horizontal-short {
                font-size: 19px !important;
            }
            @include smartphone-vertical {
                font-size: 19px !important;
                text-align: center;
            }
        }

        .captures__empty-submessage {
            margin-top: 8px;
            color: rgb(var(--v-theme-text-darken-1));
            font-size: 15px;
            @include tablet-vertical {
                font-size: 13px !important;
                text-align: center;
            }
            @include smartphone-horizontal {
                font-size: 13px !important;
                text-align: center;
            }
            @include smartphone-vertical {
                font-size: 13px !important;
                text-align: center;
                margin-top: 7px !important;
                line-height: 1.65;
            }
        }
    }
}

// 選択モードバー
.captures__selection-bar {
    flex-wrap: wrap;
    max-width: calc(100% - 32px);
    position: fixed;
    bottom: 16px;
    left: 50%;
    transform: translateX(-50%);
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 16px;
    background: rgb(var(--v-theme-background-lighten-1));
    border-radius: 12px;
    box-shadow: 0px 4px 12px rgba(0, 0, 0, 0.3);
    z-index: 100;
    @include smartphone-vertical {
        bottom: 70px;
        left: 8px;
        right: 8px;
        transform: none;
    }
}

.captures__selection-count {
    font-size: 14px;
    font-weight: 600;
    white-space: nowrap;
    margin-right: 4px;
}

// ページネーション
.captures__pagination {
    display: flex;
    justify-content: center;
    margin-top: 24px;
    @include smartphone-vertical {
        margin-top: 20px;
    }
}

</style>
<style lang="scss">

// ライトボックスダイアログ (v-dialog の content-class でスコープ外に適用する必要がある)
// 既存の zoom-capture-modal パターンに準拠
@include smartphone-horizontal {
    .captures-lightbox-dialog {
        width: auto !important;
        max-width: auto !important;
    }
}

.captures-lightbox-dialog {
    margin: 12px;

    .captures-lightbox {
        position: relative;
        display: flex;
        flex-direction: column;
        background: rgb(var(--v-theme-background-lighten-1));
        border-radius: 11px;
        overflow: hidden;

        &__image {
            display: block;
            width: 100%;
            max-height: 70vh;
            object-fit: contain;
            background: #000000;
        }

        // ダウンロードボタン (zoom-capture-modal__download パターンに準拠)
        &__download {
            display: flex;
            align-items: center;
            justify-content: center;
            width: 36px;
            height: 32px;
            color: rgb(var(--v-theme-text));
        }

        &__info {
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: 14px 16px;
            gap: 12px;
            @include smartphone-vertical {
                flex-direction: column;
                align-items: flex-start;
            }
        }

        &__metadata {
            display: flex;
            flex-direction: column;
            gap: 2px;
            min-width: 0;
            flex: 1;
        }

        &__program-title {
            font-size: 15px;
            font-weight: bold;
            color: rgb(var(--v-theme-text));
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        &__filename {
            font-size: 13px;
            color: rgb(var(--v-theme-text-darken-1));
            white-space: nowrap;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        &__details {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            font-size: 12px;
            color: rgb(var(--v-theme-text-darken-1));
        }

        &__badge {
            padding: 1px 6px;
            border-radius: 4px;
            background: rgb(var(--v-theme-primary));
            color: #ffffff;
            font-size: 11px;
        }

        &__actions {
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            flex-shrink: 0;
        }

        &__delete {
            flex-shrink: 0;
        }
    }
}

</style>
