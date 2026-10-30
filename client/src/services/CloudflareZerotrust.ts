
import APIClient from '@/services/APIClient';

interface CloudflareZerotrustIdentity {
    id: string;
    name: string;
    email: string;
    groups: string[];
    idp: {
        id: string;
        type: string;
    };
    geo: {
        country: string;
    };
    user_uuid: string;
    account_id: string;
    iat: number;
    ip: string;
    auth_status: string;
    common_name: string;
    service_token_id: string;
    service_token_status: boolean;
    is_warp: boolean;
    is_gateway: boolean;
    version: number;
}


export type ICloudflareZerotrustIdentity = CloudflareZerotrustIdentity | false | null;


class CloudflareZerotrust {

    /**
     * Cloudflare Zerotrust ユーザー情報を取得する
     * @returns 有効なユーザー情報、Access の未ログイン応答なら false、それ以外は null
     */
    static async fetchCloudflareZerotrustIdentity(): Promise<ICloudflareZerotrustIdentity> {
        if (location.hostname.endsWith('local.konomi.tv')) {
            return null;
        }
        // API リクエストを実行
        const response = await APIClient.get<unknown>('/cdn-cgi/access/get-identity', {
            baseURL: '',
        });

        // 通常のサーバーが返す HTML・空応答を、Access のユーザー情報から区別する
        const data = response.data;
        if (data === null || typeof data !== 'object' || Array.isArray(data)) {
            return null;
        }

        // Access が明示した未ログイン状態は、既存の再認証処理へ渡す
        if ('err' in data && data.err === 'no app token set') {
            return false;
        }

        // 成功した Access の組織・ユーザー識別子を持つ応答だけを、ログイン済みとして扱う
        // 404 や通信失敗の detail オブジェクトも、この境界で通常の応答として区別する
        if (response.type !== 'success' ||
            !('account_id' in data) || typeof data.account_id !== 'string' || data.account_id.length === 0 ||
            !('user_uuid' in data) || typeof data.user_uuid !== 'string' || data.user_uuid.length === 0) {
            return null;
        }
        return data as CloudflareZerotrustIdentity;
    }
}

export default CloudflareZerotrust;
