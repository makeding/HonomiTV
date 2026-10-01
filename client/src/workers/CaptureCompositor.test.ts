import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ICaptureCompositorConstructor } from '@/workers/CaptureCompositor';


const mocks = vi.hoisted(() => ({expose: vi.fn()}));
vi.mock('comlink', () => ({expose: mocks.expose}));
vi.mock('@/utils', () => ({default: {}, dayjs: vi.fn()}));

describe('認証付きキャプチャフォント', () => {
    let compositor: ICaptureCompositorConstructor;
    const fetchFont = vi.fn();
    const addFont = vi.fn();
    const loadFont = vi.fn();
    const sources: {family: string; source: ArrayBuffer; descriptors: FontFaceDescriptors}[] = [];

    beforeEach(async () => {
        vi.resetModules();
        vi.clearAllMocks();
        sources.length = 0;
        fetchFont.mockImplementation(async () => ({
            ok: true, status: 200, arrayBuffer: async () => new ArrayBuffer(4),
        }));
        loadFont.mockImplementation(async function (this: FontFace) { return this; });
        vi.stubGlobal('fetch', fetchFont);
        vi.stubGlobal('self', {fonts: {add: addFont}});
        vi.stubGlobal('FontFace', class {
            load = loadFont;
            constructor(family: string, source: ArrayBuffer, descriptors: FontFaceDescriptors) {
                sources.push({family, source, descriptors});
            }
        });
        await import('@/workers/CaptureCompositor');
        compositor = mocks.expose.mock.calls[0][0] as ICaptureCompositorConstructor;
    });

    afterEach(() => vi.unstubAllGlobals());

    it('同一オリジンの認証付き fetch で取得したバイナリから全フォントをロードする', async () => {
        await compositor.loadFonts();
        expect(fetchFont.mock.calls.map(([url]) => url)).toEqual([
            '/assets/fonts/OpenSans-Bold.woff2',
            '/assets/fonts/YakuHanJPs-Bold.woff2',
            '/assets/fonts/Twemoji.woff2',
            '/assets/fonts/NotoSansJP-Bold.woff2',
        ]);
        for (const [, options] of fetchFont.mock.calls) {
            expect(options).toEqual({credentials: 'same-origin', mode: 'same-origin', redirect: 'error'});
        }
        expect(sources.map(font => font.family)).toEqual(['Open Sans', 'YakuHanJPs', 'Twemoji', 'Noto Sans JP']);
        expect(sources.every(font => font.source instanceof ArrayBuffer && font.descriptors.weight === 'bold')).toBe(true);
        expect(loadFont).toHaveBeenCalledTimes(4);
        expect(addFont).toHaveBeenCalledTimes(4);
        await compositor.loadFonts();
        expect(fetchFont).toHaveBeenCalledTimes(4);
    });

    it('初期化とキャプチャが重なってもダウンロードを共有する', async () => {
        await Promise.all([compositor.loadFonts(), compositor.loadFonts()]);
        expect(fetchFont).toHaveBeenCalledTimes(4);
        expect(addFont).toHaveBeenCalledTimes(4);
    });

    it('HTTP エラー時は登録せず、次のロードで再試行する', async () => {
        fetchFont.mockResolvedValueOnce({ok: false, status: 403});
        await expect(compositor.loadFonts()).rejects.toThrow('HTTP 403');
        expect(addFont).not.toHaveBeenCalled();
        await compositor.loadFonts();
        expect(fetchFont).toHaveBeenCalledTimes(8);
        expect(addFont).toHaveBeenCalledTimes(4);
    });

    it('認証リダイレクトや通信の失敗後も再試行できる', async () => {
        fetchFont.mockRejectedValueOnce(new TypeError('Redirect blocked'));
        await expect(compositor.loadFonts()).rejects.toThrow('Redirect blocked');
        expect(addFont).not.toHaveBeenCalled();
        await compositor.loadFonts();
        expect(addFont).toHaveBeenCalledTimes(4);
    });

    it('フォントのデコード失敗後も再試行できる', async () => {
        loadFont.mockRejectedValueOnce(new Error('Invalid font'));
        await expect(compositor.loadFonts()).rejects.toThrow('Invalid font');
        expect(addFont).not.toHaveBeenCalled();
        await compositor.loadFonts();
        expect(addFont).toHaveBeenCalledTimes(4);
    });
});
