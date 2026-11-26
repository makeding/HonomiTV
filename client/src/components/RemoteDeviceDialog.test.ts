import { flushPromises, mount } from '@vue/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { reactive } from 'vue';

import RemoteDeviceDialog from '@/components/RemoteDeviceDialog.vue';
import RemoteControl, { type IRemoteDevice, type IRemoteInputSource, type IRemotePlaybackState } from '@/services/RemoteControl';
import { remoteDeviceActivatorElement, requestRemoteDeviceMenuOpen } from '@/services/RemoteControlUI';

const mocks = vi.hoisted(() => ({
    settingsStore: null as unknown as {
        settings: {
            selected_remote_device_id: string | null;
            remote_control_menu_pinned: boolean;
        };
    },
    handoff: vi.fn(),
}));

vi.mock('@/stores/SettingsStore', () => ({
    default: () => mocks.settingsStore,
}));

vi.mock('@/services/RemoteHandoff', () => ({
    handoffCurrentPlaybackToDevice: mocks.handoff,
}));

vi.mock('vue-router', () => ({
    useRoute: () => ({path: '/tv/', fullPath: '/tv/'}),
    useRouter: () => ({push: vi.fn()}),
}));

vi.mock('@/services/RemoteControl', () => ({
    default: {
        fetchDevices: vi.fn(),
        subscribeDevices: vi.fn(),
        selectSource: vi.fn(),
        sendCommand: vi.fn(),
        sendTemporaryNHKHideCommand: vi.fn(),
        sendOpenCommand: vi.fn(),
    },
}));

const stubs = {
    Icon: {props: ['icon'], template: '<span class="icon-stub" :data-icon="icon" />'},
    'v-menu': {
        props: ['modelValue', 'target', 'location', 'closeOnContentClick', 'persistent', 'noClickAnimation', 'offset', 'transition'],
        emits: ['update:modelValue'],
        template: '<div v-if="modelValue" class="v-menu-stub"><slot /></div>',
    },
    'v-list': {template: '<div class="v-list-stub"><slot /></div>'},
    'v-list-item': {
        props: ['title', 'subtitle', 'active', 'disabled', 'lines', 'rounded', 'color'],
        template: '<div class="v-list-item-stub" :data-title="title" @click="!disabled && $emit(\'click\')"><slot name="prepend" />{{ title }}{{ subtitle }}<slot /><slot name="append" /></div>',
    },
    'v-list-item-title': {template: '<div><slot /></div>'},
    'v-list-item-subtitle': {template: '<div><slot /></div>'},
    'v-btn': {
        props: ['disabled', 'loading', 'color', 'icon', 'size', 'variant', 'density', 'value'],
        template: '<button type="button" :disabled="disabled" @click="$emit(\'click\')"><slot /></button>',
    },
    'v-btn-toggle': {
        props: ['modelValue'],
        template: '<div><slot /></div>',
    },
    'v-progress-linear': {template: '<div class="v-progress-linear-stub" />'},
    'v-divider': {template: '<hr />'},
    'v-slider': {props: ['modelValue', 'min', 'max'], template: '<input type="range" :value="modelValue" />'},
    'v-expand-transition': {template: '<div><slot /></div>'},
};

const defaultSources: IRemoteInputSource[] = [
    {
        id: 'hdmi1',
        label: 'HDMI 1',
        type: 'HDMI',
        connection_state: 'Standby',
        selectable: true,
        selected: false,
        channels: [],
    },
    {
        id: 'aux',
        label: 'Aux',
        type: 'AUX',
        connection_state: 'Connected',
        selectable: true,
        selected: true,
        channels: [{id: 'aux-ch1', label: 'CH 1'}],
    },
    {
        id: 'av',
        label: 'AV',
        type: 'Composite',
        connection_state: 'Disconnected',
        selectable: false,
        selected: false,
        channels: [],
    },
];

function createDevice(stateOverrides: Partial<IRemotePlaybackState> = {}): IRemoteDevice {
    const state: IRemotePlaybackState = {
        capabilities: {commands: ['SelectSource']},
        input_sources: defaultSources.map((source) => ({
            ...source,
            channels: [...source.channels],
        })),
        content_type: 'Idle',
        supports_nhk_exclusion: true,
        nhk_exclusion_mode: 'OFF',
        nhk_exclusion_expires_at: null,
        nhk_exclusion_command_result: null,
        ...stateOverrides,
    };
    return {
        device_id: 'tv-1',
        device_name: 'TV',
        last_seen_at: '2026-11-26T23:00:00+09:00',
        state: state as unknown as Record<string, unknown>,
    };
}

async function openDialogWithDevices(initialDevices: IRemoteDevice[]) {
    const deviceList = initialDevices;
    vi.mocked(RemoteControl.fetchDevices).mockImplementation(async () => deviceList);
    vi.mocked(RemoteControl.subscribeDevices).mockImplementation((onDevices) => {
        onDevices(deviceList);
        return () => {};
    });

    const activator = document.createElement('button');
    document.body.appendChild(activator);
    remoteDeviceActivatorElement.value = activator;

    const wrapper = mount(RemoteDeviceDialog, {global: {stubs}});
    requestRemoteDeviceMenuOpen();
    await flushPromises();

    return {wrapper, activator};
}

beforeEach(() => {
    vi.clearAllMocks();
    mocks.settingsStore = reactive({
        settings: {
            selected_remote_device_id: 'tv-1',
            remote_control_menu_pinned: false,
        },
    });
    mocks.handoff.mockResolvedValue('Success');
    vi.mocked(RemoteControl.selectSource).mockResolvedValue('cmd-1');
    vi.mocked(RemoteControl.sendCommand).mockResolvedValue(true);
    vi.mocked(RemoteControl.sendTemporaryNHKHideCommand).mockResolvedValue({type: 'Accepted', command_id: 'nhk-1'});
});

afterEach(() => {
    remoteDeviceActivatorElement.value = null;
    vi.useRealTimers();
});

describe('RemoteDeviceDialog', () => {
    it('SelectSource 非対応または入力ソースが空の場合は入力切替を表示しない', async () => {
        const {wrapper: noCapWrapper, activator: act1} = await openDialogWithDevices([
            createDevice({capabilities: {commands: []}, input_sources: defaultSources}),
        ]);
        expect(noCapWrapper.find('.remote-device-menu__sources-toggle').exists()).toBe(false);
        noCapWrapper.unmount();
        act1.remove();

        const {wrapper: emptySourcesWrapper, activator: act2} = await openDialogWithDevices([
            createDevice({capabilities: {commands: ['SelectSource']}, input_sources: []}),
        ]);
        expect(emptySourcesWrapper.find('.remote-device-menu__sources-toggle').exists()).toBe(false);
        emptySourcesWrapper.unmount();
        act2.remove();
    });

    it('入力切替は初期状態で折りたたまれ、選択中の入力ラベルを表示する', async () => {
        const {wrapper, activator} = await openDialogWithDevices([createDevice()]);
        try {
            const toggle = wrapper.get('.remote-device-menu__sources-toggle');
            expect(toggle.attributes('aria-expanded')).toBe('false');
            expect(toggle.text()).toContain('入力切替');
            expect(toggle.text()).toContain('Aux');
            expect(wrapper.get('.remote-device-menu__sources-panel').attributes('style')).toContain('display: none');
        } finally {
            wrapper.unmount();
            activator.remove();
        }
    });

    it('展開して入力タイルやチャンネルを切り替えられ、選択不可の入力や信号情報を正しく扱う', async () => {
        const {wrapper, activator} = await openDialogWithDevices([
            createDevice({
                input_signal: {source_id: 'aux', status: 'NoSignal', reason: 'ケーブル未接続'},
            }),
        ]);
        try {
            const toggle = wrapper.get('.remote-device-menu__sources-toggle');
            await toggle.trigger('click');
            expect(toggle.attributes('aria-expanded')).toBe('true');
            expect(wrapper.get('.remote-device-menu__sources-panel').attributes('style') ?? '').not.toContain('display: none');
            expect(wrapper.get('.remote-device-menu__source-signal').text()).toContain('Aux: NoSignal (ケーブル未接続)');

            const tiles = wrapper.findAll('.remote-device-menu__source-tile');
            expect(tiles).toHaveLength(3);
            expect(tiles[2].attributes('disabled')).toBeDefined();

            await tiles[2].trigger('click');
            expect(RemoteControl.selectSource).not.toHaveBeenCalled();

            await tiles[0].trigger('click');
            expect(RemoteControl.selectSource).toHaveBeenCalledWith('tv-1', 'hdmi1');

            // 送信結果を反映して pending を解除してから、サブチャンネルの選択を検証する
            const deviceState = (wrapper.vm as unknown as {devices: IRemoteDevice[]}).devices[0].state as Record<string, unknown>;
            deviceState.source_command_result = {command_id: 'cmd-1', status: 'Applied'};
            await flushPromises();

            vi.mocked(RemoteControl.selectSource).mockResolvedValueOnce('cmd-2');
            await wrapper.get('.remote-device-menu__source-channel-chip').trigger('click');
            expect(RemoteControl.selectSource).toHaveBeenCalledWith('tv-1', 'aux-ch1');
        } finally {
            wrapper.unmount();
            activator.remove();
        }
    });

    it('入力切替の成功・失敗・送信エラーを表示し、メニューを閉じると折りたたみとタイマーをリセットする', async () => {
        vi.useFakeTimers();
        let onDevicesCallback: ((devices: IRemoteDevice[]) => void) | null = null;
        const device = createDevice();
        vi.mocked(RemoteControl.fetchDevices).mockResolvedValue([device]);
        vi.mocked(RemoteControl.subscribeDevices).mockImplementation((onDevices) => {
            onDevicesCallback = onDevices;
            onDevices([device]);
            return () => {};
        });

        const activator = document.createElement('button');
        document.body.appendChild(activator);
        remoteDeviceActivatorElement.value = activator;

        const wrapper = mount(RemoteDeviceDialog, {global: {stubs}});
        try {
            requestRemoteDeviceMenuOpen();
            await flushPromises();

            await wrapper.get('.remote-device-menu__sources-toggle').trigger('click');

            // 送信失敗
            vi.mocked(RemoteControl.selectSource).mockResolvedValueOnce(null);
            await wrapper.findAll('.remote-device-menu__source-tile')[0].trigger('click');
            await flushPromises();
            expect(wrapper.get('.remote-device-menu__source-feedback').text()).toContain('送信できませんでした');

            // テレビ側のエラー応答
            vi.mocked(RemoteControl.selectSource).mockResolvedValueOnce('cmd-fail');
            await wrapper.findAll('.remote-device-menu__source-tile')[0].trigger('click');
            await flushPromises();
            expect(wrapper.get('.remote-device-menu__source-feedback').text()).toContain('入力を切り替えています');

            onDevicesCallback!([
                createDevice({source_command_result: {command_id: 'cmd-fail', status: 'Failed', error_code: 'BUSY'}}),
            ]);
            await flushPromises();
            expect(wrapper.get('.remote-device-menu__source-feedback').text()).toContain('BUSY');

            // 成功時はフィードバックを消去
            vi.mocked(RemoteControl.selectSource).mockResolvedValueOnce('cmd-ok');
            await wrapper.findAll('.remote-device-menu__source-tile')[0].trigger('click');
            await flushPromises();
            onDevicesCallback!([
                createDevice({source_command_result: {command_id: 'cmd-ok', status: 'Applied'}}),
            ]);
            await flushPromises();
            expect(wrapper.find('.remote-device-menu__source-feedback').exists()).toBe(false);

            // 応答待ち中にメニューを閉じてもタイムアウト文言が残らず、再び開くと折りたたまれて表示される
            vi.mocked(RemoteControl.selectSource).mockResolvedValueOnce('cmd-timeout');
            await wrapper.findAll('.remote-device-menu__source-tile')[0].trigger('click');
            await flushPromises();

            await wrapper.get('.remote-device-menu__disconnect').trigger('click');
            await flushPromises();
            await vi.advanceTimersByTimeAsync(11_000);

            mocks.settingsStore.settings.selected_remote_device_id = 'tv-1';
            requestRemoteDeviceMenuOpen();
            await flushPromises();
            expect(wrapper.get('.remote-device-menu__sources-toggle').attributes('aria-expanded')).toBe('false');
            expect(wrapper.find('.remote-device-menu__source-feedback').exists()).toBe(false);
        } finally {
            wrapper.unmount();
            activator.remove();
        }
    });

    it('アートワークの5回連続タップで一時的な N〇K 除外コマンドを送信する', async () => {
        vi.useFakeTimers();
        const {wrapper, activator} = await openDialogWithDevices([createDevice()]);
        try {
            const artworkButton = wrapper.get('.remote-device-menu__artwork');
            for (let i = 0; i < 5; i++) {
                await artworkButton.trigger('click');
                await vi.advanceTimersByTimeAsync(100);
            }
            await flushPromises();
            expect(RemoteControl.sendTemporaryNHKHideCommand).toHaveBeenCalledWith('tv-1');
            expect(wrapper.get('.remote-device-menu__nhk-exclusion-feedback').text()).toContain('N〇K除外モード');
        } finally {
            wrapper.unmount();
            activator.remove();
        }
    });
});
