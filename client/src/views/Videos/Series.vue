<template>
    <div class="route-container">
        <HeaderBar />
        <main>
            <Navigation />
            <div class="series-programs-container-wrapper">
                <SPHeaderBar />
                <div class="series-programs-container">
                    <Breadcrumbs :crumbs="[
                        { name: 'ホーム', path: '/' },
                        { name: 'シリーズ', path: '/series/' },
                        { name: series_title, path: `/series/${series_id}`, disabled: true },
                    ]" />
                    <RecordedProgramList
                        :title="series_title"
                        :programs="programs"
                        :total="total_programs"
                        :page="current_page"
                        :sortOrder="sort_order"
                        :isLoading="is_loading"
                        :showBackButton="true"
                        :showEmptyMessage="!is_loading"
                        @update:page="updatePage"
                        @update:sortOrder="updateSortOrder($event as SortOrder)">
                        <template #after-header>
                            <div v-if="official_website_url || bangumi_subject_id" class="series-external-links">
                                <a v-if="official_website_url" :href="official_website_url"
                                    class="series-external-links__link" target="_blank" rel="noopener noreferrer">
                                    <Icon icon="fluent:globe-20-regular" width="18px" />
                                    公式サイト
                                    <Icon icon="fluent:open-16-regular" width="14px" />
                                </a>
                                <a v-if="bangumi_subject_id" :href="`https://bgm.tv/subject/${bangumi_subject_id}`"
                                    class="series-external-links__link" target="_blank" rel="noopener noreferrer">
                                    <img src="/assets/images/bangumi.svg" alt="" width="18" height="18">
                                    Bangumi
                                    <Icon icon="fluent:open-16-regular" width="14px" />
                                </a>
                            </div>
                        </template>
                    </RecordedProgramList>
                </div>
            </div>
        </main>
    </div>
</template>
<script lang="ts" setup>

import { computed, onMounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import RecordedProgramList from '@/components/Videos/RecordedProgramList.vue';
import Series from '@/services/Series';
import { IRecordedProgram, SortOrder } from '@/services/Videos';
import Videos from '@/services/Videos';
import useUserStore from '@/stores/UserStore';

// ルーター
const route = useRoute();
const router = useRouter();

// シリーズ ID
const series_id = computed(() => parseInt(route.params.series_id as string, 10));

// シリーズ名
const series_title = ref('シリーズ番組');
const official_website_url = ref<string | null>(null);
const bangumi_subject_id = ref<number | null>(null);

// 録画番組のリスト
const programs = ref<IRecordedProgram[]>([]);
const total_programs = ref(0);
const is_loading = ref(true);

// 現在のページ番号
const current_page = ref(1);

// 並び順
const sort_order = ref<'desc' | 'asc'>('desc');

// シリーズ情報を取得
const fetchSeries = async () => {
    const result = await Series.fetchSeriesSummary(series_id.value);
    if (result) {
        series_title.value = result.title;
        official_website_url.value = result.official_website_url;
        bangumi_subject_id.value = result.bangumi_subject_id;
    }
};

// シリーズに属する録画番組を取得
const fetchPrograms = async () => {
    is_loading.value = true;
    const result = await Videos.fetchVideosBySeries(series_id.value, null, sort_order.value, current_page.value);
    if (result) {
        programs.value = result.recorded_programs;
        total_programs.value = result.total;
        if (result.recorded_programs.length > 0 && series_title.value === 'シリーズ番組') {
            series_title.value = result.recorded_programs[0].series_title ?? result.recorded_programs[0].title;
        }
    }
    is_loading.value = false;
};

// ページを更新
const updatePage = async (page: number) => {
    current_page.value = page;
    is_loading.value = true;
    await router.replace({
        query: {
            ...route.query,
            page: page.toString(),
        },
    });
};

// 並び順を更新
const updateSortOrder = async (order: 'desc' | 'asc') => {
    sort_order.value = order;
    current_page.value = 1;  // ページを1に戻す
    is_loading.value = true;
    await router.replace({
        query: {
            ...route.query,
            order,
            page: '1',
        },
    });
};

// クエリパラメータが変更されたら録画番組を再取得
watch(() => route.query, async (newQuery) => {
    // ページ番号を同期
    if (newQuery.page) {
        current_page.value = parseInt(newQuery.page as string, 10);
    }
    // ソート順を同期
    if (newQuery.order) {
        sort_order.value = newQuery.order as 'desc' | 'asc';
    }
    await fetchPrograms();
}, { deep: true });

// 開始時に実行
onMounted(async () => {
    // 事前にログイン状態を同期（トークンがあればユーザー情報を取得）
    const userStore = useUserStore();
    await userStore.fetchUser();

    // クエリパラメータから初期値を設定
    if (route.query.page) {
        current_page.value = parseInt(route.query.page as string, 10);
    }
    if (route.query.order) {
        sort_order.value = route.query.order as 'desc' | 'asc';
    }

    // シリーズ情報と録画番組を取得
    await Promise.all([
        fetchSeries(),
        fetchPrograms(),
    ]);
});

</script>
<style lang="scss" scoped>

.series-programs-container-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;  // サイドナビゲーション横のフレックス子要素を親幅内で縮め、タブレット縦画面でのはみ出しを防ぐ
}

.series-programs-container {
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
        padding-top: 8px !important;
        padding-left: 8px !important;
        padding-right: 8px !important;
    }
}

.series-external-links {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: -4px 0 18px 47px;
    @include smartphone-vertical {
        margin-right: 8px;
        margin-left: 8px;
    }

    &__link {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 7px 11px;
        color: rgb(var(--v-theme-text));
        font-size: 13px;
        text-decoration: none;
        background: rgb(var(--v-theme-background-lighten-1));
        border-radius: 7px;
        transition: background-color 0.15s ease;
        &:hover {
            color: rgb(var(--v-theme-primary));
            background: rgb(var(--v-theme-background-lighten-2));
        }
    }
}

</style>
