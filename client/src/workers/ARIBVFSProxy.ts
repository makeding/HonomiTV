import { prepareBroadcastHtml, prepareBroadcastStylesheet } from 'libaribhtml5';

import { ARIB_PROXY_HOSTS, getARIBLocationAliases, resolveARIBDocumentURL } from '../services/player/hybridcast/ARIBProxyURL';
import { SKY_HELP_PREFIX, SKY_PUBLIC_HELP_BOOTSTRAP } from '../services/player/hybridcast/SkyPerfecTV';

const scope = self as unknown as ServiceWorkerGlobalScope;
const prefix = '/data-broadcast/';
const externalPrefix = `${prefix}.external/`;
const bootstrap = '<script src="/data-broadcast/arib-runtime-bootstrap.js"></script>';

type ExternalResource = {upstreamUrl: URL; rootPath: string; skyHelp?: boolean};

function resolveExternal(url: URL): ExternalResource | null {
    if (url.pathname.startsWith(SKY_HELP_PREFIX)) {
        const upstreamUrl = new URL(url.pathname.slice(SKY_HELP_PREFIX.length) + url.search,
            'https://tvip.hc.skyperfectv.co.jp/');
        if (upstreamUrl.hostname !== 'tvip.hc.skyperfectv.co.jp' ||
            !upstreamUrl.pathname.startsWith('/content-sky/')) return null;
        return {upstreamUrl, rootPath: SKY_HELP_PREFIX, skyHelp: true};
    }
    if (!url.pathname.startsWith(externalPrefix)) return null;
    const [scheme, hostname, ...parts] = url.pathname.slice(externalPrefix.length).split('/');
    if (!['http', 'https'].includes(scheme) || !ARIB_PROXY_HOSTS.has(hostname)) return null;
    return {
        upstreamUrl: new URL(`${scheme}://${hostname}/${parts.join('/')}${url.search}`),
        rootPath: `${externalPrefix}${scheme}/${hostname}/`,
    };
}

// 外部参照はソースを書き換えず、下の fetch handler で許可済み proxy へ送る。
function contentPolicy(legacyEvaluation = false, upstream?: URL): string {
    const aliases = upstream === undefined ? [] : [...getARIBLocationAliases(upstream, new URL(scope.location.origin)).keys()];
    const hosts = [...ARIB_PROXY_HOSTS, ...aliases].flatMap(host => [`https://${host}`, `http://${host}`]).join(' ');
    const evaluation = legacyEvaluation ? ' \'unsafe-eval\'' : '';
    return `default-src 'self' data: blob: ${hosts}; script-src 'self' 'unsafe-inline' ${hosts}` +
        `${evaluation}; style-src 'self' 'unsafe-inline' ${hosts}; connect-src 'self' ${hosts}; ` +
        'object-src \'none\'; frame-src \'none\'';
}

function responseWithBody(response: Response, body: string | null, headers = new Headers(response.headers)): Response {
    headers.delete('Content-Encoding');
    headers.delete('Content-Length');
    return new Response(body, {status: response.status, statusText: response.statusText, headers});
}

async function transformCarousel(request: Request, response: Response): Promise<Response> {
    if (!response.ok || request.method === 'HEAD') return response;
    const type = response.headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase();
    // JSON・画像・フォント・音声はバイト列もヘッダーも変更しない。
    if (type !== 'text/html') return response;
    // Carousel 文書には SDK 自身の bootstrap があるので、要求・遷移 hook だけを追加する。
    const source = prepareBroadcastHtml(await response.text(), {
        basePath: prefix, scopePath: prefix,
        bootstrap: '<script defer src="/data-broadcast/arib-runtime-bootstrap.js" data-runtime-installed></script>',
    });
    const headers = new Headers(response.headers);
    headers.set('Content-Security-Policy', contentPolicy());
    return responseWithBody(response, source, headers);
}

async function serveExternal(request: Request, resource: ExternalResource): Promise<Response> {
    if (resource.upstreamUrl.hostname === 'tvip.hc.skyperfectv.co.jp' &&
        request.method !== 'GET' && request.method !== 'HEAD') {
        return new Response('Public content GET only', {status: 403});
    }
    const proxyUrl = new URL('/api/data-broadcasting/arib-html5/request', scope.location.origin);
    proxyUrl.searchParams.set('url', resource.upstreamUrl.href);
    const method = request.method === 'HEAD' ? 'GET' : request.method;
    let response = await fetch(proxyUrl, {
        method, headers: request.headers, credentials: 'same-origin',
        body: method === 'POST' ? await request.clone().arrayBuffer() : undefined,
    });
    // 許可済み NHK 画像 CDN の同じパスに限り、未配置の番組表画像を補う。
    if (response.status === 404 && request.destination === 'image' &&
        resource.upstreamUrl.hostname === 'hc.nhk.jp' &&
        resource.upstreamUrl.pathname.startsWith('/static/assets/images/')) {
        const imageUrl = new URL(resource.upstreamUrl);
        imageUrl.hostname = 'img.nhk.jp';
        proxyUrl.searchParams.set('url', imageUrl.href);
        response = await fetch(proxyUrl, {credentials: 'same-origin'});
    }
    const headers = new Headers(response.headers);
    const type = headers.get('Content-Type')?.split(';', 1)[0].trim().toLowerCase();
    const html = type === 'text/html' || type === 'application/xhtml+xml';
    const css = type === 'text/css';
    const javascript = type?.includes('javascript') || type?.includes('ecmascript');
    // 一般の API 応答を文字列置換しない。
    if (!html && !css && !javascript) {
        return new Response(request.method === 'HEAD' ? null : response.body, {
            status: response.status, statusText: response.statusText, headers,
        });
    }
    let source = await response.text();
    if (resource.skyHelp) {
        source = source.replaceAll('https://tvip.hc.skyperfectv.co.jp/', SKY_HELP_PREFIX)
            .replaceAll('http://tvip.hc.skyperfectv.co.jp/', SKY_HELP_PREFIX);
    }
    if (html) {
        source = prepareBroadcastHtml(source, {
            basePath: resource.rootPath, scopePath: resource.rootPath,
            // Ajax 部品には再初期化用のスクリプトを入れない。
            bootstrap: ['document', 'iframe'].includes(request.destination)
                ? bootstrap + (resource.skyHelp ? SKY_PUBLIC_HELP_BOOTSTRAP : '') : undefined,
        });
    } else if (css) {
        source = prepareBroadcastStylesheet(source, {basePath: resource.rootPath, scopePath: resource.rootPath});
    }
    headers.set('Cache-Control', 'no-store');
    // BS テレ東の x_bjhc は eval() で Greg / Ureg のアクセサーを初期化する。
    // 受信機向けの既存ライブラリを使うホストへ、文書単位の互換設定を適用する。
    const legacyEvaluation = ['hc.nhk.jp', 'hc.nhk.or.jp', 'hc.bsjhc.jp'].includes(resource.upstreamUrl.hostname);
    headers.set('Content-Security-Policy', contentPolicy(legacyEvaluation, resource.upstreamUrl));
    headers.set('X-Content-Type-Options', 'nosniff');
    return responseWithBody(response, request.method === 'HEAD' ? null : source, headers);
}

/** SDK の登録した fetch handler を、VFS の要求・応答境界で一度だけ包む。 */
function handleFetch(event: FetchEvent, carousel: EventListenerOrEventListenerObject): void {
    const url = new URL(event.request.url);
    if (url.origin !== scope.location.origin) {
        if (!['http:', 'https:'].includes(url.protocol) || !['GET', 'HEAD', 'POST'].includes(event.request.method)) return;
        event.respondWith((async () => {
            const client = event.clientId ? await scope.clients.get(event.clientId) : null;
            const upstreamUrl = client ? resolveARIBDocumentURL(url, client.url) :
                ARIB_PROXY_HOSTS.has(url.hostname) ? url : null;
            if (upstreamUrl === null) return fetch(event.request);
            return serveExternal(event.request, {
                upstreamUrl,
                rootPath: `${externalPrefix}${upstreamUrl.protocol.slice(0, -1)}/${upstreamUrl.hostname}/`,
            });
        })());
        return;
    }
    // 親の network proxy が送る API 要求を、放送サイトの /api に再解釈しない。
    if (url.pathname === '/api/data-broadcasting/arib-html5/request') return;
    const external = resolveExternal(url);
    if (external !== null) {
        if (['GET', 'HEAD', 'POST'].includes(event.request.method)) {
            event.respondWith(serveExternal(event.request, external));
        }
        return;
    }
    if (!url.pathname.startsWith(prefix)) {
        event.respondWith((async () => {
            const client = event.clientId ? await scope.clients.get(event.clientId) : null;
            const referrer = event.request.referrer ? new URL(event.request.referrer) : null;
            const source = (client ? resolveExternal(new URL(client.url)) : null) ??
                (referrer?.origin === scope.location.origin ? resolveExternal(referrer) : null);
            if (source === null) return fetch(event.request);
            const mapped = new URL(source.rootPath + url.pathname.slice(1) + url.search, scope.location.origin);
            if (event.request.mode === 'navigate') return Response.redirect(mapped.href, 302);
            return serveExternal(event.request, {...source, upstreamUrl: new URL(url.pathname + url.search, source.upstreamUrl)});
        })());
        return;
    }
    // SDK の資源管理・待機・永続化はそのまま使い、返ってきた Response だけを処理する。
    const wrapped = new Proxy(event, {
        get(target, property) {
            if (property === 'respondWith') {
                return (response: Response | Promise<Response>) => event.respondWith(
                    Promise.resolve(response).then(value => transformCarousel(event.request, value)),
                );
            }
            const value = Reflect.get(target, property, target);
            return typeof value === 'function' ? value.bind(target) : value;
        },
    });
    if (typeof carousel === 'function') carousel.call(scope, wrapped);
    else carousel.handleEvent(wrapped);
}

// この後に連結される未改変 SDK Worker の fetch 登録を取得する。
// SDK のコード文字列や関数位置には依存せず、登録後はネイティブ API に戻す。
const addEventListener = scope.addEventListener.bind(scope);
scope.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) => {
    if (type === 'fetch') {
        addEventListener('fetch', event => handleFetch(event, listener), options);
        scope.addEventListener = addEventListener;
    } else {
        addEventListener(type, listener, options);
    }
}) as typeof scope.addEventListener;
