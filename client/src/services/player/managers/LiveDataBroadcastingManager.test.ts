import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { HybridcastLaunchRequest } from '@/services/player/hybridcast/XMLAIT';
import type DPlayer from 'dplayer';
import type { BMLBrowser } from 'web-bml';

const state = vi.hoisted(() => ({
    player: {
        tv_panel_active_tab: 'Twitter', is_remocon_display: false, is_data_broadcasting_display: false,
        is_data_broadcasting_panel_display: false, is_loading: true, is_video_buffering: true,
        is_background_display: true, live_stream_status: 'Standby', data_broadcasting_error: null as string | null,
    },
    channels: {channel: {current: {
        is_datachannel: true, display_channel_id: 'bs791', network_id: 4, service_id: 791, transport_stream_id: null,
    }}, current_program_present: null, current_program_following: null},
    browsers: [] as Array<EventTarget & {emitMessage: ReturnType<typeof vi.fn>; content: {processKeyDown: ReturnType<typeof vi.fn>}}>,
    errorCallback: null as null | ((reason: string) => void),
    decoderDestroy: vi.fn(),
    bmlOptions: null as ConstructorParameters<typeof BMLBrowser>[0] | null,
}));
vi.mock('comlink', () => ({proxy: (fn: unknown) => fn}));
vi.mock('mpeg2toh264/yadif', () => ({Deinterlacer: class {}}));
vi.mock('web-bml', () => ({
    AribKeyCode: {},
    BMLBrowser: class extends EventTarget {
        fonts = [];
        content = {processKeyDown: vi.fn(), processKeyUp: vi.fn()};
        emitMessage = vi.fn();
        getVideoElement = () => null;
        destroy = vi.fn();
        browserAPI = {browser: {getBrowserSupport: vi.fn((_provider: string, name: string) =>
            name === 'AITControlledApp.Start' ? 1 : 0)}};
        constructor(options: ConstructorParameters<typeof BMLBrowser>[0]) {
            super(); state.bmlOptions = options; state.browsers.push(this);
        }
    },
}));
vi.mock('@/router', () => ({default: {}}));
vi.mock('@/services/APIClient', () => ({default: {}}));
vi.mock('@/stores/ChannelsStore', () => ({default: () => state.channels}));
vi.mock('@/stores/PlayerStore', () => ({default: () => state.player}));
vi.mock('@/stores/SettingsStore', () => ({default: () => ({settings: {tv_show_data_broadcasting: false}})}));
vi.mock('@/utils', () => ({default: {api_base_url: '/api'}, PlayerUtils: {extractLiveAPIQualityFromDPlayer: () => '1080p'}}));
vi.mock('@/workers/LivePSIArchivedDataDecoderProxy', () => ({default: class {
    run(_callback: unknown, errorCallback: (reason: string) => void) {state.errorCallback = errorCallback;}
    destroy = state.decoderDestroy;
}}));

import LiveDataBroadcastingManager from '@/services/player/managers/LiveDataBroadcastingManager';

describe('standalone data broadcasting lifecycle', () => {
    let manager: LiveDataBroadcastingManager;

    beforeEach(() => {
        vi.useFakeTimers();
        state.browsers.length = 0;
        state.errorCallback = null;
        state.decoderDestroy.mockClear();
        Object.assign(state.player, {
            tv_panel_active_tab: 'Twitter', is_remocon_display: false, is_data_broadcasting_display: false,
            is_data_broadcasting_panel_display: false, is_loading: true, is_video_buffering: true,
            is_background_display: true, live_stream_status: 'Standby', data_broadcasting_error: null,
        });
        vi.stubGlobal('ResizeObserver', class {observe() {} disconnect() {}});
        document.body.innerHTML = '<div class="remote-control"><div class="remote-control-data-broadcasting"><button data-arib-key-code="20"></button></div></div><div id="video-wrap"><div id="media"></div></div>';
        manager = new LiveDataBroadcastingManager({template: {
            videoWrap: document.querySelector('#video-wrap'), videoWrapAspect: document.querySelector('#media'),
        }} as unknown as DPlayer);
    });

    afterEach(async () => {
        await manager.destroy();
        vi.useRealTimers();
        vi.unstubAllGlobals();
    });

    it('opens the remote despite optional data broadcasting being disabled and restores it on exit', async () => {
        await manager.init();
        expect(state.browsers).toHaveLength(1);
        expect(state.player.tv_panel_active_tab).toBe('Twitter');
        expect(state.player.is_remocon_display).toBe(true);
        expect(state.player.is_data_broadcasting_panel_display).toBe(true);
        expect(state.browsers[0].emitMessage).toHaveBeenCalledWith(expect.objectContaining({
            type: 'programInfo', originalNetworkId: 4, serviceId: 791, eventId: null,
        }));
        document.querySelector<HTMLButtonElement>('button')!.click();
        expect(state.browsers[0].content.processKeyDown).toHaveBeenCalledWith(20);
        await manager.destroy();
        expect(state.player.tv_panel_active_tab).toBe('Twitter');
        expect(state.player.is_remocon_display).toBe(false);
        expect(state.player.is_data_broadcasting_display).toBe(false);
        expect(state.decoderDestroy).toHaveBeenCalledOnce();
    });

    it('finishes loading on BML load without a video playing event', async () => {
        await manager.init();
        state.browsers[0].dispatchEvent(new CustomEvent('load', {detail: {resolution: {width: 960, height: 540}}}));
        expect(state.player.is_loading).toBe(false);
        expect(state.player.is_video_buffering).toBe(false);
        expect(state.player.is_background_display).toBe(false);
        expect(state.player.live_stream_status).toBe('ONAir');
        await vi.advanceTimersByTimeAsync(30000);
        expect(state.player.data_broadcasting_error).toBeNull();
    });

    it('connects standard AIT launch requests and accepts the APIGroup capability query', async () => {
        await manager.init();
        const launch = vi.spyOn(manager as unknown as {
            startHybridcast(request: HybridcastLaunchRequest): Promise<void>;
        }, 'startHybridcast').mockResolvedValue();
        // 放送局が渡した AIT と Greg を保持し、十六進 ID だけを宿主の数値へ変換する。
        const request = {organizationId: '000a', applicationId: '0010',
            aitUrl: 'https://example.org/received.xml', greg: ['received-state']};
        expect(state.bmlOptions!.startAITControlledApp!(request)).toBe(0);
        expect(launch).toHaveBeenCalledWith({organizationId: 10, applicationId: 16,
            aitUrl: request.aitUrl, greg: request.greg});
        expect(state.bmlOptions!.startAITControlledApp!({...request, applicationId: 'invalid'})).toBeNaN();
        expect(launch).toHaveBeenCalledTimes(1);
        const browser = state.browsers[0] as unknown as BMLBrowser;
        expect(browser.browserAPI.browser.getBrowserSupport('ARIB', 'APIGroup', 'AITControlledApp.Start')).toBe(1);
        expect(browser.browserAPI.browser.getBrowserSupport('ARIB', 'APIGroup', 'unimplemented')).toBe(0);
    });

    it('times out once, releases reception, and ignores late load events', async () => {
        await manager.init();
        await vi.advanceTimersByTimeAsync(30000);
        expect(state.player.data_broadcasting_error).toContain('DATA_BML_TIMEOUT');
        expect(state.player.is_loading).toBe(false);
        expect(state.decoderDestroy).toHaveBeenCalledOnce();
        state.browsers[0].dispatchEvent(new CustomEvent('load', {detail: {resolution: {width: 960, height: 540}}}));
        expect(state.player.live_stream_status).toBe('Offline');
    });

    it('keeps the receive failure code, then allows a fresh retry and ignores the previous worker', async () => {
        await manager.init();
        const oldCallback = state.errorCallback!;
        oldCallback('受信に失敗しました。[DATA_TUNER_CONNECT]');
        expect(state.player.data_broadcasting_error).toContain('DATA_TUNER_CONNECT');
        await manager.destroy();
        await manager.init();
        expect(state.player.data_broadcasting_error).toBeNull();
        oldCallback('old worker');
        expect(state.player.data_broadcasting_error).toBeNull();
        expect(state.player.is_remocon_display).toBe(true);
    });
});
