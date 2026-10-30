import DPlayer, { DPlayerType } from 'dplayer';
import {
    AribReceiverHost,
    BroadcastVfsSession,
    ServiceWorkerBroadcastVfs,
} from 'libaribhtml5';

import type {
    AribMediaPlane,
    AribMediaPlaneAdapter,
    AribMediaPlaneUnmountReason,
    ProgramInfo,
    ReceiverMedia,
    RuntimeWindow,
} from 'libaribhtml5';

import APIClient from '@/services/APIClient';
import { mapARIBProxyURL } from '@/services/player/hybridcast/ARIBProxyURL';
import { resolveTLVBroadbandEntry } from '@/services/player/hybridcast/TLVApplicationEntry';
import PlayerManager from '@/services/player/PlayerManager';
import useChannelsStore from '@/stores/ChannelsStore';
import usePlayerStore from '@/stores/PlayerStore';
import useSettingsStore from '@/stores/SettingsStore';
import { dayjs } from '@/utils';


type TLVApplicationResource = {
    contextId: number;
    path: string;
    contentType: string;
    data: Uint8Array;
};

type TLVApplicationState = {
    contextId: number;
    applicationType: number;
    organizationId: number;
    applicationId: number;
    controlCode: number;
    applicationDescriptorPresent: boolean;
    serviceBound: boolean;
    visibility: number;
    presentApplicationPriority: boolean;
    applicationPriority: number;
    entryPath: string;
    transportUrls: string[];
    transportProtocolLabels?: number[];
    transports?: Array<{protocolId: number; label: number; urls: string[]}>;
    entryReady: boolean;
};

type TLVBroadcastClock = {
    mediaTimeValue: bigint;
    mediaTimeTimescale: number;
    broadcastTimeValue: bigint;
    broadcastTimeTimescale: number;
};

type TLVEventInfo = DPlayerType.TLVEventInfo;

type TLVCaptionData = DPlayerType.TLVCaptionData;

type ManagedApplication = {
    key: string;
    state: TLVApplicationState;
    entry: string;
    broadband: boolean;
};

type TLVStreamEvent = {
    contextId: number;
    eventMessageTag: number;
    messageGroupId: number;
    messageVersion: number;
    currentNext: boolean;
    timeMode: number;
    messageId: number;
    privateData: Uint8Array;
};

type TLVViewerParticipationNotification = {
    contextId: number;
    sourcePacketId: number;
    eventMessageTag: number;
    dataEventId: number;
    messageGroupId: number;
    version: number;
    currentNext: boolean;
    sectionNumber: number;
    lastSectionNumber: number;
    inputOffset: bigint;
};

type ViewerParticipationHost = AribReceiverHost & {
    setApplicationInputActive?: (active: boolean) => void;
    notifyViewerParticipationCorner?: (notification: TLVViewerParticipationNotification) => void;
    resetViewerParticipationNotifications?: () => void;
};

type ReceiverStreamEvent = {
    source: {
        original_network_id?: number;
        tlv_stream_id?: number;
        service_id?: number;
        event_message_tag: number;
    };
    message_group_id: number;
    message_id: number;
    message_version: number;
    private_data_byte: string;
};

interface RuntimeHostWindow extends Window {
    __ARIB_HTML5_INSTALL__?: (target: RuntimeWindow) => void;
}

const RECEIVER_KEY_MAP: Record<number, number> = {
    1: 38,
    2: 40,
    3: 37,
    4: 39,
    18: 13,
    19: 461,
    20: 457,
    21: 406,
    22: 403,
    23: 404,
    24: 405,
};

const LEGACY_RECEIVER_INFO_PREFIX = 'KonomiTV-BMLBrowser_nvram_prefix=receiverinfo%2F';

type ReceiverInfo = {
    zipcode: string | null;
    prefecture: number | null;
    regioncode: number | null;
};

/**
 * DPlayer の video を元の 16:9 コンテナから動かさず、データ放送の映像枠だけへ合わせる。
 * MSE 接続済みの video を iframe へ移動すると Chromium が MediaSource を閉じるため、
 * DOM の親子関係は維持したまま位置と大きさだけを更新する。
 *
 * 通常は iframe の背面 (z=1) に置く。既定寸法の object を前景 wrapper が映像窓として包む
 * 放送ページだけは、iframe と同じ z=2 の後続 sibling にして映像窓を前へ戻す。
 */
class DPlayerMediaPlaneAdapter implements AribMediaPlaneAdapter {

    public readonly renderMode = 'external' as const;

    private readonly player: DPlayer;
    private managed_video: HTMLVideoElement | null = null;
    private application_visible = true;
    private last_plane: AribMediaPlane | null = null;

    constructor(player: DPlayer) {
        this.player = player;
    }

    public mountMediaPlane(_object: HTMLElement, plane: AribMediaPlane): void {
        this.last_plane = plane;
        if (this.application_visible) this.apply(plane);
        else this.restoreNormalLayout();
    }

    public updateMediaPlane(_object: HTMLElement, plane: AribMediaPlane): void {
        this.last_plane = plane;
        if (this.application_visible) this.apply(plane);
        else this.restoreNormalLayout();
    }

    public unmountMediaPlane(reason: AribMediaPlaneUnmountReason): void {
        // アプリ終了時と Manager 破棄時は、DPlayer 本来の CSS レイアウトへ完全に戻す。
        if (reason === 'application-exit' || reason === 'host-destroy') {
            this.last_plane = null;
            this.restoreNormalLayout();
            return;
        }

        // ページ遷移中や映像 object が消えた間は、直前の映像が全面に残らないよう隠す。
        if (this.managed_video !== null) this.managed_video.style.visibility = 'hidden';
    }

    public setApplicationVisible(visible: boolean): void {
        if (this.application_visible === visible) return;
        this.application_visible = visible;
        if (visible && this.last_plane !== null) this.apply(this.last_plane);
        else if (!visible) this.restoreNormalLayout();
    }

    private apply(plane: AribMediaPlane): void {
        const video = this.player.video;
        if (this.managed_video !== null && this.managed_video !== video) this.restoreNormalLayout();
        this.managed_video = video;

        const percent = (value: number, extent: number): string => `${value / extent * 100}%`;
        Object.assign(video.style, {
            position: 'absolute',
            left: percent(plane.x, plane.screenWidth),
            top: percent(plane.y, plane.screenHeight),
            width: percent(plane.width, plane.screenWidth),
            height: percent(plane.height, plane.screenHeight),
            maxWidth: 'none',
            maxHeight: 'none',
            display: 'block',
            visibility: plane.visible ? 'visible' : 'hidden',
            objectFit: 'contain',
            background: '#000',
            pointerEvents: 'none',
            zIndex: plane.layer.externalPlacement === 'above-application' ? '2' : '1',
        });
    }

    private restoreNormalLayout(): void {
        if (this.managed_video === null) return;
        for (const property of [
            'position', 'left', 'top', 'width', 'height', 'max-width', 'max-height',
            'display', 'visibility', 'object-fit', 'background', 'pointer-events', 'z-index',
        ]) {
            this.managed_video.style.removeProperty(property);
        }
        this.managed_video = null;
    }
}


/**
 * MMT/TLV 上の ARIB STD-B62 HTML5 データ放送を HonomiTV の UI へ接続する。
 * TLV の解析と再生は DPlayer、iframe/VFS/リモコンはアプリ側が所有する。
 */
class TLVDataBroadcastingManager implements PlayerManager {

    public readonly restart_required_when_quality_switched = true;

    private readonly player: DPlayer;
    private readonly playback_mode: 'Live' | 'Video';
    private readonly remocon_element: HTMLElement;
    private readonly remocon_data_broadcasting_element: HTMLElement;
    private viewport: HTMLDivElement | null = null;
    private iframe: HTMLIFrameElement | null = null;
    private host: AribReceiverHost | null = null;
    private vfs: BroadcastVfsSession | null = null;
    private vfs_backend: ServiceWorkerBroadcastVfs | null = null;
    private remocon_abort_controller: AbortController | null = null;
    private previous_installer?: (target: RuntimeWindow) => void;
    private runtime_installer?: (target: RuntimeWindow) => void;
    private readonly resource_revisions = new Map<string, number>();
    private readonly applications = new Map<string, ManagedApplication>();
    private readonly program_events = new Map<number, TLVEventInfo>();
    private session_generation = 0;
    private ready_context_id: number | null = null;
    private ready_entry: string | null = null;
    private ready_application_key: string | null = null;
    private active_application_key: string | null = null;
    private pending_application_key: string | null = null;
    private application_load_abort: AbortController | null = null;
    private show_requested = false;
    private dispatch_data_key_after_install = false;
    private autostart_scheduled = false;
    private media_plane_adapter: DPlayerMediaPlaneAdapter | null = null;
    private runtime_media_patch_observer: MutationObserver | null = null;
    private runtime_iframe_load_handler: (() => void) | null = null;
    private visible = false;
    private previous_remocon_display: boolean | null = null;

    constructor(player: DPlayer, playback_mode: 'Live' | 'Video') {
        this.player = player;
        this.playback_mode = playback_mode;
        this.remocon_element = document.querySelector('.watch-panel__remocon')!;
        this.remocon_data_broadcasting_element = this.remocon_element.querySelector('.remote-control-data-broadcasting')!;
    }

    public async init(): Promise<void> {
        if (this.player.quality?.type !== 'tlv' || useSettingsStore().settings.tv_show_data_broadcasting === false) {
            this.toggleRemoconLoading(false);
            this.toggleRemoconEnabled(false);
            return;
        }

        // Worker 自身を scope 配下へ配置しているため Service-Worker-Allowed ヘッダーは不要。
        this.vfs_backend = new ServiceWorkerBroadcastVfs({
            // Worker が VFS の応答を所有する。URL に版番号を付けず、登録済み Worker の
            // 通常の更新処理へ任せることで、特定の生成物へ依存した更新経路を作らない。
            workerUrl: '/data-broadcast/arib-vfs-sw.js', baseUrl: '/data-broadcast/',
        });
        this.vfs = new BroadcastVfsSession(this.vfs_backend, {
            onError: error => {
                console.error('[TLVDataBroadcastingManager] VFS operation failed.', error);
                this.player.notice('データ放送リソースの保存に失敗しました。', 3000, undefined, '#FF6F6A');
            },
        });

        this.viewport = document.createElement('div');
        this.viewport.className = 'dplayer-tlv-data-broadcast-viewport';
        Object.assign(this.viewport.style, {
            position: 'absolute',
            inset: '0',
            overflow: 'hidden',
            background: 'transparent',
            opacity: '0',
            pointerEvents: 'none',
            zIndex: '0',
        });

        this.iframe = document.createElement('iframe');
        this.iframe.className = 'dplayer-tlv-data-broadcast';
        this.iframe.setAttribute('aria-hidden', 'true');
        this.iframe.tabIndex = -1;
        Object.assign(this.iframe.style, {
            position: 'absolute',
            inset: '0',
            width: '100%',
            height: '100%',
            border: '0',
            display: 'none',
            opacity: '0',
            pointerEvents: 'none',
            zIndex: '2',
            transformOrigin: '0 0',
            // KonomiTV の dark color-scheme を継承すると、通常の light canvas を使う放送ページとの
            // scheme 不一致を Chromium が不透明な白 backdrop で補う。iframe owner を only light に固定し、
            // transparent な application canvas 越しに z=1 の映像面が見える状態を維持する。
            colorScheme: 'only light',
        });
        // receiver 背景、video、iframe を同じ 16:9 コンテナの sibling にして、
        // z=0 < z=1 < z=2 の順で外部映像面を application canvas の間へ合成する。
        // video 自体は DPlayer が生成した直系子要素のままなので、MSE 接続を失わない。
        this.player.template.videoWrapAspect.prepend(this.viewport, this.iframe);

        // 文書内 bootstrap が失われた復元ページでも、load 済みの同一オリジン文書へ
        // 直ちに runtime を導入する。AribReceiverHost より先に登録し、host の 404
        // watchdog が動く前に installed メッセージを送れる順序を維持する。
        this.runtime_iframe_load_handler = () => {
            const target = this.iframe?.contentWindow as RuntimeWindow | null;
            if (target === null || target.location.href === 'about:blank') return;
            this.runtime_installer?.(target);
        };
        this.iframe.addEventListener('load', this.runtime_iframe_load_handler);

        const receiver_info = this.readReceiverInfo();
        const source = this.playback_mode === 'Live' ?
            useChannelsStore().channel.current : usePlayerStore().recorded_program;
        const supported_media: ReceiverMedia[] = [];
        if (source.network_id === 11) supported_media.push('6');
        if (source.network_id === 12) supported_media.push('7');
        this.media_plane_adapter = new DPlayerMediaPlaneAdapter(this.player);
        const receiver_options: ConstructorParameters<typeof AribReceiverHost>[0] & {
            onViewerParticipation?: () => void;
        } = {
            iframe: this.iframe,
            viewport: this.viewport,
            broadcastBaseUrl: '/data-broadcast/',
            // BS4K 通信アプリが PC 用ページへ戻らず、MMT 受信機向け分岐を選ぶための識別子。
            userAgent: 'A-PAB/1.0',
            supportedMedia: supported_media,
            getInputSource: () => this.playback_mode === 'Live' ? 'onair' : 'recorded',
            allowExternalNetwork: useSettingsStore().settings.enable_internet_access_from_data_broadcasting,
            systemInformation: {
                zipcode: receiver_info.zipcode,
                prefecture: receiver_info.prefecture,
                regioncode: receiver_info.regioncode,
            },
            mediaPlaneAdapter: this.media_plane_adapter,
            onCaptionSubscription: subscription => {
                this.player.plugins.tlv?.setSubtitleSuppressedComponentTags(subscription.componentTags);
            },
            onViewerParticipation: () => {
                this.player.notice(
                    '視聴者参加型データ放送が始まりました。データボタンで操作できます。',
                    5000,
                );
            },
            onReplaceApplication: async request => {
                const application = [...this.applications.values()].find(candidate =>
                    candidate.state.organizationId === request.organizationId &&
                    candidate.state.applicationId === request.applicationId);
                if (application === undefined) {
                    throw new Error(`MH-AIT application not found: ${request.organizationId}:${request.applicationId}`);
                }
                await this.loadManagedApplication(application, 'アプリケーション切替中');
            },
            onLifecycle: event => {
                console.debug('[TLVDataBroadcastingManager] Receiver lifecycle.', event);
                if (event.type === 'installed') {
                    // loadManagedApplication()/replaceApplication() の完了時だけ active slot を更新する。
                    // 放送ページ内の location.replace()/reload でも新しい runtime が installed を送るが、
                    // その時 pending は null なので既存 application の identity を維持する必要がある。
                    if (this.pending_application_key !== null) {
                        this.active_application_key = this.pending_application_key;
                        this.pending_application_key = null;
                    }
                    this.applyApplicationVisibility();
                    if (this.dispatch_data_key_after_install) {
                        this.dispatch_data_key_after_install = false;
                        this.show_requested = false;
                        this.setApplicationInputActive(true);
                        this.host?.dispatchKey(RECEIVER_KEY_MAP[20]);
                    } else if (this.show_requested) {
                        void this.enterApplication();
                    }
                } else if (event.type === 'exited') {
                    this.active_application_key = null;
                    this.pending_application_key = null;
                    this.visible = false;
                    this.unlockPanelForApplication();
                    this.media_plane_adapter?.setApplicationVisible(false);
                    if (this.viewport !== null) this.viewport.style.opacity = '0';
                    if (this.iframe !== null) {
                        this.iframe.style.opacity = '0';
                        this.iframe.style.display = 'none';
                        this.iframe.setAttribute('aria-hidden', 'true');
                    }
                } else if (event.type === 'error') {
                    console.error('[TLVDataBroadcastingManager] Receiver runtime failed.', event.message);
                    this.exitApplication();
                    this.player.notice(`データ放送の実行に失敗しました：${event.message}。データボタンで再試行してください。[TLV_DATA_RUNTIME_ERROR]`, 6000, undefined, '#FF6F6A');
                }
            },
        };
        this.host = new AribReceiverHost(receiver_options);
        this.applyLayoutBackgroundColor(null);
        const host_window = window as RuntimeHostWindow;
        this.previous_installer = host_window.__ARIB_HTML5_INSTALL__;
        this.runtime_installer = target => {
            this.host?.installRuntime(target);
            this.patchBroadcastMediaCanvas(target);
            this.installReceiverFontFallback(target);
        };
        host_window.__ARIB_HTML5_INSTALL__ = this.runtime_installer;

        this.player.on('tlv_application_resources_reset', this.handleResourcesReset);
        this.player.on('tlv_application_resource', this.handleApplicationResource);
        this.player.on('tlv_application_state', this.handleApplicationState);
        this.player.on('tlv_broadcast_clock', this.handleBroadcastClock);
        this.player.on('tlv_layout_configuration', this.handleLayoutConfiguration);
        this.player.on('tlv_event_info', this.handleEventInfo);
        this.player.on('tlv_stream_event' as DPlayerType.Events, this.handleStreamEvent);
        this.player.on('tlv_viewer_participation' as DPlayerType.Events, this.handleViewerParticipation);
        this.player.on('tlv_tracks', this.handleCaptionTracks);
        this.player.on('tlv_caption_data', this.handleCaptionData);
        this.initRemoconButtons();
        await this.beginSession();
        this.handleCaptionTracks();
        this.syncBroadcastClock();
        console.log('[TLVDataBroadcastingManager] Initialized.');
    }

    public async destroy(): Promise<void> {
        this.player.off('tlv_application_resources_reset', this.handleResourcesReset);
        this.player.off('tlv_application_resource', this.handleApplicationResource);
        this.player.off('tlv_application_state', this.handleApplicationState);
        this.player.off('tlv_broadcast_clock', this.handleBroadcastClock);
        this.player.off('tlv_layout_configuration', this.handleLayoutConfiguration);
        this.player.off('tlv_event_info', this.handleEventInfo);
        this.player.off('tlv_stream_event' as DPlayerType.Events, this.handleStreamEvent);
        this.player.off('tlv_viewer_participation' as DPlayerType.Events, this.handleViewerParticipation);
        this.player.off('tlv_tracks', this.handleCaptionTracks);
        this.player.off('tlv_caption_data', this.handleCaptionData);
        this.remocon_abort_controller?.abort();
        this.remocon_abort_controller = null;
        this.session_generation += 1;
        this.exitApplication();
        this.unlockPanelForApplication();
        this.host?.setLctBackgroundColor(null);
        this.host?.destroy();
        this.host = null;
        this.runtime_media_patch_observer?.disconnect();
        this.runtime_media_patch_observer = null;
        if (this.iframe !== null && this.runtime_iframe_load_handler !== null) {
            this.iframe.removeEventListener('load', this.runtime_iframe_load_handler);
        }
        this.runtime_iframe_load_handler = null;
        this.viewport?.remove();
        this.iframe?.remove();
        this.viewport = null;
        this.iframe = null;
        const vfs = this.vfs;
        const vfs_backend = this.vfs_backend;
        this.vfs = null;
        this.vfs_backend = null;
        await vfs?.dispose().catch(error => {
            console.error('[TLVDataBroadcastingManager] VFS session dispose failed.', error);
        });
        await vfs_backend?.dispose().catch(error => {
            console.error('[TLVDataBroadcastingManager] VFS backend dispose failed.', error);
        });
        this.resource_revisions.clear();
        this.applications.clear();
        this.program_events.clear();
        this.media_plane_adapter = null;
        this.player.plugins.tlv?.setSubtitleSuppressedComponentTags([]);
        this.toggleRemoconLoading(true);
        this.toggleRemoconEnabled(false);
        const host_window = window as RuntimeHostWindow;
        // 古い PlayerManager の遅延 destroy が、新しいインスタンスの installer を消さないようにする。
        if (host_window.__ARIB_HTML5_INSTALL__ === this.runtime_installer) {
            if (this.previous_installer !== undefined) host_window.__ARIB_HTML5_INSTALL__ = this.previous_installer;
            else delete host_window.__ARIB_HTML5_INSTALL__;
        }
        this.runtime_installer = undefined;
        console.log('[TLVDataBroadcastingManager] Destroyed.');
    }

    /**
     * 同一オリジン文書の非対応 object だけを runtime の映像スロットへ置き換える。
     * 映像と iframe の合成、背景の透過処理は既存の media-plane 実装に任せる。
     */
    private patchBroadcastMediaCanvas(target: RuntimeWindow): void {
        const document = target.document;
        const apply = (): void => {
            for (const object of document.querySelectorAll<HTMLElement>(
                'object[type="video/x-arib2-broadcast"]',
            )) {
                // 放送 CSS の object / video 向け寸法指定を維持する。
                // div に変えると #vstream video の height: 100% が適用されず、
                // 空スロットが高さ 0 となって外部 DPlayer まで非表示になる。
                // src は設定せず、実際の映像は従来どおり元の DPlayer video で再生する。
                const replacement = document.createElement('video');
                for (const attribute of Array.from(object.attributes)) {
                    if (attribute.name.toLowerCase() !== 'type') {
                        replacement.setAttribute(attribute.name, attribute.value);
                    }
                }
                replacement.setAttribute('data-arib-object', 'video/x-arib2-broadcast');
                while (object.firstChild !== null) replacement.append(object.firstChild);
                object.replaceWith(replacement);
            }
        };

        this.runtime_media_patch_observer?.disconnect();
        this.runtime_media_patch_observer = null;
        apply();
        if (document.documentElement !== null && typeof target.MutationObserver === 'function') {
            this.runtime_media_patch_observer = new target.MutationObserver(apply);
            this.runtime_media_patch_observer.observe(document.documentElement, {
                childList: true,
                subtree: true,
            });
        }
    }

    private async beginSession(): Promise<void> {
        const generation = ++this.session_generation;
        this.resource_revisions.clear();
        this.applications.clear();
        this.program_events.clear();
        this.ready_context_id = null;
        this.ready_entry = null;
        this.ready_application_key = null;
        this.active_application_key = null;
        this.pending_application_key = null;
        this.show_requested = false;
        this.dispatch_data_key_after_install = false;
        this.autostart_scheduled = false;
        this.host?.resetCaptions();
        this.player.plugins.tlv?.setSubtitleSuppressedComponentTags([]);
        this.host?.clearBroadcastClock();
        this.host?.clearProgramInfo();
        this.seedProgramIdentity();
        // レイアウト設定は選局中のサービス / セッションだけに属するため、
        // 非同期の VFS 構築より先に消して、前のチャンネルの LCT 背景色を残さない。
        this.applyLayoutBackgroundColor(null);
        (this.host as ViewerParticipationHost | null)?.resetViewerParticipationNotifications?.();
        this.exitApplication();
        this.toggleRemoconLoading(true);
        this.toggleRemoconEnabled(false);
        try {
            await this.vfs?.beginSession();
            if (generation !== this.session_generation) return;
            this.replayApplicationSnapshot();
            this.handleCaptionTracks();
        } catch (error) {
            if (generation !== this.session_generation) return;
            console.error('[TLVDataBroadcastingManager] Failed to begin VFS session.', error);
            this.toggleRemoconLoading(false);
        }
    }

    private seedProgramIdentity(): void {
        // MH-AIT は MH-EIT[present] より先に起動できる。放送アプリが起動直後から現在サービスを
        // 判定できるよう、KonomiTV が既に持つ選局情報だけを先に渡し、完全な番組情報は MH-EIT で上書きする。
        const source = this.playback_mode === 'Live' ?
            useChannelsStore().channel.current :
            usePlayerStore().recorded_program;
        if (source.service_id === null || source.service_id === 0) return;
        const program_info: ProgramInfo = {service_id: source.service_id};
        if (source.network_id !== null && source.network_id !== 0) {
            program_info.original_network_id = source.network_id;
        }
        this.host?.setProgramInfo(program_info);
    }

    private readonly handleResourcesReset = (): void => {
        void this.beginSession();
    };

    /**
     * DPlayer は Manager 初期化前から TLV を解復用するため、最初の MH-AIT / resource
     * 通知を取り逃すことがある。demuxer が保持する現在値を VFS session 確立後に再投入し、
     * 以降のイベント通知と同じ経路で処理する。
     */
    private replayApplicationSnapshot(): void {
        const plugin = this.player.plugins.tlv;
        if (plugin === undefined) return;
        this.syncLayoutConfiguration();
        const resources = plugin.applicationResources();
        const applications = plugin.applications();
        console.log('[TLVDataBroadcastingManager] Replaying current demux snapshot.', {
            applications: applications.length,
            resources: resources.length,
        });
        for (const metadata of resources) {
            const resource = plugin.applicationResource(metadata.contextId, metadata.path);
            if (resource !== null) this.handleApplicationResource(resource);
        }
        for (const application of applications) this.handleApplicationState(application);
    }

    private readonly handleCaptionTracks = (): void => {
        const component_tags = this.player.plugins.tlv?.tracks
            .filter(track => track.kind === 'subtitle' && track.codec === 'ttml')
            .map(track => this.normalizeCaptionComponentTag(track.componentTag))
            .filter((tag): tag is number => tag !== null) ?? [];
        this.host?.setCaptionTracks([...new Set(component_tags)]);
    };

    private readonly handleCaptionData = (detail?: Event | TLVCaptionData): void => {
        const packet = detail as TLVCaptionData;
        const component_tag = this.normalizeCaptionComponentTag(packet?.componentTag);
        if (component_tag === null || !(packet.data instanceof Uint8Array) || this.host === null) return;
        const tmd = this.binaryNibble(packet.subtitleTimingMode ?? 0);
        const decoder = new TextDecoder();
        this.host.pushCaption({
            componentTag: component_tag,
            dataType: '0000',
            tmd,
            data: decoder.decode(packet.data),
        });
        for (const resource of packet.subtitleResources ?? []) {
            const data_type = Number(resource.dataType);
            if (!Number.isInteger(data_type) || data_type < 0 || data_type > 0x0f ||
                !(resource.data instanceof Uint8Array)) continue;
            this.host.pushCaption({
                componentTag: component_tag,
                dataType: this.binaryNibble(data_type),
                tmd,
                data: data_type === 6 ? decoder.decode(resource.data) : this.bytesToDomString(resource.data),
            });
        }
    };

    private normalizeCaptionComponentTag(value: unknown): number | null {
        const component_tag = Number(value);
        if (!Number.isInteger(component_tag) || component_tag < 0 || component_tag > 0xffff) return null;
        return component_tag & 0xff;
    }

    private binaryNibble(value: number): string {
        return (value & 0x0f).toString(2).padStart(4, '0');
    }

    private readonly handleApplicationResource = (detail?: Event | TLVApplicationResource): void => {
        const resource = detail as TLVApplicationResource;
        if (!resource || this.vfs === null) return;
        const content_type = this.normalizeResourceContentType(resource.path, resource.contentType);
        const revision = this.vfs.enqueue({
            path: resource.path,
            // 放送側が application/xhtml+xml 等を指定しても、VFS Worker が HTML/CSS を
            // 確実に準備し runtime bootstrap と放送ルートの URL 変換を適用できるようにする。
            contentType: content_type,
            // NHK の二段階ページでも parent の階層に依存せず、放送側スクリプトより前に
            // receiverDevice を同期導入する。Worker 側の bootstrap は冪等なので併存できる。
            data: content_type.startsWith('text/html') ? this.injectRuntimeBootstrap(resource.data) : resource.data,
        });
        this.resource_revisions.set(`${resource.contextId}:${resource.path}`, revision);
        if (this.show_requested) void this.enterApplication();
    };

    private readonly handleApplicationState = (detail?: Event | TLVApplicationState): void => {
        const state = detail as TLVApplicationState;
        this.syncBroadcastClock();
        if (!state) return;
        const key = this.applicationKey(state);
        if (state.controlCode === 0x04) {
            this.applications.delete(key);
            if (this.ready_application_key === key) {
                this.ready_application_key = null;
                this.ready_context_id = null;
                this.ready_entry = null;
            }
            if (this.active_application_key === key || this.pending_application_key === key) {
                this.exitApplication();
            }
            return;
        }
        // 通信専用アプリには carousel の入口ファイルが届かないため、entryReady を待たない。
        const broadcast_entry = state.entryReady
            ? this.player.plugins.tlv?.applicationEntry(state.contextId) ?? null : null;
        const network_entry = resolveTLVBroadbandEntry(state, location.origin);
        const entry = broadcast_entry ?? network_entry;
        if (entry === null) return;
        const application: ManagedApplication = {key, state: {...state}, entry, broadband: broadcast_entry === null};
        this.applications.set(key, application);

        // 放送入口が揃ったアプリと、通信入口が解決できたアプリを D ボタンから到達できる
        // 互換入口として保持する。PRESENT が後から届いた場合は必ずそちらを優先し、
        // 同種の候補同士だけ application_priority で選択する。
        const current = this.ready_application_key === null
            ? null
            : this.applications.get(this.ready_application_key) ?? null;
        const current_is_present = current?.state.controlCode === 0x02;
        const candidate_is_present = state.controlCode === 0x02;
        const current_priority = Number(current?.state.applicationPriority ?? 0);
        const candidate_priority = Number(state.applicationPriority ?? 0);
        if (current === null ||
            (candidate_is_present && !current_is_present) ||
            (candidate_is_present === current_is_present && candidate_priority >= current_priority)) {
            this.ready_application_key = key;
            this.ready_context_id = state.contextId;
            this.ready_entry = entry;
        }
        this.toggleRemoconLoading(false);
        this.toggleRemoconEnabled(true);
        if (this.show_requested) void this.enterApplication();

        if (this.active_application_key === key) this.applyApplicationVisibility();
        if (state.controlCode === 0x01) this.scheduleAutostart();
        console.log('[TLVDataBroadcastingManager] MH-AIT application is ready.', {
            applicationId: state.applicationId,
            organizationId: state.organizationId,
            controlCode: state.controlCode,
            visibility: state.visibility,
            entry,
        });
    };

    private applicationKey(state: TLVApplicationState): string {
        return `${state.applicationType}:${state.organizationId}:${state.applicationId}`;
    }

    private applicationIsUserVisible(application: ManagedApplication): boolean {
        // Older demuxer builds did not expose the mandatory descriptor. Keep
        // their historical visible behavior instead of treating missing data
        // as visibility=00.
        return !application.state.applicationDescriptorPresent || application.state.visibility === 0x03;
    }

    private scheduleAutostart(): void {
        if (this.autostart_scheduled) return;
        this.autostart_scheduled = true;
        const generation = this.session_generation;
        queueMicrotask(() => {
            this.autostart_scheduled = false;
            if (generation !== this.session_generation || this.pending_application_key !== null) return;
            const candidate = [...this.applications.values()]
                .filter(application => application.state.controlCode === 0x01 &&
                    (!application.broadband || useSettingsStore().settings.enable_internet_access_from_data_broadcasting))
                .sort((left, right) => right.state.applicationPriority - left.state.applicationPriority)[0];
            if (candidate === undefined || candidate.key === this.active_application_key) return;
            if (this.active_application_key !== null) {
                const active = this.applications.get(this.active_application_key);
                if (active?.state.controlCode !== 0x02 || active.state.presentApplicationPriority) return;
                this.exitApplication();
            }
            void this.loadManagedApplication(candidate, '連動アプリケーション自動起動中');
        });
    }

    private readonly handleBroadcastClock = (detail?: Event | TLVBroadcastClock): void => {
        const clock = detail as TLVBroadcastClock;
        if (!clock || this.host === null || !(clock.broadcastTimeTimescale > 0) || !(clock.mediaTimeTimescale > 0)) return;
        const epoch_milliseconds = Number(clock.broadcastTimeValue) * 1000 / clock.broadcastTimeTimescale - 2208988800 * 1000;
        const media_time_seconds = Number(clock.mediaTimeValue) / clock.mediaTimeTimescale;
        if (!Number.isFinite(epoch_milliseconds) || !Number.isFinite(media_time_seconds)) return;
        this.host.setBroadcastClock({
            epochMilliseconds: epoch_milliseconds,
            mediaTimeSeconds: media_time_seconds,
            currentMediaTimeSeconds: () => this.player.video.currentTime,
        });
        this.updateProgramInfo();
    };

    private syncBroadcastClock(): void {
        const plugin = this.player.plugins.tlv as (DPlayerType.TLVPlugin & {
            broadcastClock?: () => TLVBroadcastClock | null;
        }) | undefined;
        const clock = plugin?.broadcastClock?.();
        if (clock !== null && clock !== undefined) this.handleBroadcastClock(clock);
    }

    private readonly handleLayoutConfiguration = (detail?: Event | DPlayerType.TLVLayoutConfiguration | null): void => {
        const layout_configuration = detail as DPlayerType.TLVLayoutConfiguration | null | undefined;
        this.applyLayoutBackgroundColor(layout_configuration?.backgroundColorRgb ?? null);
    };

    private applyLayoutBackgroundColor(background_color_rgb: number | null): void {
        // ARIB STD-B62 に従い、背景平面の色は受信中の LCT に指定された色だけを反映する。
        // LCT に背景色がない場合は上書きを解除し、HTML アプリの背景色、透明なら受信機の黒背景へ戻す。
        this.host?.setLctBackgroundColor(background_color_rgb);
    }

    private syncLayoutConfiguration(): void {
        // リスナー登録前に受信済みの LCT も、DPlayer が保持するスナップショットから復元する。
        this.handleLayoutConfiguration(this.player.plugins.tlv?.layoutConfiguration() ?? null);
    }

    private readonly handleStreamEvent = (detail?: Event | TLVStreamEvent): void => {
        const event = detail as TLVStreamEvent;
        if (!event || event.currentNext === false || event.timeMode !== 0 || this.host === null) return;

        const present = this.program_events.get(0);
        const source: ReceiverStreamEvent['source'] = {
            event_message_tag: event.eventMessageTag,
        };
        if (present !== undefined) {
            source.original_network_id = present.originalNetworkId;
            source.tlv_stream_id = present.tlvStreamId;
            source.service_id = present.serviceId;
        }

        const value: ReceiverStreamEvent = {
            source,
            message_group_id: event.messageGroupId,
            message_id: event.messageId,
            message_version: event.messageVersion,
            private_data_byte: this.bytesToDomString(event.privateData),
        };
        // tlvdemux 0.1.2/libaribhtml5 0.1.3 以后的原生入口。旧版依赖下安全忽略，
        // 避免把 EMT 当成 URL 或硬编码 40/5；页面注册的 listener 决定后续动作。
        const host = this.host as AribReceiverHost & {
            emitStreamEvent?: (stream_event: ReceiverStreamEvent) => void;
        };
        host.emitStreamEvent?.(value);
    };

    private readonly handleViewerParticipation = (
        detail?: Event | TLVViewerParticipationNotification,
    ): void => {
        const notification = detail as TLVViewerParticipationNotification;
        if (!notification || notification.currentNext === false) return;
        (this.host as ViewerParticipationHost | null)?.notifyViewerParticipationCorner?.(notification);
    };

    private bytesToDomString(data: Uint8Array): string {
        let result = '';
        for (let offset = 0; offset < data.byteLength; offset += 8192) {
            result += String.fromCharCode(...data.subarray(offset, offset + 8192));
        }
        return result;
    }

    private readonly handleEventInfo = (detail?: Event | TLVEventInfo): void => {
        const event = detail as TLVEventInfo;
        if (!event || event.tableId !== 0x8b || event.currentNext === false || ![0, 1].includes(event.sectionNumber)) return;
        this.program_events.set(event.sectionNumber, event);
        this.updateProgramInfo();
    };

    private updateProgramInfo(): void {
        const present = this.program_events.get(0);
        if (present === undefined || present.startTimeUnixMilliseconds === null || present.durationSeconds === null) return;
        const program_info: ProgramInfo = {
            original_network_id: present.originalNetworkId,
            transport_stream_id: present.tlvStreamId,
            service_id: present.serviceId,
            event_id: present.eventId,
            name: present.title,
            event_name: present.title,
            start_time: dayjs(present.startTimeUnixMilliseconds).toDate(),
            duration: present.durationSeconds * 1000,
            desc: present.description,
            event_text: present.description,
            running_status: present.runningStatus,
            free_ca_mode: present.freeCaMode,
        };
        const following = this.program_events.get(1);
        if (following !== undefined && following.startTimeUnixMilliseconds !== null && following.durationSeconds !== null) {
            Object.assign(program_info, {
                f_event_id: following.eventId,
                f_name: following.title,
                f_start_time: dayjs(following.startTimeUnixMilliseconds).toDate(),
                f_duration: following.durationSeconds * 1000,
                f_desc: following.description,
            });
        }
        this.host?.setProgramInfo(program_info);
    }

    private initRemoconButtons(): void {
        this.remocon_abort_controller = new AbortController();
        this.remocon_element.querySelectorAll<HTMLButtonElement>('button').forEach(button => {
            button.addEventListener('click', () => {
                const key_code = Number(button.dataset.aribKeyCode);
                const remocon_id = button.dataset.remoconId !== undefined ? Number(button.dataset.remoconId) : null;

                // ライブの数字キーはデータアプリ表示中だけアプリへ渡し、通常時は既存の換台 UI に任せる。
                if (remocon_id !== null && (this.playback_mode === 'Live' && this.visible === false)) return;
                if (key_code === 20 && this.visible === false) {
                    void this.enterApplication();
                    return;
                }
                const receiver_key = remocon_id !== null ? this.resolveNumberKey(remocon_id) : RECEIVER_KEY_MAP[key_code];
                if (receiver_key !== undefined) {
                    this.setApplicationInputActive(true);
                    this.host?.dispatchKey(receiver_key);
                }
            }, {signal: this.remocon_abort_controller?.signal});
        });
    }

    private async enterApplication(): Promise<void> {
        if (this.ready_application_key === null) {
            if (this.active_application_key !== null) {
                this.show_requested = false;
                this.setApplicationInputActive(true);
                this.host?.dispatchKey(RECEIVER_KEY_MAP[20]);
            } else {
                // demo と同様に、MH-AIT または入口 HTML がまだ揃っていない時の D キーを
                // 捨てず、後続の application/resource 通知で再試行する。
                this.show_requested = true;
            }
            return;
        }
        const application = this.applications.get(this.ready_application_key);
        if (application === undefined) {
            this.show_requested = true;
            return;
        }
        // 通信専用の入口は D キーを押した時点で設定を案内し、読み込み待ちと区別する。
        if (application.broadband && !useSettingsStore().settings.enable_internet_access_from_data_broadcasting) {
            this.show_requested = false;
            this.dispatch_data_key_after_install = false;
            this.player.notice('このデータ放送の表示にはインターネット接続が必要です。設定 → データ放送の「データ放送からのインターネットアクセスを有効にする」をオンにして、データボタンを押してください。[TLV_DATA_NETWORK_DISABLED]', 8000, undefined, '#FF6F6A');
            return;
        }
        if (application.key === this.active_application_key) {
            this.show_requested = false;
            this.setApplicationInputActive(true);
            this.host?.dispatchKey(RECEIVER_KEY_MAP[20]);
            return;
        }
        if (this.vfs === null || this.iframe === null || this.pending_application_key !== null ||
            (!application.broadband && this.resource_revisions.has(`${application.state.contextId}:${application.entry}`) === false)) {
            this.show_requested = true;
            return;
        }
        // AUTOSTART は透明な常駐入口であり、D キーを受けてデータメニューへ遷移する。
        // Manager 初期化直後に D が押され、AUTOSTART の load と入力が同時になった場合も、
        // runtime 導入後に一度だけ D キーを配送して startup 画面で止めない。
        this.dispatch_data_key_after_install = application.state.controlCode === 0x01;
        this.show_requested = false;
        await this.loadManagedApplication(application, 'アプリケーション読込中');
    }

    private async loadManagedApplication(application: ManagedApplication, status: string): Promise<void> {
        if (this.vfs === null || this.iframe === null || this.pending_application_key !== null) return;
        const generation = this.session_generation;
        this.pending_application_key = application.key;
        const load_abort = new AbortController();
        this.application_load_abort = load_abort;
        try {
            const base_url = new URL('/data-broadcast/', location.origin);
            let application_url: URL;
            if (application.broadband) {
                // 通常の外部資源経路で取得可否を確認し、CORS / mixed content を避ける VFS URL へ渡す。
                const mapped = mapARIBProxyURL(application.entry, location.origin);
                if (mapped === null) throw new Error('TLV_DATA_UNSUPPORTED_URL');
                const response = await APIClient.get<string>('/data-broadcasting/arib-html5/request', {
                    params: {url: application.entry}, responseType: 'text', signal: load_abort.signal,
                });
                if (load_abort.signal.aborted || generation !== this.session_generation || this.pending_application_key !== application.key) return;
                if (response.type === 'error') {
                    throw new Error(`TLV_DATA_NETWORK_FETCH_FAILED: HTTP ${response.status}`);
                }
                await this.vfs_backend?.initialize();
                application_url = mapped;
            } else {
                const revision = this.resource_revisions.get(`${application.state.contextId}:${application.entry}`);
                if (revision === undefined) {
                    this.pending_application_key = null;
                    return;
                }
                await this.vfs.waitFor(revision);
                await this.vfs.ensure(application.entry, revision);
                application_url = new URL(application.entry.replace(/^\/+/, ''), base_url);
            }
            if (load_abort.signal.aborted || generation !== this.session_generation || this.pending_application_key !== application.key) return;
            if (application_url.origin !== location.origin || !application_url.pathname.startsWith(base_url.pathname)) {
                throw new Error(`Application entry escaped receiver scope: ${application.entry}`);
            }
            this.iframe.style.display = 'block';
            this.iframe.style.opacity = '0';
            if (this.viewport !== null) this.viewport.style.opacity = '0';
            this.media_plane_adapter?.setApplicationVisible(this.applicationIsUserVisible(application));
            this.host?.setApplicationInformation({
                type: `0x${application.state.applicationType.toString(16).padStart(4, '0')}`,
                organizationId: application.state.organizationId,
                applicationId: application.state.applicationId,
                controlCode: application.state.controlCode === 0x01
                    ? 'AUTOSTART'
                    : application.state.controlCode === 0x02 ? 'PRESENT' : '',
            });
            this.host?.loadApplication(application_url.href, status);
        } catch (error) {
            if (load_abort.signal.aborted || generation !== this.session_generation || this.pending_application_key !== application.key) return;
            this.pending_application_key = null;
            this.exitApplication();
            console.error('[TLVDataBroadcastingManager] Failed to enter application.', error);
            const reason = error instanceof Error ? error.message : 'TLV_DATA_LOAD_FAILED';
            this.player.notice(`データ放送を起動できません。通信またはリソースの取得に失敗しました。データボタンで再試行してください。[${reason}]`, 6000, undefined, '#FF6F6A');
        }
    }

    private applyApplicationVisibility(): void {
        const application = this.active_application_key === null
            ? null
            : this.applications.get(this.active_application_key) ?? null;
        const visible = application !== null && this.applicationIsUserVisible(application);
        this.visible = visible;
        this.setApplicationInputActive(visible);
        this.media_plane_adapter?.setApplicationVisible(visible);
        if (visible) this.lockPanelForApplication();
        else this.unlockPanelForApplication();
        if (this.viewport !== null) this.viewport.style.opacity = visible ? '1' : '0';
        if (this.iframe !== null) {
            // Hidden AUTOSTART applications remain loaded and scheduled; only
            // their user-facing compositor plane is suppressed.
            this.iframe.style.display = application === null ? 'none' : 'block';
            this.iframe.style.opacity = visible ? '1' : '0';
            this.iframe.setAttribute('aria-hidden', visible ? 'false' : 'true');
        }
    }

    private exitApplication(): void {
        this.application_load_abort?.abort();
        this.application_load_abort = null;
        // runtime 導入前の 404 でも iframe.src を about:blank に戻せるよう、visible にかかわらず終了する。
        this.host?.exitApplication();
        this.active_application_key = null;
        this.pending_application_key = null;
        this.dispatch_data_key_after_install = false;
        this.visible = false;
        this.setApplicationInputActive(false);
        this.media_plane_adapter?.setApplicationVisible(false);
        this.unlockPanelForApplication();
        if (this.viewport !== null) this.viewport.style.opacity = '0';
        if (this.iframe !== null) {
            this.iframe.style.opacity = '0';
            this.iframe.style.display = 'none';
        }
    }

    private lockPanelForApplication(): void {
        const player_store = usePlayerStore();
        if (this.previous_remocon_display === null) this.previous_remocon_display = player_store.is_remocon_display;
        // 初めて可視アプリケーションへ入った時だけパネルを開き、ページ遷移のたびにユーザーの手動操作を戻さない。
        if (player_store.is_data_broadcasting_display === false) {
            player_store.is_data_broadcasting_panel_display = true;
        }
        player_store.is_data_broadcasting_display = true;
        player_store.is_remocon_display = true;
    }

    private unlockPanelForApplication(): void {
        const player_store = usePlayerStore();
        player_store.is_data_broadcasting_display = false;
        player_store.is_data_broadcasting_panel_display = true;
        if (this.previous_remocon_display !== null) player_store.is_remocon_display = this.previous_remocon_display;
        this.previous_remocon_display = null;
    }

    private setApplicationInputActive(active: boolean): void {
        (this.host as ViewerParticipationHost | null)?.setApplicationInputActive?.(active);
    }

    private resolveNumberKey(remocon_id: number): number | undefined {
        if (remocon_id >= 1 && remocon_id <= 9) return 48 + remocon_id;
        if (remocon_id === 10) return 48;
        return undefined;
    }

    /**
     * libaribhtml5 の稀疎 ARIB 記号フォントを、システム日本語フォントより前の既定 fallback にする。
     * 放送ページが独自の font-family を指定した場合は、後から読まれるそちらの CSS が優先される。
     */
    private installReceiverFontFallback(target: RuntimeWindow): void {
        if (target.document.querySelector('style[data-konomi-arib-font-fallback]') !== null) return;
        const style = target.document.createElement('style');
        style.dataset.konomiAribFontFallback = '';
        style.textContent = `
            html, body {
                font-family: "ARIB Symbols", "Hiragino Kaku Gothic ProN", "Yu Gothic", YuGothic,
                    Meiryo, "Noto Sans CJK JP", "Noto Sans JP", sans-serif;
            }
        `;
        (target.document.head ?? target.document.documentElement).append(style);
    }

    private normalizeResourceContentType(path: string, content_type: string): string {
        const pathname = path.split(/[?#]/, 1)[0].toLowerCase();
        if (pathname.endsWith('.html') || pathname.endsWith('.htm')) return 'text/html; charset=utf-8';
        if (pathname.endsWith('.css')) return 'text/css; charset=utf-8';
        return content_type;
    }

    private injectRuntimeBootstrap(data: Uint8Array): Uint8Array {
        const source = new TextDecoder().decode(data);
        const bootstrap = '<script>top.__ARIB_HTML5_INSTALL__?.(window)</script>';
        const head = /<head(?:\s[^>]*)?>/i.exec(source);
        const prepared = head === null ? `${bootstrap}${source}` :
            `${source.slice(0, head.index + head[0].length)}${bootstrap}${source.slice(head.index + head[0].length)}`;
        return new TextEncoder().encode(prepared);
    }

    private readReceiverInfo(): ReceiverInfo {
        const decode = (name: string): string | null => {
            const value = localStorage.getItem(`${LEGACY_RECEIVER_INFO_PREFIX}${name}`);
            if (value === null) return null;
            try {
                return window.atob(value);
            } catch {
                return null;
            }
        };
        const zipcode = decode('zipcode');
        const prefecture_raw = decode('prefecture');
        const regioncode_raw = decode('regioncode');
        return {
            zipcode: zipcode?.match(/^\d{7}$/) ? zipcode : null,
            prefecture: prefecture_raw?.length === 1 ? prefecture_raw.charCodeAt(0) : null,
            regioncode: regioncode_raw?.length === 2 ?
                (regioncode_raw.charCodeAt(0) << 8) | regioncode_raw.charCodeAt(1) : null,
        };
    }

    private toggleRemoconLoading(loading: boolean): void {
        this.remocon_data_broadcasting_element.classList.toggle('remote-control-data-broadcasting--loading', loading);
    }

    private toggleRemoconEnabled(enabled: boolean): void {
        this.remocon_data_broadcasting_element.classList.toggle('remote-control-data-broadcasting--disabled', !enabled);
    }
}

export default TLVDataBroadcastingManager;
