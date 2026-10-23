import { adaptNHKProgramGuideNavigation } from './src/services/player/hybridcast/NHK';
import { adaptSkyPerfecTVPublicHelp, SKY_HELP_PREFIX } from './src/services/player/hybridcast/SkyPerfecTV';

const EXTERNAL_PROXY_ALLOWED_HOSTS = [
    '4kdata-p.qvc.jp',
    'api.qvc.jp',
    'api.nhk.or.jp',
    'beacon.nhk.jp',
    'img.nhk.jp',
    'hc.nhk.or.jp',
    'hc.nhk.jp',
    'nhk.jp',
    'qvc.jp',
    'qvc.scene7.com',
    'shv.nhk.jp',
    'tv-stream.nhk.jp',
    'tvip.hc.skyperfectv.co.jp',
    'www.nhk-cs.jp',
    'www.nhk.or.jp',
];

const EXTERNAL_PROXY_RUNTIME = String.raw`
  // location.href への直接代入は Fetch / XHR の差し替えでは捕捉できないため、
  // 許可ホスト上の文書も VFS Worker の scope 内に写像して receiver runtime を維持する。
  const SKY_HELP_PREFIX = ${JSON.stringify(SKY_HELP_PREFIX)};
  const adaptSkyPerfecTVPublicHelp = ${adaptSkyPerfecTVPublicHelp.toString()};
  const adaptNHKProgramGuideNavigation = ${adaptNHKProgramGuideNavigation.toString()};
  const EXTERNAL_PROXY_PREFIX = VFS_PREFIX + ".external/";
  const EXTERNAL_PROXY_ALLOWED_HOSTS = new Set(${JSON.stringify(EXTERNAL_PROXY_ALLOWED_HOSTS)});
  function externalProxyRoot(scheme, hostname) {
    return EXTERNAL_PROXY_PREFIX + scheme + "/" + hostname + "/";
  }
  function rewriteExternalProxyUrls(source) {
    for (const hostname of EXTERNAL_PROXY_ALLOWED_HOSTS) {
      const escapedHostname = hostname.replaceAll(".", "\\.");
      const pattern = new RegExp("(?:https?:)?//" + escapedHostname + "(?:/|(?=[?#\\\"'\\\\s]|$))", "gi");
      source = source.replace(pattern, value => {
        const scheme = value.toLowerCase().startsWith("http:") ? "http" : "https";
        return externalProxyRoot(scheme, hostname);
      });
    }
    return source;
  }
  function resolveExternalProxyUrl(url) {
    if (url.pathname.startsWith(SKY_HELP_PREFIX)) {
      const upstreamUrl = new URL(url.pathname.slice(SKY_HELP_PREFIX.length) + url.search, "https://tvip.hc.skyperfectv.co.jp/");
      if (upstreamUrl.hostname !== "tvip.hc.skyperfectv.co.jp" || !upstreamUrl.pathname.startsWith("/content-sky/")) return null;
      return { upstreamUrl, rootPath: SKY_HELP_PREFIX, skyHelp: true };
    }
    if (!url.pathname.startsWith(EXTERNAL_PROXY_PREFIX)) return null;
    const relativePath = url.pathname.slice(EXTERNAL_PROXY_PREFIX.length);
    const separator = relativePath.indexOf("/");
    if (separator <= 0) return null;
    const scheme = relativePath.slice(0, separator);
    const hostAndPath = relativePath.slice(separator + 1);
    const hostSeparator = hostAndPath.indexOf("/");
    if (hostSeparator <= 0 || !["https", "http"].includes(scheme)) return null;
    const hostname = hostAndPath.slice(0, hostSeparator).toLowerCase();
    if (!EXTERNAL_PROXY_ALLOWED_HOSTS.has(hostname)) return null;
    const path = hostAndPath.slice(hostSeparator + 1);
    const upstreamUrl = new URL(scheme + "://" + hostname + "/" + path);
    upstreamUrl.search = url.search;
    return { upstreamUrl, rootPath: externalProxyRoot(scheme, hostname) };
  }
  async function serveExternalProxy(request, url) {
    const resolved = resolveExternalProxyUrl(url);
    if (!resolved) return new Response("Bad external broadcast path", { status: 400 });
    if (resolved.upstreamUrl.hostname === "tvip.hc.skyperfectv.co.jp" && request.method !== "GET" && request.method !== "HEAD") {
      return new Response("Public content GET only", { status: 403 });
    }
    const proxyUrl = new URL("/api/data-broadcasting/arib-html5/request", self.location.origin);
    proxyUrl.searchParams.set("url", resolved.upstreamUrl.href);
    const method = request.method === "HEAD" ? "GET" : request.method;
    let response = await fetch(proxyUrl, {
      method,
      headers: request.headers,
      body: method === "POST" ? await request.clone().arrayBuffer() : void 0,
      credentials: "same-origin"
    });
    // NHK 番組表は画像 CDN のホストを hc.nhk.jp に置換するが、同ホストに未配置の画像がある。
    // 画像要求の 404 に限って、既に許可済みの元画像 CDN の同一パスを参照する。
    if (response.status === 404 && request.destination === "image" &&
        resolved.upstreamUrl.hostname === "hc.nhk.jp" &&
        resolved.upstreamUrl.pathname.startsWith("/static/assets/images/")) {
      const imageUrl = new URL(resolved.upstreamUrl);
      imageUrl.hostname = "img.nhk.jp";
      proxyUrl.searchParams.set("url", imageUrl.href);
      response = await fetch(proxyUrl, { credentials: "same-origin" });
    }
    const headers = new Headers(response.headers);
    const type = headers.get("Content-Type") || "application/octet-stream";
    const rewriteHtml = /^text\/html(?:;|$)/i.test(type) || /^application\/xhtml\+xml(?:;|$)/i.test(type);
    const rewriteCss = /^text\/css(?:;|$)/i.test(type);
    const rewriteJavaScript = /(?:java|ecma)script/i.test(type);
    // メニュー JSON に含まれる遷移先も同じ origin 写像を通す。
    // HTML/JS だけを書き換えると、JSON からの location.href 代入で runtime を失う。
    const rewriteJson = /^application\/(?:[\w.+-]+\+)?json(?:;|$)/i.test(type);
    if (!rewriteHtml && !rewriteCss && !rewriteJavaScript && !rewriteJson) {
      return new Response(request.method === "HEAD" ? null : response.body, {
        status: response.status,
        headers
      });
    }
    let source = await response.text();
    if (resolved.skyHelp) {
      try {
        source = await adaptSkyPerfecTVPublicHelp(source, resolved.upstreamUrl.href, 4153, 1);
      } catch {
        // 元の JavaScript の代わりに、現在の iframe からのみ受理される失敗通知を返す。
        return new Response('parent.postMessage({type:"KonomiHybridcastError",code:"HYBRIDCAST_ADAPTER_MISMATCH"},location.origin)', {
          status: 200, headers: { "Content-Type": "text/javascript", "Cache-Control": "no-store" }
        });
      }
      source = source.replace(/https?:\/\/tvip\.hc\.skyperfectv\.co\.jp\//g, SKY_HELP_PREFIX);
    }
    source = rewriteExternalProxyUrls(source);
    if (rewriteJavaScript) source = adaptNHKProgramGuideNavigation(source, resolved.upstreamUrl.href, resolved.rootPath);
    if (rewriteHtml) {
      source = prepareBroadcastHtml(source, {
        basePath: resolved.rootPath,
        scopePath: resolved.rootPath,
        // Ajax で読む HTML 部品に runtime を再注入すると、挿入時に二重初期化される。
        bootstrap: ["document", "iframe"].includes(request.destination) ? RUNTIME_BOOTSTRAP : undefined
      });
    } else if (rewriteCss) {
      source = prepareBroadcastStylesheet(source, {
        basePath: resolved.rootPath,
        scopePath: resolved.rootPath
      });
    }
    headers.delete("Content-Encoding");
    headers.delete("Content-Length");
    headers.set("Cache-Control", "no-store");
    // NHK の既存アプリは jQuery.getScript() の間接 eval で同一 origin の追加モジュールを実行する。
    // 対象の放送アプリ origin だけに限定し、ネットワークの許可範囲は拡張しない。
    const legacyScriptEvaluation = ["hc.nhk.jp", "hc.nhk.or.jp"].includes(resolved.upstreamUrl.hostname) ? " 'unsafe-eval'" : "";
    headers.set("Content-Security-Policy", "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'" + legacyScriptEvaluation + "; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-src 'none'");
    headers.set("X-Content-Type-Options", "nosniff");
    return new Response(request.method === "HEAD" ? null : source, {
      status: response.status,
      headers
    });
  }
`;

const ORIGINAL_FETCH_HANDLER = String.raw`  self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin || !url.pathname.startsWith(VFS_PREFIX) || event.request.method !== "GET" && event.request.method !== "HEAD") return;
    event.respondWith((async () => {
      await restorePersistentResources();
      if (!enabled || !hasResourceCandidate(url.pathname)) return fetch(event.request);
      return serve(event.request);
    })());
  });`;

const PATCHED_FETCH_HANDLER = String.raw`  self.addEventListener("fetch", (event) => {
    const url = new URL(event.request.url);
    if (url.origin !== self.location.origin) return;
    if (!url.pathname.startsWith(VFS_PREFIX)) {
      // 外部文書内の JS が要求する /path は、その文書の上流 origin に属する。
      // 呼出元 client を確認し、通常の HonomiTV ページからの要求には適用しない。
      event.respondWith((async () => {
        const client = event.clientId ? await self.clients.get(event.clientId) : null;
        // navigation request の clientId は空の場合があるので、同一 origin の参照元も確認する。
        const referrer = event.request.referrer ? new URL(event.request.referrer) : null;
        const source = (client ? resolveExternalProxyUrl(new URL(client.url)) : null) ||
          (referrer?.origin === self.location.origin ? resolveExternalProxyUrl(referrer) : null);
        if (!source) return fetch(event.request);
        const mapped = new URL(source.rootPath + url.pathname.slice(1) + url.search, self.location.origin);
        // 文書 URL 自体を scope 内に戻す。本文だけを代理すると相対 URL とアプリ境界が壊れる。
        if (event.request.mode === "navigate") return Response.redirect(mapped.href, 302);
        return serveExternalProxy(event.request, mapped);
      })());
      return;
    }
    if (url.pathname.startsWith(EXTERNAL_PROXY_PREFIX) || url.pathname.startsWith(SKY_HELP_PREFIX)) {
      if (event.request.method !== "GET" && event.request.method !== "HEAD" && event.request.method !== "POST") return;
      event.respondWith(serveExternalProxy(event.request, url));
      return;
    }
    if (event.request.method !== "GET" && event.request.method !== "HEAD") return;
    event.respondWith((async () => {
      await restorePersistentResources();
      if (!enabled || !hasResourceCandidate(url.pathname)) return fetch(event.request);
      return serve(event.request);
    })());
  });`;

/** libaribhtml5 の生成済み VFS Worker に HonomiTV 固有の外部文書プロキシを追加する。 */
export function patchARIBVFSWorkerSource(source: string): string {
    const serveMarker = '  async function serve(request) {';
    if (!source.includes(serveMarker) || !source.includes(ORIGINAL_FETCH_HANDLER)) {
        throw new Error('Unsupported libaribhtml5 VFS Worker layout.');
    }

    // 放送波由来の HTML / CSS / JavaScript に埋め込まれた絶対 URL も、Worker scope 内へ戻す。
    source = source
        .replace(serveMarker, `${EXTERNAL_PROXY_RUNTIME}\n${serveMarker}`)
        .replace(
            'const source = prepareBroadcastHtml(new TextDecoder().decode(resource.data), {',
            'const source = prepareBroadcastHtml(rewriteExternalProxyUrls(new TextDecoder().decode(resource.data)), {',
        )
        .replace(
            'const source = prepareBroadcastStylesheet(new TextDecoder().decode(resource.data), {',
            'const source = prepareBroadcastStylesheet(rewriteExternalProxyUrls(new TextDecoder().decode(resource.data)), {',
        )
        .replace(
            '      body = new TextEncoder().encode(source);\n    }\n    return new Response(request.method === "HEAD" ? null : body, {',
            '      body = new TextEncoder().encode(source);\n' +
            '    } else if (/(?:java|ecma)script/i.test(type)) {\n' +
            '      const source = rewriteExternalProxyUrls(new TextDecoder().decode(resource.data));\n' +
            '      body = new TextEncoder().encode(source);\n' +
            '    }\n' +
            '    return new Response(request.method === "HEAD" ? null : body, {',
        )
        .replace(ORIGINAL_FETCH_HANDLER, PATCHED_FETCH_HANDLER);

    return source;
}
