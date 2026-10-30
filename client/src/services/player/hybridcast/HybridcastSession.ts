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
    private readonly stage = document.createElement('div');
    private readonly mediaSurface = document.createElement('div');
    private mediaParent: HTMLElement | null = null;
    private mediaNextSibling: ChildNode | null = null;
    private mediaStyle: string | null = null;
    private installedDocument: Document | null = null;
    private preparingDocument: Document | null = null;
    private presentationGeneration = 0;
    private presentationFrame: number | null = null;
    private presentationTimeout: ReturnType<typeof setTimeout> | null = null;
    private presentationReady = false;
    private presented = false;
    private presentationFailed = false;
    private readonly frameLoadHandler = () => { void this.preparePresentation(); };
    private readonly resizeObserver: ResizeObserver;
    private readonly resizeHandler = () => this.schedulePresentationBoundsSync();
    private readonly backend = new ServiceWorkerBroadcastVfs({
        workerUrl: '/data-broadcast/arib-vfs-sw.js', baseUrl: '/data-broadcast/',
    });
    private readonly host: AribReceiverHost;
    private readonly abortController = new AbortController();
    private readonly previousInstaller = (window as RuntimeHostWindow).__ARIB_HTML5_INSTALL__;
    private readonly installer: (target: RuntimeWindow) => void;
    private disposed = false;

    constructor(private readonly container: HTMLElement, private readonly presentationTarget: HTMLElement,
        private readonly mediaElement: HTMLElement,
        program: ProgramInfo, private readonly onExit: () => void,
        private readonly onError: (code: string) => void, private readonly onReady: () => void,
        private readonly onLoading: () => void,
        private readonly publicHelp = false) {
        // BML は 720×480 の論理サイズを 16:9 に補正して表示するため、videoWrap 全体ではなく
        // 実際に見えている BML 面の矩形へ HTML5 面を重ねる。これにより余白や BML の下端が露出しない。
        Object.assign(this.stage.style, {
            // 映像・BML より前、DPlayer の操作面 (3) と番組ヘッダー (5) より後ろに置く。
            position: 'absolute', overflow: 'hidden', zIndex: '2', pointerEvents: 'none', backgroundColor: '#000',
            // レイアウトと SDK の映像矩形計算は動かし、読込途中の白い iframe だけを描画しない。
            opacity: '0',
        });
        this.stage.inert = true;
        Object.assign(this.viewport.style, {position: 'absolute', inset: '0', pointerEvents: 'none'});
        Object.assign(this.iframe.style, {
            position: 'absolute', inset: '0', border: '0', width: '100%', height: '100%',
            transformOrigin: '0 0', colorScheme: 'only light',
        });
        this.iframe.title = 'Hybridcast';
        // この宿主はライブ BML の AIT 要求からのみ起動する。VFS の先行フックへ実際の入力源を渡す。
        this.iframe.tabIndex = -1;
        // MSE 接続済みの映像は同じ文書内に保持し、SDK の既存 external media plane へ渡す。
        // BML の非表示な Shadow DOM 内に残すと、HTML5 の映像窓だけ黒くなる。
        // 映像の所有元とスタイルは実際の表示切替時に保存する。
        // AIT・HTML の取得中は宿主の通常映像を保持し、非表示の stage へ移動しない。
        Object.assign(this.mediaSurface.style, {position: 'absolute', inset: '0', pointerEvents: 'none'});
        this.stage.append(this.viewport, this.mediaSurface, this.iframe);
        this.container.append(this.stage);
        this.iframe.addEventListener('load', this.frameLoadHandler);
        this.resizeObserver = new ResizeObserver(() => this.schedulePresentationBoundsSync());
        this.resizeObserver.observe(this.container);
        this.resizeObserver.observe(this.presentationTarget);
        window.addEventListener('resize', this.resizeHandler);
        this.syncPresentationBounds();
        this.host = new AribReceiverHost({
            iframe: this.iframe, viewport: this.viewport, videoSurface: this.mediaSurface,
            broadcastBaseUrl: '/data-broadcast/',
            // 起動可否は呼び出し元の通信設定で判定済み。実通信は VFS の allowlist proxy を通す。
            // 外部 URL を保持した fetch / XHR を、runtime で取得前に遮断しない。
            allowExternalNetwork: true, getDeviceIdentifier: () => null,
            userAgent: 'Hybridcast/1.0',
            getInputSource: () => 'onair',
            onLaunchDataBroadcastingBrowser: () => this.onExit(),
            onProgramGuideUnavailable: () => onError('HYBRIDCAST_EPG_UNAVAILABLE'),
            onLifecycle: event => {
                if (this.disposed) return;
                if (event.type === 'installed') {
                    // installed は head 内の先行フックでも届くため、表示完了とは扱わない。
                    this.installedDocument = this.iframe.contentDocument;
                    void this.preparePresentation();
                } else if (event.type === 'loading' || event.type === 'navigating') {
                    this.installedDocument = null;
                    this.preparingDocument = null;
                    this.presentationReady = false;
                    ++this.presentationGeneration;
                    this.cancelPresentationFrame();
                    this.host.setApplicationInputActive(false);
                    this.onLoading();
                    // SDK の導入成功後も、文書の読込が完了しない場合は復帰操作を残す。
                    if (this.presentationTimeout === null) {
                        this.presentationTimeout = setTimeout(() => {
                            this.presentationTimeout = null;
                            this.presentationFailed = true;
                            this.onError('HYBRIDCAST_LOAD_TIMEOUT');
                        }, 30000);
                    }
                } else if (event.type === 'exited') {
                    // SDK は読込エラー後にも exit を通知する。失敗理由と復帰操作は宿主に残す。
                    if (!this.presentationFailed) this.onExit();
                }
                else if (event.type === 'error') {
                    this.presentationFailed = true;
                    this.clearPresentationTimeout();
                    console.error('[HybridcastSession] Runtime error:', event.message);
                    this.onError('HYBRIDCAST_RUNTIME_ERROR');
                }
                else if (event.type === 'navigation-blocked' || event.type === 'frame-blocked') {
                    this.presentationFailed = true;
                    this.clearPresentationTimeout();
                    this.onError(event.type === 'frame-blocked' ? 'HYBRIDCAST_FRAME_BLOCKED' : 'HYBRIDCAST_NAVIGATION_BLOCKED');
                }
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
        // 初期化中のアプリへ操作を送らず、表示済みのページだけに入力権を渡す。
        if (!this.presented || !this.presentationReady || this.presentationFailed) return;
        // 宿主リモコンを操作した時点で HTML5 アプリへ入力権を渡す。
        // ページ遷移直後の show() 通知で入力権が解除されていても、最初のキーを失わない。
        this.host.setApplicationInputActive(true);
        this.host.dispatchKey(code);
    }

    /** 読込済み文書のフォントと最初のレイアウトを待ち、宿主へ表示切替を通知する。 */
    private async preparePresentation(): Promise<void> {
        const applicationDocument = this.iframe.contentDocument;
        if (this.disposed || this.presentationFailed || applicationDocument === null ||
            applicationDocument !== this.installedDocument || applicationDocument.readyState !== 'complete' ||
            applicationDocument === this.preparingDocument) return;
        this.preparingDocument = applicationDocument;
        const generation = this.presentationGeneration;
        await applicationDocument.fonts?.ready;
        // フォント待機中にリダイレクト・退出した文書は、遅れて表示しない。
        if (this.disposed || this.presentationFailed || generation !== this.presentationGeneration ||
            this.iframe.contentDocument !== applicationDocument) return;
        // load ハンドラー内の DOM 更新と SDK の画面・映像矩形通知を先に反映する。
        this.presentationFrame = requestAnimationFrame(() => {
            this.presentationFrame = requestAnimationFrame(() => {
                this.presentationFrame = null;
                if (this.disposed || this.presentationFailed || generation !== this.presentationGeneration ||
                    this.iframe.contentDocument !== applicationDocument) return;
                this.syncPresentationBounds();
                this.presentationReady = true;
                this.clearPresentationTimeout();
                this.host.setApplicationInputActive(true);
                this.onReady();
            });
        });
    }

    /** 宿主が BML の映像を返した後、同じ描画フレーム内で HTML 面と映像を一緒に表示する。 */
    present(): void {
        if (this.disposed || this.presented || !this.presentationReady || this.presentationFailed) return;
        this.mediaParent = this.mediaElement.parentElement;
        this.mediaNextSibling = this.mediaElement.nextSibling;
        this.mediaStyle = this.mediaElement.getAttribute('style');
        Object.assign(this.mediaElement.style, {
            position: 'absolute', inset: '0', width: '100%', height: '100%', transform: 'none',
        });
        this.mediaSurface.append(this.mediaElement);
        this.presented = true;
        this.stage.style.opacity = '1';
        this.stage.inert = false;
        this.host.setApplicationInputActive(true);
    }

    /** 文書遷移・退出中は SDK の非表示映像面から通常プレイヤーへ戻し、テレビ映像を継続する。 */
    suspend(): void {
        this.stage.style.opacity = '0';
        this.stage.inert = true;
        // 初回読込では宿主の映像をまだ借りていないため、BML / DPlayer の配置に触れない。
        if (!this.presented) return;
        if (this.mediaParent !== null) {
            const next = this.mediaNextSibling?.parentNode === this.mediaParent ? this.mediaNextSibling : null;
            this.mediaParent.insertBefore(this.mediaElement, next);
        }
        if (this.mediaStyle === null) this.mediaElement.removeAttribute('style');
        else this.mediaElement.setAttribute('style', this.mediaStyle);
        this.presented = false;
        this.mediaParent = null;
        this.mediaNextSibling = null;
        this.mediaStyle = null;
    }

    private cancelPresentationFrame(): void {
        if (this.presentationFrame === null) return;
        cancelAnimationFrame(this.presentationFrame);
        this.presentationFrame = null;
    }

    private clearPresentationTimeout(): void {
        if (this.presentationTimeout === null) return;
        clearTimeout(this.presentationTimeout);
        this.presentationTimeout = null;
    }

    /** BML の CSS 変形後の画面矩形を、未変形の HTML5 stage に反映する。 */
    private schedulePresentationBoundsSync(): void {
        // BML の拡大率は ResizeObserver の処理中に変更されるため、レイアウト確定後に矩形を読む。
        requestAnimationFrame(() => {
            if (!this.disposed) this.syncPresentationBounds();
        });
    }

    /** BML が実際に描画している 16:9 領域へ stage を位置合わせする。 */
    private syncPresentationBounds(): void {
        const containerRect = this.container.getBoundingClientRect();
        const targetRect = this.presentationTarget.getBoundingClientRect();
        const hasVisibleTarget = containerRect.width > 0 && containerRect.height > 0 &&
            targetRect.width > 0 && targetRect.height > 0;

        // 選局・破棄の途中で不完全な矩形を描画して、左上に一瞬だけ固定されることを避ける。
        this.stage.style.display = hasVisibleTarget ? '' : 'none';
        if (!hasVisibleTarget) return;

        this.stage.style.left = `${targetRect.left - containerRect.left}px`;
        this.stage.style.top = `${targetRect.top - containerRect.top}px`;
        this.stage.style.width = `${targetRect.width}px`;
        this.stage.style.height = `${targetRect.height}px`;
    }

    async destroy(): Promise<void> {
        if (this.disposed) return;
        this.disposed = true;
        this.abortController.abort();
        this.cancelPresentationFrame();
        this.clearPresentationTimeout();
        this.iframe.removeEventListener('load', this.frameLoadHandler);
        this.resizeObserver.disconnect();
        window.removeEventListener('resize', this.resizeHandler);
        this.host.destroy();
        // stage を外す前に所有元へ戻し、通常再生 / BML の既存復元処理へ引き渡す。
        this.suspend();
        this.stage.remove();
        const hostWindow = window as RuntimeHostWindow;
        if (hostWindow.__ARIB_HTML5_INSTALL__ === this.installer) {
            if (this.previousInstaller) hostWindow.__ARIB_HTML5_INSTALL__ = this.previousInstaller;
            else delete hostWindow.__ARIB_HTML5_INSTALL__;
        }
        await this.backend.dispose();
    }
}
