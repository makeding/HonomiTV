
import { AxiosError } from 'axios';

import Message from '@/message';
import APIClient, { IErrorResponse } from '@/services/APIClient';
import Utils, { Semaphore } from '@/utils';


/** キャプチャ一覧レスポンスを表すインターフェイス (サーバー側の Captures に対応) */
export interface ICaptures {
    total: number;
    captures: ICapture[];
}

/** キャプチャ画像の情報を表すインターフェイス (サーバー側の Capture に対応) */
export interface ICapture {
    id: string;
    filename: string;
    image_width: number;
    image_height: number;
    file_size: number;
    file_modified_at: string;
    mime_type: 'image/jpeg' | 'image/png';
    capture_metadata: ICaptureMetadata | null;
}


/** EXIF XPComment に格納されたキャプチャメタデータを表すインターフェイス (サーバー側の CaptureMetadata に対応) */
export interface ICaptureMetadata {
    captured_at: string | null;
    captured_playback_position: number | null;
    network_id: number | null;
    service_id: number | null;
    event_id: number | null;
    title: string | null;
    description: string | null;
    start_time: string | null;
    end_time: string | null;
    duration: number | null;
    caption_text: string | null;
    is_caption_composited: boolean | null;
    is_comment_composited: boolean | null;
}
/** キャプチャフォルダ一覧レスポンスを表すインターフェイス (サーバー側の CaptureFolders に対応) */
export interface ICaptureFolders {
    total: number;
    folders: ICaptureFolder[];
}

/** キャプチャフォルダ情報を表すインターフェイス (サーバー側の CaptureFolder に対応) */
export interface ICaptureFolder {
    id: number;
    name: string;
    sort_order: number;
    capture_count: number;
    created_at: string;
    updated_at: string;
}


class Captures {

    // 同時アップロード数の上限 & セマフォインスタンス
    private static readonly MAX_CONCURRENT_UPLOADS = 5;
    private static readonly semaphore = new Semaphore(Captures.MAX_CONCURRENT_UPLOADS);

    /**
     * キャプチャをサーバーにアップロードし保存する
     * @param blob キャプチャ画像の Blob
     * @param filename サーバーに保存するときのファイル名
     */
    static async uploadCapture(blob: Blob, filename: string): Promise<void> {

        // リトライループ
        // 成功するか、リトライ不可なエラーが発生するまで継続
        // eslint-disable-next-line no-constant-condition
        while (true) {

            // キャプチャ画像の File オブジェクト (= Blob) を FormData に入れる
            // multipart/form-data で送るために必要
            // ref: https://r17n.page/2020/02/04/nodejs-axios-file-upload-api/
            const form_data = new FormData();
            form_data.append('image', blob, filename);

            // セマフォを取得し、アップロードを同時 5 件までに制限する
            await this.semaphore.acquire();
            let response: Awaited<ReturnType<typeof APIClient.post>>;
            try {
                // API リクエストを実行
                response = await APIClient.post('/captures', form_data, {
                    headers: {'Content-Type': 'multipart/form-data'},
                    // 回線状況によってはアップロードに時間がかかることがあるので、
                    // タイムアウトを 60 秒に伸ばす
                    timeout: 60 * 1000,
                });
            } finally {
                // セマフォを必ず解放する (アップロード試行 1 回分のみを占有)
                this.semaphore.release();
            }

            // 成功
            if (response.type !== 'error') {
                return;
            }

            // エラー処理
            switch (response.data.detail) {
                case 'Permission denied to save the file': {
                    Message.error('キャプチャのアップロードに失敗しました。保存先フォルダに書き込み権限がありません。');
                    return;
                }
                case 'No space left on the device': {
                    Message.error('キャプチャのアップロードに失敗しました。保存先フォルダに空き容量がありません。');
                    return;
                }
                case 'Unexpected error occurred while saving the file': {
                    Message.error('キャプチャのアップロードに失敗しました。保存中に予期しないエラーが発生しました。');
                    return;
                }
                default: {
                    if (Number.isNaN(response.status)) {
                        // HTTP リクエスト自体が失敗した場合はネットワークエラーの可能性が高い & 基本アップロードに失敗してはならないので、リトライする
                        // リトライする前に 1 ~ 15 秒程度ランダムに待機することで、
                        // リトライリクエストが同じタイミングで殺到するのを回避する
                        const sleep_time_seconds = 1 + Math.random() * 15;
                        if (response.error.code === AxiosError.ECONNABORTED) {
                            // ネットワーク接続エラーの場合
                            Message.warning(`キャプチャのアップロード中にサーバーへの接続が切断されました。${sleep_time_seconds.toFixed(2)} 秒後にリトライします。`);
                        } else if (response.error.code === AxiosError.ETIMEDOUT) {
                            // タイムアウトの場合
                            Message.warning(`キャプチャのアップロード中にサーバーへの接続がタイムアウトしました。${sleep_time_seconds.toFixed(2)} 秒後にリトライします。`);
                        } else if (response.error.code === AxiosError.ERR_NETWORK) {
                            // 予期しないネットワークエラーの場合
                            Message.warning(`キャプチャのアップロード中に予期しないネットワークエラーが発生しました。${sleep_time_seconds.toFixed(2)} 秒後にリトライします。`);
                        } else {
                            // それ以外のエラーの場合
                            Message.warning(`キャプチャのアップロードに失敗しました。${sleep_time_seconds.toFixed(2)} 秒後にリトライします。`);
                        }
                        await Utils.sleep(sleep_time_seconds);  // 秒単位で指定
                        // ループ先頭に戻ってリトライ
                        continue;
                    } else {
                        APIClient.showGenericError(response, 'キャプチャのアップロードに失敗しました。');
                        return;
                    }
                }
            }
        }
    }

    /**
     * キャプチャ一覧を取得する
     * @param order ソート順序 ('desc': 新しい順, 'asc': 古い順)
     * @param page ページ番号 (1始まり)
     * @param search 番組名・ファイル名・チャンネル名での部分一致検索キーワード (省略時は全件取得)
     * @returns キャプチャ一覧情報 (取得失敗時は例外)
     */
    static async fetchCaptures(
        order: 'desc' | 'asc' = 'desc',
        page: number = 1,
        search?: string,
    ): Promise<ICaptures> {
        // undefined のパラメータは axios が自動で除外するため、そのまま渡す
        const response = await APIClient.get<ICaptures>('/captures', {
            params: { order, page, search },
        });
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return response.data;
    }

    /**
     * キャプチャ画像の URL を取得する
     * @param captureId キャプチャ画像の ID
     * @param thumbnail サムネイル画像を取得するかどうか
     * @returns キャプチャ画像の URL
     */
    static getCaptureImageURL(captureId: string, thumbnail: boolean = false, download: boolean = false): string {
        const base = Utils.api_base_url;
        const params = thumbnail ? '?thumbnail=true' : download ? '?download=true' : '';
        return `${base}/captures/${encodeURIComponent(captureId)}${params}`;
    }

    /**
     * キャプチャ画像を削除する
     * @param captureId 削除するキャプチャ画像の ID
     * @returns 削除に成功した場合は true
     */
    static async deleteCapture(captureId: string): Promise<boolean> {
        const response = await APIClient.delete(`/captures/${encodeURIComponent(captureId)}`);
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return true;
    }

    // ==================== フォルダ CRUD ====================

    /**
     * ログインユーザーのキャプチャフォルダ一覧を取得する
     * @returns フォルダ一覧情報 (取得失敗時は例外)
     */
    static async fetchFolders(): Promise<ICaptureFolders> {
        const response = await APIClient.get<ICaptureFolders>('/captures/folders');
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return response.data;
    }

    /**
     * 新しいキャプチャフォルダを作成する
     * @param name フォルダ名
     * @returns 作成されたフォルダ情報 (作成失敗時は例外)
     */
    static async createFolder(name: string): Promise<ICaptureFolder> {
        const response = await APIClient.post<ICaptureFolder>('/captures/folders', { name });
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return response.data;
    }

    /**
     * キャプチャフォルダの名前や表示順序を更新する
     * @param folderId 更新するフォルダの ID
     * @param updates 更新する内容 (name, sort_order のいずれかまたは両方)
     * @returns 更新後のフォルダ情報 (更新失敗時は例外)
     */
    static async updateFolder(
        folderId: number,
        updates: { name?: string; sort_order?: number },
    ): Promise<ICaptureFolder> {
        const response = await APIClient.put<ICaptureFolder>(`/captures/folders/${folderId}`, updates);
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return response.data;
    }

    /**
     * キャプチャフォルダを削除する
     * フォルダに紐付けられたブックマークも自動削除されるが、実際のキャプチャ画像は削除されない。
     * @param folderId 削除するフォルダの ID
     * @returns 削除に成功した場合は true
     */
    static async deleteFolder(folderId: number): Promise<boolean> {
        const response = await APIClient.delete(`/captures/folders/${folderId}`);
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return true;
    }

    // ==================== フォルダ内キャプチャ操作 ====================

    /**
     * 指定されたフォルダ内のキャプチャ一覧を取得する
     * @param folderId フォルダ ID
     * @param order ソート順序 ('desc': 新しい順, 'asc': 古い順)
     * @param page ページ番号 (1始まり)
     * @returns キャプチャ一覧情報 (取得失敗時は例外)
     */
    static async fetchFolderCaptures(
        folderId: number,
        order: 'desc' | 'asc' = 'desc',
        page: number = 1,
    ): Promise<ICaptures> {
        const response = await APIClient.get<ICaptures>(`/captures/folders/${folderId}/captures`, {
            params: { order, page },
        });
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return response.data;
    }

    /**
     * 指定されたフォルダにキャプチャを一括追加する
     * 既にフォルダ内に存在するキャプチャは無視される。
     * @param folderId フォルダ ID
     * @param captureIds 追加するキャプチャ画像の ID リスト
     * @returns 追加に成功した場合は true
     */
    static async addCapturesToFolder(folderId: number, captureIds: string[]): Promise<boolean> {
        const response = await APIClient.post(`/captures/folders/${folderId}/captures`, { capture_ids: captureIds });
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return true;
    }

    /**
     * 指定されたフォルダからキャプチャを一括削除する
     * CaptureBookmark レコードのみを削除し、実際のキャプチャ画像ファイルは削除しない。
     * @param folderId フォルダ ID
     * @param captureIds 削除するキャプチャ画像の ID リスト
     * @returns 削除に成功した場合は true
     */
    static async removeCapturesFromFolder(folderId: number, captureIds: string[]): Promise<boolean> {
        const response = await APIClient.delete(`/captures/folders/${folderId}/captures`, {
            data: { capture_ids: captureIds },
        });
        if (response.type === 'error') {
            throw this.createRequestError(response);
        }
        return true;
    }
    /** API の失敗理由をダイアログや一覧に保持できる形へ変換する。 */
    private static createRequestError(response: IErrorResponse): Error {
        const reasons: Record<string, string> = {
            'Capture storage is unavailable': '保存先フォルダを読み取れません。接続やアクセス権を確認して再試行してください。',
            'Invalid capture IDs': '選択した画像が見つかりません。一覧を更新して選び直してください。',
            'Capture file not found': '画像が見つかりません。一覧を更新してください。',
            'Capture folder not found': 'フォルダが見つからないか、アクセス権がありません。',
            'Folder name cannot be empty': 'フォルダ名を入力してください。',
            'Permission denied to delete the file': '保存先の権限により削除できません。管理者に確認してください。',
            'Failed to generate thumbnail': '画像を読み込めません。元のファイルを確認してください。',
        };
        const detail = typeof response.data.detail === 'string' ? response.data.detail : 'Invalid request';
        let reason = reasons[detail];
        if (!reason) {
            if (Number.isNaN(response.status)) reason = 'サーバーに接続できません。接続を確認して再試行してください。';
            else if (response.status === 401) reason = 'ログインが必要です。ログインして再試行してください。';
            else if (response.status === 403) reason = 'この操作には管理者権限が必要です。';
            else reason = '処理できませんでした。再試行してください。';
        }
        return new Error(reason + ' (' + (Number.isNaN(response.status) ? 'NetworkError' : response.status + ': ' + detail) + ')');
    }

}

export default Captures;
