import { describe, expect, it } from 'vitest';

import { resolveARIBDocumentURL } from './ARIBProxyURL';

describe('VFS document location mapping', () => {
    const documentURL = 'http://127.0.0.1:7011/data-broadcast/.external/http/hc.nhk.or.jp/p/index.html';

    it('restores an upstream host derived from the proxy hostname', () => {
        expect(resolveARIBDocumentURL('https://127.nhk.jp/hc-ssl/1.txt', documentURL)?.href)
            .toBe('https://hc.nhk.jp/hc-ssl/1.txt');
        expect(resolveARIBDocumentURL('http://127.nhk.or.jp/hc-ssl/2.txt', documentURL)?.href)
            .toBe('http://hc.nhk.or.jp/hc-ssl/2.txt');
    });

    it('retains the actual destination and maps root-relative navigation', () => {
        expect(resolveARIBDocumentURL('https://hc.nhk.jp/received/path.html?q=1#focus', documentURL)?.href)
            .toBe('https://hc.nhk.jp/received/path.html?q=1#focus');
        expect(resolveARIBDocumentURL('/received/path.html', documentURL)?.href)
            .toBe('http://hc.nhk.or.jp/received/path.html');
    });

    it('does not reinterpret a VFS URL or invent hosts outside the allowlist', () => {
        expect(resolveARIBDocumentURL(documentURL, documentURL)).toBeNull();
        expect(resolveARIBDocumentURL('https://other.nhk.jp/path', documentURL)).toBeNull();
        expect(resolveARIBDocumentURL('https://127.nhk.jp/path',
            'http://127.0.0.1:7011/data-broadcast/.external/https/tvip.hc.skyperfectv.co.jp/app/')).toBeNull();
    });
});
