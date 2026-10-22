<template>
    <div class="route-container">
        <HeaderBar />
        <main>
            <Navigation />
            <div class="recording-programs-container-wrapper">
                <SPHeaderBar />
                <div class="recording-programs-container">
                    <Breadcrumbs :crumbs="[
                        { name: 'ホーム', path: '/' },
                        { name: 'ビデオをみる', path: '/videos/' },
                        { name: '追いかけ再生', path: '/videos/recording', disabled: true },
                    ]" />
                    <RecordedProgramList
                        title="追いかけ再生"
                        :programs="programs"
                        :total="total_programs"
                        :page="current_page"
                        :hideSort="true"
                        :isLoading="is_loading"
                        :showBackButton="true"
                        :showEmptyMessage="!is_loading"
                        :emptyIcon="'fluent:video-clip-20-regular'"
                        :emptyMessage="'現在録画中の番組はありません。'"
                        :emptySubMessage="'録画中の番組はここから追いかけ再生できます。'"
                        @update:page="updatePage" />
                </div>
            </div>
        </main>
    </div>
</template>
<script lang="ts" setup>

import { onMounted, onUnmounted, ref, watch } from 'vue';
import { useRoute, useRouter } from 'vue-router';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import RecordedProgramList from '@/components/Videos/RecordedProgramList.vue';
import { IRecordedProgram, isChasePlaybackProgram } from '@/services/Videos';
import Videos from '@/services/Videos';
import useUserStore from '@/stores/UserStore';

const route = useRoute();
const router = useRouter();

// 追いかけ再生できる録画中番組のリスト
const programs = ref<IRecordedProgram[]>([]);
const total_programs = ref(0);
const is_loading = ref(true);

// 録画中の時長と早期生成サムネイルを定期的に反映するためのタイマー
const autoRefreshInterval = ref<number | null>(null);
const AUTO_REFRESH_INTERVAL = 30 * 1000;
let isRefreshing = false;
let isMounted = false;

// 現在のページ番号
const current_page = ref(1);

// 追いかけ再生できる録画中番組を取得
const fetchPrograms = async () => {
    if (isRefreshing === true) return;
    isRefreshing = true;
    try {
        // Recording のみに絞り、録画済み全件を定期取得しない。
        const first_page = await Videos.fetchVideos('desc', 1, null, 'Recording');
        if (first_page) {
            const all_programs = [...first_page.recorded_programs];
            const total_pages = Math.ceil(first_page.total / 30);
            for (let page = 2; page <= total_pages; page++) {
                const result = await Videos.fetchVideos('desc', page, null, 'Recording');
                if (result) {
                    all_programs.push(...result.recorded_programs);
                }
            }
            const recording_programs = all_programs.filter(isChasePlaybackProgram);
            if (isMounted === true) {
                programs.value = recording_programs.slice((current_page.value - 1) * 30, current_page.value * 30);
                total_programs.value = recording_programs.length;
            }
        }
        if (isMounted === true) {
            is_loading.value = false;
        }
    } finally {
        isRefreshing = false;
    }
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

// クエリパラメータが変更されたら録画中番組を再取得
watch(() => route.query, async (newQuery) => {
    if (newQuery.page) {
        current_page.value = parseInt(newQuery.page as string);
    }
    await fetchPrograms();
}, { deep: true });

// 開始時に実行
onMounted(async () => {
    isMounted = true;
    const userStore = useUserStore();
    await userStore.fetchUser();

    if (route.query.page) {
        current_page.value = parseInt(route.query.page as string);
    }

    await fetchPrograms();
    if (isMounted === true) {
        autoRefreshInterval.value = window.setInterval(fetchPrograms, AUTO_REFRESH_INTERVAL);
    }
});

// ページ離脱後に録画一覧を取得し続けないよう、定期更新を終了する
onUnmounted(() => {
    isMounted = false;
    if (autoRefreshInterval.value !== null) {
        window.clearInterval(autoRefreshInterval.value);
        autoRefreshInterval.value = null;
    }
});

</script>
<style lang="scss" scoped>

.recording-programs-container-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;  // サイドナビゲーション横のフレックス子要素を親幅内で縮め、タブレット縦画面でのはみ出しを防ぐ
}

.recording-programs-container {
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
        padding-bottom: 20px !important;
    }
}

</style>
