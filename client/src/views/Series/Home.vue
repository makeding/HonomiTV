<template>
    <div class="route-container">
        <HeaderBar v-model:searchQuery="search_query" @search="updateSearchQuery" />
        <main>
            <Navigation :icon-only="true" />
            <div class="series-home-container-wrapper">
                <SPHeaderBar v-model:searchQuery="search_query" @search="updateSearchQuery" />
                <div class="series-home-container">
                    <Breadcrumbs :crumbs="[
                        { name: 'ホーム', path: '/' },
                        { name: 'シリーズ', path: '/series/', disabled: true },
                    ]" />
                    <div class="series-home-container__header">
                        <h2 class="series-home-container__title">
                            シリーズ
                            <span class="series-home-container__count">{{is_loading ? '取得中…' : `${total_series}件`}}</span>
                        </h2>
                        <div class="series-home-container__actions">
                            <v-btn to="/series/on-air" variant="tonal" color="primary"
                                prepend-icon="mdi-calendar-week">
                                放送中
                            </v-btn>
                            <v-select
                            v-model="sort_order"
                            :items="[
                                { title: '更新が新しい順', value: 'desc' },
                                { title: '更新が古い順', value: 'asc' },
                            ]"
                            item-title="title"
                            item-value="value"
                            class="series-home-container__sort"
                            color="primary"
                            bg-color="background-lighten-1"
                            variant="solo"
                            density="comfortable"
                            hide-details
                                @update:model-value="updateSortOrder($event as 'desc' | 'asc')" />
                        </div>
                    </div>

                    <div v-if="is_loading" class="series-grid">
                        <v-skeleton-loader v-for="index in 15" :key="index"
                            type="image" class="series-card series-card--skeleton" />
                    </div>
                    <div v-else-if="series_list.length > 0" ref="series_grid_element" class="series-grid">
                        <template v-for="series_row in series_rows" :key="series_row[0].id">
                            <div class="series-grid__row">
                                <button v-for="series in series_row" :key="series.id" v-ripple
                                    class="series-card"
                                    type="button"
                                    :data-series-id="series.id"
                                    :aria-expanded="expanded_series_id === series.id"
                                    @click="toggleSeries(series.id)">
                            <div class="series-card__thumbnails"
                                :class="`series-card__thumbnails--${series.thumbnail_recorded_program_ids.length}`"
                                aria-hidden="true">
                                <img v-for="(recorded_program_id, index) in series.thumbnail_recorded_program_ids"
                                    :key="recorded_program_id"
                                    class="series-card__thumbnail"
                                    :class="`series-card__thumbnail--${index + 1}`"
                                    loading="lazy"
                                    decoding="async"
                                    :src="`${Utils.api_base_url}/videos/${recorded_program_id}/thumbnail`"
                                    alt="">
                            </div>
                            <div class="series-card__overlay"></div>
                            <div class="series-card__body">
                                <h3 class="series-card__title">{{series.title}}</h3>
                                <div class="series-card__meta">
                                    <div class="series-card__meta-info">
                                        <span>{{series.recorded_programs_count}}件の録画</span>
                                        <span v-for="genre in getMajorGenres(series)" :key="genre"
                                            class="series-card__genre">{{genre}}</span>
                                    </div>
                                    <div class="series-card__channels">
                                        <div v-for="channel_id in series.channel_ids"
                                            :key="channel_id"
                                            class="series-card__channel-logo">
                                            <div class="ch-sprite" :chid="channel_id">
                                                <img loading="lazy"
                                                    decoding="async"
                                                    :src="`${Utils.api_base_url}/channels/${channel_id}/logo`"
                                                    alt="">
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                                    <Icon class="series-card__chevron"
                                        :icon="expanded_series_id === series.id
                                            ? 'fluent:chevron-up-12-regular'
                                            : 'fluent:chevron-down-12-regular'"
                                        width="22px" />
                                </button>
                            </div>
                            <SeriesEpisodeList v-if="expandedSeriesInRow(series_row)"
                                :key="`episodes-${expandedSeriesInRow(series_row)!.id}`"
                                class="series-grid__episodes"
                                :seriesId="expandedSeriesInRow(series_row)!.id"
                                :title="expandedSeriesInRow(series_row)!.title"
                                :description="expandedSeriesInRow(series_row)!.description"
                                :bangumiSubjectId="expandedSeriesInRow(series_row)!.bangumi_subject_id"
                                :bangumiSubjectName="expandedSeriesInRow(series_row)!.bangumi_subject_name"
                                :bangumiSubjectNameCn="expandedSeriesInRow(series_row)!.bangumi_subject_name_cn"
                                :bangumiSubjectSummary="expandedSeriesInRow(series_row)!.bangumi_subject_summary"
                                :bangumiSubjectImageUrl="expandedSeriesInRow(series_row)!.bangumi_subject_image_url"
                                @heightChanged="rememberDetailsHeight" />
                        </template>
                    </div>
                    <div v-else class="series-empty">
                        <Icon icon="fluent:video-clip-multiple-20-regular" width="56px" />
                        <h2>{{search_query ? 'シリーズが見つかりませんでした。' : 'シリーズ情報がありません。'}}</h2>
                        <p v-if="!search_query">シリーズ情報を持つ録画番組が追加されると、ここに表示されます。</p>
                    </div>

                    <v-pagination v-if="!is_loading && total_series > 0"
                        v-model="current_page"
                        class="series-pagination"
                        active-color="primary"
                        density="comfortable"
                        :length="Math.ceil(total_series / 50)"
                        :total-visible="Utils.isSmartphoneVertical() ? 5 : 7"
                        @update:model-value="updatePage" />

                    <!-- 詳細自体は自然な高さで表示し、過去に開いた最も高い詳細との差分だけを末尾で補う。 -->
                    <!-- 短い詳細を開いたときもカード内に空白を作らず、ページ全体の高さだけを安定させる。 -->
                    <div v-if="series_list.length > 0"
                        class="series-details-footer"
                        :style="details_footer_height !== null
                            ? {height: `${details_footer_height}px`}
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
import Series, { ISeriesSummary } from '@/services/Series';
import useUserStore from '@/stores/UserStore';
import Utils from '@/utils';

const route = useRoute();
const router = useRouter();

const series_list = ref<ISeriesSummary[]>([]);
const total_series = ref(0);
const current_page = ref(1);
const sort_order = ref<'desc' | 'asc'>('desc');
const search_query = ref('');
const is_loading = ref(true);
const is_mounted = ref(false);
const expanded_series_id = ref<number | null>(null);
const series_grid_element = ref<HTMLElement | null>(null);
const grid_column_count = ref(1);
const remembered_details_height = ref(0);
const current_details_height = ref(0);
let grid_resize_observer: ResizeObserver | null = null;

const series_rows = computed(() => {
    const rows: ISeriesSummary[][] = [];
    for (let index = 0; index < series_list.value.length; index += grid_column_count.value) {
        rows.push(series_list.value.slice(index, index + grid_column_count.value));
    }
    return rows;
});

const expandedSeriesInRow = (seriesRow: ISeriesSummary[]): ISeriesSummary | undefined => {
    return seriesRow.find(series => series.id === expanded_series_id.value);
};

// ページ内で一度表示した最も高い詳細を保持し、次に短い Series を開いたときは末尾の余白で差分を補う。
const rememberDetailsHeight = (height: number) => {
    current_details_height.value = height;
    remembered_details_height.value = Math.max(remembered_details_height.value, height);
};

// 未展開時は詳細全体、展開時は過去最高との差分だけを末尾に確保する。
// まだ実測値がない未展開時だけ CSS の初期高さを使い、最初の展開によるページ伸長も抑える。
const details_footer_height = computed<number | null>(() => {
    if (expanded_series_id.value === null) {
        return remembered_details_height.value > 0 ? remembered_details_height.value : null;
    }
    return Math.max(0, remembered_details_height.value - current_details_height.value);
});

const updateGridColumnCount = () => {
    if (!series_grid_element.value) return;
    const columns = window.getComputedStyle(series_grid_element.value).gridTemplateColumns.split(' ').length;
    grid_column_count.value = Math.max(1, columns);
};

const getRouteSeriesID = (): number | null => {
    const routeSeriesID = Array.isArray(route.params.series_id)
        ? route.params.series_id[0]
        : route.params.series_id;
    if (typeof routeSeriesID !== 'string') return null;
    const parsedSeriesID = Number.parseInt(routeSeriesID, 10);
    return Number.isFinite(parsedSeriesID) && parsedSeriesID > 0 ? parsedSeriesID : null;
};

const buildSeriesQuery = (query: string, order: 'desc' | 'asc', page: number) => ({
    ...(query ? { query } : {}),
    ...(order === 'asc' ? { order } : {}),
    ...(page > 1 ? { page: page.toString() } : {}),
});

const toggleSeries = async (seriesID: number) => {
    const targetCard = series_grid_element.value?.querySelector<HTMLElement>(`[data-series-id="${seriesID}"]`);
    const targetTopBeforeUpdate = targetCard?.getBoundingClientRect().top;
    const isClosingCurrentSeries = expanded_series_id.value === seriesID;
    await router.push({
        path: isClosingCurrentSeries ? '/series/' : `/series/${seriesID}`,
        query: buildSeriesQuery(search_query.value, sort_order.value, current_page.value),
    });

    // 既存の展開領域が消えると、下側のカードはその高さ分だけ上へ跳ねる。
    // 切り替え先カードの画面内位置を基準にスクロール差分を相殺し、視線の位置を維持する。
    if (isClosingCurrentSeries || targetCard == null || targetTopBeforeUpdate === undefined) return;
    await nextTick();
    const targetTopAfterUpdate = targetCard.getBoundingClientRect().top;
    window.scrollBy(0, targetTopAfterUpdate - targetTopBeforeUpdate);
};

// Series の詳細を開いているときだけ、Escape キーで一覧へ戻してカードを収める。
const handleEscapeKey = async (event: KeyboardEvent) => {
    if (event.key !== 'Escape' || expanded_series_id.value === null) return;
    await router.push({
        path: '/series/',
        query: buildSeriesQuery(search_query.value, sort_order.value, current_page.value),
    });
};

const syncStateFromRoute = () => {
    const parsed_page = Number.parseInt(route.query.page as string ?? '1', 10);
    current_page.value = Number.isFinite(parsed_page) && parsed_page > 0 ? parsed_page : 1;
    sort_order.value = route.query.order === 'asc' ? 'asc' : 'desc';
    search_query.value = typeof route.query.query === 'string' ? route.query.query : '';
};

const fetchSeries = async () => {
    is_loading.value = true;
    const result = search_query.value
        ? await Series.searchSeries(search_query.value, sort_order.value, current_page.value)
        : await Series.fetchSeriesList(sort_order.value, current_page.value);
    if (result) {
        series_list.value = result.series_list;
        total_series.value = result.total;
    }
    is_loading.value = false;
    await nextTick();
    updateGridColumnCount();
    await restoreExpandedSeriesFromRoute();
};

const restoreExpandedSeriesFromRoute = async () => {
    const routeSeriesID = getRouteSeriesID();
    if (routeSeriesID === null) {
        expanded_series_id.value = null;
        return;
    }

    // 指定された Series が現在のページにあれば、その場で展開する。
    if (series_list.value.some(series => series.id === routeSeriesID)) {
        expanded_series_id.value = routeSeriesID;
        return;
    }

    // 深いリンクではページ番号がない、または古いことがあるため、現在の検索・並び順での実ページを解決する。
    const targetPage = await Series.fetchSeriesListPosition(routeSeriesID, search_query.value, sort_order.value);
    if (targetPage === null) {
        expanded_series_id.value = null;
        return;
    }
    if (targetPage !== current_page.value) {
        await router.replace({
            path: `/series/${routeSeriesID}`,
            query: buildSeriesQuery(search_query.value, sort_order.value, targetPage),
        });
        return;
    }
    expanded_series_id.value = null;
};

const replaceQuery = async (query: string, order: 'desc' | 'asc', page: number) => {
    await router.replace({
        path: '/series/',
        query: buildSeriesQuery(query, order, page),
    });
};

const updateSearchQuery = async (query: string) => {
    search_query.value = query.trim();
    await replaceQuery(search_query.value, sort_order.value, 1);
};

const updateSortOrder = async (order: 'desc' | 'asc') => {
    sort_order.value = order;
    await replaceQuery(search_query.value, order, 1);
};

const updatePage = async (page: number) => {
    await replaceQuery(search_query.value, sort_order.value, page);
};

const getMajorGenres = (series: ISeriesSummary): string[] => {
    return [...new Set(series.genres.map(genre => genre.major))]
        .filter(genre => genre !== '福祉')
        .slice(0, 2);
};

watch([
    () => route.query.page,
    () => route.query.order,
    () => route.query.query,
], async () => {
    if (!is_mounted.value) return;
    syncStateFromRoute();
    await fetchSeries();
});

watch(() => route.params.series_id, async () => {
    if (!is_mounted.value || is_loading.value) return;
    await restoreExpandedSeriesFromRoute();
});

onMounted(async () => {
    window.addEventListener('keydown', handleEscapeKey);
    const userStore = useUserStore();
    await userStore.fetchUser();
    syncStateFromRoute();
    is_mounted.value = true;
    await fetchSeries();
    if (series_grid_element.value) {
        grid_resize_observer = new ResizeObserver(updateGridColumnCount);
        grid_resize_observer.observe(series_grid_element.value);
    }
});

onBeforeUnmount(() => {
    window.removeEventListener('keydown', handleEscapeKey);
    grid_resize_observer?.disconnect();
});

</script>
<style lang="scss" scoped>

.series-home-container-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;
}

.series-home-container {
    width: 100%;
    max-width: 1800px;
    padding: 20px;
    margin: 0 auto;
    @include smartphone-horizontal {
        padding: 16px 20px;
    }
    @include smartphone-vertical {
        padding: 8px;
    }

    &__header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        margin-bottom: 20px;
        @include smartphone-vertical {
            flex-wrap: wrap;
            gap: 8px;
            padding: 0 8px;
        }
    }

    &__title {
        min-width: 0;
        font-size: 24px;
        font-weight: 700;
    }

    &__count {
        margin-left: 10px;
        color: rgb(var(--v-theme-text-darken-1));
        font-size: 14px;
        font-weight: 400;
    }

    &__sort {
        flex: 0 0 190px;
        max-width: 190px;
        @include smartphone-vertical {
            flex-basis: 160px;
            max-width: 160px;
        }
    }

    &__actions {
        display: flex;
        align-items: center;
        gap: 10px;
    }
}

.series-grid {
    display: grid;
    // デスクトップでは260pxを基準に列を増やし、余った幅を各列へ均等に配分する。
    // auto-fill で空の列も確保し、件数が少ないときも同じカード幅を保つ。
    grid-template-columns: repeat(auto-fill, minmax(min(260px, 100%), 1fr));
    justify-content: start;
    gap: 12px;

    &__row {
        display: grid;
        grid-column: 1 / -1;
        grid-template-columns: subgrid;
        gap: inherit;
    }

    &__episodes {
        grid-column: 1 / -1;
        margin: 2px 0 8px;
    }
    @include tablet-horizontal {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
    }
    @include tablet-vertical {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
    }
    @include smartphone-horizontal {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 10px;
    }
    @include smartphone-vertical {
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 8px;
    }
}

.series-card {
    position: relative;
    display: block;
    aspect-ratio: 16 / 9;
    min-width: 0;
    padding: 0;
    border: 0;
    font: inherit;
    text-align: left;
    cursor: pointer;
    overflow: hidden;
    color: rgb(var(--v-theme-text));
    text-decoration: none;
    background: rgb(var(--v-theme-background-lighten-1));
    border-radius: 8px;
    box-shadow: 0 2px 6px rgb(0 0 0 / 18%);
    transition: transform 0.15s ease, box-shadow 0.15s ease;
    @include smartphone-vertical {
        aspect-ratio: 4 / 3;
    }
    &:hover {
        box-shadow: 0 5px 14px rgb(0 0 0 / 28%);
        transform: translateY(-2px);
    }

    &--skeleton {
        width: 100%;
        // Vuetify の既定の画像高さをカードの比率に揃え、読み込み前後の行の高さを保つ。
        :deep(.v-skeleton-loader__image) {
            position: absolute;
            inset: 0;
            height: 100%;
        }
    }

    &__thumbnails,
    &__overlay {
        position: absolute;
        inset: 0;
    }

    &__thumbnails {
        z-index: 0;
        background: rgb(var(--v-theme-background-lighten-2));
    }

    &__thumbnail {
        position: absolute;
        width: 92%;
        height: 92%;
        object-fit: cover;
        border: 1px solid rgb(255 255 255 / 14%);
        border-radius: 7px;
        box-shadow: 0 4px 14px rgb(0 0 0 / 42%);

        &--1 {
            top: 8%;
            left: 9%;
            z-index: 3;
        }

        &--2 {
            top: 4%;
            left: 4%;
            z-index: 2;
            transform: rotate(1deg);
        }

        &--3 {
            top: 1%;
            left: -2%;
            z-index: 1;
            transform: rotate(-2deg);
        }
    }

    &__thumbnails--1 &__thumbnail--1 {
        top: 0;
        left: 0;
        width: 100%;
        height: 100%;
        border: 0;
        border-radius: 0;
        transform: none;
    }

    &__thumbnails--2 &__thumbnail {
        width: 96%;
        height: 96%;
    }

    &__overlay {
        z-index: 4;
        background: linear-gradient(180deg, rgb(0 0 0 / 4%) 20%, rgb(0 0 0 / 82%) 100%);
    }

    &__body {
        position: absolute;
        right: 16px;
        bottom: 14px;
        left: 16px;
        z-index: 5;
        min-width: 0;
        @include smartphone-vertical {
            right: 10px;
            bottom: 9px;
            left: 10px;
        }
    }

    &__title {
        overflow: hidden;
        color: white;
        font-size: 17px;
        font-weight: 700;
        text-shadow: 0 1px 4px rgb(0 0 0 / 75%);
        text-overflow: ellipsis;
        white-space: nowrap;
        @include smartphone-vertical {
            font-size: 14px;
        }
    }

    &__meta {
        display: flex;
        align-items: center;
        margin-top: 6px;
        color: rgb(255 255 255 / 82%);
        font-size: 12px;
        text-shadow: 0 1px 3px rgb(0 0 0 / 80%);
        @include smartphone-vertical {
            margin-top: 4px;
            font-size: 11px;
        }
    }

    &__meta-info,
    &__channels {
        display: flex;
        align-items: center;
        width: 50%;
        min-width: 0;
    }

    &__meta-info {
        gap: 6px;
        overflow: hidden;
        white-space: nowrap;
        @include smartphone-vertical {
            gap: 4px;
        }
    }

    &__channels {
        justify-content: flex-end;
        gap: 5px;
        overflow: hidden;
    }

    &__genre {
        padding: 2px 6px;
        background: rgb(0 0 0 / 38%);
        border-radius: 10px;
    }

    &__channel-logo {
        flex: 0 0 auto;
        --ch-sprite-width: 44;
        --ch-sprite-height: 25;
        --ch-sprite-border-radius: 4;
        width: calc(var(--ch-sprite-width) * 1px);
        height: calc(var(--ch-sprite-height) * 1px);
        overflow: hidden;
        background: linear-gradient(150deg, rgb(var(--v-theme-gray)), rgb(var(--v-theme-background-lighten-2)));
        border-radius: calc(var(--ch-sprite-border-radius) * 1px);
        filter: drop-shadow(0 1px 2px rgb(0 0 0 / 80%));
        @include smartphone-vertical {
            --ch-sprite-width: 36;
            --ch-sprite-height: 21;
            --ch-sprite-border-radius: 3;
        }
    }

    &__chevron {
        position: absolute;
        right: 12px;
        top: 12px;
        z-index: 5;
        color: white;
        filter: drop-shadow(0 1px 3px rgb(0 0 0 / 80%));
        @include smartphone-vertical {
            top: 8px;
            right: 8px;
        }
    }
}

.series-empty {
    display: flex;
    align-items: center;
    justify-content: center;
    flex-direction: column;
    min-height: 320px;
    color: rgb(var(--v-theme-text-darken-1));
    text-align: center;
    h2 {
        margin-top: 16px;
        color: rgb(var(--v-theme-text));
        font-size: 20px;
    }
    p {
        margin-top: 8px;
        font-size: 14px;
    }
}

.series-details-footer {
    height: clamp(440px, 52vh, 620px);
    pointer-events: none;
    @include smartphone-vertical {
        height: 70vh;
    }
}

.series-pagination {
    margin-top: 28px;
}

</style>
