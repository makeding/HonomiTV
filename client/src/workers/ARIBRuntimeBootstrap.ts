import { mapARIBProxyURL, resolveARIBDocumentURL } from '../services/player/hybridcast/ARIBProxyURL';

type BroadcastWindow = Window & typeof globalThis & {
    __ARIB_HTML5_INSTALL__?: (target: Window) => void;
    __HONOMI_ARIB_BOOTSTRAP__?: boolean;
    navigator: Navigator & {receiverDevice?: {
        confirmIPNetwork?: () => boolean;
        streamEvent?: {sourceIs?: (source: string) => boolean};
    }};
    nhksh?: {lu: (url: string, ...args: unknown[]) => unknown};
    nhkship?: {redirectIfPc: (checkOnly?: boolean) => boolean | undefined};
    navigation?: EventTarget;
};

const target = window as BroadcastWindow;

/** IPTV-F の放送映像 MIME を、SDK が扱う共通の映像スロットへ接続する。 */
function installBroadcastMediaSlots(): void {
    const normalize = () => {
        for (const object of document.querySelectorAll('object[type="video/x-iptvf-broadcast"]')) {
            // 特定局の JS は変更せず、実際に挿入された放送映像オブジェクトだけを扱う。
            // plugin を起動せず、既存 SDK の API・映像矩形通知を同じスロットに適用する。
            const slot = document.createElement('video');
            for (const attribute of object.attributes) {
                if (attribute.name !== 'type') slot.setAttribute(attribute.name, attribute.value);
            }
            slot.setAttribute('data-arib-object', 'video/x-arib2-broadcast');
            while (object.firstChild !== null) slot.append(object.firstChild);
            object.replaceWith(slot);
        }
    };
    // SDK の DOMContentLoaded より先に登録し、放送アプリの初期化時には API を使えるようにする。
    normalize();
    document.addEventListener('DOMContentLoaded', normalize, {once: true});
    const observer = new MutationObserver(normalize);
    observer.observe(document.documentElement, {childList: true, subtree: true, attributes: true, attributeFilter: ['type']});
    target.addEventListener('pagehide', () => observer.disconnect(), {once: true});
}

/** 上流の location 代入も、遷移が確定する前に VFS へ写像する。 */
function installDocumentNavigation(): void {
    target.navigation?.addEventListener('navigate', event => {
        const navigation = event as Event & {destination: {url: string}};
        if (!navigation.cancelable || target.navigator.receiverDevice?.confirmIPNetwork?.() === false) return;
        const upstream = resolveARIBDocumentURL(navigation.destination.url, target.location.href);
        const mapped = upstream === null ? null : mapARIBProxyURL(upstream, target.location.origin);
        if (mapped === null) return;
        // cross-origin navigation は intercept() できないため、取消して同一 origin の VFS へ移る。
        navigation.preventDefault();
        target.location.assign(mapped.href);
    });
}

/** SDK に未実装の入力源判定を、宿主が宣言した実際の入力源へ接続する。 */
function installSourceCheck(): void {
    const streamEvent = target.navigator.receiverDevice?.streamEvent;
    // SDK 側で提供されている場合は、その実装と権限判定を優先する。
    if (streamEvent === undefined || typeof streamEvent.sourceIs === 'function') return;
    const source = target.frameElement?.getAttribute('data-arib-source');
    // 通信ページの URL や局名から放送中と推測しない。未宣言の宿主では一致しない。
    streamEvent.sourceIs = value => source !== null && source !== undefined && value === source;
}

// 放送スクリプトの評価後に、実際の遷移操作だけを同一 origin の VFS に向ける。
// メニューや番組 JSON は原文のままなので、new URL(linkIP) や URL 比較を壊さない。
function installNavigation(): void {
    const nhksh = target.nhksh;
    if (nhksh === undefined || typeof nhksh.lu !== 'function') return;
    const navigate = nhksh.lu;
    nhksh.lu = function(url, ...args) {
        // 断網設定を同一 origin への写像で迂回せず、SDK の既存判定へ元の URL を渡す。
        const mapped = target.navigator.receiverDevice?.confirmIPNetwork?.() === false ? null :
            mapARIBProxyURL(url, target.location.origin);
        return navigate.call(this, mapped?.href ?? url, ...args);
    };
}

function installReceiverCheck(nhkship: BroadcastWindow['nhkship']): void {
    // NHK の通信ページは makerid を十六進数として判定するため、正規の receiver API を
    // 持つ独立実装 (makerid: huggy) も PC と誤判定する。メーカー ID は偽装せず、
    // この runtime が提供する受信機 API の有無で判定し、通常ブラウザの処理は元へ渡す。
    if (nhkship !== undefined && typeof nhkship.redirectIfPc === 'function') {
        const redirectIfPc = nhkship.redirectIfPc;
        nhkship.redirectIfPc = function(checkOnly) {
            if (typeof target.navigator.receiverDevice?.confirmIPNetwork === 'function') return true;
            return redirectIfPc.call(this, checkOnly);
        };
    }
}

function scheduleNavigation(): void {
    if (document.readyState === 'complete') installNavigation();
    else document.addEventListener('DOMContentLoaded', installNavigation, {once: true});
}

// SDK の bootstrap と同じ親側 installer を使う。映像の DOM・合成・寸法は親が所有する。
function install(attempt: number): void {
    try {
        const installer = (target.parent as BroadcastWindow).__ARIB_HTML5_INSTALL__;
        if (typeof installer === 'function') {
            installer(target);
            installSourceCheck();
            scheduleNavigation();
            return;
        }
    } catch (error) {
        console.error('ARIB runtime installation failed', error);
    }
    if (attempt < 100) setTimeout(() => install(attempt + 1), 50);
}
// 同じ文書に script が再挿入されても installer と hook を重ねない。
if (target.__HONOMI_ARIB_BOOTSTRAP__ !== true) {
    target.__HONOMI_ARIB_BOOTSTRAP__ = true;
    installBroadcastMediaSlots();
    installDocumentNavigation();
    // commu.js は評価中に redirectIfPc() を呼ぶため DOMContentLoaded では遅い。
    // 先行ライブラリの公開オブジェクト代入時に適用し、直後に通常のプロパティへ戻す。
    if (target.nhkship !== undefined) installReceiverCheck(target.nhkship);
    else Object.defineProperty(target, 'nhkship', {
        configurable: true,
        enumerable: true,
        get: () => undefined,
        set(value: BroadcastWindow['nhkship']) {
            installReceiverCheck(value);
            Object.defineProperty(target, 'nhkship', {configurable: true, enumerable: true, writable: true, value});
        },
    });
    if (document.currentScript?.hasAttribute('data-runtime-installed')) {
        installSourceCheck();
        scheduleNavigation();
    }
    else install(0);
}
