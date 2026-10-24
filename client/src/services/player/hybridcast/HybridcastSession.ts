import { AribReceiverHost, ServiceWorkerBroadcastVfs } from 'libaribhtml5';


import { SKY_HELP_PREFIX } from './SkyPerfecTV';
import { HybridcastLaunchRequest, resolveXMLAIT } from './XMLAIT';

import type { ProgramInfo, RuntimeWindow } from 'libaribhtml5';

import APIClient from '@/services/APIClient';


type RuntimeHostWindow = Window & {__ARIB_HTML5_INSTALL__?: (target: RuntimeWindow) => void};

/** BML から起動する HTML5 アプリの一回の視聴セッション。放送局別の書換えは Worker 側が担当する。 */
export default class HybridcastSession {
    private readonly iframe = document.createElement('iframe');
    private readonly viewport = document.createElement('div');
    private readonly backend = new ServiceWorkerBroadcastVfs({
        workerUrl: '/data-broadcast/arib-vfs-sw.js', baseUrl: '/data-broadcast/',
    });
    private readonly host: AribReceiverHost;
    private readonly abortController = new AbortController();
    private readonly previousInstaller = (window as RuntimeHostWindow).__ARIB_HTML5_INSTALL__;
    private readonly installer: (target: RuntimeWindow) => void;
    private disposed = false;

    constructor(container: HTMLElement, program: ProgramInfo, private readonly onExit: () => void,
        private readonly onError: (code: string) => void, private readonly onReady: () => void,
        private readonly publicHelp = false) {
        // 動画面と BML の DOM は保持し、同じ視聴枠の上に HTML5 の面だけを重ねる。
        Object.assign(this.viewport.style, {position: 'absolute', inset: '0', pointerEvents: 'none', zIndex: '4'});
        Object.assign(this.iframe.style, {
            position: 'absolute', inset: '0', border: '0', width: '100%', height: '100%',
            transformOrigin: '0 0', zIndex: '5', colorScheme: 'only light',
        });
        this.iframe.title = 'Hybridcast';
        this.iframe.tabIndex = -1;
        container.append(this.viewport, this.iframe);
        this.host = new AribReceiverHost({
            iframe: this.iframe, viewport: this.viewport, broadcastBaseUrl: '/data-broadcast/',
            allowExternalNetwork: false, getDeviceIdentifier: () => null,
            userAgent: 'Hybridcast/1.0',
            onLaunchDataBroadcastingBrowser: () => this.onExit(),
            onProgramGuideUnavailable: () => onError('HYBRIDCAST_EPG_UNAVAILABLE'),
            onLifecycle: event => {
                if (this.disposed) return;
                if (event.type === 'installed') {
                    this.host.setApplicationInputActive(true);
                    this.onReady();
                } else if (event.type === 'exited') this.onExit();
                else if (event.type === 'error') this.onError('HYBRIDCAST_RUNTIME_ERROR');
                else if (event.type === 'navigation-blocked') this.onError('HYBRIDCAST_NAVIGATION_BLOCKED');
            },
        });
        this.host.setProgramInfo(program);
        this.installer = target => {
            // 別の iframe から宿主 runtime を取り付けることは許可しない。
            if (!this.disposed && target === this.iframe.contentWindow) this.host.installRuntime(target);
        };
        (window as RuntimeHostWindow).__ARIB_HTML5_INSTALL__ = this.installer;
    }

    async start(request: HybridcastLaunchRequest, publicHelp = false): Promise<void> {
        const response = await APIClient.get<string>('/data-broadcasting/arib-html5/request', {
            params: {url: request.aitUrl}, responseType: 'text', signal: this.abortController.signal,
        });
        if (this.disposed) return;
        if (response.type === 'error') throw new Error('HYBRIDCAST_AIT_FETCH_FAILED');
        const entry = new URL(resolveXMLAIT(response.data, request));
        // ホストの allowlist は XML AIT の managedURL によって拡張しない。
        const allowedHosts = ['tvip.hc.skyperfectv.co.jp', 'www.nhk.or.jp', 'www.nhk-cs.jp', 'shv.nhk.jp', 'hc.nhk.or.jp', 'hc.nhk.jp'];
        if (!allowedHosts.includes(entry.hostname)) throw new Error('HYBRIDCAST_UNSUPPORTED_URL');
        if (publicHelp && (request.organizationId !== 4153 || request.applicationId !== 1 ||
            entry.hostname !== 'tvip.hc.skyperfectv.co.jp' || entry.pathname !== '/content-sky/app/checktls.html')) {
            throw new Error('HYBRIDCAST_UNSUPPORTED_APPLICATION');
        }
        // 公開ヘルプでは BML の選局状態を要求する導入ページを経由せず、検証済みアプリへ入る。
        if (publicHelp) entry.pathname = entry.pathname.replace(/\/checktls\.html$/i, '/index.html');
        // 公開ヘルプも通常の放送アプリと同じ VFS Worker を通し、資源取得時に
        // Worker 側の動的アダプターと外部 URL 写像を適用できるようにする。
        await this.backend.initialize();
        if (this.disposed) return;
        this.host.setBmlGreg(request.greg);
        this.host.setApplicationInformation({
            type: 'ISDB-HTML', organizationId: request.organizationId, applicationId: request.applicationId,
            controlCode: 'AUTOSTART',
        });
        const root = publicHelp ? SKY_HELP_PREFIX :
            `/data-broadcast/.external/${entry.protocol.slice(0, -1)}/${entry.hostname}/`;
        this.host.loadApplication(`${root}${entry.pathname.slice(1)}${entry.search}${entry.hash}`);
    }

    dispatchKey(code: number): void {
        // 宿主リモコンを操作した時点で HTML5 アプリへ入力権を渡す。
        // ページ遷移直後の show() 通知で入力権が解除されていても、最初のキーを失わない。
        this.host.setApplicationInputActive(true);
        this.host.dispatchKey(code);
    }

    async destroy(): Promise<void> {
        if (this.disposed) return;
        this.disposed = true;
        this.abortController.abort();
        this.host.destroy();
        this.iframe.remove();
        this.viewport.remove();
        const hostWindow = window as RuntimeHostWindow;
        if (hostWindow.__ARIB_HTML5_INSTALL__ === this.installer) {
            if (this.previousInstaller) hostWindow.__ARIB_HTML5_INSTALL__ = this.previousInstaller;
            else delete hostWindow.__ARIB_HTML5_INSTALL__;
        }
        await this.backend.dispose();
    }
}
