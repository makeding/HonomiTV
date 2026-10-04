import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vitest/config';


export default defineConfig({
    plugins: [vue()],
    css: {
        preprocessorOptions: {
            scss: {additionalData: '@import "@/styles/mixin.scss";'},
        },
    },
    resolve: {
        alias: {'@': fileURLToPath(new URL('./src', import.meta.url))},
    },
    test: {
        environment: 'happy-dom',
        include: [
            'src/**/*.test.ts',
            'tests/**/*.test.ts',
        ],
        restoreMocks: true,
        setupFiles: ['./tests/setup.ts'],
    },
});
