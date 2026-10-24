import { Buffer } from 'buffer';

import type { ResponseMessage } from 'web-bml/worker';

/** VFS Worker の scope 内で SkyPerfecTV 公開ヘルプを表す内部パス。 */
export const SKY_HELP_PREFIX = '/data-broadcast/.hybridcast/4153/1/public-help/';

/** CS101 の黄キーだけに、カード不要の公開ヘルプ起動経路を追加する。 */
export async function adaptSkyPerfecTVBML(
    message: ResponseMessage, networkId: number | null, serviceId: number | null,
): Promise<ResponseMessage> {
    // 放送局固有の処理は汎用 BML エンジンに入れず、対象の起動文書だけに限定する。
    if (networkId !== 6 || serviceId !== 101 || message.type !== 'moduleDownloaded') return message;
    let adapted = false;
    const files = await Promise.all(message.files.map(async file => {
        if (file.contentLocation?.toLowerCase() !== 'startup.bml' || file.contentType.type !== 'text') return file;
        // EUC-JP を再エンコードせず、ASCII の起動処理以外のバイト列はそのまま保持する。
        const bytes = Buffer.from(file.dataBase64, 'base64');
        const source = bytes.toString('latin1');
        const launcherStart = source.indexOf('function startIPTV()');
        const launcherCall = source.indexOf('return SJC_launchIPTV();', launcherStart);
        const launcherEnd = source.indexOf('}', launcherCall);
        if (launcherStart < 0 || launcherCall <= launcherStart || launcherEnd <= launcherCall) return file;
        const launcher = source.slice(launcherStart, launcherEnd + 1);
        // 自動起動の SJC_launchIPTV は変更しない。黄キーからの明示操作のみ、同じ AIT を公開ヘルプで開く。
        // カードは存在しないまま扱い、既存の接続確認を通す。BML に戻った際のキー入力も解除しておく。
        const fallback = 'if(browser.getIRDID(5)==null){' +
            'var ip=browser.isIPConnected();if(ip!=1&&ip!=2)return -5;' +
            'var sid=browser.getProgramID(2);if(sid==null)return -9;' +
            'var base=SJC_CONFIG["XMLURL"];if(base.charAt(base.length-1)!="/")base+="/";' +
            'var ait=base+SJC_CONFIG["SI_TYPE"]+"/"+String(1000+parseInt(sid,16)).substring(1)+".xml";' +
            'kf=0;return isNaN(browser.X_DPA_startResidentApp("AITControlledApp",0,ait,"1039","1"))?-8:1;}';
        const replacement = launcher.replace('return SJC_launchIPTV();', fallback + 'return SJC_launchIPTV();');
        if (replacement === launcher) return file;
        adapted = true;
        return {...file, dataBase64: Buffer.from(source.replace(launcher, replacement), 'latin1').toString('base64')};
    }));
    return adapted ? {...message, files} : message;
}

/**
 * VFS が HTML の先頭へ導入する公開ヘルプ専用の起動フック。
 * 上流スクリプトは一切書き換えず、DOMContentLoaded の capture 段階で
 * 公開一覧の初期化に必要な API だけを包む。カード情報やログイン状態は作らない。
 */
export const SKY_PUBLIC_HELP_BOOTSTRAP = String.raw`
<style>object[type="video/x-iptvf-broadcast"]{visibility:hidden!important}</style>
<script>
document.addEventListener("DOMContentLoaded", function () {
    // この HTML は明示的に選択された公開ヘルプの VFS scope にだけ配信される。
    // 上流の通常起動は保存し、公開モードでは放送波 Greg とカード取得を要求しない。
    var originalLoad = Util.bml.loadBroadcastParam;
    var originalBoot = Util.getBootUrl;
    Util.bml.loadBroadcastParam = function () {
        if (!window.__KONOMI_PUBLIC_HELP__) return originalLoad.apply(this, arguments);
        // AppStart が参照する値も同じオブジェクトへ設定する。
        // リソース文字列が揃ってから公開一覧へ進み、Action 内で場面遷移の完了を待たない。
        return LoadResouceString().then(function () {
            Util.bml.gregInfo = {VERSION: "0002", BML_NAME: "", parsedGreg: []};
        });
    };
    Util.getBootUrl = function () {
        return window.__KONOMI_PUBLIC_HELP__ ? "sv_help-list" : originalBoot.apply(this, arguments);
    };
    window.__KONOMI_PUBLIC_HELP__ = true;
}, {capture: true, once: true});
</script>`;
