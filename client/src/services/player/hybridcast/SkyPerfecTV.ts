import { Buffer } from 'buffer';

import type { ResponseMessage } from 'web-bml/worker';

/** 明示的に選択された CS101 公開ヘルプ実験だけに適用する放送局アダプター。 */
export const SKY_HELP_PREFIX = '/data-broadcast/.hybridcast/4153/1/public-help/';

/** CS101 の黄キーだけに、カード不要の公開ヘルプ起動経路を追加する。 */
export async function adaptSkyPerfecTVBML(
    message: ResponseMessage, networkId: number | null, serviceId: number | null,
): Promise<ResponseMessage> {
    // 放送局固有の処理は汎用 BML エンジンに入れず、対象の起動文書だけに限定する。
    if (networkId !== 6 || serviceId !== 101 || message.type !== 'moduleDownloaded' ||
        message.componentId !== 0x40 || message.moduleId !== 0) return message;
    let adapted = false;
    const files = await Promise.all(message.files.map(async file => {
        if (file.contentLocation !== 'startup.bml' || file.contentType.type !== 'text' ||
            file.contentType.subtype !== 'x-arib-bml') return file;
        // EUC-JP を再エンコードせず、ASCII の起動処理以外のバイト列はそのまま保持する。
        const bytes = Buffer.from(file.dataBase64, 'base64');
        const source = bytes.toString('latin1');
        const start = source.indexOf('function SJC_launchIPTV()');
        const end = source.indexOf('function SJC_redirectTo()', start);
        if (start < 0 || end <= start) return file;
        const digest = await crypto.subtle.digest('SHA-256', bytes.subarray(start, end));
        const hash = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
        if (hash !== 'c74a5eb8e9ab6cd110a6c6dfbd0abc5e4fa646231c1c3548f0ab211e98e602f8') return file;
        const launcher = 'function startIPTV(){\r\n' +
            '\tif(browser.getBrowserSupport("ARIB","APIGroup","Misc.Greg")==1)browser.Greg[58]="";\r\n' +
            '\treturn SJC_launchIPTV();\r\n}';
        if (source.split(launcher).length !== 2) return file;
        // 自動起動の SJC_launchIPTV は変更しない。黄キーからの明示操作のみ、同じ AIT を公開ヘルプで開く。
        // カードは存在しないまま扱い、既存の接続確認を通す。BML に戻った際のキー入力も解除しておく。
        const fallback = 'if(browser.getIRDID(5)==null){' +
            'var ip=browser.isIPConnected();if(ip!=1&&ip!=2)return -5;' +
            'var sid=browser.getProgramID(2);if(sid==null)return -9;' +
            'var base=SJC_CONFIG["XMLURL"];if(base.charAt(base.length-1)!="/")base+="/";' +
            'var ait=base+SJC_CONFIG["SI_TYPE"]+"/"+String(1000+parseInt(sid,16)).substring(1)+".xml";' +
            'kf=0;return isNaN(browser.X_DPA_startResidentApp("AITControlledApp",0,ait,"1039","1"))?-8:1;}';
        const replacement = launcher.replace('return SJC_launchIPTV();', fallback + 'return SJC_launchIPTV();');
        adapted = true;
        return {...file, dataBase64: Buffer.from(source.replace(launcher, replacement), 'latin1').toString('base64')};
    }));
    return adapted ? {...message, files} : message;
}

/**
 * Worker にも同じ関数を埋め込むため、外部変数や DOM に依存させない。
 * 未知の版を部分的に書き換えると初期化が壊れるので、ハッシュと境界を両方検証する。
 */
export async function adaptSkyPerfecTVPublicHelp(
    source: string, url: string, organizationId: number, applicationId: number,
): Promise<string> {
    const resource = new URL(url);
    if (organizationId !== 4153 || applicationId !== 1 || resource.hostname !== 'tvip.hc.skyperfectv.co.jp' ||
        resource.pathname !== '/content-sky/app/js/main.min.js') return source;
    const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(source));
    const hash = Array.from(new Uint8Array(digest), value => value.toString(16).padStart(2, '0')).join('');
    if (hash !== 'f816e16fb0cc1cda0ab8d8eaefe856695d7d2a521a3daeca3613b1749d0cdd19') {
        throw new Error('HYBRIDCAST_ADAPTER_MISMATCH');
    }
    const start = source.indexOf('DspInit002.prototype.onLoad=function');
    const end = source.indexOf('DspInit002.prototype.onKeydown=', start);
    if (start < 0 || end <= start) throw new Error('HYBRIDCAST_ADAPTER_MISMATCH');
    // 公開 CMS のヘルプ一覧だけへ進む。カード情報・ログイン結果・番組情報は捏造しない。
    return source.slice(0, start) +
        'DspInit002.prototype.onLoad=function(){Action.call(function(){return $.when(LoadResouceString()).then(function(){' +
        'Util.dom.setVobjInvisible();$("#startUpMessage").remove();Action.changeScene("sv_help-list")})})},' +
        source.slice(end);
}
