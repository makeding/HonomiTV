import { flushPromises, mount } from '@vue/test-utils';
import { createPinia, setActivePinia } from 'pinia';
import { beforeEach, describe, expect, it } from 'vitest';
import { defineComponent } from 'vue';

import Reservation from '@/components/Reservations/Reservation.vue';
import ReservationList from '@/components/Reservations/ReservationList.vue';
import { ILiveChannelDefault } from '@/services/Channels';
import { IProgramDefault, type IGGuideCollectionAsset } from '@/services/Programs';
import { IRecordSettingsDefault, type IReservation } from '@/services/Reservations';
import useServerSettingsStore from '@/stores/ServerSettingsStore';

const VImgStub = defineComponent({
    props: { src: { type: String, required: true } },
    template: '<div class="v-img-stub" :data-src="src"><slot /><slot name="placeholder" /><slot name="error" /></div>',
});

const ReservationStub = defineComponent({
    props: { reservation: { type: Object, required: true } },
    emits: ['click'],
    template: '<button class="reservation-stub" @click="$emit(\'click\', reservation)">予約</button>',
});

const ReservationDetailDrawerStub = defineComponent({
    props: {
        modelValue: { type: Boolean, required: true },
        reservation: { type: Object, default: null },
        gguideImage: { type: Object, default: null },
        isPastProgram: { type: Boolean, required: true },
    },
    template: '<div class="reservation-detail-drawer-stub" />',
});

const gguideImage = (id: string): IGGuideCollectionAsset => ({
    id,
    width: 144,
    height: 96,
    programs: [],
    advertisements: [],
});

const reservation = (overrides: Partial<IReservation> = {}): IReservation => ({
    id: 31,
    channel: {...ILiveChannelDefault, channel_number: '101', name: 'テストテレビ'},
    program: {
        ...IProgramDefault,
        id: 'NID1-SID1-EID1',
        title: '番組タイトル',
        description: '番組概要',
        start_time: '2099-01-01T18:00:00+09:00',
        end_time: '2099-01-01T18:30:00+09:00',
    },
    is_recording_in_progress: false,
    recording_availability: 'Full',
    comment: '',
    scheduled_recording_file_name: '',
    estimated_recording_file_size: 0,
    record_settings: {...IRecordSettingsDefault},
    ...overrides,
});

const reservationStubs = {
    Icon: true,
    'v-img': VImgStub,
    'v-btn': { template: '<button><slot /></button>' },
    'v-chip': { template: '<span><slot /></span>' },
    'v-progress-circular': true,
    'v-switch': { template: '<div class="v-switch-stub" />' },
};

beforeEach(() => {
    setActivePinia(createPinia());
    const serverSettingsStore = useServerSettingsStore();
    serverSettingsStore.server_settings.general.backend = 'EDCB';
    serverSettingsStore.is_loaded = true;
});

describe('録画予約の G ガイド画像', () => {

    it('EPGStation では手動予約だけスイッチを隠し、すべての予約に操作欄を確保する', () => {
        useServerSettingsStore().server_settings.general.backend = 'EPGStation';
        const manual = mount(Reservation, {
            props: {reservation: reservation({gguide_image: gguideImage('image-a')})},
            global: {directives: {ripple: {}}, stubs: reservationStubs},
        });
        const automatic = mount(Reservation, {
            props: {reservation: reservation({id: 32, comment: 'EPG自動予約'})},
            global: {directives: {ripple: {}}, stubs: reservationStubs},
        });
        const recording = mount(Reservation, {
            props: {reservation: reservation({id: 33, is_recording_in_progress: true})},
            global: {directives: {ripple: {}}, stubs: reservationStubs},
        });

        expect(manual.find('.reservation__controls').exists()).toBe(true);
        expect(automatic.find('.reservation__controls').exists()).toBe(true);
        expect(recording.find('.reservation__controls').exists()).toBe(true);
        expect(manual.find('.v-switch-stub').exists()).toBe(false);
        expect(automatic.find('.v-switch-stub').exists()).toBe(true);
        expect(manual.find('.reservation__image').exists()).toBe(true);
        expect(automatic.find('.reservation__image').exists()).toBe(false);
        manual.unmount();
        automatic.unmount();
        recording.unmount();
    });

    it('画像取得失敗の再読み込みは予約カードの選択を発火せず、同じ画像を再作成する', async () => {
        const wrapper = mount(Reservation, {
            props: {reservation: reservation({gguide_image: gguideImage('image-a')})},
            global: {directives: {ripple: {}}, stubs: reservationStubs},
        });
        const image = wrapper.getComponent(VImgStub);
        expect(image.vm.$.vnode.key).toBe('image-a-0');

        await wrapper.get('.reservation__image-state button').trigger('click');

        expect(wrapper.emitted('click')).toBeUndefined();
        expect(wrapper.getComponent(VImgStub).vm.$.vnode.key).toBe('image-a-1');
        wrapper.unmount();
    });

    it('画像 ID の更新先を切り替え、選択した予約の画像メタデータを詳細へ渡す', async () => {
        const first = reservation({gguide_image: gguideImage('image-a')});
        const second = reservation({id: 32, gguide_image: gguideImage('image-b')});
        const wrapper = mount(Reservation, {
            props: {reservation: first},
            global: {directives: {ripple: {}}, stubs: reservationStubs},
        });
        expect(wrapper.get('.v-img-stub').attributes('data-src')).toContain('/programs/gguide/images/image-a');
        await wrapper.setProps({reservation: {...first, gguide_image: gguideImage('image-b')}});
        expect(wrapper.get('.v-img-stub').attributes('data-src')).toContain('/programs/gguide/images/image-b');
        wrapper.unmount();

        const list = mount(ReservationList, {
            props: {title: '録画予約', reservations: [first, second], total: 2, hideHeader: true, hideSort: true},
            global: {
                directives: {ripple: {}},
                stubs: {
                    Icon: true,
                    Reservation: ReservationStub,
                    ReservationDetailDrawer: ReservationDetailDrawerStub,
                    VBtn: true,
                    VPagination: true,
                    VSelect: true,
                },
            },
        });
        await list.findAll('.reservation-stub')[1].trigger('click');
        await flushPromises();

        const drawer = list.getComponent(ReservationDetailDrawerStub);
        expect(drawer.props('reservation')).toEqual(second);
        expect(drawer.props('gguideImage')).toEqual(second.gguide_image);
        list.unmount();
    });
});
