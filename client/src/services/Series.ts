
import APIClient from '@/services/APIClient';
import { IChannel } from '@/services/Channels';
import { IRecordedProgram } from '@/services/Videos';


/** シリーズ情報を表すインターフェース */
export interface ISeries {
    id: number;
    title: string;
    description: string;
    genres: { major: string; middle: string; }[];
    bangumi_subject_id: number | null;
    bangumi_subject_name: string | null;
    bangumi_subject_name_cn: string | null;
    bangumi_subject_summary: string | null;
    bangumi_subject_image_url: string | null;
    broadcast_periods: ISeriesBroadcastPeriod[];
    created_at: string;
    updated_at: string;
}

/** シリーズ情報リストを表すインターフェース */
export interface ISeriesList {
    total: number;
    series_list: ISeriesSummary[];
}

export interface ISeriesListPosition {
    page: number;
}

export interface IOnAirSeries {
    id: number;
    title: string;
    thumbnail_recorded_program_ids: number[];
    channel_ids: string[];
    recorded_episodes_count: number;
    missing_episodes_count: number;
    partially_recorded_episodes_count: number;
    weekday: number;
    broadcast_time: string;
    latest_broadcast_at: string;
}

export interface IOnAirSeriesList {
    series_list: IOnAirSeries[];
}

export interface IOnAirSeason {
    season_id: string;
    season_label: string;
    is_current: boolean;
    start_date: string;
    end_date: string;
    series_list: IOnAirSeries[];
}

export interface IOnAirSeriesListResponse {
    seasons: IOnAirSeason[];
    current_season_id: string;
}

/** シリーズ一覧に表示する概要情報 */
export interface ISeriesSummary {
    id: number;
    title: string;
    description: string;
    genres: { major: string; middle: string; }[];
    thumbnail_recorded_program_ids: number[];
    channel_ids: string[];
    official_website_url: string | null;
    bangumi_subject_id: number | null;
    bangumi_subject_name: string | null;
    bangumi_subject_name_cn: string | null;
    bangumi_subject_summary: string | null;
    bangumi_subject_image_url: string | null;
    recorded_programs_count: number;
    created_at: string;
    updated_at: string;
}

/** シリーズ放送期間を表すインターフェース */
export interface ISeriesBroadcastPeriod {
    channel: IChannel;
    start_date: string;
    end_date: string;
    recorded_programs: IRecordedProgram[];
}


class Series {

    static async fetchOnAirSeriesList(): Promise<IOnAirSeriesListResponse | null> {
        const response = await APIClient.get<IOnAirSeriesListResponse>('/series/on-air');
        if (response.type === 'error') {
            APIClient.showGenericError(response, '放送中のシリーズを取得できませんでした。');
            return null;
        }
        return response.data;
    }

    /**
     * シリーズ一覧を取得する
     * @param order ソート順序 ('desc' or 'asc')
     * @param page ページ番号
     * @returns シリーズ一覧情報 or シリーズ一覧情報の取得に失敗した場合は null
     */
    static async fetchSeriesList(order: 'desc' | 'asc' = 'desc', page: number = 1): Promise<ISeriesList | null> {

        // API リクエストを実行
        const response = await APIClient.get<ISeriesList>('/series', {
            params: {
                order,
                page,
            },
        });

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'シリーズ一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * シリーズ番組を検索する
     * @param query 検索キーワード
     * @param order ソート順序 ('desc' or 'asc')
     * @param page ページ番号
     * @returns 検索結果のシリーズ番組一覧情報 or 検索に失敗した場合は null
     */
    static async searchSeries(query: string, order: 'desc' | 'asc' = 'desc', page: number = 1): Promise<ISeriesList | null> {

        // API リクエストを実行
        const response = await APIClient.get<ISeriesList>('/series/search', {
            params: {
                query,
                order,
                page,
            },
        });

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'シリーズ番組の検索に失敗しました。');
            return null;
        }

        return response.data;
    }


    /**
     * 一覧の検索・ソート条件におけるシリーズのページ番号を取得する
     * @param series_id シリーズ ID
     * @param query 検索キーワード
     * @param order ソート順序
     * @returns ページ番号 or 取得に失敗した場合は null
     */
    static async fetchSeriesListPosition(
        series_id: number,
        query: string,
        order: 'desc' | 'asc',
    ): Promise<number | null> {

        const response = await APIClient.get<ISeriesListPosition>(`/series/${series_id}/list-position`, {
            params: { query, order },
        });
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'シリーズの表示位置を取得できませんでした。');
            return null;
        }
        return response.data.page;
    }


    /**
     * シリーズ情報を取得する
     * @param series_id シリーズ ID
     * @returns シリーズ情報 or シリーズ情報の取得に失敗した場合は null
     */
    static async fetchSeries(series_id: number): Promise<ISeries | null> {

        // API リクエストを実行
        const response = await APIClient.get<ISeries>(`/series/${series_id}`);

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'シリーズ情報を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * シリーズ概要を取得する
     * @param series_id シリーズ ID
     * @returns シリーズ概要 or シリーズ概要の取得に失敗した場合は null
     */
    static async fetchSeriesSummary(series_id: number): Promise<ISeriesSummary | null> {

        const response = await APIClient.get<ISeriesSummary>(`/series/${series_id}/summary`);
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'シリーズ概要を取得できませんでした。');
            return null;
        }
        return response.data;
    }
}

export default Series;
