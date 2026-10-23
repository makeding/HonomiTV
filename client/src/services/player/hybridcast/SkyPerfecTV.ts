/** 明示的に選択された CS101 公開ヘルプ実験だけに適用する放送局アダプター。 */
export const SKY_HELP_PREFIX = '/data-broadcast/.hybridcast/4153/1/public-help/';

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
