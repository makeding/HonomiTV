import Message from '@/message';
import APIClient from '@/services/APIClient';
import Utils from '@/utils';

export interface IRemoteDevice {
    device_id: string;
    device_name: string;
    last_seen_at: string;
    state: Record<string, unknown> | null;
}

export type RemoteCommand =
    | {type: 'OpenLive'; display_channel_id: string;}
    | {type: 'OpenRecording'; recorded_program_id: number; position_seconds: number;}
    | {type: 'Play' | 'Pause' | 'Stop';}
    | {type: 'SeekRelative'; delta_seconds: number;}
    | {type: 'SeekTo'; position_seconds: number;}
    | {type: 'SkipChapter'; direction: 'Next' | 'Previous';}
    | {type: 'SkipCM';}
    | {type: 'SetCMSkipMode'; mode: RemoteCMSkipMode;}
    | {type: 'VolumeUp' | 'VolumeDown' | 'VolumeMute';};

export type RemoteCMSkipMode = 'Off' | 'Manual' | 'Auto';

/** 受信側 (Komorebi) が CM 判定から導いたチャプター区間。CM 区間は is_cm で区別される */
export interface IRemoteChapter {
    start_seconds: number;
    end_seconds: number;
    is_cm: boolean;
    label: string;
}

/**
 * 受信側が State メッセージで通知する再生状態。
 * サーバーは state を不透明な dict として中継するだけなので、この型がクライアント間の唯一の契約になる。
 */
export interface IRemotePlaybackState {
    content_type: 'Idle' | 'Live' | 'Recorded';
    title?: string;
    subtitle?: string;
    artwork_url?: string;
    is_playing?: boolean;
    is_buffering?: boolean;
    can_seek?: boolean;
    can_adjust_volume?: boolean;
    position_seconds?: number;
    duration_seconds?: number;
    // 再生位置をブラウザ側で補間するために使う。受信側の State 送信は 5 秒周期なので、この値がないと進捗バーがカクつく
    playback_rate?: number;
    // State を受け取るたびに単調増加する。値が変わったときだけ補間の基点を取り直し、
    // 他デバイスの接続などで同じ state が再ブロードキャストされたときに進捗バーが巻き戻るのを防ぐ
    state_sequence?: number;
    // 追いかけ再生中は duration_seconds が録画の進行に合わせて伸び続ける
    is_chase_playback?: boolean;
    chapters?: IRemoteChapter[];
    cm_skip_mode?: RemoteCMSkipMode;
}

class RemoteControl {
    static subscribeDevices(
        onDevices: (devices: IRemoteDevice[]) => void,
        onDisconnected: () => void,
    ): () => void {
        const accessToken = Utils.getAccessToken();
        if (accessToken === null) return () => {};

        const websocketURL = new URL(`${Utils.api_base_url}/remote/devices/ws`);
        websocketURL.protocol = websocketURL.protocol === 'https:' ? 'wss:' : 'ws:';
        const websocket = new WebSocket(websocketURL);
        let isDisposed = false;
        websocket.addEventListener('open', () => {
            websocket.send(JSON.stringify({type: 'Authenticate', token: accessToken}));
        });
        websocket.addEventListener('message', (event) => {
            const message: unknown = JSON.parse(event.data);
            if (typeof message === 'object' && message !== null && 'devices' in message && Array.isArray(message.devices)) {
                onDevices(message.devices as IRemoteDevice[]);
            }
        });
        websocket.addEventListener('close', () => {
            if (isDisposed === false) onDisconnected();
        });

        return () => {
            isDisposed = true;
            websocket.close();
        };
    }

    static async fetchDevices(): Promise<IRemoteDevice[] | null> {
        const response = await APIClient.get<{devices: IRemoteDevice[];}>('/remote/devices');
        if (response.type === 'error') {
            APIClient.showGenericError(response, 'テレビの一覧を取得できませんでした。');
            return null;
        }
        return response.data.devices;
    }

    static async sendCommand(deviceId: string, command: RemoteCommand): Promise<boolean> {
        const response = await APIClient.post<{command_id: string;}>(
            `/remote/devices/${encodeURIComponent(deviceId)}/commands`,
            command,
        );
        if (response.type === 'error') {
            if (response.status === 409) {
                Message.error('選択したテレビはオフラインです。テレビの接続を確認して、もう一度送信してください。');
            } else {
                APIClient.showGenericError(response, 'テレビへ送信できませんでした。');
            }
            return false;
        }
        return true;
    }

    static async sendOpenCommand(deviceId: string, command: Extract<RemoteCommand, {type: 'OpenLive' | 'OpenRecording'}>): Promise<boolean> {
        return this.sendCommand(deviceId, command);
    }
}

export default RemoteControl;
