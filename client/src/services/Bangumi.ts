import Message from '@/message';
import APIClient from '@/services/APIClient';


/** Bangumi アカウントと個人アクセストークンで連携するためのリクエストを表すインターフェイス */
export interface IBangumiAuthRequest {
    access_token: string;
}

class Bangumi {

    /**
     * Bangumi アカウントと個人アクセストークンで連携する
     * @param auth_request Bangumi 個人アクセストークン
     * @returns 連携に成功した場合は true、失敗した場合は false
     */
    static async loginAccount(auth_request: IBangumiAuthRequest): Promise<boolean> {

        // API リクエストを実行
        const response = await APIClient.post('/bangumi/auth', auth_request);

        // 無効または期限切れの個人アクセストークンは、再発行が必要なことを明示する
        if (response.type === 'error') {
            if (response.data.detail === 'Bangumi access token is invalid or expired') {
                Message.error('Bangumi の個人アクセストークンが無効、または期限切れです。');
                return false;
            }
            APIClient.showGenericError(response, 'Bangumi アカウントとの連携に失敗しました。');
            return false;
        }

        return true;
    }


    /**
     * 現在ログイン中のユーザーアカウントに紐づく Bangumi アカウントとの連携を解除する
     * @returns 連携解除に成功した場合は true、失敗した場合は false
     */
    static async logoutAccount(): Promise<boolean> {

        // API リクエストを実行
        const response = await APIClient.delete('/bangumi/logout');

        // エラー処理
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'Bangumi アカウントとの連携を解除できませんでした。');
            return false;
        }

        return true;
    }
}

export default Bangumi;
