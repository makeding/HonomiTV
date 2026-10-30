import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import ReservationRecordingSettings from '@/components/Reservations/ReservationRecordingSettings.vue';
import APIClient from '@/services/APIClient';
import { ILiveChannelDefault } from '@/services/Channels';
import { IProgramDefault } from '@/services/Programs';
import Reservations, { IRecordSettingsDefault, type IReservation } from '@/services/Reservations';
import useServerSettingsStore from '@/stores/ServerSettingsStore';

vi.mock('@/services/APIClient', () => ({default: {get: vi.fn(), showGenericError: vi.fn()}}));
vi.mock('@/services/Version', () => ({default: {fetchServerVersion: vi.fn(async () => null)}}));

function reservation(comment = ''): IReservation {
    return {
        id: 31, channel: ILiveChannelDefault, program: IProgramDefault, is_recording_in_progress: false,
        recording_availability: 'Full', comment, scheduled_recording_file_name: '', estimated_recording_file_size: 0,
        record_settings: {...IRecordSettingsDefault, data_broadcasting_recording_mode: 'Enable'},
    };
}

beforeEach(() => { setActivePinia(createPinia()); vi.clearAllMocks(); });

describe('既存の録画設定のバックエンド分岐', () => {
    async function render(backend: 'EDCB' | 'EPGStation', comment = '') {
        const store = useServerSettingsStore();
        store.server_settings.general.backend = backend;
        store.is_loaded = true;
        const wrapper = mount(ReservationRecordingSettings, {
            props: {reservation: reservation(comment), presets: null, hasChanges: false},
            global: {stubs: {Icon: true, VSelect: true, VSwitch: true, VSlider: true, VTextField: true, VCheckbox: true}},
        });
        await flushPromises();
        return wrapper;
    }

    it('EPGStation の手動予約には対応する既存設定だけを表示する', async () => {
        const wrapper = await render('EPGStation');
        expect(wrapper.text()).toContain('データ放送録画設定');
        expect(wrapper.text()).not.toContain('録画予約の有効/無効');
        expect(wrapper.text()).not.toContain('録画予約の優先度');
        expect(wrapper.text()).not.toContain('録画マージン');
        expect(wrapper.text()).not.toContain('録画後動作設定');
        wrapper.unmount();
    });

    it('EPGStation の自動予約では本回の有効・無効を共通のスイッチで変更する', async () => {
        const wrapper = await render('EPGStation', 'EPG自動予約');
        expect(wrapper.text()).toContain('録画予約の有効/無効');
        expect(wrapper.text()).not.toContain('データ放送録画設定');
        expect(wrapper.text()).not.toContain('録画予約の優先度');
        wrapper.unmount();
    });

    it('EDCB の既存設定とプリセットの取得境界を維持する', async () => {
        const wrapper = await render('EDCB');
        expect(wrapper.text()).toContain('録画予約の優先度');
        expect(wrapper.text()).toContain('録画マージン');
        expect(wrapper.text()).toContain('録画後動作設定');
        wrapper.unmount();
        const store = useServerSettingsStore();
        store.server_settings.general.backend = 'EPGStation';
        await Reservations.fetchDefaultRecordSettings();
        expect(APIClient.get).not.toHaveBeenCalled();
    });
});
