<template>
    <div class="route-container">
        <HeaderBar />
        <main>
            <!-- 横幅のある放送中グリッドを番組表と同じ幅で表示するため、サイドバーはアイコン表示へ縮める。 -->
            <Navigation :icon-only="true" />
            <div class="on-air-wrapper">
                <SPHeaderBar />
                <div class="on-air-container">
                    <Breadcrumbs :crumbs="[
                        { name: 'ホーム', path: '/' },
                        { name: 'シリーズ', path: '/series/' },
                        { name: '放送中', path: '/series/on-air', disabled: true },
                    ]" />
                    <div class="on-air-header">
                        <div>
                            <h2>放送中</h2>
                            <div v-if="seasons.length > 1" class="on-air-season-switcher">
                                <v-btn icon="mdi-chevron-left" variant="text" size="small"
                                    :disabled="!previousSeasonId" @click="switchSeason(previousSeasonId)"></v-btn>
                                <span class="on-air-season-label">{{ currentSeasonLabel }}</span>
                                <v-btn icon="mdi-chevron-right" variant="text" size="small"
                                    :disabled="!nextSeasonId" @click="switchSeason(nextSeasonId)"></v-btn>
                            </div>
                        </div>
                        <v-btn to="/series/" variant="tonal" prepend-icon="mdi-view-grid-outline">すべてのシリーズ</v-btn>
                    </div>

                    <div ref="onAirGridElement" class="on-air-week">
                        <header v-for="day in weekdays" :key="`header-${day.index}`"
                            class="on-air-day-header"
                            :class="[
                                `on-air-day-header--${day.index}`,
                                {'on-air-day-header--attention': hasAttentionSeries(day.index)},
                            ]">
                                <h3>{{day.label}}</h3>
                                <span>{{isLoading ? '取得中…' : `${seriesByWeekday[day.index].length}件`}}</span>
                        </header>
                        <template v-if="isLoading">
                            <v-skeleton-loader v-for="cell in skeletonCells"
                                :key="`skeleton-${cell.weekday}-${cell.row}`"
                                type="image" class="on-air-card-skeleton"
                                :style="{gridColumn: cell.weekday + 1, gridRow: cell.row + 2}" />
                        </template>
                        <template v-else v-for="(seriesRow, rowIndex) in onAirRows" :key="`row-${rowIndex}`">
                            <div v-for="(series, weekday) in seriesRow" :key="`cell-${rowIndex}-${weekday}`"
                                class="on-air-cell">
                                <button v-if="series"
                                    class="on-air-card" type="button"
                                    :class="{'on-air-card--attention': isInAttentionWindow(series)}"
                                    :data-series-id="series.id"
                                    :aria-expanded="expandedSeriesID === series.id"
                                    @click="toggleSeries(series.id)">
                                    <div class="on-air-card__thumbnails"
                                        :class="`on-air-card__thumbnails--${series.thumbnail_recorded_program_ids.length}`">
                                        <img v-for="(programId, index) in series.thumbnail_recorded_program_ids"
                                            :key="programId" :class="`on-air-card__thumbnail--${index + 1}`"
                                            :src="`${Utils.api_base_url}/videos/${programId}/thumbnail`" alt=""
                                            loading="lazy" decoding="async">
                                    </div>
                                    <div class="on-air-card__shade"></div>
                                    <time class="on-air-card__time">{{getDisplayBroadcastTime(series)}}</time>
                                    <div class="on-air-card__body">
                                        <strong>{{series.title}}</strong>
                                        <div class="on-air-card__meta">
                                            <span class="on-air-card__episode-status">
                                                {{series.recorded_episodes_count > 0
                                                    ? `${series.recorded_episodes_count}話`
                                                    : '話数情報なし'}}
                                                <span v-if="series.missing_episodes_count > 0" class="on-air-card__status-warning">
                                                    ・{{series.missing_episodes_count}}話未録画
                                                </span>
                                                <span v-if="series.partially_recorded_episodes_count > 0"
                                                    class="on-air-card__status-warning">
                                                    ・{{series.partially_recorded_episodes_count}}話部分録画
                                                </span>
                                            </span>
                                            <div class="on-air-card__logos">
                                                <div v-for="channelId in series.channel_ids.slice(0, 2)" :key="channelId"
                                                    class="on-air-card__logo">
                                                    <div class="ch-sprite" :chid="channelId">
                                                        <img :src="`${Utils.api_base_url}/channels/${channelId}/logo`" alt="">
                                                    </div>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </button>
                                <div v-else class="on-air-cell__placeholder"></div>
                            </div>
                            <div v-if="expandedSeriesInRow(seriesRow)" class="on-air-week__episodes">
                                <div v-if="isSummaryLoading" class="on-air-week__loading">
                                    <v-skeleton-loader type="heading, image, paragraph, paragraph" />
                                </div>
                                <SeriesEpisodeList v-else-if="expandedSeriesSummary"
                                    :seriesId="expandedSeriesSummary.id"
                                    :title="expandedSeriesSummary.title"
                                    :description="expandedSeriesSummary.description"
                                    :bangumiSubjectId="expandedSeriesSummary.bangumi_subject_id"
                                    :bangumiSubjectName="expandedSeriesSummary.bangumi_subject_name"
                                    :bangumiSubjectNameCn="expandedSeriesSummary.bangumi_subject_name_cn"
                                    :bangumiSubjectSummary="expandedSeriesSummary.bangumi_subject_summary"
                                    :bangumiSubjectImageUrl="expandedSeriesSummary.bangumi_subject_image_url"
                                    @heightChanged="rememberDetailsHeight" />
                            </div>
                        </template>
                    </div>
                    <!-- 週間グリッド内の詳細は自然な高さにし、過去最高との差分だけをページ末尾で補う。 -->
                    <div v-if="seriesList.length > 0"
                        class="on-air-details-footer"
                        :style="detailsFooterHeight !== null
                            ? {height: `${detailsFooterHeight}px`}
                            : undefined"
                        aria-hidden="true"></div>
                </div>
            </div>
        </main>
    </div>
</template>
<script lang="ts" setup>

import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SeriesEpisodeList from '@/components/Series/SeriesEpisodeList.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import Series, { IOnAirSeason, IOnAirSeries, ISeriesSummary } from '@/services/Series';
import useSettingsStore from '@/stores/SettingsStore';
import Utils, { dayjsOriginal } from '@/utils';
import { isOnAirSeriesInAttentionWindow } from '@/views/Series/OnAirUtils';

const weekdays = [
    { index: 0, label: '月' }, { index: 1, label: '火' }, { index: 2, label: '水' },
    { index: 3, label: '木' }, { index: 4, label: '金' }, { index: 5, label: '土' },
    { index: 6, label: '日' },
];
// 実際の週間編成の密度に近く見えるよう、曜日ごとに異なる枚数の骨格を配置する。
const skeletonWeekdayCounts = [3, 2, 4, 2, 3, 3, 5];
const skeletonCells = skeletonWeekdayCounts.flatMap((count, weekday) =>
    Array.from({length: count}, (_, row) => ({weekday, row})),
);
const seasons = ref<IOnAirSeason[]>([]);
const currentSeasonId = ref<string>('');
const selectedSeasonId = ref<string>('');
const isLoading = ref(true);
const route = useRoute();
const router = useRouter();
const settingsStore = useSettingsStore();
const expandedSeriesID = ref<number | null>(null);
const expandedSeriesSummary = ref<ISeriesSummary | null>(null);
const isSummaryLoading = ref(false);
const onAirGridElement = ref<HTMLElement | null>(null);
const rememberedDetailsHeight = ref(0);
const currentDetailsHeight = ref(0);

// 一度開いた最も高い詳細を保持し、別の短い番組では末尾の余白でページ全体の収縮を防ぐ。
const rememberDetailsHeight = (height: number) => {
    currentDetailsHeight.value = height;
    rememberedDetailsHeight.value = Math.max(rememberedDetailsHeight.value, height);
};

// 未展開時は詳細全体、展開時は過去最高との差分だけを週間グリッドの外側へ確保する。
const detailsFooterHeight = computed<number | null>(() => {
    if (expandedSeriesID.value === null) {
        return rememberedDetailsHeight.value > 0 ? rememberedDetailsHeight.value : null;
    }
    return Math.max(0, rememberedDetailsHeight.value - currentDetailsHeight.value);
});

// 既存の番組表設定と同じく、28 時間表記では 0:00〜3:59 を前日の 24:00〜27:59 として並べる。
// API の曜日・時刻は自然時刻のまま保持し、このページの表示順とラベルだけを切り替える。
const getDisplayWeekday = (series: IOnAirSeries): number => {
    const hour = Number.parseInt(series.broadcast_time.slice(0, 2), 10);
    return settingsStore.settings.use_28hour_clock && hour < 4 ? (series.weekday + 6) % 7 : series.weekday;
};

const getDisplayBroadcastMinutes = (series: IOnAirSeries): number => {
    const [hour, minute] = series.broadcast_time.split(':').map(Number);
    return (settingsStore.settings.use_28hour_clock && hour < 4 ? hour + 24 : hour) * 60 + minute;
};

const getDisplayBroadcastTime = (series: IOnAirSeries): string => {
    const displayMinutes = getDisplayBroadcastMinutes(series);
    return `${Math.floor(displayMinutes / 60).toString().padStart(2, '0')}:${(displayMinutes % 60).toString().padStart(2, '0')}`;
};

const seriesByWeekday = computed(() => weekdays.map(day =>
    seriesList.value
        .filter(series => getDisplayWeekday(series) === day.index)
        .sort((first, second) => getDisplayBroadcastMinutes(first) - getDisplayBroadcastMinutes(second)),
));
const onAirRows = computed(() => {
    const rowCount = Math.max(0, ...seriesByWeekday.value.map(series => series.length));
    return Array.from({length: rowCount}, (_, rowIndex) =>
        weekdays.map(day => seriesByWeekday.value[day.index][rowIndex] ?? null),
    );
});
const currentJST = ref(dayjsOriginal().tz('Asia/Tokyo'));
let currentTimeUpdateTimer: number | null = null;

// 番組表の曜日・時刻は日本時間なので、ブラウザのローカルタイムゾーンには依存させない。
// 夜間は放送日の 20:00〜翌朝 05:00 に固定し、日中は直近 3 時間から翌朝までを対象にする。
const isInAttentionWindow = (series: IOnAirSeries): boolean => {
    return isOnAirSeriesInAttentionWindow(series, currentJST.value);
};

const hasAttentionSeries = (weekday: number): boolean => {
    return seriesByWeekday.value[weekday].some(isInAttentionWindow);
};

const expandedSeriesInRow = (seriesRow: Array<IOnAirSeries | null>): IOnAirSeries | undefined => {
    return seriesRow.find(series => series?.id === expandedSeriesID.value) ?? undefined;
};

const syncExpandedSeriesFromRoute = async () => {
    const routeSeriesID = Array.isArray(route.params.series_id)
        ? route.params.series_id[0]
        : route.params.series_id;
    const parsedSeriesID = typeof routeSeriesID === 'string' ? Number.parseInt(routeSeriesID, 10) : Number.NaN;
    if (!Number.isFinite(parsedSeriesID) || parsedSeriesID <= 0) {
        expandedSeriesID.value = null;
        expandedSeriesSummary.value = null;
        isSummaryLoading.value = false;
        return;
    }
    if (!seriesList.value.some(series => series.id === parsedSeriesID)) {
        await router.replace('/series/on-air');
        return;
    }
    expandedSeriesID.value = parsedSeriesID;
    expandedSeriesSummary.value = null;
    isSummaryLoading.value = true;
    expandedSeriesSummary.value = await Series.fetchSeriesSummary(parsedSeriesID);
    isSummaryLoading.value = false;
};

const toggleSeries = async (seriesID: number) => {
    const targetCard = onAirGridElement.value?.querySelector<HTMLElement>(`[data-series-id="${seriesID}"]`);
    const targetTopBeforeUpdate = targetCard?.getBoundingClientRect().top;
    const horizontalScrollBeforeUpdate = onAirGridElement.value?.scrollLeft;
    const isClosing = expandedSeriesID.value === seriesID;
    await router.push(isClosing ? '/series/on-air' : `/series/on-air/${seriesID}`);

    // 上の行で開いていた詳細が消えても、クリックしたカードの画面内位置を維持する。
    if (isClosing || targetCard === null || targetCard === undefined || targetTopBeforeUpdate === undefined) return;
    await nextTick();
    // モバイルでは展開位置を現在の曜日のまま保ち、詳細追加によって月曜日側へ戻らないようにする。
    if (onAirGridElement.value && horizontalScrollBeforeUpdate !== undefined) {
        onAirGridElement.value.scrollLeft = horizontalScrollBeforeUpdate;
    }
    window.scrollBy(0, targetCard.getBoundingClientRect().top - targetTopBeforeUpdate);
};

// Series の詳細を開いているときだけ、Escape キーで放送中一覧へ戻してカードを収める。
const handleEscapeKey = async (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || expandedSeriesID.value === null) return;
    await router.push('/series/on-air');
};

const currentSeason = computed(() => {
    return seasons.value.find(s => s.season_id === selectedSeasonId.value);
});

const seriesList = computed(() => {
    return currentSeason.value?.series_list ?? [];
});

const currentSeasonLabel = computed(() => {
    return currentSeason.value?.season_label ?? '';
});

const previousSeasonId = computed(() => {
    const index = seasons.value.findIndex(s => s.season_id === selectedSeasonId.value);
    return index < seasons.value.length - 1 ? seasons.value[index + 1].season_id : '';
});

const nextSeasonId = computed(() => {
    const index = seasons.value.findIndex(s => s.season_id === selectedSeasonId.value);
    return index > 0 ? seasons.value[index - 1].season_id : '';
});

const switchSeason = (seasonId: string) => {
    if (!seasonId) return;
    selectedSeasonId.value = seasonId;
    expandedSeriesID.value = null;
    expandedSeriesSummary.value = null;
};

const loadOnAirSeries = async () => {
    isLoading.value = true;
    const result = await Series.fetchOnAirSeriesList();
    if (result) {
        seasons.value = result.seasons;
        currentSeasonId.value = result.current_season_id;
        selectedSeasonId.value = result.current_season_id;
    }
    isLoading.value = false;
    await syncExpandedSeriesFromRoute();
};

onMounted(async () => {
    window.addEventListener('keydown', handleEscapeKey);
    currentTimeUpdateTimer = window.setInterval(() => {
        currentJST.value = dayjsOriginal().tz('Asia/Tokyo');
    }, 60_000);
    await loadOnAirSeries();
});

onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleEscapeKey);
    if (currentTimeUpdateTimer !== null) window.clearInterval(currentTimeUpdateTimer);
});

watch(() => route.params.series_id, async () => {
    if (isLoading.value) return;
    await syncExpandedSeriesFromRoute();
});

</script>
<style lang="scss" scoped>

.on-air-wrapper { position: relative; width: 100%; min-width: 0; }
.on-air-container { max-width: 1800px; padding: 20px; margin: 0 auto; }
.on-air-header {
    display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 20px;
    h2 { font-size: 24px; }
}
.on-air-season-switcher {
    display: flex; align-items: center; gap: 4px; margin-top: 8px;
}
.on-air-season-label {
    font-size: 13px; font-weight: 500; min-width: 90px; text-align: center;
    opacity: 0.85;
}
.on-air-week {
    display: grid; grid-template-columns: repeat(7, minmax(180px, 1fr)); gap: 10px;
    padding-bottom: 8px;
}
.on-air-card-skeleton {
    aspect-ratio: 16 / 10;
    min-width: 180px;
    :deep(.v-skeleton-loader__image) { height: 100%; }
}
.on-air-day-header {
    --on-air-day-color: #64748b;
    position: sticky;
    top: 73px;
    z-index: 8;
    display: flex; align-items: baseline; justify-content: space-between;
    min-width: 180px; padding: 6px 9px;
    color: white; background: var(--on-air-day-color); border-radius: 6px;
    h3 { font-size: 18px; text-shadow: 0 1px 2px rgb(0 0 0 / 28%); }
    span { color: rgb(255 255 255 / 88%); font-size: 11px; }
    &--0 { --on-air-day-color: #e76f51; }
    &--1 { --on-air-day-color: #e9a23b; }
    &--2 { --on-air-day-color: #84a83f; }
    &--3 { --on-air-day-color: #3aa889; }
    &--4 { --on-air-day-color: #438ac7; }
    &--5 { --on-air-day-color: #646fc1; }
    &--6 { --on-air-day-color: #ff69b4; }
    &--attention {
        box-shadow: 0 0 0 3px rgb(var(--v-theme-primary) / 16%);
    }
}
.on-air-cell {
    min-width: 0;
    &__placeholder { min-width: 0; }
}
.on-air-week {
    &__episodes {
        grid-column: 1 / -1;
        min-width: 0;
        margin: 2px 0 8px;
    }
    &__loading {
        min-height: 340px; padding: 18px;
        background: rgb(var(--v-theme-background-lighten-1)); border-radius: 8px;
    }
}
.on-air-details-footer {
    height: clamp(440px, 52vh, 620px);
    pointer-events: none;
}
.on-air-card {
    position: relative; display: block; aspect-ratio: 16 / 10; overflow: hidden;
    width: 100%; padding: 0; border: 0; font: inherit; text-align: left; cursor: pointer;
    color: white; text-decoration: none; background: rgb(var(--v-theme-background-lighten-2)); border-radius: 8px;
    box-shadow: 0 2px 6px rgb(0 0 0 / 18%);
    transition: transform 0.15s ease, box-shadow 0.15s ease, filter 0.15s ease;
    &:hover, &:focus-visible {
        z-index: 1;
        filter: brightness(1.07);
        box-shadow: 0 6px 16px rgb(0 0 0 / 34%);
        transform: translateY(-2px) scale(1.012);
    }
    @media (hover: none) {
        &:hover { filter: none; box-shadow: 0 2px 6px rgb(0 0 0 / 18%); transform: none; }
    }
    &__thumbnails, &__shade { position: absolute; inset: 0; }
    &__thumbnails img { position: absolute; width: 94%; height: 94%; object-fit: cover; border-radius: 7px; }
    &__thumbnail--1 { right: 0; bottom: 0; z-index: 3; }
    &__thumbnail--2 { top: 3%; left: 2%; z-index: 2; }
    &__thumbnail--3 { top: 0; left: -3%; z-index: 1; }
    &__thumbnails--1 img { width: 100%; height: 100%; border-radius: 0; }
    &__shade { z-index: 4; background: linear-gradient(180deg, rgb(0 0 0 / 8%), rgb(0 0 0 / 88%)); }
    &__time {
        position: absolute; top: 8px; right: 9px; z-index: 5;
        padding: 3px 6px; font-size: 16px; font-weight: 700; line-height: 1;
        background: rgb(0 0 0 / 58%); border-radius: 4px;
        text-shadow: 0 1px 2px rgb(0 0 0 / 55%);
    }
    &__body { position: absolute; right: 9px; bottom: 8px; left: 9px; z-index: 5; }
    strong { display: -webkit-box; overflow: hidden; font-size: 12px; -webkit-box-orient: vertical; -webkit-line-clamp: 2; }
    &__meta { display: flex; align-items: center; justify-content: space-between; margin-top: 5px; font-size: 10px; }
    &__episode-status { min-width: 0; }
    &__status-warning { color: rgb(var(--v-theme-warning-lighten-1)); font-weight: 700; }
    &__logos { display: flex; gap: 3px; }
    &__logo { --ch-sprite-width: 34; --ch-sprite-height: 20; --ch-sprite-border-radius: 3; overflow: hidden; border-radius: 3px; }
    &--attention {
        outline: 2px solid rgb(var(--v-theme-primary) / 78%);
        box-shadow: 0 0 0 4px rgb(var(--v-theme-primary) / 18%), 0 0 16px rgb(var(--v-theme-primary) / 34%);
    }
    &--attention::after {
        position: absolute; top: 0; right: 0; left: 0; z-index: 6;
        height: 4px; content: ''; pointer-events: none;
        background: rgb(var(--v-theme-primary));
        box-shadow: 0 2px 8px rgb(var(--v-theme-primary) / 72%);
    }
    &--attention &__time {
        background: rgb(var(--v-theme-primary) / 92%);
        box-shadow: 0 0 0 2px rgb(255 255 255 / 20%), 0 0 12px rgb(var(--v-theme-primary) / 58%);
    }
}
@include smartphone-vertical {
    .on-air-container { padding: 8px; }
    .on-air-header { align-items: flex-start; padding: 0 8px; }
    .on-air-week {
        grid-template-columns: repeat(7, min(36vw, 180px));
        gap: 8px;
        overflow-x: auto;
        scroll-snap-type: x proximity;
    }
    .on-air-day-header {
        top: 0;
        min-width: min(36vw, 180px);
        padding: 5px 7px;
        scroll-snap-align: start;
        h3 { font-size: 16px; }
    }
    .on-air-card {
        &__body { right: 7px; bottom: 6px; left: 7px; }
        &__time { top: 6px; right: 7px; padding: 2px 4px; font-size: 14px; }
        strong { font-size: 11px; line-height: 1.35; }
        &__meta { margin-top: 3px; font-size: 9px; }
        &__logo { --ch-sprite-width: 30; --ch-sprite-height: 18; --ch-sprite-border-radius: 3; }
    }
    .on-air-week__episodes {
        // 週間グリッド全体の行を使い、現在の横スクロール位置に関係なく可視領域の左端から展開する。
        position: sticky;
        left: 0;
        grid-column: 1 / -1;
        width: calc(100vw - 16px);
        max-width: 480px;
        min-width: 0;
    }
    .on-air-details-footer {
        height: 70vh;
    }
}

</style>
