import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ICaptureCompositorConstructor } from '@/workers/CaptureCompositor';


const mocks = vi.hoisted(() => ({expose: vi.fn()}));
vi.mock('comlink', () => ({expose: mocks.expose}));
vi.mock('@/utils', () => ({default: {}, dayjs: vi.fn()}));

describe('上流キャプチャフォント', () => {
    let compositor: ICaptureCompositorConstructor;
    const addFont = vi.fn();
    const loadFont = vi.fn();
    const sources: {family: string; source: string; descriptors: FontFaceDescriptors}[] = [];

    beforeEach(async () => {
        vi.resetModules();
        vi.clearAllMocks();
        sources.length = 0;
        loadFont.mockImplementation(async function (this: FontFace) { return this; });
        vi.stubGlobal('self', {fonts: {add: addFont}});
        vi.stubGlobal('FontFace', class {
            load = loadFont;
            constructor(family: string, source: string, descriptors: FontFaceDescriptors) {
                sources.push({family, source, descriptors});
            }
        });
        await import('@/workers/CaptureCompositor');
        compositor = mocks.expose.mock.calls[0][0] as ICaptureCompositorConstructor;
    });

    afterEach(() => vi.unstubAllGlobals());

    it('Worker に URL からフォントを登録し、読込済みなら再登録しない', async () => {
        await compositor.loadFonts();
        expect(sources.map(font => font.source)).toEqual([
            'url(/assets/fonts/OpenSans-Bold.woff2)',
            'url(/assets/fonts/YakuHanJPs-Bold.woff2)',
            'url(/assets/fonts/Twemoji.woff2)',
            'url(/assets/fonts/NotoSansJP-Bold.woff2)',
        ]);
        expect(addFont).toHaveBeenCalledTimes(4);
        await compositor.loadFonts();
        expect(loadFont).toHaveBeenCalledTimes(4);
        expect(addFont).toHaveBeenCalledTimes(4);
    });
});
