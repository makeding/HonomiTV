
import { fileURLToPath, URL } from 'node:url';

import vue from '@vitejs/plugin-vue';
import { defineConfig } from 'vite';
import { comlink } from 'vite-plugin-comlink';
import { VitePWA } from 'vite-plugin-pwa';
import vuetify, { transformAssetUrls } from 'vite-plugin-vuetify';

import { loadARIBRuntimeBootstrapSource, loadARIBVFSWorkerSource } from './vite.arib-vfs-worker.mts';

const readableStreamBrowserPath = fileURLToPath(new URL(
    './node_modules/@tsukumijima/aribts/node_modules/readable-stream/lib/ours/browser.js',
    import.meta.url,
));

const aribVfsWorkerPlugin = {
    name: 'konomitv-arib-vfs-worker',
    configureServer(server: {middlewares: {use: (path: string, handler: (request: unknown, response: any) => void) => void}}) {
        server.middlewares.use('/data-broadcast/arib-vfs-sw.js', (_request, response) => {
            response.statusCode = 200;
            response.setHeader('Content-Type', 'text/javascript; charset=utf-8');
            response.end(loadARIBVFSWorkerSource());
        });
        server.middlewares.use('/data-broadcast/arib-runtime-bootstrap.js', (_request, response) => {
            response.statusCode = 200;
            response.setHeader('Content-Type', 'text/javascript; charset=utf-8');
            response.end(loadARIBRuntimeBootstrapSource());
        });
    },
    generateBundle(this: {emitFile: (asset: {type: 'asset'; fileName: string; source: string | Uint8Array}) => void}) {
        this.emitFile({
            type: 'asset',
            fileName: 'data-broadcast/arib-vfs-sw.js',
            source: loadARIBVFSWorkerSource(),
        });
        this.emitFile({
            type: 'asset',
            fileName: 'data-broadcast/arib-runtime-bootstrap.js',
            source: loadARIBRuntimeBootstrapSource(),
        });
    },
};


// Vite の設定
// https://vitejs.dev/config/
export default defineConfig({
    // バージョン情報をビルド時に埋め込む
    // ref: https://stackoverflow.com/a/68093777/17124142
    define: {
        'process.env': {},  // これがないと assert がエラーになる
        'import.meta.env.KONOMITV_VERSION': JSON.stringify(process.env.npm_package_version),
    },
    // ビルドの設定
    build: {
        chunkSizeWarningLimit: 3 * 1024 * 1024,  // 3MB に緩和
        rollupOptions: {
            output: {
                assetFileNames: (assetInfo) => {
                    // フォントファイルのみ、ハッシュを付けずに assets/fonts/ に出力する
                    if (['.ttf', '.eot', '.woff', '.woff2'].some((ext) => assetInfo.name?.endsWith(ext))) {
                        return 'assets/fonts/[name][extname]';
                    }
                    return 'assets/[name].[hash][extname]';
                },
            },
        },
    },
    resolve: {
        alias: {
            '@': fileURLToPath(new URL('./src', import.meta.url)),
            // @tsukumijima/aribts の readable-stream@4 が Node の stream を参照しないよう、
            // パッケージに同梱されたブラウザ実装へ固定する。
            stream: readableStreamBrowserPath,
        },
        extensions: ['.js', '.json', '.jsx', '.mjs', '.ts', '.tsx', '.vue'],
    },
    // mpeg2toh264 は配布済みの Worker を import.meta.url から解決するため、依存関係の事前バンドルから除外する
    // エントリーポイントだけを .vite/deps/ へ移動すると相対 URL の起点が変わり、同梱 Worker を取得できなくなる
    optimizeDeps: {
        exclude: [
            'mpeg2toh264/player',
            'mpeg2toh264/yadif',
        ],
    },
    // SASS / SCSS の設定
    css: {
        preprocessorOptions: {
            scss: {
                // 共通の mixin を読み込む
                // ref: https://qiita.com/nanohanabuttobasu/items/f73ed978cc10d8bcaa59
                additionalData: '@import "@/styles/mixin.scss";',
            },
        },
    },
    // 開発用サーバーの設定
    server: {
        host: '0.0.0.0',
        port: 7011,
        strictPort: true,
        allowedHosts: true,
        proxy: {
            // データ放送 iframe は CSP で同一 origin に閉じ込め、既存 API 反代だけを経由させる。
            '/api': {
                target: process.env.VITE_KONOMITV_API_BASE_URL?.replace(/\/api\/?$/, '') ?? 'http://127.0.0.1:7000',
                changeOrigin: true,
            },
        },
    },
    preview: {
        host: '0.0.0.0',
        port: 7011,
        strictPort: true,
        allowedHosts: true,
    },
    // プラグインの設定
    plugins: [
        aribVfsWorkerPlugin,
        comlink(),
        vue({
            template: {
                transformAssetUrls: transformAssetUrls,
            },
        }),
        // https://github.com/vuetifyjs/vuetify-loader/tree/master/packages/vite-plugin#readme
        vuetify({
            autoImport: true,
            styles: {
                configFile: 'src/styles/settings.scss',
            }
        }),
        // ref: https://vite-pwa-org.netlify.app/guide/
        VitePWA({
            // Service Worker の登録方法
            strategies: 'injectManifest',
            srcDir: 'src',
            filename: 'sw.ts',
            registerType: 'prompt',  // PWA の更新前にユーザーに確認する
            injectRegister: 'auto',
            useCredentials: true,
            // PWA のキャッシュに含めるファイル
            includeAssets: [
                'assets/**',
            ],
            // manifest.json の内容
            manifest: {
                name: 'KonomiTV',
                short_name: 'KonomiTV',
                start_url: '.',
                display: 'standalone',
                theme_color: '#0D0807',
                background_color: '#1E1310',
                lang: 'ja',
                icons: [
                    {
                        src: '/assets/images/icons/icon-192px.png',
                        sizes: '192x192',
                        type: 'image/png',
                    },
                    {
                        src: '/assets/images/icons/icon-512px.png',
                        sizes: '512x512',
                        type: 'image/png',
                    },
                    {
                        src: '/assets/images/icons/icon-maskable-192px.png',
                        sizes: '192x192',
                        type: 'image/png',
                        purpose: 'maskable',
                    },
                    {
                        src: '/assets/images/icons/icon-maskable-512px.png',
                        sizes: '512x512',
                        type: 'image/png',
                        purpose: 'maskable',
                    }
                ]
            },
            // 独自 Service Worker へ注入する事前キャッシュの設定
            injectManifest: {
                maximumFileSizeToCacheInBytes: 1024 * 1024 * 15,  // 15MB
            },
        }),
    ],
    // Web Worker 上のプラグインの設定
    worker: {
        plugins: () => [
            comlink(),
        ]
    }
});
