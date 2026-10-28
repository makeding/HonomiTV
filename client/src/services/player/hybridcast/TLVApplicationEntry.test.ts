import { describe, expect, it } from 'vitest';

import { resolveTLVBroadbandEntry } from './TLVApplicationEntry';

const origin = 'https://receiver.example';
const tokyo = {
    entryPath: 'entry/html_mm/startup.min.html',
    transportUrls: ['http://hc.bsjhc.jp/hc/'],
};

describe('MH-AIT broadband entry', () => {
    it('resolves the real BS TV Tokyo entry without carousel readiness', () => {
        expect(resolveTLVBroadbandEntry(tokyo, origin))
            .toBe('http://hc.bsjhc.jp/hc/entry/html_mm/startup.min.html');
    });
    it('honors the application transport label and HTTP protocol', () => {
        expect(resolveTLVBroadbandEntry({...tokyo, transportProtocolLabels: [2], transports: [
            {protocolId: 3, label: 1, urls: tokyo.transportUrls},
            {protocolId: 5, label: 2, urls: tokyo.transportUrls},
        ]}, origin)).toBeNull();
        expect(resolveTLVBroadbandEntry({...tokyo, transportProtocolLabels: [2], transports: [
            {protocolId: 3, label: 2, urls: tokyo.transportUrls},
        ]}, origin)).toContain('/hc/entry/html_mm/startup.min.html');
    });
    it('keeps carousel paths out of the broadband path', () => {
        for (const base of ['sh4/40/001/', 'bsfuji4k/', 'sv1/cmp40/001/']) {
            expect(resolveTLVBroadbandEntry({...tokyo, transportUrls: [base]}, origin)).toBeNull();
        }
    });
    it('rejects unapproved hosts, credentials and nonstandard ports', () => {
        for (const base of ['http://127.0.0.1/', 'http://hc.bsjhc.jp.evil.example/',
            'http://user:secret@hc.bsjhc.jp/', 'http://hc.bsjhc.jp:8080/']) {
            expect(resolveTLVBroadbandEntry({...tokyo, transportUrls: [base]}, origin)).toBeNull();
        }
    });
});
