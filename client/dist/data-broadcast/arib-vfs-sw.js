(function() {
  "use strict";
  function deferRomSoundMarkup(source) {
    return source.replace(
      /\s+src\s*=\s*(?:(["'])(romsound:\/\/\d+)\1|(romsound:\/\/\d+)(?=[\s/>]))/gi,
      (_match, _quote, quoted, bare) => ` data-arib-romsound="${quoted ?? bare}"`
    );
  }
  const DEFAULT_RECEIVER_DEVICE_IDENTIFIER = "4194c4ae4730";
  function createRuntimeBootstrap(scopePath = "/data-broadcast/") {
    const basePath = normalizeBasePath(scopePath);
    return `<script>
(function installAribHtml5Runtime(attempt) {
  var broadcastType = 'video/x-arib2-broadcast'
  var scheduledObjects = new WeakSet()
  function replaceBroadcastObject(object) {
    if (!object || object.nodeType !== 1 ||
        String(object.getAttribute('type') || '').trim().toLowerCase() !== broadcastType) return
    // Keep broadcaster object/video sizing rules; a div loses them and collapses to zero height.
    // This source-less element is only the slot; the host's existing video keeps playing.
    var replacement = document.createElement('video')
    for (var index = 0; index < object.attributes.length; index += 1) {
      var attribute = object.attributes[index]
      if (attribute.name.toLowerCase() === 'type') continue
      replacement.setAttribute(attribute.name, attribute.value)
    }
    replacement.setAttribute('data-arib-object', broadcastType)
    while (object.firstChild) replacement.appendChild(object.firstChild)
    object.replaceWith(replacement)
  }
  function scanBroadcastObjects(root) {
    if (!root) return
    if (root.nodeType === 1 && root.matches && root.matches('object')) {
      scheduleBroadcastObject(root)
    }
    if (!root.querySelectorAll) return
    var objects = root.querySelectorAll('object')
    for (var index = 0; index < objects.length; index += 1) {
      scheduleBroadcastObject(objects[index])
    }
  }
  function scheduleBroadcastObject(object) {
    if (!object || scheduledObjects.has(object)) return
    scheduledObjects.add(object)
    setTimeout(function () {
      scheduledObjects.delete(object)
      replaceBroadcastObject(object)
    }, 0)
  }
  function observeBroadcastObjects() {
    scanBroadcastObjects(document)
    if (!document.documentElement) {
      queueMicrotask(observeBroadcastObjects)
      return
    }
    var observer = new MutationObserver(function (records) {
      for (var recordIndex = 0; recordIndex < records.length; recordIndex += 1) {
        var record = records[recordIndex]
        for (var nodeIndex = 0; nodeIndex < record.addedNodes.length; nodeIndex += 1) {
          scanBroadcastObjects(record.addedNodes[nodeIndex])
        }
      }
    })
    observer.observe(document.documentElement, { childList: true, subtree: true })
  }
  if (typeof document !== 'undefined' && typeof MutationObserver === 'function') {
    observeBroadcastObjects()
  }
  var install
  try {
    install = parent && parent.__ARIB_HTML5_INSTALL__
  } catch (error) {
    install = null
  }
  if (typeof install === 'function') {
    try {
      install(window)
      return
    } catch (error) {
      console.error('ARIB HTML5 runtime installation failed', error)
    }
  }
  if (!navigator.receiverDevice) {
    Object.defineProperty(navigator, 'receiverDevice', {
      configurable: true,
      enumerable: true,
      value: {
        getSystemInformation: function () {
          return {
            browsername: 'unknown',
            browserversion: 'unknown',
            makerid: 'unknown',
            modelname: 'unknown',
            baseurl: new URL(${JSON.stringify(basePath)}, location.origin).href
          }
        },
        getDeviceIdentifier: function (kind, callback) {
          queueMicrotask(function () { callback(${JSON.stringify(DEFAULT_RECEIVER_DEVICE_IDENTIFIER)}) })
        }
      }
    })
  }
  if (attempt < 100) {
    setTimeout(function () { installAribHtml5Runtime(attempt + 1) }, 50)
  }
})(0)
<\/script>`;
  }
  function normalizeBasePath(value) {
    const base = value || "/data-broadcast/";
    return `/${base.replace(/^\/+|\/+$/g, "")}/`;
  }
  function rewriteBroadcastObjectMarkup(source) {
    return source;
  }
  function receiverPath(path, base, scope) {
    if (path.startsWith(scope)) return path;
    const mount = base.startsWith(scope) ? base.slice(scope.length).replace(/^\/+|\/+$/g, "") : "";
    const requested = path.replace(/^\/+/, "");
    if (mount && (requested === mount || requested.startsWith(`${mount}/`))) {
      return `${scope}${requested}`;
    }
    return `${base}${requested}`;
  }
  function prefixBroadcastRootAttributes(source, basePath, scopePath) {
    const base = normalizeBasePath(basePath);
    const scope = normalizeBasePath(scopePath ?? basePath);
    return source.replace(
      /(\b(?:href|src|action|poster)\s*=\s*)(?:(['"])(\/[^/][^"']*)\2|(\/[^\s>]*))/gi,
      (match, name, quote, quotedPath, barePath) => {
        const path = quotedPath ?? barePath;
        if (!path || path.startsWith(base)) return match;
        const value = receiverPath(path, base, scope);
        return quote ? `${name}${quote}${value}${quote}` : `${name}${value}`;
      }
    );
  }
  function prepareBroadcastStylesheet(source, options = {}) {
    const base = normalizeBasePath(options.basePath);
    const scope = normalizeBasePath(options.scopePath ?? options.basePath);
    return source.replace(
      /url\(\s*(["']?)(\/[^/)][^)]*)\1\s*\)/gi,
      (match, quote, path) => path.startsWith(base) ? match : `url(${quote}${receiverPath(path, base, scope)}${quote})`
    ).replace(
      /(@import\s+)(["'])(\/[^/][^"']*)\2/gi,
      (match, keyword, quote, path) => path.startsWith(base) ? match : `${keyword}${quote}${receiverPath(path, base, scope)}${quote}`
    );
  }
  function prepareBroadcastHtml(source, options = {}) {
    const prepared = prefixBroadcastRootAttributes(
      deferRomSoundMarkup(rewriteBroadcastObjectMarkup(source)),
      options.basePath,
      options.scopePath
    );
    if (!options.bootstrap) return prepared;
    const head = /<head(?:\s[^>]*)?>/i.exec(prepared);
    if (!head) return `${options.bootstrap}${prepared}`;
    const offset = head.index + head[0].length;
    return `${prepared.slice(0, offset)}${options.bootstrap}${prepared.slice(offset)}`;
  }
  const scopeUrl = new URL(self.registration.scope);
  const VFS_PREFIX = scopeUrl.pathname.endsWith("/") ? scopeUrl.pathname : `${scopeUrl.pathname}/`;
  const CACHE_NAME = `libaribhtml5-arib-vfs-v1:${VFS_PREFIX}`;
  const CACHE_RESOURCE_PATH = `${VFS_PREFIX}.libaribhtml5-arib-vfs-resource`;
  const CACHE_SESSION_PATH = `${VFS_PREFIX}.libaribhtml5-arib-vfs-session`;
  const RUNTIME_BOOTSTRAP = createRuntimeBootstrap(VFS_PREFIX);
  const resources = /* @__PURE__ */ new Map();
  const waiters = /* @__PURE__ */ new Map();
  let enabled = false;
  let uniqueBasenameFallback = false;
  let restorePromise = null;
  function cacheRequest(path) {
    const url = new URL(CACHE_RESOURCE_PATH, self.location.origin);
    url.searchParams.set("path", path);
    return new Request(url.href);
  }
  function sessionRequest() {
    return new Request(new URL(CACHE_SESSION_PATH, self.location.origin).href);
  }
  async function beginPersistentSession() {
    await caches.delete(CACHE_NAME);
    const cache = await caches.open(CACHE_NAME);
    await cache.put(sessionRequest(), new Response("", {
      headers: {
        "X-Arib-VFS-Session": "active",
        "X-Arib-VFS-Unique-Basename-Fallback": uniqueBasenameFallback ? "1" : "0"
      }
    }));
    restorePromise = null;
  }
  async function persistResource(path, resource) {
    const cache = await caches.open(CACHE_NAME);
    await cache.put(cacheRequest(path), new Response(resource.data.slice(), {
      headers: {
        "Content-Type": resource.contentType || "application/octet-stream",
        "X-Arib-VFS-Path": path
      }
    }));
  }
  async function restorePersistentResources() {
    if (enabled) return true;
    if (restorePromise) return restorePromise;
    restorePromise = (async () => {
      const cache = await caches.open(CACHE_NAME);
      const session = await cache.match(sessionRequest());
      if (!session) return false;
      uniqueBasenameFallback = session.headers.get("X-Arib-VFS-Unique-Basename-Fallback") === "1";
      for (const request of await cache.keys()) {
        const response = await cache.match(request);
        const path = response?.headers.get("X-Arib-VFS-Path");
        if (!path) continue;
        resources.set(path, {
          data: new Uint8Array(await response.arrayBuffer()),
          contentType: response.headers.get("Content-Type") || ""
        });
      }
      enabled = true;
      return true;
    })();
    try {
      return await restorePromise;
    } finally {
      restorePromise = null;
    }
  }
  function normalizePath(value) {
    let pathname;
    try {
      pathname = decodeURIComponent(new URL(String(value), self.location.origin).pathname);
    } catch {
      return null;
    }
    if (pathname.startsWith(VFS_PREFIX)) pathname = pathname.slice(VFS_PREFIX.length);
    const parts = pathname.split("/").filter(Boolean);
    if (!parts.length || parts.some((part) => part === "." || part === "..")) return null;
    return parts.join("/");
  }
  function uniqueBasenameMatch(path) {
    if (!uniqueBasenameFallback) return null;
    const slash = path.lastIndexOf("/");
    const basename = slash >= 0 ? path.slice(slash + 1) : path;
    if (!basename) return null;
    let match = null;
    for (const candidate of resources.keys()) {
      if (candidate !== basename && !candidate.endsWith(`/${basename}`)) continue;
      if (match !== null) return null;
      match = candidate;
    }
    return match;
  }
  function hasBroadcastRoot(path) {
    const slash = path.indexOf("/");
    const root = slash < 0 ? path : path.slice(0, slash);
    for (const candidate of resources.keys()) {
      if (candidate === root || candidate.startsWith(`${root}/`)) return true;
    }
    return false;
  }
  function hasResourceCandidate(value) {
    const path = normalizePath(value);
    return Boolean(path && (resources.has(path) || hasBroadcastRoot(path) || uniqueBasenameMatch(path) !== null));
  }
  function contentType(path, supplied) {
    if (supplied) return supplied;
    const extension = path.slice(path.lastIndexOf(".")).toLowerCase();
    return (/* @__PURE__ */ new Map([
      [".html", "text/html; charset=utf-8"],
      [".htm", "text/html; charset=utf-8"],
      [".css", "text/css; charset=utf-8"],
      [".js", "text/javascript; charset=utf-8"],
      [".json", "application/json; charset=utf-8"],
      [".svg", "image/svg+xml"],
      [".png", "image/png"],
      [".jpg", "image/jpeg"],
      [".jpeg", "image/jpeg"],
      [".gif", "image/gif"],
      [".webp", "image/webp"],
      [".woff", "font/woff"],
      [".woff2", "font/woff2"]
    ])).get(extension) || "application/octet-stream";
  }
  function broadcastRootPath(path) {
    const separator = path.indexOf("/");
    return separator <= 0 ? VFS_PREFIX : `${VFS_PREFIX}${path.slice(0, separator)}/`;
  }
  function wake(path, resource) {
    const exact = waiters.get(path);
    if (exact) {
      waiters.delete(path);
      for (const resolve of exact) resolve({ path, resource });
    }
    if (!uniqueBasenameFallback) return;
    for (const [requestedPath, pending] of [...waiters]) {
      const fallback = uniqueBasenameMatch(requestedPath);
      if (!fallback) continue;
      const fallbackResource = resources.get(fallback);
      if (!fallbackResource) continue;
      waiters.delete(requestedPath);
      for (const resolve of pending) resolve({ path: fallback, resource: fallbackResource });
    }
  }
  function waitFor(path, timeout = 3e4) {
    const current = resources.get(path);
    if (current) return Promise.resolve({ path, resource: current });
    const fallback = uniqueBasenameMatch(path);
    if (fallback) return Promise.resolve({ path: fallback, resource: resources.get(fallback) });
    return new Promise((resolve) => {
      const pending = waiters.get(path) || /* @__PURE__ */ new Set();
      pending.add(resolve);
      waiters.set(path, pending);
      setTimeout(() => {
        pending.delete(resolve);
        if (!pending.size) waiters.delete(path);
        resolve(null);
      }, timeout);
    });
  }
  async function hasAvailableResource(value) {
    await restorePersistentResources();
    const path = normalizePath(`/${value || ""}`);
    return Boolean(enabled && path && resources.has(path));
  }

  // location.href への直接代入は Fetch / XHR の差し替えでは捕捉できないため、
  // 許可ホスト上の文書も VFS Worker の scope 内に写像して receiver runtime を維持する。
  const SKY_HELP_PREFIX = "/data-broadcast/.hybridcast/4153/1/public-help/";
  const adaptSkyPerfecTVPublicHelp = function adaptSkyPerfecTVPublicHelp(source, url, organizationId, applicationId) {
  const resource = new URL(url);
  if (organizationId !== 4153 || applicationId !== 1 || resource.hostname !== "tvip.hc.skyperfectv.co.jp" || resource.pathname !== "/content-sky/app/js/main.min.js") return source;
  if (source.includes("__KONOMI_PUBLIC_HELP__")) return source;
  const onLoadPattern = /([A-Za-z_$][\w$]*)\.prototype\.onLoad=function/g;
  let owner = null;
  let start = -1;
  let end = -1;
  for (const match of source.matchAll(onLoadPattern)) {
    if (match.index === void 0) continue;
    const candidateOwner = match[1];
    const candidateStart = match.index;
    const nextPrototype = source.indexOf(`${candidateOwner}.prototype.`, candidateStart + match[0].length);
    const candidateBody = source.slice(
      candidateStart,
      nextPrototype > candidateStart ? nextPrototype : candidateStart + 4e3
    );
    if (!candidateBody.includes("initSeqProd()") || !candidateBody.includes('Model.get("bcas")')) continue;
    const candidateEnd = source.indexOf(`${candidateOwner}.prototype.onKeydown=`, candidateStart);
    if (candidateEnd <= candidateStart) continue;
    owner = candidateOwner;
    start = candidateStart;
    end = candidateEnd;
    break;
  }
  if (owner === null || start < 0 || end <= start) return source;
  const broadcastParamPattern = /Util\.bml\.loadBroadcastParam=function\([^)]*\)\{/;
  if (!broadcastParamPattern.test(source)) return source;
  source = source.replace(
    broadcastParamPattern,
    (match) => match + 'if(window.__KONOMI_PUBLIC_HELP__)return $.Deferred().resolve({VERSION:"0002",BML_NAME:"",parsedGreg:[]});'
  );
  const replacement = `${owner}.prototype.onLoad=(function(original){return function(){if(window.__KONOMI_PUBLIC_HELP__)return Action.call(function(){return $.when(LoadResouceString()).then(function(){Util.dom.setVobjInvisible();$("#startUpMessage").remove();return Action.changeScene("sv_help-list")})});return original.apply(this,arguments)}})(${owner}.prototype.onLoad),`;
  return source.slice(0, start) + replacement + source.slice(end);
};
  const adaptNHKProgramGuideNavigation = function adaptNHKProgramGuideNavigation(source, url, rootPath) {
  const resource = new URL(url);
  if (resource.hostname !== "hc.nhk.jp" || ![
    "/hc-program/common/js/hc_action.js",
    "/hc-program/common/js/hc_action_bs4k8k.js"
  ].includes(resource.pathname)) return source;
  for (const page of ["index.html", "index_bs4k8k.html"]) {
    source = source.replaceAll(
      `window.location.href="/hc-program/${page}"`,
      `window.location.href="${rootPath}hc-program/${page}"`
    );
  }
  return source;
};
  const EXTERNAL_PROXY_PREFIX = VFS_PREFIX + ".external/";
  const EXTERNAL_PROXY_ALLOWED_HOSTS = new Set(["4kdata-p.qvc.jp","api.qvc.jp","api.nhk.or.jp","beacon.nhk.jp","img.nhk.jp","hc.nhk.or.jp","hc.nhk.jp","nhk.jp","qvc.jp","qvc.scene7.com","shv.nhk.jp","tv-stream.nhk.jp","tvip.hc.skyperfectv.co.jp","www.nhk-cs.jp","www.nhk.or.jp"]);
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
      source = adaptSkyPerfecTVPublicHelp(source, resolved.upstreamUrl.href, 4153, 1);
      source = source.replace(/https?:\/\/tvip\.hc\.skyperfectv\.co\.jp\//g, SKY_HELP_PREFIX);
    }
    source = rewriteExternalProxyUrls(source);
    if (rewriteJavaScript) source = adaptNHKProgramGuideNavigation(source, resolved.upstreamUrl.href, resolved.rootPath);
    if (rewriteHtml) {
      source = prepareBroadcastHtml(source, {
        basePath: resolved.rootPath,
        scopePath: resolved.rootPath,
        // Ajax で読む HTML 部品に runtime を再注入すると、挿入時に二重初期化される。
        bootstrap: ["document", "iframe"].includes(request.destination)
          ? RUNTIME_BOOTSTRAP + (resolved.skyHelp ? '<script>window.__KONOMI_PUBLIC_HELP__=true</script>' : '')
          : undefined
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

  async function serve(request) {
    const path = normalizePath(request.url);
    if (!path) return new Response("Bad broadcast path", { status: 400 });
    const resolved = await waitFor(path);
    if (!resolved) return new Response("Broadcast resource is not available", { status: 404 });
    if (resolved.path !== path) {
      return Response.redirect(new URL(`${VFS_PREFIX}${resolved.path}`, self.location.origin), 302);
    }
    const resource = resolved.resource;
    const type = contentType(path, resource.contentType);
    let body = resource.data.slice(0);
    if (/^text\/html(?:;|$)/i.test(type)) {
      const source = prepareBroadcastHtml(rewriteExternalProxyUrls(new TextDecoder().decode(resource.data)), {
        basePath: broadcastRootPath(resolved.path),
        scopePath: VFS_PREFIX,
        bootstrap: RUNTIME_BOOTSTRAP
      });
      body = new TextEncoder().encode(source);
    } else if (/^text\/css(?:;|$)/i.test(type)) {
      const source = prepareBroadcastStylesheet(rewriteExternalProxyUrls(new TextDecoder().decode(resource.data)), {
        basePath: broadcastRootPath(resolved.path),
        scopePath: VFS_PREFIX
      });
      body = new TextEncoder().encode(source);
    } else if (/(?:java|ecma)script/i.test(type)) {
      const source = rewriteExternalProxyUrls(new TextDecoder().decode(resource.data));
      body = new TextEncoder().encode(source);
    }
    return new Response(request.method === "HEAD" ? null : body, {
      status: 200,
      headers: {
        "Cache-Control": "no-store",
        "Content-Type": type,
        "Content-Security-Policy": "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-src 'none'",
        "X-Content-Type-Options": "nosniff"
      }
    });
  }
  self.addEventListener("install", (event) => {
    event.waitUntil(self.skipWaiting());
  });
  self.addEventListener("activate", (event) => {
    event.waitUntil(self.clients.claim());
  });
  self.addEventListener("message", (event) => {
    const message = event.data || {};
    const reply = (value) => event.ports[0]?.postMessage(value);
    const handle = async () => {
      if (message.type === "arib-vfs-probe") {
        reply({ ok: true, available: await hasAvailableResource(message.path) });
        return;
      }
      if (message.type === "arib-vfs-begin") {
        enabled = true;
        uniqueBasenameFallback = Boolean(message.uniqueBasenameFallback);
        resources.clear();
        for (const pending of waiters.values()) {
          for (const resolve of pending) resolve(null);
        }
        waiters.clear();
        await beginPersistentSession();
        reply({ ok: true });
        return;
      }
      if (message.type === "arib-vfs-put") {
        const path = normalizePath(`/${message.path || ""}`);
        if (!path || !(message.data instanceof ArrayBuffer)) {
          reply({ ok: false, error: "invalid resource" });
          return;
        }
        const resource = {
          data: new Uint8Array(message.data),
          contentType: String(message.contentType || "")
        };
        enabled = true;
        resources.set(path, resource);
        await persistResource(path, resource);
        wake(path, resource);
        reply({ ok: true });
        return;
      }
      if (message.type === "arib-vfs-reset") {
        enabled = false;
        uniqueBasenameFallback = false;
        resources.clear();
        restorePromise = null;
        for (const pending of waiters.values()) {
          for (const resolve of pending) resolve(null);
        }
        waiters.clear();
        await caches.delete(CACHE_NAME);
        reply({ ok: true });
        return;
      }
      reply({ ok: false, error: "unsupported VFS message" });
    };
    event.waitUntil(handle().catch((error) => {
      reply({ ok: false, error: error?.message || String(error) });
    }));
  });
  self.addEventListener("fetch", (event) => {
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
  });
})();
