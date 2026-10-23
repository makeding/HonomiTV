/** NHK 番組表の文書遷移を、現在の受信機プロキシの scope 内に保つ。 */
export function adaptNHKProgramGuideNavigation(source: string, url: string, rootPath: string): string {
    const resource = new URL(url);
    // 対象の二つの番組表スクリプトだけに適用し、一般の文字列や別局のコードは変更しない。
    if (resource.hostname !== 'hc.nhk.jp' || ![
        '/hc-program/common/js/hc_action.js',
        '/hc-program/common/js/hc_action_bs4k8k.js',
    ].includes(resource.pathname)) return source;
    // scope 外への document navigation は Worker に届かないため、遷移前に宛先を写像する。
    for (const page of ['index.html', 'index_bs4k8k.html']) {
        source = source.replaceAll(`window.location.href="/hc-program/${page}"`,
            `window.location.href="${rootPath}hc-program/${page}"`);
    }
    return source;
}
