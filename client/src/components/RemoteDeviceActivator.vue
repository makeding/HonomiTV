<template>
    <v-btn ref="activatorButton" icon variant="text"
        :color="selectedDeviceId === null ? undefined : 'primary'" aria-label="テレビを選択"
        @click="openMenu">
        <Icon icon="material-symbols:cast-rounded" height="26px" />
        <v-tooltip activator="parent" location="bottom">{{ selectedRemoteDeviceName ?? 'テレビを選択' }}</v-tooltip>
    </v-btn>
</template>

<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue';

import {
    registerRemoteDeviceActivator,
    requestRemoteDeviceMenuOpen,
    selectedRemoteDeviceName,
    unregisterRemoteDeviceActivator,
} from '@/services/RemoteControlUI';
import useSettingsStore from '@/stores/SettingsStore';

const settingsStore = useSettingsStore();
const activatorButton = ref<{ $el: HTMLElement } | null>(null);
const selectedDeviceId = computed(() => settingsStore.settings.selected_remote_device_id);

function registerIfVisible(): void {
    const element = activatorButton.value?.$el;
    if (element) registerRemoteDeviceActivator(element);
}

function openMenu(): void {
    registerIfVisible();
    requestRemoteDeviceMenuOpen();
}

onMounted(async () => {
    window.addEventListener('resize', registerIfVisible);
    await nextTick();
    registerIfVisible();
});

onBeforeUnmount(() => {
    window.removeEventListener('resize', registerIfVisible);
    const element = activatorButton.value?.$el;
    if (element) unregisterRemoteDeviceActivator(element);
});
</script>
