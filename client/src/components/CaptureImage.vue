<template>
    <div class="capture-image" :aria-busy="isLoading">
        <img v-if="!hasError" :src="imageURL" :alt="alt" :loading="lazy ? 'lazy' : 'eager'"
            @load="isLoading = false" @error="hasError = true; isLoading = false">
        <div class="capture-image__status" v-if="hasError" @click.stop="" @keydown.stop="">
            <span>画像を読み込めませんでした。</span>
            <v-btn size="small" variant="text" @click="retry">再試行</v-btn>
        </div>
        <v-progress-circular v-else-if="isLoading" class="capture-image__loading" indeterminate size="24"
            aria-label="画像を読み込み中" />
    </div>
</template>
<script setup lang="ts">
import { computed, ref, watch } from 'vue';

const props = withDefaults(defineProps<{src: string; alt: string; lazy?: boolean}>(), {lazy: false});
const isLoading = ref(true);
const hasError = ref(false);
const retryCount = ref(0);
const imageURL = computed(() => `${props.src}${props.src.includes('?') ? '&' : '?'}retry=${retryCount.value}`);

// 失敗時も同じ画像枠を維持し、親カードの選択を変えずに再取得する。
const retry = () => {
    retryCount.value++;
    hasError.value = false;
    isLoading.value = true;
};
watch(() => props.src, () => {
    retryCount.value = 0;
    hasError.value = false;
    isLoading.value = true;
});
</script>
<style scoped lang="scss">
.capture-image {
    position: relative;
    aspect-ratio: 16 / 9;
    background: rgb(var(--v-theme-background-lighten-2));
    img {
        display: block;
        width: 100%;
        height: 100%;
        object-fit: contain;
    }
    &__status {
        position: absolute;
        inset: 0;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 4px;
        font-size: 12px;
        text-align: center;
        z-index: 3;
    }
    &__loading {
        position: absolute;
        top: calc(50% - 12px);
        left: calc(50% - 12px);
    }
}
</style>
