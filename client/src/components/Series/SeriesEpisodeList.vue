<template>
    <div ref="seriesEpisodeListElement" class="series-episode-list">
        <div class="series-episode-list__header">
            <div class="series-episode-list__titles">
                <h3>{{title}}</h3>
                <small v-if="bangumiSubjectNameCn">{{bangumiSubjectNameCn}}</small>
            </div>
            <span>{{seriesCountLabel}}</span>
        </div>
        <div v-if="bangumiSubjectId" class="series-episode-list__bangumi">
            <div class="series-episode-list__bangumi-cover">
                <img v-if="bangumiSubjectImageUrl" :src="bangumiSubjectImageUrl" alt=""
                    loading="lazy" decoding="async">
            </div>
            <div class="series-episode-list__bangumi-profile">
                <p v-if="japaneseSummary">{{japaneseSummary}}</p>
                <p v-if="chineseSummary" class="series-episode-list__chinese-summary">{{chineseSummary}}</p>
                <a :href="`https://bgm.tv/subject/${bangumiSubjectId}`"
                    target="_blank" rel="noopener noreferrer" @click.stop>
                    Bangumi で作品情報を見る
                    <Icon icon="fluent:open-16-regular" width="13px" />
                </a>
            </div>
            <div v-if="profilePreviewFrames.length > 0" class="series-episode-list__profile-thumbnails"
                :class="[
                    `series-episode-list__profile-thumbnails--count-${profilePreviewFrames.length}`,
                    {'series-episode-list__profile-thumbnails--keyframes': hoveredProgram !== null},
                ]"
                @mouseenter="keepEpisodeThumbnailPreview"
                @mouseleave="scheduleEpisodeThumbnailPreviewReset">
                <template v-for="(frame, index) in profilePreviewFrames" :key="frame.key">
                    <a v-if="index === profilePreviewCenterIndex"
                        class="series-episode-list__profile-thumbnail"
                        :class="[
                            `series-episode-list__profile-thumbnail--position-${index}`,
                            {'series-episode-list__profile-thumbnail--slideshow': hoveredProgram === null},
                        ]"
                        :style="getThumbnailStyle(frame.program, frame.tileIndex)"
                        :href="router.resolve(getEpisodeWatchRoute(frame.program, frame.tileIndex)).href"
                        @click="openProfileCenterFrame($event, frame.program, frame.tileIndex)">
                        <span v-if="frame.tileIndex !== null">{{formatTileTime(frame.program, frame.tileIndex)}}</span>
                    </a>
                    <button v-else type="button" class="series-episode-list__profile-thumbnail"
                        :class="`series-episode-list__profile-thumbnail--position-${index}`"
                        :style="getThumbnailStyle(frame.program, frame.tileIndex)"
                        @click="selectProfileSideFrame(frame.program, frame.tileIndex)">
                        <span v-if="frame.tileIndex !== null">{{formatTileTime(frame.program, frame.tileIndex)}}</span>
                    </button>
                </template>
                <div v-if="hoveredProgram && hoveredTileIndex !== null"
                    class="series-episode-list__profile-seekbar">
                    <input type="range" min="0" :max="profileSeekbarMaxTileIndex"
                        :value="hoveredTileIndex" aria-label="キーフレーム位置"
                        @input="onProfileSeekbarInput">
                    <span>{{formatTileTime(hoveredProgram, hoveredTileIndex)}}</span>
                </div>
            </div>
        </div>
        <div v-if="is_loading" class="series-episode-list__loading">
            <div class="series-episode-list__loading-corner">放送局</div>
            <v-skeleton-loader v-for="index in 6" :key="`header-${index}`"
                class="series-episode-list__loading-heading" type="text" />
            <template v-for="row in 2" :key="`row-${row}`">
                <div class="series-episode-list__loading-channel">
                    <v-skeleton-loader type="avatar" />
                    <v-skeleton-loader type="text" />
                </div>
                <v-skeleton-loader v-for="column in (row === 1 ? 6 : 4)" :key="`episode-${row}-${column}`"
                    class="series-episode-list__loading-episode" type="image" />
            </template>
        </div>
        <div v-else class="series-episode-list__matrix-scroll"
            :class="{'series-episode-list__matrix-scroll--single-channel-wrapped': isSingleChannelWrapped}">
            <div v-if="isSingleChannelWrapped" class="series-episode-list__wrapped-channel">
                <div v-if="displayEpisodeMatrix.rows[0].channel_id" class="series-episode-list__channel-logo">
                    <div class="ch-sprite" :chid="displayEpisodeMatrix.rows[0].channel_id">
                        <img loading="lazy" decoding="async"
                            :src="`${Utils.api_base_url}/channels/${displayEpisodeMatrix.rows[0].channel_id}/logo`" alt="">
                    </div>
                </div>
                <div class="series-episode-list__channel-name">
                    <span>{{displayEpisodeMatrix.rows[0].name}}</span>
                    <small>{{displayEpisodeMatrix.rows[0].episode_label}}</small>
                </div>
            </div>
            <div class="series-episode-list__matrix"
                :class="{'series-episode-list__matrix--single-channel-wrapped': isSingleChannelWrapped}"
                :style="{'--episode-column-count': displayEpisodeMatrix.slots.length}">
                <div class="series-episode-list__corner">放送局</div>
                <div v-for="slot in displayEpisodeMatrix.slots" :key="slot.key"
                    class="series-episode-list__column-header">
                    {{slot.label}}
                </div>
                <template v-for="(channel_row, row_index) in displayEpisodeMatrix.rows" :key="channel_row.id">
                    <div class="series-episode-list__channel-logo-cell">
                        <div v-if="channel_row.channel_id" class="series-episode-list__channel-logo">
                            <div class="ch-sprite" :chid="channel_row.channel_id">
                                <img loading="lazy"
                                    decoding="async"
                                    :src="`${Utils.api_base_url}/channels/${channel_row.channel_id}/logo`"
                                alt="">
                            </div>
                        </div>
                    </div>
                    <div class="series-episode-list__channel-name">
                        <span>{{channel_row.name}}</span>
                        <small>{{channel_row.episode_label}}</small>
                    </div>
                    <template v-for="(program, slot_index) in channel_row.programs"
                        :key="displayEpisodeMatrix.slots[slot_index].key">
                        <div v-if="program" class="series-episode-list__episode">
                            <span class="series-episode-list__wrapped-slot-label">
                                {{displayEpisodeMatrix.slots[slot_index].label}}
                            </span>
                            <a v-ripple class="series-episode-list__episode-link"
                                :href="router.resolve(getEpisodeWatchRoute(program)).href"
                                @click.left.exact.prevent="openEpisodeNormally(program.id)"
                                @mousemove="onEpisodeThumbnailMouseMove($event, program)"
                                @mouseleave="onEpisodeThumbnailMouseLeave(program.id)">
                                <img loading="lazy" decoding="async"
                                    :src="`${Utils.api_base_url}/videos/${program.id}/thumbnail`"
                                    alt="">
                                <div v-if="hoveredProgramID === program.id && hoveredTileIndex !== null"
                                    class="series-episode-list__episode-tile-preview"
                                    :style="getThumbnailStyle(program, hoveredTileIndex)"></div>
                                <div v-if="hoveredProgramID === program.id && hoveredTileIndex !== null"
                                    class="series-episode-list__episode-hover-position"
                                    :class="{'series-episode-list__episode-hover-position--right-half':
                                        hoveredPointerPositionRatio > 0.5}"
                                    :style="{left: `${hoveredPointerPositionRatio * 100}%`}">
                                    <span>{{formatTileTime(program, hoveredTileIndex)}}</span>
                                </div>
                                <div v-if="shouldShowPartialRecordingWarning(program)"
                                    class="series-episode-list__episode-partial-warning">
                                    ⚠ 一部のみ録画
                                </div>
                                <div class="series-episode-list__episode-label">
                                    <span>{{getEpisodeCaption(program)}}</span>
                                </div>
                            </a>
                            <RecordedProgramMenu :program="program" variant="Thumbnail"
                                @deleted="onProgramDeleted" />
                        </div>
                        <div v-else class="series-episode-list__episode-placeholder"
                            :class="{'series-episode-list__episode-placeholder--missing':
                                row_index === 0 &&
                                missingEpisodeSlotKeys.has(displayEpisodeMatrix.slots[slot_index].key)}">
                            <small class="series-episode-list__wrapped-slot-label">
                                {{displayEpisodeMatrix.slots[slot_index].label}}
                            </small>
                            <span v-if="row_index === 0 &&
                                missingEpisodeSlotKeys.has(displayEpisodeMatrix.slots[slot_index].key)">未録画</span>
                        </div>
                    </template>
                </template>
            </div>
        </div>
    </div>
</template>
<script lang="ts" setup>

import { computed, onBeforeUnmount, onMounted, ref } from 'vue';
import { useRouter } from 'vue-router';

import { collapseBatchEpisodeColumns } from '@/components/Series/SeriesEpisodeMatrix';
import RecordedProgramMenu from '@/components/Videos/RecordedProgramMenu.vue';
import { IRecordedProgram } from '@/services/Videos';
import Videos from '@/services/Videos';
import Utils, { dayjs } from '@/utils';

const props = defineProps<{
    seriesId: number;
    title: string;
    description: string;
    bangumiSubjectId: number | null;
    bangumiSubjectName: string | null;
    bangumiSubjectNameCn: string | null;
    bangumiSubjectSummary: string | null;
    bangumiSubjectImageUrl: string | null;
}>();
const emit = defineEmits<{
    (e: 'heightChanged', height: number): void;
}>();
const router = useRouter();

interface IEpisodeSlot {
    key: string;
    label: string;
}

interface IChannelRow {
    id: string;
    channel_id: string | null;
    name: string;
    episode_label: string;
    programs: Array<IRecordedProgram | null>;
}

const programs = ref<IRecordedProgram[]>([]);
const seriesEpisodeListElement = ref<HTMLElement | null>(null);
const total_programs = ref(0);
const is_loading = ref(true);
const episode_number_collator = new Intl.Collator('ja', { numeric: true });
const activeProfileThumbnailIndex = ref(0);
const activeProfileRandomFrame = ref<{programID: number; tileIndex: number} | null>(null);
const profileRepresentativeRoundCount = ref(0);
const profileRandomFrameCount = ref(0);
const hoveredProgramID = ref<number | null>(null);
const hoveredTileIndex = ref<number | null>(null);
const hoveredPointerPositionRatio = ref(0);
let profileThumbnailTimerID: number | null = null;
let heightResizeObserver: ResizeObserver | null = null;
let episodeThumbnailPreviewResetTimerID: number | null = null;
let episodeThumbnailPreviewAnimationFrameID: number | null = null;
let episodeThumbnailPreviewHoverIntentTimerID: number | null = null;
let episodeThumbnailPreviewSwitchSuppressionUntil = 0;
const profileRandomProgramIDs = new Set<number>();
let pendingEpisodeThumbnailPreview: {
    programID: number;
    tileIndex: number;
    positionRatio: number;
} | null = null;

const programSummary = computed(() => props.description.trim());
const bangumiSummary = computed(() => props.bangumiSubjectSummary?.trim() ?? '');
const splitBangumiSummary = computed(() => {
    const summaryParts = bangumiSummary.value.split(/[【[][简簡]介原文[】\]]/, 2);
    return {
        chinese: summaryParts[0]?.trim() ?? '',
        original: summaryParts[1]?.trim() ?? '',
    };
});
const japaneseSummary = computed(() => {
    return splitBangumiSummary.value.original.length >= 12
        ? splitBangumiSummary.value.original
        : programSummary.value;
});
const chineseSummary = computed(() => splitBangumiSummary.value.chinese);

// 右側の余白には、同じ話数の別局版を重ねず最近の異なる話だけを順番に表示する。
const profileThumbnailPrograms = computed(() => {
    const uniquePrograms: IRecordedProgram[] = [];
    const observedSlots = new Set<string>();
    for (const program of [...programs.value].reverse()) {
        const slotKey = getEpisodeSlots(program)[0]?.key ?? `program:${program.id}`;
        if (observedSlots.has(slotKey)) continue;
        observedSlots.add(slotKey);
        uniquePrograms.push(program);
        if (uniquePrograms.length === 5) break;
    }
    return uniquePrograms;
});
const hoveredProgram = computed(() => {
    if (hoveredProgramID.value === null) return null;
    return programs.value.find(program => program.id === hoveredProgramID.value) ?? null;
});

// 通常時は異なる回の代表画像を、hover 中は同じ回の前後タイルを Cover Flow 風に並べる。
const profilePreviewFrames = computed(() => {
    const program = hoveredProgram.value;
    const centerTileIndex = hoveredTileIndex.value;
    const tileInfo = program?.recorded_video.thumbnail_info?.tile;
    if (program && centerTileIndex !== null && tileInfo) {
        // 左右の補助フレームは隣接タイルでは変化が乏しいため、動画尺の 1/60 を基準に 15～45 秒離す。
        // 30 分番組なら約 30 秒となり、現在位置を見失わず別シーンを比較できる。
        const previewIntervalSeconds = Math.min(45, Math.max(15, program.recorded_video.duration / 60));
        const previewTileOffset = Math.max(1, Math.round(previewIntervalSeconds / tileInfo.interval_sec));
        const tileIndexes = [
            Math.max(0, centerTileIndex - previewTileOffset * 2),
            Math.max(0, centerTileIndex - previewTileOffset),
            centerTileIndex,
            Math.min(tileInfo.total_tiles - 1, centerTileIndex + previewTileOffset),
            Math.min(tileInfo.total_tiles - 1, centerTileIndex + previewTileOffset * 2),
        ];
        return tileIndexes.map((tileIndex, position) => ({
            key: `${program.id}:${tileIndex}:${position}`,
            program,
            tileIndex,
        }));
    }
    const previewPrograms = profileThumbnailPrograms.value;
    if (previewPrograms.length === 0) return [];
    const randomFrame = activeProfileRandomFrame.value;
    if (randomFrame !== null) {
        const randomProgram = previewPrograms.find(program => program.id === randomFrame.programID);
        if (randomProgram) {
            return [{
                key: `${randomProgram.id}:${randomFrame.tileIndex}:random`,
                program: randomProgram,
                tileIndex: randomFrame.tileIndex,
            }];
        }
    }
    const previewProgram = previewPrograms[activeProfileThumbnailIndex.value % previewPrograms.length];
    return [{
        key: `${previewProgram.id}:representative`,
        program: previewProgram,
        tileIndex: null,
    }];
});
const profilePreviewCenterIndex = computed(() => Math.floor(profilePreviewFrames.value.length / 2));
const profileSeekbarMaxTileIndex = computed(() => {
    return Math.max(0, (hoveredProgram.value?.recorded_video.thumbnail_info?.tile.total_tiles ?? 1) - 1);
});

// mousemove / range input は非常に高頻度で発火するため、表示更新は描画フレームごとに最新の位置だけを反映する。
const scheduleEpisodeThumbnailPreviewUpdate = (
    programID: number,
    tileIndex: number,
    positionRatio: number,
) => {
    pendingEpisodeThumbnailPreview = {programID, tileIndex, positionRatio};
    if (episodeThumbnailPreviewAnimationFrameID !== null) return;
    episodeThumbnailPreviewAnimationFrameID = window.requestAnimationFrame(() => {
        episodeThumbnailPreviewAnimationFrameID = null;
        const preview = pendingEpisodeThumbnailPreview;
        pendingEpisodeThumbnailPreview = null;
        if (preview === null) return;
        keepEpisodeThumbnailPreview();
        hoveredProgramID.value = preview.programID;
        hoveredPointerPositionRatio.value = preview.positionRatio;
        hoveredTileIndex.value = preview.tileIndex;
    });
};

// デスクトップではサムネイル上の横位置をタイル番号へ対応させ、動画を開かず内容を拾い見できるようにする。
// タッチ端末では横スクロール操作と競合するため、hover と精密ポインターの両方を持つ端末だけで有効にする。
const onEpisodeThumbnailMouseMove = (event: MouseEvent, program: IRecordedProgram) => {
    if (!window.matchMedia('(hover: hover) and (pointer: fine)').matches) return;
    const tileInfo = program.recorded_video.thumbnail_info?.tile;
    if (!tileInfo || tileInfo.total_tiles <= 0) return;
    const element = event.currentTarget as HTMLElement;
    const rect = element.getBoundingClientRect();
    const positionRatio = Math.min(0.999999, Math.max(0, (event.clientX - rect.left) / rect.width));
    pendingEpisodeThumbnailPreview = {
        programID: program.id,
        tileIndex: Math.floor(positionRatio * tileInfo.total_tiles),
        positionRatio,
    };
    if (hoveredProgramID.value === program.id) {
        // 上側の Finder 舞台へ向かう動きが始まったら、途中で横切る別の話へ切り替わらないよう短時間固定する。
        if (event.movementY < -1) {
            episodeThumbnailPreviewSwitchSuppressionUntil = window.performance.now() + 500;
        }
        scheduleEpisodeThumbnailPreviewUpdate(
            program.id,
            pendingEpisodeThumbnailPreview.tileIndex,
            positionRatio,
        );
        return;
    }
    if (hoveredProgramID.value !== null && event.movementY < -1) {
        episodeThumbnailPreviewSwitchSuppressionUntil = window.performance.now() + 500;
    }
    if (window.performance.now() < episodeThumbnailPreviewSwitchSuppressionUntil) return;
    // 一覧を横切っただけでは Finder 表示へ切り替えず、同じカード上に短時間留まった時だけ有効化する。
    if (episodeThumbnailPreviewHoverIntentTimerID !== null) return;
    episodeThumbnailPreviewHoverIntentTimerID = window.setTimeout(() => {
        episodeThumbnailPreviewHoverIntentTimerID = null;
        const preview = pendingEpisodeThumbnailPreview;
        if (preview === null || preview.programID !== program.id) return;
        scheduleEpisodeThumbnailPreviewUpdate(
            preview.programID,
            preview.tileIndex,
            preview.positionRatio,
        );
    }, 180);
};

const keepEpisodeThumbnailPreview = () => {
    if (episodeThumbnailPreviewResetTimerID === null) return;
    window.clearTimeout(episodeThumbnailPreviewResetTimerID);
    episodeThumbnailPreviewResetTimerID = null;
};

const scheduleEpisodeThumbnailPreviewReset = () => {
    keepEpisodeThumbnailPreview();
    episodeThumbnailPreviewResetTimerID = window.setTimeout(() => {
        hoveredProgramID.value = null;
        hoveredTileIndex.value = null;
        hoveredPointerPositionRatio.value = 0;
        episodeThumbnailPreviewResetTimerID = null;
    }, 3000);
};

const onEpisodeThumbnailMouseLeave = (programID: number) => {
    pendingEpisodeThumbnailPreview = null;
    if (episodeThumbnailPreviewHoverIntentTimerID !== null) {
        window.clearTimeout(episodeThumbnailPreviewHoverIntentTimerID);
        episodeThumbnailPreviewHoverIntentTimerID = null;
    }
    if (episodeThumbnailPreviewAnimationFrameID !== null) {
        window.cancelAnimationFrame(episodeThumbnailPreviewAnimationFrameID);
        episodeThumbnailPreviewAnimationFrameID = null;
    }
    if (hoveredProgramID.value === programID) scheduleEpisodeThumbnailPreviewReset();
};

const getThumbnailStyle = (program: IRecordedProgram, tileIndex: number | null) => {
    const tileInfo = program.recorded_video.thumbnail_info?.tile;
    if (!tileInfo || tileIndex === null) {
        return {backgroundImage: `url(${Utils.api_base_url}/videos/${program.id}/thumbnail)`};
    }
    const column = tileIndex % tileInfo.column_count;
    const row = Math.floor(tileIndex / tileInfo.column_count);
    return {
        backgroundImage: `url(${Utils.api_base_url}/videos/${program.id}/thumbnail/tiled)`,
        backgroundPosition: `${tileInfo.column_count > 1 ? column / (tileInfo.column_count - 1) * 100 : 0}% ${
            tileInfo.row_count > 1 ? row / (tileInfo.row_count - 1) * 100 : 0}%`,
        backgroundSize: `${tileInfo.column_count * 100}% ${tileInfo.row_count * 100}%`,
    };
};

const formatTileTime = (program: IRecordedProgram, tileIndex: number): string => {
    const tileInfo = program.recorded_video.thumbnail_info?.tile;
    const seconds = Math.min(program.recorded_video.duration, tileIndex * (tileInfo?.interval_sec ?? 0));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    const remainingSeconds = Math.floor(seconds % 60);
    return hours > 0
        ? `${hours}:${String(minutes).padStart(2, '0')}:${String(remainingSeconds).padStart(2, '0')}`
        : `${minutes}:${String(remainingSeconds).padStart(2, '0')}`;
};

// href 自体には hover 中の秒数を含め、ブラウザー標準の右クリック・新規タブでその位置から開けるようにする。
const getEpisodeWatchRoute = (program: IRecordedProgram, explicitTileIndex: number | null = null) => {
    const tileInfo = program.recorded_video.thumbnail_info?.tile;
    const tileIndex = explicitTileIndex ?? (hoveredProgramID.value === program.id ? hoveredTileIndex.value : null);
    return {
        path: `/videos/watch/${program.id}`,
        query: tileInfo && tileIndex !== null ? {t: String(tileIndex * tileInfo.interval_sec)} : {},
    };
};

// 通常の左クリックは従来通り視聴履歴を優先し、hover プレビューの位置を再生開始位置へ持ち込まない。
const openEpisodeNormally = (programID: number) => {
    router.push(`/videos/watch/${programID}`);
};

// Cover Flow の中央だけを動画へのリンクとし、左右は現在フレームを送るための操作に限定する。
const openProfileCenterFrame = (event: MouseEvent, program: IRecordedProgram, tileIndex: number | null) => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    router.push(getEpisodeWatchRoute(program, tileIndex));
};

const selectProfileSideFrame = (program: IRecordedProgram, tileIndex: number | null) => {
    keepEpisodeThumbnailPreview();
    if (hoveredProgram.value && tileIndex !== null) {
        hoveredProgramID.value = program.id;
        hoveredTileIndex.value = tileIndex;
        hoveredPointerPositionRatio.value = profileSeekbarMaxTileIndex.value > 0
            ? tileIndex / profileSeekbarMaxTileIndex.value
            : 0;
        return;
    }
    const programIndex = profileThumbnailPrograms.value.findIndex(candidate => candidate.id === program.id);
    if (programIndex >= 0) activeProfileThumbnailIndex.value = programIndex;
};

const onProfileSeekbarInput = (event: Event) => {
    const tileIndex = Number((event.currentTarget as HTMLInputElement).value);
    const programID = hoveredProgramID.value;
    if (programID === null) return;
    const positionRatio = profileSeekbarMaxTileIndex.value > 0
        ? tileIndex / profileSeekbarMaxTileIndex.value
        : 0;
    scheduleEpisodeThumbnailPreviewUpdate(programID, tileIndex, positionRatio);
};

const getEpisodeSlots = (program: IRecordedProgram): IEpisodeSlot[] => {
    if (program.episode_number) {
        const range_match = program.episode_number.match(/^(\d+)-(\d+)$/);
        const episode_numbers = range_match
            ? Array.from(
                { length: Number(range_match[2]) - Number(range_match[1]) + 1 },
                (_, index) => String(Number(range_match[1]) + index),
            )
            : program.episode_number.split('・');
        return episode_numbers.map(episode_number => ({
            key: `episode:${episode_number}`,
            label: `第${episode_number}話`,
        }));
    }
    const date = dayjs(program.start_time).format('YYYY-MM-DD');
    return [{
        key: `date:${date}`,
        label: dayjs(program.start_time).format('M/D'),
    }];
};

// 同じチャンネル・同じ自然話数に複数の録画がある場合、実際に視聴できる品質が最も高い録画を代表にする。
// 完全録画を最優先し、すべて部分録画なら実ファイルの録画時間が最も長いものを選ぶ。
const shouldReplaceEpisodeProgram = (
    currentProgram: IRecordedProgram,
    candidateProgram: IRecordedProgram,
): boolean => {
    if (currentProgram.is_partially_recorded !== candidateProgram.is_partially_recorded) {
        return candidateProgram.is_partially_recorded === false;
    }
    if (currentProgram.recorded_video.duration !== candidateProgram.recorded_video.duration) {
        return candidateProgram.recorded_video.duration > currentProgram.recorded_video.duration;
    }
    const startTimeComparison = dayjs(candidateProgram.start_time).valueOf() - dayjs(currentProgram.start_time).valueOf();
    if (startTimeComparison !== 0) return startTimeComparison > 0;
    return candidateProgram.id > currentProgram.id;
};

// 放送局が異なっていても同じ自然話数の完全録画が一つあれば、作品として視聴可能なので部分録画警告は抑止する。
const completeEpisodeSlotKeys = computed(() => new Set(
    programs.value
        .filter(program => program.is_partially_recorded === false)
        .flatMap(program => getEpisodeSlots(program)
            .filter(slot => slot.key.startsWith('episode:'))
            .map(slot => slot.key)),
));

const shouldShowPartialRecordingWarning = (program: IRecordedProgram): boolean => {
    if (program.is_partially_recorded === false) return false;
    const episodeSlots = getEpisodeSlots(program).filter(slot => slot.key.startsWith('episode:'));
    if (episodeSlots.length === 0) return true;
    return episodeSlots.some(slot => completeEpisodeSlotKeys.value.has(slot.key) === false);
};

const episode_matrix = computed<{ slots: IEpisodeSlot[]; rows: IChannelRow[] }>(() => {
    const observedSlots = [...new Map(programs.value.flatMap(program =>
        getEpisodeSlots(program).map(slot => [slot.key, slot] as const),
    )).values()].sort((left, right) => episode_number_collator.compare(left.key, right.key));
    const integerEpisodeNumbers = observedSlots
        .filter(slot => /^episode:\d+$/.test(slot.key))
        .map(slot => Number(slot.key.slice('episode:'.length)));
    const continuousEpisodeSlots = integerEpisodeNumbers.length > 0
        ? Array.from(
            {
                length: Math.max(...integerEpisodeNumbers) - Math.min(...integerEpisodeNumbers) + 1,
            },
            (_, index): IEpisodeSlot => {
                const episodeNumber = Math.min(...integerEpisodeNumbers) + index;
                return {key: `episode:${episodeNumber}`, label: `第${episodeNumber}話`};
            },
        )
        : [];
    const otherSlots = observedSlots.filter(slot => !/^episode:\d+$/.test(slot.key));
    const slots = [...continuousEpisodeSlots, ...otherSlots];

    const groups = new Map<string, {
        id: string;
        channel_id: string | null;
        name: string;
        programs: Map<string, IRecordedProgram>;
    }>();
    for (const program of programs.value) {
        const id = program.channel?.id ?? 'unknown';
        const group = groups.get(id) ?? {
            id,
            channel_id: program.channel?.id ?? null,
            name: program.channel?.name ?? 'チャンネル情報なし',
            programs: new Map<string, IRecordedProgram>(),
        };
        for (const slot of getEpisodeSlots(program)) {
            const currentProgram = group.programs.get(slot.key);
            if (currentProgram === undefined || shouldReplaceEpisodeProgram(currentProgram, program)) {
                group.programs.set(slot.key, program);
            }
        }
        groups.set(id, group);
    }

    const rows = [...groups.values()].map(group => ({
        id: group.id,
        channel_id: group.channel_id,
        name: group.name,
        episode_label: formatEpisodeCoverage(group.programs),
        programs: slots.map(slot => group.programs.get(slot.key) ?? null),
    }));
    return { slots, rows };
});

const displayEpisodeMatrix = computed(() => collapseBatchEpisodeColumns(episode_matrix.value));

// 1 局だけで 20 件を超える長寿番組は、局間の話数比較をする必要がない。
// 横一列を延々スクロールさせず、利用可能な横幅へ折り返して一覧性を優先する。
const isSingleChannelWrapped = computed(() => {
    return displayEpisodeMatrix.value.rows.length === 1 && displayEpisodeMatrix.value.slots.length > 20;
});

// 同じ話数の別局録画は数えず、視聴者が「どこまで録れているか」を一目で把握できる表記にする。
const formatEpisodeCoverage = (programsBySlot: Map<string, IRecordedProgram>): string => {
    const recordedEpisodeCount = [...programsBySlot.keys()].filter(key => key.startsWith('episode:')).length;
    return recordedEpisodeCount > 0 ? `${recordedEpisodeCount}話録画` : `${programsBySlot.size}件の録画`;
};

// どの放送局にも録画が存在しない自然話数だけを、作品全体の「未録画」として扱う。
// 別局版を視聴できる話数まで各局行で未録画扱いすると、実際には困っていない欠番が大量に表示される。
const missingEpisodeSlotKeys = computed(() => {
    const recordedSlotKeys = new Set(programs.value.flatMap(program => getEpisodeSlots(program).map(slot => slot.key)));
    return new Set(episode_matrix.value.slots
        .filter(slot => slot.key.startsWith('episode:') && !recordedSlotKeys.has(slot.key))
        .map(slot => slot.key));
});

const seriesCountLabel = computed(() => {
    const episodeSlots = episode_matrix.value.slots.filter(slot => slot.key.startsWith('episode:'));
    if (episodeSlots.length === 0) return `${total_programs.value}件の録画`;
    const latestEpisode = episodeSlots[episodeSlots.length - 1].key.slice('episode:'.length);
    const recordedEpisodeCount = episodeSlots.length - missingEpisodeSlotKeys.value.size;
    const missingLabel = missingEpisodeSlotKeys.value.size > 0
        ? `・${missingEpisodeSlotKeys.value.size}話未録画`
        : '';
    return `第${latestEpisode}話まで・${recordedEpisodeCount}話録画${missingLabel}`;
});

const getEpisodeCaption = (program: IRecordedProgram): string => {
    return program.subtitle || dayjs(program.start_time).format('YYYY/M/D (dd) HH:mm');
};

// 共通メニューから録画ファイルを削除した場合は、Series 全体を取り直さず表示中の行列だけを更新する。
const onProgramDeleted = (programID: number) => {
    programs.value = programs.value.filter(program => program.id !== programID);
    total_programs.value = Math.max(0, total_programs.value - 1);
};

const fetchPrograms = async () => {
    is_loading.value = true;
    const first_page = await Videos.fetchVideosBySeries(props.seriesId, null, 'asc', 1);
    if (first_page) {
        const all_programs = [...first_page.recorded_programs];
        const total_pages = Math.ceil(first_page.total / 30);
        const remaining_pages = await Promise.all(
            Array.from({ length: total_pages - 1 }, (_, index) =>
                Videos.fetchVideosBySeries(props.seriesId, null, 'asc', index + 2),
            ),
        );
        for (const page of remaining_pages) {
            if (page) all_programs.push(...page.recorded_programs);
        }
        programs.value = all_programs;
        total_programs.value = first_page.total;
    }
    is_loading.value = false;
};

// 各話の代表画像を 3 周してから、3 回だけランダムな話の中盤キーフレームを挟んで変化を付ける。
// 初期表示直後には tiled thumbnail を要求せず、通常の代表画像だけで十分な時間を保つ。
const advanceProfileSlideshow = () => {
    const previewPrograms = profileThumbnailPrograms.value;
    if (previewPrograms.length === 0 || hoveredProgram.value !== null) return;
    if (activeProfileRandomFrame.value === null) {
        if (activeProfileThumbnailIndex.value < previewPrograms.length - 1) {
            activeProfileThumbnailIndex.value += 1;
            return;
        }
        profileRepresentativeRoundCount.value += 1;
        activeProfileThumbnailIndex.value = 0;
        if (profileRepresentativeRoundCount.value < 3) return;
    }
    if (profileRandomFrameCount.value < 3) {
        const candidates = previewPrograms.filter(program => {
            return !profileRandomProgramIDs.has(program.id) &&
                (program.recorded_video.thumbnail_info?.tile.total_tiles ?? 0) > 2;
        });
        if (candidates.length > 0) {
            const program = candidates[Math.floor(Math.random() * candidates.length)];
            const totalTiles = program.recorded_video.thumbnail_info!.tile.total_tiles;
            profileRandomProgramIDs.add(program.id);
            activeProfileRandomFrame.value = {
                programID: program.id,
                tileIndex: 1 + Math.floor(Math.random() * (totalTiles - 2)),
            };
            profileRandomFrameCount.value += 1;
            return;
        }
    }
    activeProfileThumbnailIndex.value = 0;
    activeProfileRandomFrame.value = null;
    profileRepresentativeRoundCount.value = 0;
    profileRandomFrameCount.value = 0;
    profileRandomProgramIDs.clear();
};

onMounted(() => {
    fetchPrograms();
    profileThumbnailTimerID = window.setInterval(advanceProfileSlideshow, 4000);

    // 親ページが一度表示した最も高い詳細を記憶できるよう、読み込みや折り返しで変わる実高さを通知する。
    if (seriesEpisodeListElement.value) {
        heightResizeObserver = new ResizeObserver((entries) => {
            const height = entries[0]?.borderBoxSize[0]?.blockSize ?? entries[0]?.contentRect.height;
            if (height !== undefined) emit('heightChanged', Math.ceil(height));
        });
        heightResizeObserver.observe(seriesEpisodeListElement.value);
    }
});

onBeforeUnmount(() => {
    heightResizeObserver?.disconnect();
    if (profileThumbnailTimerID !== null) window.clearInterval(profileThumbnailTimerID);
    if (episodeThumbnailPreviewResetTimerID !== null) window.clearTimeout(episodeThumbnailPreviewResetTimerID);
    if (episodeThumbnailPreviewAnimationFrameID !== null) {
        window.cancelAnimationFrame(episodeThumbnailPreviewAnimationFrameID);
    }
    if (episodeThumbnailPreviewHoverIntentTimerID !== null) {
        window.clearTimeout(episodeThumbnailPreviewHoverIntentTimerID);
    }
});

</script>
<style lang="scss" scoped>

.series-episode-list {
    padding: 18px 20px 20px;
    background: rgb(var(--v-theme-background-lighten-1));
    border: 1px solid rgb(var(--v-theme-background-lighten-2));
    border-radius: 8px;
    box-shadow: 0 4px 16px rgb(0 0 0 / 24%);
    @include smartphone-vertical {
        padding: 14px 12px 16px;
    }

    &__header {
        display: flex;
        align-items: flex-start;
        gap: 12px;
        margin-bottom: 14px;
        h3 {
            min-width: 0;
            overflow: hidden;
            font-size: 21px;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        span {
            flex: 0 0 auto;
            color: rgb(var(--v-theme-text-darken-1));
            font-size: 13px;
        }
    }

    &__titles {
        min-width: 0;
        h3 {
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        small {
            display: block;
            margin-top: 2px;
            overflow: hidden;
            color: rgb(var(--v-theme-text-darken-1));
            font-family: 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif;
            font-size: 12px;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
    }

    &__loading {
        display: grid;
        grid-template-columns: 142px repeat(6, 170px);
        gap: 8px;
        width: max-content;
        min-width: 100%;
        overflow: hidden;
    }

    &__loading-corner,
    &__loading-heading {
        align-self: center;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 11px;
        text-align: center;
    }

    &__loading-corner { text-align: left; }

    &__loading-heading {
        :deep(.v-skeleton-loader__text) { width: 42%; margin: 0 auto; }
    }

    &__loading-channel {
        display: grid;
        grid-template-columns: 52px 1fr;
        align-items: center;
        gap: 10px;
        min-height: 96px;
        :deep(.v-skeleton-loader__avatar) { width: 52px; height: 30px; border-radius: 4px; }
        :deep(.v-skeleton-loader__text) { width: 72px; }
    }

    &__loading-episode {
        width: 170px;
        height: calc(170px * 9 / 16);
        aspect-ratio: 16 / 9;
        overflow: hidden;
        border-radius: 6px;
        :deep(.v-skeleton-loader__bone),
        :deep(.v-skeleton-loader__image) { width: 100%; height: 100%; }
    }

    &__bangumi {
        display: grid;
        grid-template-columns: 112px minmax(240px, 68ch) minmax(280px, 1fr);
        align-items: start;
        gap: 12px;
        margin-bottom: 16px;
        padding: 10px;
        background: rgb(var(--v-theme-background-lighten-2) / 45%);
        border-radius: 7px;
        @include tablet-vertical {
            grid-template-columns: 112px minmax(0, 1fr);
        }
        @include smartphone-vertical {
            grid-template-columns: 76px minmax(0, 1fr);
        }
    }

    &__bangumi-cover {
        min-width: 0;
        > img { display: block; width: 112px; height: 158px; object-fit: cover; border-radius: 5px; }
        @include smartphone-vertical {
            > img { width: 76px; height: 108px; }
        }
    }

    &__bangumi-profile {
        display: flex;
        flex-direction: column;
        min-width: 0;
        min-height: 158px;
        max-width: 68ch;
        p {
            color: rgb(var(--v-theme-text-darken-1));
            display: -webkit-box;
            margin: 5px 0;
            overflow: hidden;
            font-size: 12px;
            line-height: 1.65;
            -webkit-box-orient: vertical;
            -webkit-line-clamp: 3;
        }
        .series-episode-list__chinese-summary {
            font-family: 'PingFang SC', 'Noto Sans CJK SC', 'Microsoft YaHei', sans-serif;
            opacity: 0.82;
        }
        > a {
            display: flex;
            align-items: center;
            align-self: flex-start;
            gap: 3px;
            margin-top: auto;
            color: rgb(var(--v-theme-primary));
            font-size: 11px;
            text-decoration: none;
        }
        @include smartphone-vertical { min-height: 108px; }
    }

    &__profile-thumbnails {
        position: relative;
        align-self: start;
        justify-self: stretch;
        width: 100%;
        max-width: 440px;
        height: 220px;
        margin: 0 auto;
        perspective: 700px;
        @include tablet-vertical { display: none; }
        @include smartphone-horizontal { display: none; }
        @include smartphone-vertical { display: none; }
    }

    &__profile-thumbnails--keyframes {
        max-width: 390px;
    }

    &__profile-seekbar {
        position: absolute;
        right: 18%;
        bottom: 8%;
        left: 18%;
        z-index: 6;
        display: flex;
        flex-direction: column;
        align-items: stretch;
        gap: 0;
        opacity: 0.48;
        transition: opacity 160ms ease;
        &:hover,
        &:focus-within { opacity: 1; }
        input {
            appearance: none;
            width: 100%;
            height: 16px;
            margin: 0;
            cursor: pointer;
            background: transparent;
            &::-webkit-slider-runnable-track {
                height: 2px;
                background: linear-gradient(90deg,
                    rgb(var(--v-theme-primary) / 62%),
                    rgb(var(--v-theme-primary) / 20%));
                border-radius: 1px;
            }
            &::-webkit-slider-thumb {
                width: 3px;
                height: 15px;
                margin-top: -6px;
                appearance: none;
                background: rgb(var(--v-theme-primary));
                border: 0;
                border-radius: 2px;
                box-shadow: 0 0 5px rgb(var(--v-theme-primary) / 55%);
            }
            &::-moz-range-track {
                height: 2px;
                background: rgb(var(--v-theme-primary) / 28%);
                border: 0;
                border-radius: 1px;
            }
            &::-moz-range-progress {
                height: 2px;
                background: rgb(var(--v-theme-primary) / 62%);
            }
            &::-moz-range-thumb {
                width: 3px;
                height: 15px;
                background: rgb(var(--v-theme-primary));
                border: 0;
                border-radius: 2px;
            }
        }
        > span {
            align-self: flex-end;
            color: rgb(var(--v-theme-text-darken-1));
            font-size: 8px;
            font-variant-numeric: tabular-nums;
            line-height: 1;
            text-align: right;
        }
    }

    &__profile-thumbnail {
        position: absolute;
        top: 50%;
        left: 16%;
        z-index: 1;
        width: 68%;
        aspect-ratio: 16 / 9;
        padding: 0;
        cursor: pointer;
        background-position: center;
        background-repeat: no-repeat;
        background-size: cover;
        border: 1px solid rgb(var(--v-theme-text) / 22%);
        border-radius: 7px;
        box-shadow: 0 5px 15px rgb(0 0 0 / 36%);
        transition: background-position 80ms linear, transform 180ms ease, opacity 180ms ease;
        span {
            position: absolute;
            right: 6px;
            bottom: 5px;
            padding: 1px 5px;
            color: white;
            font-size: 10px;
            line-height: 1.45;
            text-shadow: 0 1px 2px black;
            background: rgb(0 0 0 / 65%);
            border-radius: 3px;
        }
        &--position-0 { transform: translate(-62%, -50%) rotateY(20deg) scale(0.66); opacity: 0.52; }
        &--position-1 { z-index: 2; transform: translate(-36%, -50%) rotateY(14deg) scale(0.82); opacity: 0.76; }
        &--position-2 { z-index: 4; transform: translate(0, -50%); }
        &--position-3 { z-index: 2; transform: translate(36%, -50%) rotateY(-14deg) scale(0.82); opacity: 0.76; }
        &--position-4 { transform: translate(62%, -50%) rotateY(-20deg) scale(0.66); opacity: 0.52; }
        &--slideshow {
            animation: profile-thumbnail-fade-in 420ms ease-out both;
            @media (prefers-reduced-motion: reduce) { animation: none; }
        }
    }

    &__profile-thumbnails--count-1 &__profile-thumbnail--position-0 {
        left: 0;
        z-index: 3;
        width: 100%;
        aspect-ratio: 2.15 / 1;
        transform: translate(0, -50%);
        opacity: 1;
    }

    &__profile-thumbnails--count-2 &__profile-thumbnail--position-0 {
        transform: translate(-24%, -50%) rotateY(12deg) scale(0.9);
        opacity: 0.82;
    }

    &__profile-thumbnails--count-2 &__profile-thumbnail--position-1 {
        transform: translate(24%, -50%) rotateY(-12deg) scale(0.9);
    }

    &__profile-thumbnails--count-3 &__profile-thumbnail--position-0 {
        transform: translate(-42%, -50%) rotateY(16deg) scale(0.82);
        opacity: 0.72;
    }

    &__profile-thumbnails--count-3 &__profile-thumbnail--position-1 {
        z-index: 4;
        transform: translate(0, -50%);
        opacity: 1;
    }

    &__profile-thumbnails--count-3 &__profile-thumbnail--position-2 {
        transform: translate(42%, -50%) rotateY(-16deg) scale(0.82);
        opacity: 0.72;
    }

    &__profile-thumbnails--count-4 &__profile-thumbnail--position-0 {
        transform: translate(-52%, -50%) rotateY(18deg) scale(0.72);
        opacity: 0.6;
    }

    &__profile-thumbnails--count-4 &__profile-thumbnail--position-1 {
        z-index: 4;
        transform: translate(-12%, -50%);
        opacity: 1;
    }

    &__profile-thumbnails--count-4 &__profile-thumbnail--position-2 {
        transform: translate(25%, -50%) rotateY(-12deg) scale(0.84);
        opacity: 0.78;
    }

    &__profile-thumbnails--count-4 &__profile-thumbnail--position-3 {
        transform: translate(53%, -50%) rotateY(-18deg) scale(0.7);
        opacity: 0.58;
    }

    &__matrix-scroll {
        overflow-x: auto;
        overscroll-behavior-x: contain;
        -webkit-overflow-scrolling: touch;
    }

    &__matrix {
        display: grid;
        grid-template-columns: 60px 82px repeat(var(--episode-column-count), 170px);
        gap: 8px;
        width: max-content;
        min-width: 100%;
        @include smartphone-vertical {
            // 局情報を残しつつ、狭い画面でも次の作品が見える幅にして横スクロールの存在を伝える。
            grid-template-columns: 52px 56px repeat(var(--episode-column-count), clamp(120px, 34vw, 145px));
        }
    }

    &__wrapped-channel {
        display: none;
    }

    &__matrix--single-channel-wrapped {
        grid-template-columns: repeat(auto-fill, minmax(150px, 1fr));
        width: 100%;

        .series-episode-list__corner,
        .series-episode-list__column-header,
        .series-episode-list__channel-logo-cell,
        .series-episode-list__channel-name {
            display: none;
        }
    }

    &__matrix-scroll--single-channel-wrapped &__wrapped-channel {
        display: flex;
        align-items: center;
        gap: 10px;
        margin-bottom: 10px;
    }

    &__wrapped-slot-label {
        display: none;
    }

    &__matrix--single-channel-wrapped &__wrapped-slot-label {
        position: absolute;
        top: 6px;
        left: 7px;
        z-index: 4;
        display: block;
        padding: 1px 5px;
        color: white;
        font-size: 10px;
        line-height: 1.4;
        pointer-events: none;
        text-shadow: 0 1px 2px black;
        background: rgb(0 0 0 / 58%);
        border-radius: 3px;
    }

    &__corner,
    &__column-header {
        position: sticky;
        top: 0;
        z-index: 2;
        padding: 3px 8px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 11px;
        text-align: center;
        background: rgb(var(--v-theme-background-lighten-1));
    }

    &__corner {
        grid-column: span 2;
        text-align: left;
    }

    &__channel-logo-cell {
        position: sticky;
        left: 0;
        z-index: 3;
        display: flex;
        align-items: center;
        justify-content: center;
        pointer-events: none;
        background: transparent;
    }

    &__channel-logo {
        flex: 0 0 auto;
        --ch-sprite-width: 52;
        --ch-sprite-height: 30;
        --ch-sprite-border-radius: 4;
        width: calc(var(--ch-sprite-width) * 1px);
        height: calc(var(--ch-sprite-height) * 1px);
        overflow: hidden;
        background: linear-gradient(150deg, rgb(var(--v-theme-gray)), rgb(var(--v-theme-background-lighten-2)));
        border-radius: calc(var(--ch-sprite-border-radius) * 1px);
        box-shadow: 0 2px 7px rgb(0 0 0 / 42%);
        @include smartphone-vertical {
            --ch-sprite-width: 44;
            --ch-sprite-height: 25;
        }
    }

    &__channel-name {
        display: flex;
        flex-direction: column;
        justify-content: center;
        min-width: 0;
        span,
        small {
            display: block;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        span {
            font-size: 12px;
            font-weight: 700;
        }
        small {
            color: rgb(var(--v-theme-text-darken-1));
            font-size: 10px;
        }
    }

    &__episode,
    &__episode-placeholder {
        position: relative;
        aspect-ratio: 16 / 9;
        border-radius: 6px;
    }

    &__episode {
        overflow: hidden;
        color: white;
        background: rgb(var(--v-theme-background-lighten-2));
    }

    &__episode-link {
        position: absolute;
        inset: 0;
        overflow: hidden;
        color: white;
        text-decoration: none;
        border-radius: inherit;
        img {
            width: 100%;
            height: 100%;
            object-fit: cover;
        }
        &::after {
            position: absolute;
            inset: 0;
            content: '';
            background: linear-gradient(180deg, transparent 45%, rgb(0 0 0 / 86%) 100%);
        }
    }

    &__episode-tile-preview {
        position: absolute;
        inset: 0;
        z-index: 1;
        background-repeat: no-repeat;
        transition: background-position 70ms linear;
    }

    &__episode-hover-position {
        position: absolute;
        top: 0;
        bottom: 0;
        z-index: 3;
        width: 1px;
        pointer-events: none;
        background: rgb(var(--v-theme-primary));
        box-shadow: 0 0 5px rgb(var(--v-theme-primary));
        span {
            position: absolute;
            top: 5px;
            left: 4px;
            padding: 1px 4px;
            color: white;
            font-size: 9px;
            line-height: 1.45;
            white-space: nowrap;
            text-shadow: 0 1px 2px black;
            background: rgb(0 0 0 / 70%);
            border-radius: 3px;
        }
        &--right-half span {
            right: 4px;
            left: auto;
        }
    }

    &__episode-placeholder {
        background: rgb(var(--v-theme-background-lighten-2) / 34%);
        border: 1px dashed rgb(var(--v-theme-text-darken-1) / 18%);
        &--missing {
            display: flex;
            align-items: center;
            justify-content: center;
            color: rgb(var(--v-theme-error-lighten-1));
            background: rgb(var(--v-theme-error) / 7%);
            border-color: rgb(var(--v-theme-error) / 30%);
            span {
                font-size: 11px;
                font-weight: 700;
            }
        }
    }

    &__episode-label {
        position: absolute;
        right: 8px;
        bottom: 6px;
        left: 8px;
        z-index: 2;
        min-width: 0;
        text-shadow: 0 1px 3px rgb(0 0 0 / 85%);
        span {
            display: block;
            overflow: hidden;
            font-size: 10px;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
    }

    &__episode-partial-warning {
        position: absolute;
        top: 6px;
        left: 7px;
        z-index: 4;
        padding: 2px 5px;
        color: white;
        font-size: 9px;
        font-weight: 700;
        line-height: 1.35;
        text-shadow: 0 1px 2px rgb(0 0 0 / 85%);
        background: rgb(0 0 0 / 68%);
        border-radius: 3px;
        box-shadow: 0 1px 4px rgb(0 0 0 / 28%);
    }
}

@keyframes profile-thumbnail-fade-in {
    from {
        filter: brightness(0.82);
        opacity: 0;
    }
    to {
        filter: brightness(1);
        opacity: 1;
    }
}

</style>
