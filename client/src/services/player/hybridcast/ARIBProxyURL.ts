/** VFS で代理取得できる放送アプリのホスト。サーバー側の許可範囲は拡張しない。 */
export const ARIB_PROXY_HOSTS = new Set([
    'hc.bsjhc.jp',
    '4kdata-p.qvc.jp', 'api.qvc.jp', 'api.nhk.or.jp', 'beacon.nhk.jp', 'img.nhk.jp',
    'hc.nhk.or.jp', 'hc.nhk.jp', 'nhk.jp', 'qvc.jp', 'qvc.scene7.com', 'shv.nhk.jp',
    'tv-stream.nhk.jp', 'tvip.hc.skyperfectv.co.jp', 'www.nhk-cs.jp', 'www.nhk.or.jp',
]);

/** URL オブジェクトだけを扱い、JSON 本文や番組識別子は変更しない。 */
export function mapARIBProxyURL(value: string | URL, origin: string): URL | null {
    let url: URL;
    try {
        url = new URL(value, origin);
    } catch {
        return null;
    }
    if (!['https:', 'http:'].includes(url.protocol) || !ARIB_PROXY_HOSTS.has(url.hostname)) return null;
    return new URL(`/data-broadcast/.external/${url.protocol.slice(0, -1)}/${url.host}${url.pathname}${url.search}${url.hash}`, origin);
}

/** location.hostname から局内ホストを組み立てるアプリに対する、VFS 内だけの別名。 */
export function getARIBLocationAliases(upstream: URL, origin: URL): Map<string, string> {
    const aliases = new Map<string, string>();
    // NHK の HTTP / HTTPS 判定は、配信ホストの先頭ラベルを維持してドメインだけを切り替える。
    // 実際の上流ホストから導き、proxy のホスト名を新たな接続先として許可しない。
    if (!/^[^.]+\.nhk\.(or\.jp|jp)$/.test(upstream.hostname)) return aliases;
    for (const suffix of ['nhk.jp', 'nhk.or.jp']) {
        const hostname = `${upstream.hostname.split('.')[0]}.${suffix}`;
        const alias = `${origin.hostname.split('.')[0]}.${suffix}`;
        if (ARIB_PROXY_HOSTS.has(hostname) && alias !== hostname) aliases.set(alias, hostname);
    }
    return aliases;
}

/** VFS 文書を起点に、location 由来の URL を元の上流 namespace へ戻す。 */
export function resolveARIBDocumentURL(value: string | URL, documentURL: string | URL): URL | null {
    const document = new URL(documentURL);
    const match = /^\/data-broadcast\/\.external\/(https?)\/([^/]+)\//.exec(document.pathname);
    if (match === null || !ARIB_PROXY_HOSTS.has(match[2])) return null;
    const upstream = new URL(`${match[1]}://${match[2]}/`);
    const url = new URL(value, document);
    if (url.username || url.password || !['http:', 'https:'].includes(url.protocol)) return null;
    if (url.origin === document.origin) {
        // 既に VFS に写像された要求は再解釈しない。通常の絶対パスだけを局側に戻す。
        if (url.pathname.startsWith('/data-broadcast/')) return null;
        return new URL(url.pathname + url.search + url.hash, upstream);
    }
    const hostname = getARIBLocationAliases(upstream, document).get(url.hostname);
    if (hostname !== undefined && url.port === '') url.hostname = hostname;
    return ARIB_PROXY_HOSTS.has(url.hostname) ? url : null;
}
