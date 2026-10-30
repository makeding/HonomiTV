<template>
    <div class="series-container">
        <!-- フィルターコントロール -->
        <div class="series-controls">
            <v-btn-toggle v-model="filter_mode" mandatory density="compact" color="primary" variant="outlined" class="series-controls__toggle">
                <v-btn value="strict" size="small">
                    <Icon icon="fluent:collections-20-regular" width="16px" class="mr-1" />
                    同シリーズ
                </v-btn>
                <v-btn value="relaxed" size="small">
                    <Icon icon="fluent:apps-20-regular" width="16px" class="mr-1" />
                    関連番組
                </v-btn>
            </v-btn-toggle>

            <v-checkbox
                v-model="show_other_channels"
                label="他CH含む"
                density="compact"
                hide-details
                color="primary"
                class="series-controls__checkbox"
            ></v-checkbox>

            <v-btn
                v-if="current_series_path !== null"
                :to="current_series_path"
                size="small"
                variant="text"
                color="primary"
                icon
                class="series-controls__series-link">
                <Icon icon="fluent:open-20-regular" width="18px" />
            </v-btn>
        </div>

        <!-- 現在表示中の情報 -->
        <div class="series-info" v-if="!is_loading && filtered_series.length > 0">
            <span class="series-info__count">
                {{ filtered_series.length }}件のシリーズ番組
            </span>
            <v-btn
                v-if="needs_refresh"
                @click="forceRefresh"
                size="x-small"
                variant="text"
                color="primary"
                icon
                class="series-info__refresh">
                <Icon icon="fluent:arrow-clockwise-20-regular" width="16px" />
            </v-btn>
        </div>

        <!-- ローディング -->
        <div v-if="is_loading" class="series-loading">
            <Icon icon="line-md:loading-twotone-loop" width="32px" class="series-loading__icon" />
            <p>シリーズ番組を検索中...</p>
        </div>

        <!-- シリーズ番組リスト -->
        <div v-else-if="filtered_series.length > 0" class="series-list">
            <router-link
                v-for="match in filtered_series"
                :key="match.program.id"
                :to="`/videos/watch/${match.program.id}`"
                class="series-item"
                :class="{
                    'series-item--current': match.program.id === playerStore.recorded_program?.id,
                }">

                <!-- サムネイル -->
                <div class="series-item__thumbnail">
                    <img class="series-item__thumbnail-image" loading="lazy" decoding="async"
                        :src="`${Utils.api_base_url}/videos/${match.program.id}/thumbnail`"
                        :alt="match.program.title">
                    <div class="series-item__thumbnail-duration">
                        {{ ProgramUtils.getProgramDuration(match.program) }}
                    </div>
                    <!-- 録画状態インジケーター -->
                    <div v-if="match.program.recorded_video.status === 'Recording'"
                         class="series-item__thumbnail-status series-item__thumbnail-status--recording">
                        <div class="series-item__thumbnail-status-dot"></div>
                        録画中
                    </div>
                </div>

                <!-- 情報 -->
                <div class="series-item__info">
                    <div class="series-item__title" v-html="ProgramUtils.decorateProgramInfo(match.program, 'title')"></div>
                    <div class="series-item__meta">
                        <div class="series-item__meta-channel" v-if="match.program.channel">
                            <div class="series-item__meta-channel-icon">
                                <div class="ch-sprite" :chid="match.program.channel.id">
                                    <img loading="lazy" :src="`${Utils.api_base_url}/channels/${match.program.channel.id}/logo`">
                                </div>
                            </div>
                            <span class="series-item__meta-channel-name">{{ match.program.channel.name }}</span>
                        </div>
                        <div class="series-item__meta-date">
                            {{ formatDate(match.program.start_time) }}
                        </div>
                    </div>
                </div>
            </router-link>
        </div>

        <!-- 取得失敗 -->
        <!-- 「見つかりません (0 件)」と取得の失敗を区別して、次の行動を示す。 -->
        <div v-else-if="is_load_failed" class="series-empty">
            <Icon icon="fluent:tv-20-regular" width="48px" class="series-empty__icon" />
            <p class="series-empty__text">シリーズ番組を取得できませんでした</p>
            <v-btn size="small" variant="text" color="text" @click="forceRefresh">
                <Icon icon="fluent:arrow-clockwise-20-regular" width="16px" class="mr-1" />
                再試行
            </v-btn>
        </div>

        <!-- 結果なし -->
        <div v-else class="series-empty">
            <Icon icon="fluent:tv-20-regular" width="48px" class="series-empty__icon" />
            <p class="series-empty__text">シリーズ番組が見つかりません</p>
            <small v-if="filter_mode === 'strict'" class="series-empty__hint">
                「関連番組」で再度お試しください
            </small>
        </div>

        <!-- APIから追加読み込みボタン -->
        <div v-if="!is_loading && has_more_api_data" class="series-load-more-container">
            <v-btn
                @click="loadMoreFromAPI"
                :loading="is_loading_more"
                variant="text"
                color="primary"
                class="series-load-more">
                <Icon v-if="!is_loading_more" icon="fluent:arrow-download-20-regular" width="18px" class="mr-1" />
                さらに読み込む
            </v-btn>
        </div>
    </div>
</template>
<script lang="ts">

import { mapStores } from 'pinia';
import { defineComponent } from 'vue';

import Message from '@/message';
import { IRecordedProgram } from '@/services/Videos';
import Videos from '@/services/Videos';
import usePlayerStore from '@/stores/PlayerStore';
import Utils, { dayjs } from '@/utils';
import { ProgramUtils } from '@/utils/ProgramUtils';

/** シリーズ番組リストの1件 */
interface SeriesMatch {
    program: IRecordedProgram;
}

export default defineComponent({
    name: 'Panel-SeriesTab',
    data() {
        return {
            // ユーティリティをテンプレートで使えるように
            Utils: Object.freeze(Utils),
            ProgramUtils: Object.freeze(ProgramUtils),

            // フィルターモード
            filter_mode: 'strict' as 'strict' | 'relaxed',

            // 他チャンネル表示フラグ
            show_other_channels: false,

            // ローディング状態
            is_loading: false,

            // 取得した全番組リスト
            all_programs: [] as IRecordedProgram[],

            // シリーズ番組リスト
            series_matches: [] as SeriesMatch[],

            // API から取得済みのページ数
            fetched_api_pages: 0,

            // API にまだデータがあるか
            has_more_api_data: true,

            // 追加読み込み中
            is_loading_more: false,

            // 現在検索中の番組ID（重複検索を防ぐ）
            current_searching_program_id: null as number | null,

            // 手動リフレッシュが必要かどうか
            needs_refresh: false,

            // 一覧の取得に失敗したことを、0 件と区別するために保持する
            is_load_failed: false,
        };
    },
    computed: {
        ...mapStores(usePlayerStore),

        // 現在の録画番組のシリーズページパス
        current_series_path(): string | null {
            const current_program = this.playerStore.recorded_program;
            if (!current_program?.series_id) return null;
            return `/series/${current_program.series_id}`;
        },

        // Series タブが実際に表示中かどうか
        is_series_tab_active(): boolean {
            return this.playerStore.video_panel_active_tab === 'Series';
        },

        // フィルタリング済みシリーズ番組
        filtered_series(): SeriesMatch[] {
            return this.series_matches;
        },

    },
    watch: {
        // 番組が変わったら再検索
        'playerStore.recorded_program': {
            handler() {
                if (!this.is_series_tab_active) return;
                this.current_searching_program_id = null;
                this.searchSeriesPrograms();
            },
            immediate: false,
        },
        // Series タブを開いた時点で初めて検索する
        'playerStore.video_panel_active_tab': {
            handler(active_tab: string) {
                if (active_tab !== 'Series') return;
                this.searchSeriesPrograms();
            },
            immediate: true,
        },
        // フィルターモードが変わったら API 条件を変えて再検索
        filter_mode() {
            if (!this.is_series_tab_active) return;
            this.current_searching_program_id = null;
            this.searchSeriesPrograms();
        },
        // 他チャンネル表示フラグが変わったら API 条件を変えて再検索
        show_other_channels() {
            if (!this.is_series_tab_active) return;
            this.current_searching_program_id = null;
            this.searchSeriesPrograms();
        },
    },
    methods: {

        // シリーズ番組を検索
        async searchSeriesPrograms() {
            // Series タブが非表示の間は何もしない。初期表示時に recorded_program がまだ未確定のまま
            // /videos/related?video_id=-1 を叩くことを避け、タブを開いた時点で遅延ロードする。
            if (!this.is_series_tab_active) return;

            let current_program = this.playerStore.recorded_program;

            // playerStore に録画番組情報がない、または初期値のままの場合は URL から取得
            if (!current_program || current_program.id < 0) {
                const video_id = this.$route.params.video_id;
                if (!video_id) return;

                let fetched_program: Awaited<ReturnType<typeof Videos.fetchVideo>>;
                try {
                    fetched_program = await Videos.fetchVideo(parseFloat(video_id as string));
                } catch (error) {
                    console.warn('[Series] Failed to fetch current recorded program. Skip series search.', error);
                    return;
                }
                if (!fetched_program) return;

                // playerStore に保存
                this.playerStore.recorded_program = fetched_program;
                current_program = fetched_program;
            }
            if (current_program.id < 0) return;

            // 同じ番組で既に検索中の場合はスキップ
            if (this.is_loading && this.current_searching_program_id === current_program.id) {
                return;
            }

            // 同じ番組・同じ API 条件で既に検索済みの場合はスキップ
            if (this.current_searching_program_id === current_program.id && this.series_matches.length > 0) {
                return;
            }

            this.current_searching_program_id = current_program.id;
            this.is_loading = true;
            this.needs_refresh = false; // リフレッシュフラグをリセット
            this.series_matches = []; // 前回の結果をクリア
            this.all_programs = [];
            this.fetched_api_pages = 0;
            this.has_more_api_data = true;
            this.is_load_failed = false;

            try {
                const result = await Videos.fetchRelatedVideos(
                    current_program.id,
                    this.filter_mode,
                    this.show_other_channels,
                    'desc',
                    1,
                );
                // result が null のときは一覧の取得に失敗している (0 件は空配列で返る)
                // 失敗を 0 件と同じ「見つかりません」にせず、失敗したことを表示する
                if (result === null) {
                    this.is_load_failed = true;
                    return;
                }

                const related_programs = result.recorded_programs;
                this.all_programs = related_programs;
                this.fetched_api_pages = 1;
                this.has_more_api_data = related_programs.length < result.total;

                // API 側で Series.vue と同等の条件でフィルタ済みの番組を表示用データへ変換する
                const program_ids = new Set<number>();
                this.series_matches = related_programs
                    .filter(program => {
                        if (program_ids.has(program.id)) {
                            return false; // 重複をスキップ
                        }
                        program_ids.add(program.id);
                        return true;
                    })
                    .map(program => ({ program }));

            } catch (error) {
                console.error('Failed to search series programs:', error);
                this.is_load_failed = true;
            } finally {
                this.is_loading = false;
            }
        },

        // API からさらに番組を読み込む（一度に5ページ）
        async loadMoreFromAPI() {
            if (!this.has_more_api_data || this.is_loading_more) return;

            const current_program = this.playerStore.recorded_program;
            if (!current_program) return;

            this.is_loading_more = true;

            try {
                const start_page = this.fetched_api_pages + 1;
                const pages_to_load = 5;
                let loaded_count = 0;

                // 既存の番組IDを記録
                const existing_program_ids = new Set(this.all_programs.map(p => p.id));

                for (let i = 0; i < pages_to_load; i++) {
                    const page = start_page + i;
                    const result = await Videos.fetchRelatedVideos(
                        current_program.id,
                        this.filter_mode,
                        this.show_other_channels,
                        'desc',
                        page,
                    );

                    if (result && result.recorded_programs.length > 0) {
                        // 重複しない番組のみ追加
                        const new_programs = result.recorded_programs.filter(p => !existing_program_ids.has(p.id));

                        if (new_programs.length > 0) {
                            this.all_programs.push(...new_programs);
                            loaded_count += new_programs.length;

                            // 新しい番組IDを記録
                            new_programs.forEach(p => existing_program_ids.add(p.id));

                            // API 側でフィルタ済みの番組を表示用データへ変換して追加
                            const new_matches = new_programs.map(program => ({ program }));
                            this.series_matches.push(...new_matches);
                        }

                        this.fetched_api_pages = page;
                        this.has_more_api_data = this.series_matches.length < result.total;
                        if (!this.has_more_api_data) {
                            break;
                        }
                    } else {
                        if (result === null) {
                            // ページ単位の取得失敗を「データなし」と同じ扱いにしない
                            // 読み込めた分だけ表示を維持し、失敗したことを通知して再度読み込めるようにする
                            Message.error('追加の番組を取得できませんでした。しばらく待ってから、もう一度お試しください。');
                        }
                        // データがなくなったら終了
                        this.has_more_api_data = false;
                        break;
                    }
                }

            } catch (error) {
                console.error('Failed to load more programs:', error);
            } finally {
                this.is_loading_more = false;
            }
        },

        // 手動リフレッシュ
        forceRefresh() {
            // 検索済みフラグをクリアして再検索
            this.current_searching_program_id = null;
            this.needs_refresh = false;
            this.searchSeriesPrograms();
        },

        // 日付をフォーマット
        formatDate(date_string: string): string {
            const date = dayjs(date_string);
            const now = dayjs();

            // 今日
            if (date.isSame(now, 'day')) {
                return `今日 ${date.format('HH:mm')}`;
            }

            // 昨日
            if (date.isSame(now.subtract(1, 'day'), 'day')) {
                return `昨日 ${date.format('HH:mm')}`;
            }

            // 今週
            if (date.isSame(now, 'week')) {
                const day_names = ['日', '月', '火', '水', '木', '金', '土'];
                return `${day_names[date.day()]}曜 ${date.format('HH:mm')}`;
            }

            // それ以前
            return date.format('M/D HH:mm');
        },
    }
});

</script>
<style lang="scss" scoped>

.series-container {
    display: flex;
    flex-direction: column;
    padding-left: 16px;
    padding-right: 16px;
    overflow-y: auto;
    @include tablet-vertical {
        margin-top: 20px;
        padding-left: 24px;
        padding-right: 24px;
    }
    @include smartphone-horizontal {
        margin-top: 12px;
    }
    @include smartphone-vertical {
        margin-top: 14px;
    }
}

.series-controls {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 16px;
    @include smartphone-vertical {
        gap: 8px;
    }

    &__toggle {
        flex-shrink: 0;
    }

    &__checkbox {
        margin-left: auto;
        flex-shrink: 0;
    }

    &__series-link {
        flex-shrink: 0;
    }
}

.series-info {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 12px;
    font-size: 13px;
    color: rgb(var(--v-theme-text-darken-1));

    &__count {
        font-weight: 600;
        color: rgb(var(--v-theme-text));
    }

    &__refresh {
        margin-left: -4px;
        opacity: 0.8;
        transition: opacity 0.2s;

        &:hover {
            opacity: 1;
        }
    }
}

.series-loading {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 48px 24px;
    color: rgb(var(--v-theme-text-darken-1));

    &__icon {
        margin-bottom: 12px;
        color: rgb(var(--v-theme-primary));
    }

    p {
        margin: 0;
        font-size: 14px;
    }
}

.series-list {
    display: flex;
    flex-direction: column;
    gap: 8px;
}

.series-item {
    display: flex;
    gap: 12px;
    padding: 10px;
    border-radius: 6px;
    background: rgb(var(--v-theme-background-lighten-1));
    transition: background-color 0.15s, transform 0.15s;
    text-decoration: none;
    color: rgb(var(--v-theme-text));
    cursor: pointer;
    user-select: none;
    min-height: 105px;
    @include smartphone-vertical {
        min-height: 80px;
    }

    &:hover {
        background: rgb(var(--v-theme-background-lighten-2));
        transform: translateX(2px);
    }

    &--current {
        background: rgba(var(--v-theme-primary), 0.12);
        border-left: 3px solid rgb(var(--v-theme-primary));
        padding-left: 7px;

        &:hover {
            background: rgba(var(--v-theme-primary), 0.18);
        }
    }

    &__thumbnail {
        position: relative;
        width: 120px;
        height: auto;
        aspect-ratio: 16 / 9;
        flex-shrink: 0;
        border-radius: 4px;
        overflow: hidden;
        background: rgb(var(--v-theme-background));
        @include smartphone-vertical {
            width: 100px;
        }

        &-image {
            display: block;
            width: 100%;
            height: 100%;
            aspect-ratio: 16 / 9;
            object-fit: cover;
            object-position: center;
        }

        &-duration {
            position: absolute;
            right: 4px;
            bottom: 4px;
            padding: 2px 4px;
            border-radius: 2px;
            background: rgba(0, 0, 0, 0.75);
            color: #fff;
            font-size: 10px;
            line-height: 1.2;
        }

        &-status {
            position: absolute;
            top: 4px;
            left: 4px;
            display: flex;
            align-items: center;
            gap: 3px;
            padding: 3px 6px;
            border-radius: 3px;
            font-size: 10px;
            line-height: 1;
            font-weight: 600;

            &--recording {
                background: rgba(244, 67, 54, 0.9);
                color: #fff;
            }

            &-dot {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: #fff;
                animation: recording-pulse 1.5s ease-in-out infinite;
            }
        }
    }

    &__info {
        display: flex;
        flex-direction: column;
        justify-content: space-between;
        gap: 6px;
        flex: 1;
        min-width: 0;
    }

    &__title {
        font-size: 13.5px;
        font-weight: 600;
        line-height: 1.5;
        overflow: hidden;
        text-overflow: ellipsis;
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 3;
        line-clamp: 3;
        word-break: break-word;
        @include smartphone-vertical {
            font-size: 13px;
            -webkit-line-clamp: 2;
            line-clamp: 2;
        }
    }

    &__meta {
        display: flex;
        flex-direction: row;
        align-items: center;
        justify-content: space-between;
        gap: 8px;
        font-size: 11.5px;
        color: rgb(var(--v-theme-text-darken-1));

        &-channel {
            display: flex;
            align-items: center;
            gap: 6px;
            min-width: 0;
            flex: 1;

            &-icon {
                display: inline-block;
                flex-shrink: 0;
                --ch-sprite-width: 32;
                --ch-sprite-height: 18;
                --ch-sprite-border-radius: 2;
                width: calc(var(--ch-sprite-width) * 1px);
                height: calc(var(--ch-sprite-height) * 1px);
                border-radius: calc(var(--ch-sprite-border-radius) * 1px);
                background: linear-gradient(150deg, rgb(var(--v-theme-gray)), rgb(var(--v-theme-background-lighten-2)));
                overflow: hidden;

                .ch-sprite {
                    width: 100%;
                    height: 100%;

                    img {
                        width: 100%;
                        height: 100%;
                        object-fit: cover;
                    }
                }
            }

            &-name {
                overflow: hidden;
                text-overflow: ellipsis;
                white-space: nowrap;
            }
        }

        &-date {
            font-weight: 500;
            flex-shrink: 0;
            white-space: nowrap;
        }
    }
}

.series-empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    padding: 48px 24px;
    color: rgb(var(--v-theme-text-darken-1));
    text-align: center;

    &__icon {
        margin-bottom: 16px;
        opacity: 0.5;
    }

    &__text {
        margin: 0 0 8px 0;
        font-size: 14px;
        font-weight: 600;
    }

    &__hint {
        font-size: 12px;
        opacity: 0.8;
    }
}

.series-load-more-container {
    display: flex;
    justify-content: center;
    margin-top: 16px;
    padding-bottom: 8px;
}

@keyframes recording-pulse {
    0%, 100% {
        opacity: 1;
    }
    50% {
        opacity: 0.3;
    }
}

</style>
