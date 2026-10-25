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
    // 同じ呼出窓口が公開 JSON にもカード取得と POST を適用している。
    // 設定から解決した公開資源だけは GET し、契約 API は従来の処理へ返す。
    var originalApi = Api.common;
    Api.common = function (name, path) {
        var url = new URL(path, document.baseURI);
        var publicRoot = new URL("../pub/", document.baseURI);
        if (!window.__KONOMI_PUBLIC_HELP__ || url.origin !== publicRoot.origin ||
            !url.pathname.startsWith(publicRoot.pathname)) return originalApi.apply(this, arguments);
        var result = $.Deferred();
        $.ajax({url: url.href, method: "GET", dataType: "json", timeout: Conf.ajaxTimeout})
            .done(result.resolve).fail(function (xhr, status) {
                // 既存の各 API が失敗理由を引き継げるよう、上流と同じエラー型で返す。
                var error = new CommonError("APIエラー", "", {url: url.href, xhr: xhr, textStatus: status});
                result.reject(error);
                error.execIfYet(function () { Action.error("番組情報を取得できませんでした (HTTP " + xhr.status + ")"); });
            });
        return result;
    };
    // 通常の番組表初期化には契約照会・購入商品・配信推薦が混在する。
    // 公開モードの番組表だけは公開チャンネルと番組 JSON で既存 EPG を起動する。
    // ログインや契約情報を空の成功応答へ置き換えることはしない。
    var originalEpgLoad = DspSv001.prototype.onLoad;
    var originalGenre = Util.getChannelGenreTbl;
    Util.getChannelGenreTbl = function () {
        if (!window.__KONOMI_PUBLIC_HELP__) return originalGenre.apply(this, arguments);
        // 公開チャンネル一覧にもジャンル名があるため、会員 API のマスタ取得を不要にする。
        return Action.call("EPGチャンネルリスト取得").then(function () {
            var genres = [{id: "すべて", text: "すべて"}];
            (Model.get("EPGチャンネルリスト") || []).forEach(function (channel) {
                if (channel.genre && !genres.some(function (genre) { return genre.id === channel.genre; }))
                    genres.push({id: channel.genre, text: channel.genre});
            });
            return genres;
        });
    };
    DspSv001.prototype.onLoad = function () {
        if (!window.__KONOMI_PUBLIC_HELP__ || this.sceneName !== "sv_epg")
            return originalEpgLoad.apply(this, arguments);
        var scene = this;
        // 詳細から戻る際は、上流が保存した日時・チャンネルを再利用する。
        // 現在時刻の Model を直接変更せず、番組表の表示位置だけを復元する。
        var restoreChannel = scene.getParam("bid-bak");
        var restoreTime = scene.getParam("ftm-bak");
        var restoreOffset = scene.getParam("ctm-bak");
        scene.timeOffset = Util.timeCreateAddHour(Model.get("現在時刻"), 0);
        if (restoreOffset != null) scene.timeOffset.setTime(restoreOffset);
        scene.target = "all";
        Model.set("target-channel", "all");
        Model.set("epg-login-status", "logout");
        return $.when(Action.call("EPGチャンネルリスト取得"), Action.call("チャンネルロゴ情報取得"),
            Action.call("選局情報取得"), Util.getChannelGenreTbl(), Api.IPTV01F014())
            .then(function (channels, logos, tune, genres, pps) {
            // 番組枠が参照する PPV 商品一覧も公開 JSON から作る。契約可否は取得・設定しない。
            var programs = [];
            (pps.cappvPpsPgmInfoList || []).forEach(function (program) {
                var channel = program.chInfoList[0];
                var series = channel.caSeriesIdList || [];
                if (series.some(function (item) {
                    return Util.getProdCodeListFromPpsProdList(item.ppsProdList).ppsProdCodeList.length > 0;
                })) programs.push(Util.createBroadcastChannelId(channel.chNum, channel.siType, null) + "_" + channel.eventIdBrdcstWave);
            });
            Model.set("DSPSV001_PPS一覧情報", programs);
            scene.genreTbl = genres;
            Model.set("target-genre", 0);
            scene.genreId = scene.convertTargetGenreId(0);
            Model.set("target-genre-text", scene.convertTargetGenreText(0));
            scene.convertTargetChannelText("all");
            scene.setChannelTable(Model.get("EPGチャンネルリスト"));
            return scene.getChannelTableByGenre(scene.genreId);
        }).then(function (table) {
            var tune = Model.get("選局情報");
            scene.epg.setCurrentBroadcastChannelId(tune.broadcastChannelId);
            scene.epg.setCurrentTime(Model.get("現在時刻"));
            scene.epg.start(tune.broadcastChannelId, scene.timeOffset, table, restoreChannel);
            scene.$top.hide();
            scene.setMiniEpg(false);
            // 通常起動の最適化通知はカード初期化の後に来る。公開起動では初回データの描画後に
            // 同じ既存処理を呼び、不可視のまま保持されている番組表を入力可能な状態へ移す。
            return scene.epg.waitFirstLoad().then(function () {
                if (restoreTime != null) {
                    var focusTime = Util.timeCreateAddHour(scene.timeOffset, 0);
                    focusTime.setTime(restoreTime);
                    scene.epg.initFocus(focusTime, restoreChannel);
                }
                scene.setParam("bid-bak", null);
                scene.setParam("ctm-bak", null);
                scene.setParam("ftm-bak", null);
                scene.optimisation();
            });
        });
    };
    // 公開番組表から選んだ番組は、同じ公開 JSON の詳細を表示する。
    // 会員向け詳細 API の成功を偽装せず、契約操作を持たない閲覧経路として初期化する。
    var originalDetailDom = DspSv012.prototype.createDom;
    var originalDetailLoad = DspSv012.prototype.onLoad;
    var originalDetailSelect = DspSv012.prototype.onFocusSelect;
    var originalDetailKey = DspSv012.prototype.onKeydown;
    DspSv012.prototype.createDom = function (root) {
        originalDetailDom.apply(this, arguments);
        if (!window.__KONOMI_PUBLIC_HELP__) return;
        // 公開詳細の左側には、カード不要で完結する番組表への復帰操作を配置する。
        $(root).find('[data-focus="channel"]').attr("data-focus", "public-back")
            .removeAttr("data-model-res data-model-enableif").text("番組表に戻る");
    };
    DspSv012.prototype.onLoad = function () {
        if (!window.__KONOMI_PUBLIC_HELP__) return originalDetailLoad.apply(this, arguments);
        var scene = this;
        var selected = Model.get("epg-focus-programInfo");
        scene.ignoreInitFocus = true;
        // 選択番組との一致を確認し、他の画面から来た古い選択情報を流用しない。
        if (!selected || String(selected.contentId) !== String(scene.getParam("contentsId"))) {
            Action.error("公開番組表から番組を選び直してください。");
            return;
        }
        var channelId = selected.broadcastChannelId;
        return Api.IPTV01F013(Util.timeFormatYYYYMMDD(selected.startTime),
            Util.getChNum5FromBroadcastChannelId(channelId), Util.getSiTypeFromBroadcastChannelId(channelId))
            .then(function (data) {
                if (scene.isFinish()) return;
                var channel = (data.chInfoList || []).find(function (item) {
                    return String(item.chNum) === String(Util.getChNum5FromBroadcastChannelId(channelId));
                });
                var program = channel && (channel.programInfoDtoType || []).find(function (item) {
                    return String(item.contentId) === String(selected.contentId) &&
                        String(item.infoId) === String(selected.infoId);
                });
                if (!program) {
                    Action.error("番組情報が更新されました。番組表に戻って選び直してください。");
                    return;
                }
                Model.set("logo", Util.getChannelImage(channelId, true));
                Model.set("thumbnail", {list: selected.images || [], alt_url: Util.getChannelImage(channelId, true)});
                Model.set("chnum", Util.getSiTypeStringFromBroadcastChannelId(channelId) + " " +
                    Util.getChNum3FromBroadcastChannelId(channelId));
                Model.set("name", channel.chName || "");
                Model.set("title", program.title);
                Model.set("onair", Util.getTimStringStartToEnd(program.brdcstStartYearMonthDayTimeMinuteSecond,
                    program.brdcstEndYearMonthDayTimeMinuteSecond));
                var genres = (program.brdcstGenreDtoType || []).map(function (genre) {
                    return [genre.bigGenreName, genre.durGenreName].filter(Boolean).join(" ＞ ");
                });
                // 上流の見出しは一行分だけ確保されているため、主ジャンルを表示する。
                // 複数ジャンルも省略せず、スクロール可能な説明欄にすべて残す。
                Model.set("genre", genres[0] || "");
                var details = (program.expandProgramDetailsInfoDtoType || []).slice().sort(function (a, b) {
                    return a.order - b.order;
                }).map(function (item) { return item.itemName + "\n" + item.itemDescript; });
                if (genres.length > 1) details.push("ジャンル\n" + genres.join("\n"));
                scene.detailText = [program.overviewLongLetter || program.overviewMidLetter ||
                    program.overviewShortLetter || "番組概要はありません。"].concat(details).join("\n\n");
                Model.set("text", scene.detailText);
                Model.set("sv012-focus-enable", true);
                // 元の詳細画面のスクロールとフォーカス処理をそのまま使う。
                scene.focusEngine.addRule("textbox", VK_LEFT, "public-back");
                scene.focusEngine.addRule("public-back", VK_RIGHT, "textbox");
                return Action.call("update-dombind").then(function () {
                    if (scene.isFinish()) return;
                    scene.ignoreInitFocus = false;
                    scene.focusEngine.init();
                    scene.setFocus("public-back");
                });
            });
    };
    DspSv012.prototype.onFocusSelect = function (focus) {
        if (!window.__KONOMI_PUBLIC_HELP__) return originalDetailSelect.apply(this, arguments);
        if (focus === "public-back") Action.call("back-scene");
    };
    DspSv012.prototype.onKeydown = function (key) {
        if (!window.__KONOMI_PUBLIC_HELP__) return originalDetailKey.apply(this, arguments);
        if (key === VK_BACK) { Action.call("back-scene"); return true; }
    };
    window.__KONOMI_PUBLIC_HELP__ = true;
}, {capture: true, once: true});
</script>`;
