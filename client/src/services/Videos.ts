
import APIClient from  '@/services/APIClient';
import { IChannel } from '@/services/Channels';
import { CommentUtils } from '@/utils';

/** ソート順序を表す型 */
export type SortOrder = 'desc' | 'asc';

/** マイリストのソート順序を表す型 */
export type MylistSortOrder = 'mylist_added_desc' | 'mylist_added_asc' | 'recorded_desc' | 'recorded_asc';

/** 録画ファイル情報を表すインターフェース */
export interface IRecordedVideo {
    id: number;
    status: 'Recording' | 'Recorded' | 'AnalysisFailed';
    file_path: string;
    file_hash: string;
    file_size: number;
    file_created_at: string;
    file_modified_at: string;
    recording_start_time: string | null;
    recording_end_time: string | null;
    duration: number;
    container_format: 'MPEG-TS' | 'MPEG-4' | 'MMT/TLV';
    video_codec: 'MPEG-2' | 'H.264' | 'H.265';
    video_codec_profile: 'High' | 'High 10' | 'Main' | 'Main 10' | 'Baseline' | 'Constrained Baseline';
    video_scan_type: 'Interlaced' | 'Progressive';
    video_frame_rate: number;
    video_resolution_width: number;
    video_resolution_height: number;
    has_video_stream_changes: boolean;
    has_key_frames: boolean;
    primary_audio_codec: 'AAC-LC';
    primary_audio_channel: 'Monaural' | 'Stereo' | '3ch' | '4ch' | '5ch' | '5.1ch' | '6.1ch' | '7.1ch' | '10.2ch' | '22.2ch';
    primary_audio_sampling_rate: number;
    secondary_audio_codec: 'AAC-LC' | null;
    secondary_audio_channel: 'Monaural' | 'Stereo' | '3ch' | '4ch' | '5ch' | '5.1ch' | '6.1ch' | '7.1ch' | '10.2ch' | '22.2ch' | null;
    secondary_audio_sampling_rate: number | null;
    cm_sections: { start_time: number; end_time: number; }[] | null;
    thumbnail_info: IThumbnailInfo | null;
    created_at: string;
    updated_at: string;
}

/** サムネイル情報を表すインターフェース */
export interface IThumbnailInfo {
    version: number;
    representative: IThumbnailImageInfo;
    tile: IThumbnailTileInfo;
}

/** 代表サムネイル情報を表すインターフェース */
export interface IThumbnailImageInfo {
    format: 'WebP';
    width: number;
    height: number;
}

/** サムネイルタイル情報を表すインターフェース */
export interface IThumbnailTileInfo {
    format: 'WebP';
    image_width: number;
    image_height: number;
    tile_width: number;
    tile_height: number;
    total_tiles: number;
    column_count: number;
    row_count: number;
    interval_sec: number;
}

/** 録画ファイル情報を表すインターフェースのデフォルト値 */
export const IRecordedVideoDefault: IRecordedVideo = {
    id: -1,
    status: 'Recorded',
    file_path: '',
    file_hash: '',
    file_size: 0,
    file_created_at: '2000-01-01T00:00:00+09:00',
    file_modified_at: '2000-01-01T00:00:00+09:00',
    recording_start_time: null,
    recording_end_time: null,
    duration: 0,
    container_format: 'MPEG-TS',
    video_codec: 'MPEG-2',
    video_codec_profile: 'High',
    video_scan_type: 'Interlaced',
    video_frame_rate: 29.97,
    video_resolution_width: 1440,
    video_resolution_height: 1080,
    has_video_stream_changes: false,
    has_key_frames: true,
    primary_audio_codec: 'AAC-LC',
    primary_audio_channel: 'Stereo',
    primary_audio_sampling_rate: 48000,
    secondary_audio_codec: null,
    secondary_audio_channel: null,
    secondary_audio_sampling_rate: null,
    cm_sections: null,
    thumbnail_info: null,
    created_at: '2000-01-01T00:00:00+09:00',
    updated_at: '2000-01-01T00:00:00+09:00',
};

/** 録画番組情報を表すインターフェース */
export interface IRecordedProgram {
    id: number;
    recorded_video: IRecordedVideo;
    recording_start_margin: number;
    recording_end_margin: number;
    is_partially_recorded: boolean;
    channel: IChannel | null;
    network_id: number | null;
    service_id: number | null;
    event_id: number | null;
    series_id: number | null;
    series_broadcast_period_id: number | null;
    title: string;
    series_title: string | null;
    episode_number: string | null;
    subtitle: string | null;
    bangumi_subject_id: number | null;
    bangumi_episode_id: number | null;
    description: string;
    detail: { [key: string]: string };
    start_time: string;
    end_time: string;
    duration: number;
    is_free: boolean;
    genres: { major: string; middle: string; }[];
    primary_audio_type: string;
    primary_audio_language: string;
    secondary_audio_type: string | null;
    secondary_audio_language: string | null;
    created_at: string;
    updated_at: string;
}

/** 録画番組情報を表すインターフェースのデフォルト値 */
export const IRecordedProgramDefault: IRecordedProgram = {
    id: -1,
    recorded_video: IRecordedVideoDefault,
    recording_start_margin: 0,
    recording_end_margin: 0,
    is_partially_recorded: false,
    channel: null,
    network_id: null,
    service_id: null,
    event_id: null,
    series_id: null,
    series_broadcast_period_id: null,
    title: '取得中…',
    series_title: null,
    episode_number: null,
    subtitle: null,
    bangumi_subject_id: null,
    bangumi_episode_id: null,
    description: '取得中…',
    detail: {},
    start_time: '2000-01-01T00:00:00+09:00',
    end_time: '2000-01-01T00:00:00+09:00',
    duration: 0,
    is_free: true,
    genres: [],
    primary_audio_type: '2/0モード(ステレオ)',
    primary_audio_language: '日本語',
    secondary_audio_type: null,
    secondary_audio_language: null,
    created_at: '2000-01-01T00:00:00+09:00',
    updated_at: '2000-01-01T00:00:00+09:00',
};

const CHASE_PLAYBACK_STALE_GRACE_MS = 30 * 60 * 1000;

/**
 * 追いかけ再生として表示できる録画中番組かどうかを判定する。
 * DB に古い Recording 状態が残った場合でも、番組終了から十分時間が経ったものは表示しない。
 */
export const isChasePlaybackProgram = (program: IRecordedProgram): boolean => {
    if (program.recorded_video.status !== 'Recording') {
        return false;
    }
    const end_time_ms = new Date(program.end_time).getTime();
    if (Number.isNaN(end_time_ms)) {
        return true;
    }
    return end_time_ms + CHASE_PLAYBACK_STALE_GRACE_MS >= Date.now();
};

/** 録画番組情報リストを表すインターフェース */
export interface IRecordedPrograms {
    total: number;
    recorded_programs: IRecordedProgram[];
}

/** 過去ログコメントを表すインターフェース */
export interface IJikkyoComment {
    time: number;
    type: 'top' | 'right' | 'bottom';
    size: 'big' | 'medium' | 'small';
    color: string;
    author: string;
    text: string;
}

/** 過去ログコメントのリストを表すインターフェース */
export interface IJikkyoComments {
    is_success: boolean;
    comments: IJikkyoComment[];
    detail: string;
}


class Videos {

    /**
     * 録画番組一覧を取得する
     * @param order ソート順序 ('desc' or 'asc' or 'ids')
     * @param page ページ番号
     * @param ids 録画番組の ID のリスト
     * @param recording_status 録画状態
     * @returns 録画番組一覧情報 or 録画番組一覧情報の取得に失敗した場合は null
     */
    static async fetchVideos(
        order: 'desc' | 'asc' | 'ids' = 'desc',
        page: number = 1,
        ids: number[] | null = null,
        recording_status: IRecordedVideo['status'] | null = null,
    ): Promise<IRecordedPrograms | null> {

        // API リクエストを実行
        const response = await APIClient.get<IRecordedPrograms>('/videos', {
            params: {
                order,
                page,
                ids,
                recording_status,
            },
            // 録画番組の ID のリストを FastAPI が受け付ける &ids=1&ids=2&ids=3&... の形式にエンコードする
            // ref: https://github.com/axios/axios/issues/5058#issuecomment-1272107602
            paramsSerializer: {
                indexes: null,
            },
        });

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, '録画番組一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * 録画番組を検索する
     * @param query 検索キーワード
     * @param order ソート順序 ('desc' or 'asc')
     * @param page ページ番号
     * @returns 検索結果の録画番組一覧情報 or 検索に失敗した場合は null
     */
    static async searchVideos(query: string, order: 'desc' | 'asc' = 'desc', page: number = 1): Promise<IRecordedPrograms | null> {

        // API リクエストを実行
        const response = await APIClient.get<IRecordedPrograms>('/videos/search', {
            params: {
                query,
                order,
                page,
            },
        });

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, '録画番組の検索に失敗しました。');
            return null;
        }

        return response.data;
    }


    /**
     * 指定した録画番組に関連する録画番組を取得する
     * @param video_id 検索基準となる録画番組の ID
     * @param mode 検索モード ('strict' or 'relaxed')
     * @param include_other_channels 他チャンネルの録画番組も含めるか
     * @param order ソート順序 ('desc' or 'asc')
     * @param page ページ番号
     * @returns 関連録画番組一覧情報 or 取得に失敗した場合は null
     */
    static async fetchRelatedVideos(
        video_id: number,
        mode: 'strict' | 'relaxed' = 'strict',
        include_other_channels: boolean = false,
        order: 'desc' | 'asc' = 'desc',
        page: number = 1,
    ): Promise<IRecordedPrograms | null> {

        // API リクエストを実行
        const response = await APIClient.get<IRecordedPrograms>('/videos/related', {
            params: {
                video_id,
                mode,
                include_other_channels,
                order,
                page,
            },
        });

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, '関連する録画番組を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * 指定したシリーズに属する録画番組を取得する
     * @param series_id シリーズ ID
     * @param series_broadcast_period_id シリーズ放送期間 ID
     * @param order ソート順序 ('desc' or 'asc')
     * @param page ページ番号
     * @returns シリーズ別録画番組一覧情報 or 取得に失敗した場合は null
     */
    static async fetchVideosBySeries(
        series_id: number,
        series_broadcast_period_id: number | null = null,
        order: 'desc' | 'asc' = 'desc',
        page: number = 1,
    ): Promise<IRecordedPrograms | null> {

        // API リクエストを実行
        const response = await APIClient.get<IRecordedPrograms>(`/videos/series/${series_id}`, {
            params: {
                series_broadcast_period_id,
                order,
                page,
            },
        });

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'シリーズの録画番組を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * 録画番組情報を取得する
     * @param video_id 録画番組の ID
     * @returns 録画番組情報 or 録画番組が存在しない場合は null
     */
    static async fetchVideo(video_id: number): Promise<IRecordedProgram | null> {

        // API リクエストを実行
        const response = await APIClient.get<IRecordedProgram>(`/videos/${video_id}`);

        // エラー処理
        if (response.type === 'error') {
            // 視聴画面では null を 404 ページ遷移の合図として扱うため、404 以外の一時的なサーバーエラーは null に潰さない
            // 502 などのリバースプロキシ/サーバー再起動中エラーで NotFound に飛ぶと、実在する録画番組が消えたように見えてしまう
            APIClient.showGenericError(response, '録画番組情報を取得できませんでした。');
            if (response.status === 404) {
                return null;
            }
            throw response.error;
        }

        return response.data;
    }


    /**
     * 録画番組の放送中に投稿されたニコニコ実況の過去ログコメントを取得する
     * @param video_id 録画番組の ID
     * @returns 過去ログコメントのリスト
     */
    static async fetchVideoJikkyoComments(video_id: number): Promise<IJikkyoComments> {

        // API リクエストを実行
        const response = await APIClient.get<IJikkyoComments>(`/videos/${video_id}/jikkyo`);

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, '過去ログコメントを取得できませんでした。');
            return {
                is_success: false,
                comments: [],
                detail: '過去ログコメントを取得できませんでした。',
            };
        }

        // ミュート対象のコメントを除外して返す
        response.data.comments = response.data.comments.filter((comment) => {
            return CommentUtils.isMutedComment(comment.text, comment.author, comment.color, comment.type, comment.size) === false;
        });
        return response.data;
    }


    /**
     * 録画番組で利用可能なチャンネル一覧を取得する
     * @param video_id 録画番組の ID
     * @returns 利用可能なチャンネル一覧 or 取得に失敗した場合は null
     */
    static async fetchVideoAvailableChannels(video_id: number): Promise<Array<{service_id: number, channel_name: string}> | null> {

        // API リクエストを実行
        const response = await APIClient.get<Array<{service_id: number, channel_name: string}>>(`/videos/${video_id}/available-channels`);

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, '利用可能なチャンネル一覧を取得できませんでした。');
            return null;
        }

        return response.data;
    }


    /**
     * 録画番組のメタデータを再解析する
     * @param video_id 録画番組の ID
     * @param selected_service_id 選択されたサービス ID (オプション)
     * @returns メタデータ再解析に成功した場合は true
     */
    static async reanalyzeVideo(video_id: number, selected_service_id?: number): Promise<boolean> {

        // API リクエストを実行
        const response = await APIClient.post(`/videos/${video_id}/reanalyze`, undefined, {
            params: selected_service_id ? { selected_service_id } : undefined,
            // 数分以上かかるのでタイムアウトを 10 分に設定
            timeout: 10 * 60 * 1000,
        });

        // エラー処理
        if (response.type === 'error') {
            switch (response.data.detail) {
                default:
                    APIClient.showGenericError(response, 'メタデータの再解析に失敗しました。');
                    break;
            }
            return false;
        }

        return true;
    }


    /**
     * 録画番組のサムネイルを再生成する
     * @param video_id 録画番組の ID
     * @returns サムネイルの再生成に成功した場合は true
     */
    static async regenerateThumbnail(video_id: number): Promise<boolean> {

        // API リクエストを実行
        const response = await APIClient.post(`/videos/${video_id}/thumbnail/regenerate`, undefined, {
            // 数分以上かかるのでタイムアウトを 30 分に設定
            timeout: 30 * 60 * 1000,
        });

        // エラー処理
        if (response.type === 'error') {
            switch (response.data.detail) {
                default:
                    APIClient.showGenericError(response, 'サムネイルの再生成に失敗しました。');
                    break;
            }
            return false;
        }

        return true;
    }

    /**
     * 録画番組を削除する
     * @param video_id 録画番組の ID
     * @returns 削除に成功した場合は true
     */
    static async deleteVideo(video_id: number): Promise<boolean> {

        // API リクエストを実行
        const response = await APIClient.delete(`/videos/${video_id}`);

        // エラー処理
        if (response.type === 'error') {
            switch (response.data.detail) {
                default:
                    APIClient.showGenericError(response, '録画ファイルの削除に失敗しました。');
                    break;
            }
            return false;
        }

        return true;
    }
}

export default Videos;
