<template>
    <div class="gguide-layout" :class="{'gguide-layout--banner-row': props.enabled && !canUseHeader && groups[0].assets.length > 0}">
        <Teleport v-for="group in visibleGroups" :key="group.kind" :to="props.bannerTarget ?? 'body'"
            :disabled="group.kind !== 'Banner' || !canUseHeader">
            <section class="gguide" :class="[`gguide--${group.kind.toLowerCase()}`, {'gguide--header': group.kind === 'Banner' && canUseHeader}]"
                :aria-label="`H ガイド ${group.kind}`"
                @mouseenter="onMouseEnter" @mouseleave="onMouseLeave"
                @focusin="isFocused = true" @focusout="onFocusOut">
                <v-btn v-if="group.kind !== 'Banner'" class="gguide__close" variant="flat" icon="mdi-close"
                    size="x-small" :aria-label="`H ガイド ${group.kind} を閉じる`" @click="closeGroup(group.kind)" />
                <div class="gguide__frame" :aria-busy="isLoading">
                    <v-carousel v-if="group.assets.length" v-model="selectedIds[group.kind]" height="100%" hide-delimiters
                        :show-arrows="group.assets.length > 1">
                        <v-carousel-item v-for="(asset, index) in group.assets" :key="asset.id" :value="asset.id"
                            :eager="isAdjacent(group.kind, group.assets, index)">
                            <div class="gguide__media" :class="{'gguide__media--controls': group.assets.length > 1}">
                                <div class="gguide__image" :style="{'--gguide-image-ratio': asset.width / asset.height}">
                                    <v-img :src="imageURL(asset.id)" :key="`${asset.id}-${imageRetries[asset.id] ?? 0}`"
                                        :aspect-ratio="asset.width / asset.height" height="100%" alt="H ガイドの放送案内"
                                        :eager="isAdjacent(group.kind, group.assets, index)"
                                        @error="imageFailures[asset.id] = true" @load="imageFailures[asset.id] = false">
                                        <template #placeholder>
                                            <div class="gguide__state"><v-progress-circular indeterminate size="28" /></div>
                                        </template>
                                        <template #error>
                                            <div class="gguide__state gguide__state--error" role="status">
                                                <span>画像を取得できませんでした。再読み込みしてください。</span>
                                                <small>[GGUIDE_IMAGE_UNAVAILABLE]</small>
                                                <v-btn variant="tonal" size="small" @click.stop="retryImage(asset.id)">再読み込み</v-btn>
                                            </div>
                                        </template>
                                    </v-img>
                                    <button v-if="!imageFailures[asset.id] && (asset.programs.length > 0 ||
                                        asset.advertisements.some(advertisement => advertisement.text.trim()))"
                                        class="gguide__open" type="button"
                                        :disabled="openingAssetId !== null" :aria-busy="openingAssetId === asset.id"
                                        :aria-label="asset.programs.length > 0 ? '番組詳細を開く' : 'H ガイドの詳細を開く'"
                                        @click="openDetails(asset)">
                                        <v-progress-circular v-if="openingAssetId === asset.id" indeterminate size="28" />
                                        <span v-else-if="programErrors[asset.id]" class="gguide__program-error" role="status">
                                            番組情報を取得できませんでした。クリックして再試行してください。
                                            <small>[GGUIDE_PROGRAM_UNAVAILABLE]</small>
                                        </span>
                                    </button>
                                </div>
                            </div>
                        </v-carousel-item>
                    </v-carousel>
                    <div v-else-if="isLoading" class="gguide__state" role="status" aria-label="H ガイドを読み込み中">
                        <v-progress-circular indeterminate size="28" />
                    </div>
                    <div v-else class="gguide__state" role="status">
                        <span>H ガイドを取得できませんでした。受信サーバーとの接続を確認して再試行してください。</span>
                        <small>[GGUIDE_UNAVAILABLE]</small>
                        <v-btn variant="tonal" size="small" @click="refresh">再試行</v-btn>
                    </div>
                </div>
            </section>
        </Teleport>
        <template v-for="group in groups" :key="group.kind">
            <v-btn v-if="props.enabled && closedGroups[group.kind] && group.assets.length" class="gguide-reopen"
                :class="`gguide-reopen--${group.kind.toLowerCase()}`" size="small"
                :aria-label="`H ガイド ${group.kind} を表示`" variant="tonal"
                @click="closedGroups[group.kind] = false">H ガイド</v-btn>
        </template>
        <slot />
        <v-dialog v-model="isDetailOpen" max-width="720" scrollable>
            <v-card v-if="detailAsset" class="gguide-detail">
                <v-card-title class="d-flex align-center">
                    <span>H ガイド</span>
                    <v-spacer />
                    <v-btn icon="mdi-close" variant="text" aria-label="閉じる" @click="isDetailOpen = false" />
                </v-card-title>
                <v-card-text>
                    <v-img :src="imageURL(detailAsset.id)" :key="`${detailAsset.id}-${imageRetries[detailAsset.id] ?? 0}`"
                        class="gguide-detail__image" height="280" alt="H ガイドの案内画像" eager>
                        <template #placeholder>
                            <div class="gguide__state"><v-progress-circular indeterminate size="28" /></div>
                        </template>
                        <template #error>
                            <div class="gguide__state" role="status">
                                <span>画像を取得できませんでした。再読み込みしてください。</span>
                                <small>[GGUIDE_IMAGE_UNAVAILABLE]</small>
                                <v-btn variant="tonal" size="small" @click="retryImage(detailAsset.id)">再読み込み</v-btn>
                            </div>
                        </template>
                    </v-img>
                    <!-- 番組詳細と同じパーサーで HTML をエスケープし、受信した URL だけをリンクにする。 -->
                    <p v-for="(advertisement, index) in detailAsset.advertisements" :key="index"
                        class="gguide-detail__text" v-html="Utils.URLtoLink(advertisement.text)"></p>
                </v-card-text>
            </v-card>
        </v-dialog>
    </div>
</template>

<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue';

import { IChannel } from '@/services/Channels';
import Programs, { IGGuideCollectionAsset, ITimeTableChannel, ITimeTableProgram } from '@/services/Programs';
import { dayjs } from '@/utils';
import Utils from '@/utils/Utils';

const props = withDefaults(defineProps<{
    bannerTarget?: HTMLElement | null;
    channels?: ITimeTableChannel[];
    enabled?: boolean;
    paused?: boolean;
}>(), {enabled: true});
const emit = defineEmits<{
    (event: 'loaded', assets: IGGuideCollectionAsset[]): void;
    (event: 'overlay-visible', visible: boolean): void;
    (event: 'select-program', selection: {asset: IGGuideCollectionAsset; program: ITimeTableProgram; channel: IChannel}): void;
}>();
const headerWidth = ref(0);
let headerObserver: ResizeObserver | null = null;
// ロゴと既存の操作ボタンが占めた後の実幅で判断し、画面幅だけでは押し込まない。
const canUseHeader = computed(() => headerWidth.value >= 480);
watch(() => props.bannerTarget, target => {
    headerObserver?.disconnect();
    headerWidth.value = target?.getBoundingClientRect().width ?? 0;
    if (!target) return;
    headerObserver = new ResizeObserver(entries => {
        headerWidth.value = entries[0]?.contentRect.width ?? 0;
    });
    headerObserver.observe(target);
}, {flush: 'post', immediate: true});

const assets = ref<IGGuideCollectionAsset[]>([]);
type Presentation = 'Banner' | 'Landscape' | 'Poster';
const selectedIds = ref<Record<Presentation, string | null>>({Banner: null, Landscape: null, Poster: null});
const isLoading = ref(true);
const isVisible = ref(true);
const hasError = ref(false);
const isMotionReduced = ref(false);
const closedGroups = ref<Record<Presentation, boolean>>({Banner: false, Landscape: false, Poster: false});
const isHovered = ref(false);
const isFocused = ref(false);
const isDocumentHidden = ref(document.hidden);
const imageRetries = ref<Record<string, number>>({});
const imageFailures = ref<Record<string, boolean>>({});
const detailAsset = ref<IGGuideCollectionAsset | null>(null);
const isDetailOpen = ref(false);
const openingAssetId = ref<string | null>(null);
const programErrors = ref<Record<string, boolean>>({});
let isDisposed = false;
let requestPending = false;
let resumeTimer: ReturnType<typeof setTimeout> | null = null;
let rotationTimer: ReturnType<typeof setTimeout> | null = null;
// 三か所が同時に動かないよう一つの時計で順番に送る。各領域は十二秒ずつ表示する。
const slideDuration = 12000;

// 寸法は表示形式だけを選ぶ。広告・番組との関連付けを推測する条件には使わない。
const groups = computed(() => (['Banner', 'Landscape', 'Poster'] as const).map(kind => ({
    kind,
    assets: assets.value.filter(asset => {
        const presentation = asset.width / asset.height >= 3 ? 'Banner' :
            asset.width >= asset.height ? 'Landscape' : 'Poster';
        return presentation === kind;
    }),
})));
// 放送回の四項目をすべて照合する。EID 再利用や同名番組に誤って移動しない。
const matchedPrograms = computed(() => {
    const broadcasts = new Map<string, {program: ITimeTableProgram; channel: IChannel}>();
    for (const entry of props.channels ?? []) {
        for (const group of [entry, ...(entry.subchannels ?? [])]) {
            for (const program of group.programs) {
                if (program.source !== 'Broadcast') continue;
                broadcasts.set([program.network_id, program.service_id, program.event_id,
                    dayjs(program.start_time).valueOf()].join(':'), {program, channel: group.channel});
            }
        }
    }
    const matches = new Map<string, {program: ITimeTableProgram; channel: IChannel}>();
    for (const asset of assets.value) {
        for (const association of asset.programs) {
            const broadcast = broadcasts.get([association.network_id, association.service_id,
                association.event_id, association.start_at].join(':'));
            if (!broadcast) continue;
            matches.set(asset.id, broadcast);
            break;
        }
    }
    return matches;
});

async function openDetails(asset: IGGuideCollectionAsset): Promise<void> {
    if (openingAssetId.value !== null) return;
    // 番組関連付けの有無と、現在の画面に読み込まれたかどうかを混同しない。
    if (asset.programs.length > 0) {
        openingAssetId.value = asset.id;
        programErrors.value[asset.id] = false;
        try {
            const match = matchedPrograms.value.get(asset.id) ?? await Programs.fetchGGuideProgram(asset.programs);
            // 取得中にページを離れたり表示設定を切った場合、遅れて詳細を開かない。
            if (isDisposed || !props.enabled) return;
            if (match) emit('select-program', {asset, ...match});
            else programErrors.value[asset.id] = true;
        } catch {
            programErrors.value[asset.id] = true;
        } finally {
            openingAssetId.value = null;
        }
        return;
    }
    // 本文のない素材には詳細の行き先がないため、画像だけの拡大表示は行わない。
    if (!asset.advertisements.some(advertisement => advertisement.text.trim())) return;
    detailAsset.value = asset;
    isDetailOpen.value = true;
}

function isAdjacent(kind: Presentation, images: IGGuideCollectionAsset[], index: number): boolean {
    // Vuetify の eager を使い、現在・次・前だけを事前取得する。一覧全体を一括で取得しない。
    const selectedIndex = Math.max(0, images.findIndex(asset => asset.id === selectedIds.value[kind]));
    const distance = Math.abs(index - selectedIndex);
    return distance <= 1 || distance === images.length - 1;
}
const visibleGroups = computed(() => props.enabled && isVisible.value ? groups.value.filter(group =>
    !closedGroups.value[group.kind] &&
    (group.assets.length > 0 || ((isLoading.value || hasError.value) && group.kind === 'Landscape'))) : []);
watch(visibleGroups, groups => emit('overlay-visible', groups.some(group => group.kind !== 'Banner')), {immediate: true});
// 操作中・バックグラウンドでは、Vuetify の自動送りを止めてフォーカスと選択を維持する。
const canCycle = computed(() => assets.value.length > 1 && !isMotionReduced.value && !isHovered.value &&
    !isFocused.value && !isDocumentHidden.value && !isDetailOpen.value && !props.paused && openingAssetId.value === null);
const rotatingGroups = computed(() => visibleGroups.value.filter(group => group.assets.length > 1));
watch([canCycle, rotatingGroups], ([playing, groups]) => {
    if (rotationTimer !== null) clearTimeout(rotationTimer);
    rotationTimer = null;
    if (!playing || !groups.length) return;
    let nextGroup = 0;
    const advance = () => {
        const group = groups[nextGroup];
        const current = group.assets.findIndex(asset => asset.id === selectedIds.value[group.kind]);
        selectedIds.value[group.kind] = group.assets[(current + 1) % group.assets.length].id;
        nextGroup = (nextGroup + 1) % groups.length;
        rotationTimer = setTimeout(advance, slideDuration / groups.length);
    };
    // 開始時・再開時は一枚を十分に読める時間を確保し、その後は領域間で均等にずらす。
    rotationTimer = setTimeout(advance, slideDuration);
}, {immediate: true});
watch(() => props.enabled, enabled => {
    // 番組表自体は作り直さず、任意の画像表示と通信だけを切り替える。
    if (!enabled) isDetailOpen.value = false;
    else if (!assets.value.length) void refresh();
});

function onMouseEnter(): void {
    if (resumeTimer !== null) clearTimeout(resumeTimer);
    resumeTimer = null;
    isHovered.value = true;
}

function closeGroup(kind: Presentation): void {
    closedGroups.value[kind] = true;
    if (resumeTimer !== null) clearTimeout(resumeTimer);
    resumeTimer = null;
    isHovered.value = false;
    isFocused.value = false;
}

function onMouseLeave(): void {
    if (resumeTimer !== null) clearTimeout(resumeTimer);
    // ポインターが離れてから五秒待つ。戻ってきた場合は予約を取り消す。
    resumeTimer = setTimeout(() => {
        isHovered.value = false;
        resumeTimer = null;
    }, 5000);
}

function imageURL(id: string): string {
    return `${Utils.api_base_url}/programs/gguide/images/${id}`;
}

function retryImage(id: string): void {
    imageFailures.value[id] = false;
    imageRetries.value[id] = (imageRetries.value[id] ?? 0) + 1;
}

function onFocusOut(event: FocusEvent): void {
    // カルーセル内のボタン間を移動しても再生を再開しない。
    isFocused.value = event.relatedTarget instanceof Node &&
        (event.currentTarget as HTMLElement).contains(event.relatedTarget);
}

function onVisibilityChange(): void {
    isDocumentHidden.value = document.hidden;
}

async function refresh(): Promise<void> {
    // 再試行の操作が重なっても、取得は一回だけ行う。
    if (requestPending) return;
    requestPending = true;
    isLoading.value = true;
    const result = await Programs.fetchGGuideCollection();
    requestPending = false;
    // ページを離れた後のリクエスト結果で状態を更新しない。
    if (isDisposed) return;
    isLoading.value = false;
    hasError.value = result.status === 'Unavailable';
    if (hasError.value) return;
    assets.value = result.assets;
    emit('loaded', result.assets);
    isVisible.value = result.status === 'Available' && assets.value.length > 0;
    // 更新後にも同じ素材があれば選択を維持し、消えた場合だけ先頭を選ぶ。
    for (const group of groups.value) {
        if (!group.assets.some(asset => asset.id === selectedIds.value[group.kind])) {
            selectedIds.value[group.kind] = group.assets[0]?.id ?? null;
        }
    }
}

onMounted(() => {
    // モーション低減設定では自動送りを行わず、標準の矢印で切り替えられる。
    isMotionReduced.value = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    document.addEventListener('visibilitychange', onVisibilityChange);
    if (props.enabled) void refresh();
});

onBeforeUnmount(() => {
    isDisposed = true;
    headerObserver?.disconnect();
    if (resumeTimer !== null) clearTimeout(resumeTimer);
    document.removeEventListener('visibilitychange', onVisibilityChange);
    if (rotationTimer !== null) clearTimeout(rotationTimer);
});
</script>

<style scoped lang="scss">
.gguide-layout {
    position: relative;
    isolation: isolate;
    display: grid;
    grid-template-rows: auto minmax(0, 1fr);
    grid-template-columns: minmax(0, 1fr);
    flex: 1;
    min-width: 0;
    min-height: 0;
    &--banner-row {
        --gguide-banner-row-height: 152px;
        @include smartphone-vertical { --gguide-banner-row-height: 96px; }
    }
    :deep(> .timetable-grid) { grid-area: 2 / 1; z-index: 0; }
}
.gguide {
    position: relative;
    border-radius: 8px;
    background: rgb(var(--v-theme-background));
    &--banner {
        grid-area: 1 / 1;
        justify-self: end;
        width: min(824px, calc(100% - 24px));
        margin: 8px 12px;
        .gguide__frame { height: 136px; }
        .gguide__media--controls { margin: 0 48px; }
        @include smartphone-vertical {
            width: calc(100% - 16px);
            margin: 8px;
            .gguide__frame { height: 80px; }
            .gguide__media--controls { margin: 0 36px; }
        }
    }
    &--header {
        width: 100%;
        height: 57px;
        margin: 0;
        .gguide__frame { height: 57px; }
        .gguide__media--controls { margin: 0 28px; }
        :deep(.v-window__controls .v-btn) { width: 24px; height: 24px; }
    }
    &--landscape,
    &--poster {
        position: absolute;
        right: 16px;
        z-index: 1;
        box-shadow: 0 4px 16px rgb(0 0 0 / 30%);
        @include smartphone-vertical {
            right: 8px;
        }
    }
    &--landscape {
        top: calc(var(--gguide-banner-row-height, 0px) + 66px);
        width: 192px;
        .gguide__frame { height: 144px; }
        @include smartphone-vertical {
            width: 128px;
            .gguide__frame { height: 96px; }
        }
    }
    &--poster {
        bottom: 16px;
        width: 176px;
        .gguide__frame { height: 264px; }
        @include smartphone-vertical {
            bottom: 8px;
            width: 128px;
            .gguide__frame { height: 192px; }
        }
    }
    &__close {
        position: absolute;
        top: -12px;
        right: -4px;
        z-index: 2;
    }
    &__frame { border-radius: inherit; }
    &__media { height: 100%; }
    &__image {
        // contain で生じる余白を除き、実際の画像と同じ寸法にクリック領域を合わせる。
        container-type: size;
        position: relative;
        display: block;
        width: 100%;
        height: 100%;
        color: inherit;
        border-radius: inherit;
    }
    &__open {
        position: absolute;
        inset: 0;
        margin: auto;
        width: min(100cqw, calc(100cqh * var(--gguide-image-ratio)));
        height: min(100cqh, calc(100cqw / var(--gguide-image-ratio)));
        z-index: 1;
        display: flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        &:disabled { cursor: wait; }
        &:focus-visible { outline: 2px solid rgb(var(--v-theme-secondary)); outline-offset: -2px; }
    }
    &__program-error {
        padding: 4px;
        background: rgb(var(--v-theme-background) / 95%);
        font-size: 12px;
        small { display: block; }
    }
    :deep(.v-window__controls) { align-items: center; padding: 4px; }
    :deep(.v-window__controls .v-btn) { width: 32px; height: 32px; }
    &__state {
        position: relative;
        z-index: 2;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        gap: 4px;
        height: 100%;
        width: 100%;
        padding: 8px;
        font-size: 12px;
        text-align: center;
        overflow-wrap: anywhere;
    }
    &--banner .gguide__state--error {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        small { grid-column: 1; }
        .v-btn { grid-column: 2; grid-row: 1 / 3; }
    }
}
.gguide-reopen {
    position: absolute;
    right: 16px;
    bottom: 16px;
    z-index: 1;
    &--landscape { top: calc(var(--gguide-banner-row-height, 0px) + 66px); bottom: auto; }
}
.gguide-detail {
    &__image { background: rgb(var(--v-theme-background)); }
    &__text { margin-top: 20px; white-space: pre-wrap; overflow-wrap: anywhere; }
}
</style>
