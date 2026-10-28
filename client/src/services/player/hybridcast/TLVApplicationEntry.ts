import { mapARIBProxyURL } from './ARIBProxyURL';

/** MH-AIT の通信 transport を解決する。broadcast VFS の準備状態とは独立して扱う。 */
export function resolveTLVBroadbandEntry(state: {
    entryPath: string;
    transportUrls: string[];
    transportProtocolLabels?: number[];
    transports?: Array<{protocolId: number; label: number; urls: string[]}>;
}, origin: string): string | null {
    const urls = state.transports === undefined ? state.transportUrls : state.transports
        .filter(transport => transport.protocolId === 0x0003 &&
            (!state.transportProtocolLabels?.length || state.transportProtocolLabels.includes(transport.label)))
        .flatMap(transport => transport.urls);
    for (const base of urls) {
        try {
            const url = new URL(state.entryPath, base);
            if (url.username || url.password || url.port || !/^https?:/.test(base)) continue;
            if (mapARIBProxyURL(url, origin) !== null) return url.href;
        } catch {
            // 放送経由の相対 transport と不正 URL は通信入口にしない。
        }
    }
    return null;
}
