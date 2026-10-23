import { XMLParser, XMLValidator } from 'fast-xml-parser';

export interface HybridcastLaunchRequest {
    organizationId: number;
    applicationId: number;
    aitUrl: string;
    greg: string[];
}

/** XML AIT 内の要求されたアプリだけを選び、HTTP 配信開始 URL を返す。 */
export function resolveXMLAIT(source: string, request: HybridcastLaunchRequest): string {
    if (/<!DOCTYPE/i.test(source) || XMLValidator.validate(source) !== true) {
        throw new Error('HYBRIDCAST_INVALID_AIT');
    }
    // 数字のように見える URI / アプリ ID も文字列のまま読み、後段で明示的に検証する。
    const parsed: unknown = new XMLParser({removeNSPrefix: true, parseTagValue: false,
        processEntities: false, isArray: name => name === 'Application'}).parse(source);
    const record = (value: unknown): Record<string, unknown> =>
        typeof value === 'object' && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : {};
    const discovery = record(record(parsed).ServiceDiscovery).ApplicationDiscovery;
    const applications = record(record(discovery).ApplicationList).Application;
    if (!Array.isArray(applications)) throw new Error('HYBRIDCAST_INVALID_AIT');
    for (const value of applications) {
        const application = record(value);
        const identifier = record(application.applicationIdentifier);
        const org = identifier.orgId;
        const app = identifier.appId;
        if (typeof org !== 'string' || typeof app !== 'string' || !/^\d+$/.test(org) || !/^\d+$/.test(app) ||
            Number(org) !== request.organizationId || Number(app) !== request.applicationId) continue;
        const descriptor = record(application.applicationDescriptor);
        if (record(descriptor.type).IsdbApp !== 'ISDB-HTML' ||
            !['AUTOSTART', 'PRESENT'].includes(String(descriptor.controlCode))) {
            throw new Error('HYBRIDCAST_UNSUPPORTED_AIT');
        }
        const base = record(application.applicationTransport).URLBase;
        const location = application.applicationLocation;
        if (typeof base !== 'string' || !base || typeof location !== 'string' || !location) {
            throw new Error('HYBRIDCAST_INVALID_AIT');
        }
        const entry = new URL(location, new URL(base, request.aitUrl));
        if (!['http:', 'https:'].includes(entry.protocol) || entry.username || entry.password || entry.port) {
            throw new Error('HYBRIDCAST_UNSUPPORTED_URL');
        }
        // managedURL は新しい通信権限を与えない。最終的な許可範囲はホスト側が決める。
        return entry.href;
    }
    throw new Error('HYBRIDCAST_APPLICATION_NOT_FOUND');
}
