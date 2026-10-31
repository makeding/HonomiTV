# Selective upstream integration

## Full upstream merge (2026-10-31)

- Merge upstream/master at `1319cf39` into h-dev, preserving fork playback, BS4K, Hybridcast, remote-device integration, capture thumbnail selection, loading-failure recovery and device-local time display.
- Adopt upstream capture filters, keyboard selection and navigation, lightbox transitions, tag filtering, IME handling, network-storage deletion fixes and database-vacuum controls. No database migration or dependency change is included.
- Pass the explicit shared process-pool context to the fork's CM analysis as well as upstream workers, retaining existing worker priority and asynchronous submission/cancellation behavior.
- Preserve tracked `client/dist` byte-for-byte from pre-merge `331fb954`; release artifact generation remains owned by CI. No frontend build, service restart, user-database write or deployment is part of this merge.
- Client ESLint/TypeScript and 27 selected regression checks passed. Server Ruff/Pyright passed with eight existing FastAPI deprecation warnings; 18 selected checks and four snapshots passed, including capture filters/sections, database-vacuum authorization/data preservation, concurrent program updates and CM cancellation/responsiveness. HTTP checks use the test application and test database. No local user-managed API is running; live-service HTTP, browser interaction and real broadcast acceptance remain unverified.

## Series compact navigation and grid (2026-10-30)

- `/series/` and its inline series details use the existing icon-only navigation, matching On Air. Desktop navigation occupies 68px; smartphone portrait keeps the bottom navigation.
- Desktop cards use a 260px minimum column width with 12px gaps and share the remaining width, fitting five columns at a 1536px viewport. Empty columns remain reserved when there are fewer cards. Tablet and smartphone layouts retain two columns, and inline episode details span the entire row.
- Loading uses 15 skeleton cards in the same grid and aspect ratio as loaded cards (16:9, or 4:3 on smartphone portrait). Loading completion preserves column widths and row spacing. Verify desktop/narrow layouts, empty results, expansion, collapse and keyboard focus.

## Reservation GGuide images (2026-10-30)

- Reservation list, detail and update responses include optional `gguide_image` metadata from the existing Mirakurun GGuide asset API. Match network ID, service ID, event ID and start time in milliseconds; reused event IDs, unmatched assets and banner images (aspect ratio >= 3) are excluded. No title inference or database migration is involved. EPGStation uses the same Mirakurun program source.
- Matched reservation rows and their detail drawer show the same image without cropping. Image loading, failure and retry retain the image bounds and do not toggle the reservation or open the drawer. Rows without an image retain their normal text layout. Missing, unsupported or unavailable GGuide data does not prevent reservation access.
- The controls column has the same width with a switch, recording indicator or no available action, on desktop and narrow layouts. Manual EPGStation reservations retain their existing actions while their program information aligns with automatic reservations.
- Acceptance: exact association and mismatches through reservation HTTP responses, list/detail propagation, image failure/retry, and desktop/narrow alignment with mixed manual, automatic and recording rows. Automated checks do not establish deployed broadcast-image availability.
- Local verification: six EPGStation common API tests passed through ASGI (list/detail/update included), with client ESLint/TypeScript and server Ruff/Pyright passing. Browser acceptance remains open: the browser access host is unavailable, no local API process is running, and the configured Akebi executable is absent. No server was started or restarted for this verification.

## Full upstream merge (2026-10-30)

- Merge upstream/master at `9a48e789` into h-dev, preserving the fork playback, Hybridcast, device integration and device-local time display.
- Adopt upstream program-update batching, capture scanning/query improvements and capture UI fixes. Number the capture-file status index migration as 21 and generate its model state from this fork.
- Preserve tracked `client/dist` from pre-merge `0cf34f9f`; keep existing local changes separate. Release artifacts remain owned by CI. No service restart or deployment is performed.
- Client ESLint/TypeScript and server Ruff/Pyright passed (existing FastAPI deprecation warnings remain). Eight program/capture tests and four snapshots passed, including both backend update paths under concurrent writes. Live-service and real broadcast acceptance are not included.

## Full upstream merge (2026-10-28)

- Merge upstream/master at `8cf7f0fd` into h-dev, retaining fork playback, Hybridcast, BS4K, CM analysis, notifications and device-local time display.
- Adopt upstream Python 3.13 and its application dependencies across server, installer, CI and Nix. Retain the fork DPlayer and web-bml pins.
- Include upstream capture management and network-folder polling. Resolve migration numbering against the fork history without modifying the user database.
- Preserve tracked `client/dist` byte-for-byte from pre-merge `de7f931a`. Do not run a local frontend build or include local compiled assets in this merge. Release artifacts remain owned by the release workflow.
- Validate dependency locks, lint/type checks and focused integration tests. No service restart or deployment is authorized by this merge.

## Scope (2026-10-14 selective integration)

- Port playback stability fixes from KonomiTV through `ddbc50c4`: default 60fps selection, MediaSession metadata readiness, subtitle teardown, Document Picture-in-Picture recovery, EPG detail preservation, disconnect-aware file responses, and HLS failure details.
- Add online and offline HLS secondary audio, update mpeg2toh264 without replacing this fork's DPlayer, and support same-stem WebVTT sidecars when a recording has no embedded subtitles.
- Move server, installer, Docker, CI, Nix packaging and development instructions from Poetry to uv. Preserve the Python 3.11 runtime and current application dependencies; require Python 3.11.4 or newer for filtered tar extraction.
- Keep tracked `client/dist` under the upstream release workflow; do not generate or commit a local dist in this integration.
- NFS polling is deferred. DPlayer version changes are a separate task. Preserve device-local time display, Raw TLV as the initial BS4K choice, recording chase playback, and the existing TLV notification contract.

## Observable playback contract

- Standalone data services (`service_type=0xC0`) outside terrestrial one-seg are selectable in the broadcast channel list, including BS SID 791. Their type comes from the last successful Mirakurun/EDCB channel discovery, refreshed before startup accepts traffic and on normal channel updates; unavailable metadata defaults to ordinary broadcast behavior without changing stored records or requiring a database migration.
- Data-only viewing receives the original decoded MPEG-TS directly from Mirakurun/EDCB into the existing web-bml decoder. It never starts an encoder, waits for a video frame, or subscribes to video-task events. BML remains enabled for this channel even when optional data broadcasting on video channels is disabled. The existing remote and player restart control remain usable; loading ends on BML load, and receive/render timeouts show a reason and adjacent retry action without automatic video restarts. Leaving or switching channels cancels reception and releases the owned tuner.
- Entering a standalone data channel opens the data-broadcasting panel and remote while preserving the selected viewing tab, as in h-dev before this merge. This does not overwrite saved panel preferences. Leaving restores the previous remote state, and the user can still close the remote or panel while viewing the data service.
- Acceptance for standalone data includes channel discovery (both backends, excluding terrestrial one-seg), complete channel API metadata, raw TS reception/disconnect cleanup and failure, BML load/error/timeout/retry, desktop/narrow layout and remote operation. Local automated checks do not establish actual SID 791 broadcast compatibility.
- Selecting 1080p (60fps) uses that quality for live and recorded playback; it must not fall back to the first quality because of a display-name mismatch. Explicit resume choices remain authoritative.
- Automatically select the mobile quality profile only when `navigator.connection.type` explicitly reports `cellular`. An `unknown` or unavailable connection type uses the Wi-Fi profile in both live and recorded playback, without inferring cellular service from `effectiveType` or enabling the mobile defaults (480p / HEVC data saver). An explicit in-player profile selection remains authoritative until leaving the watch screen.
- Metadata changes before video duration is known do not throw MediaSession exceptions. Leaving the player stops caption rendering before asynchronous teardown.
- Closing Document Picture-in-Picture resumes frame delivery only when playback was active and teardown has not started. A paused video stays paused.
- EPG details whose first item has no heading remain visible. Normalizing duplicate headings never discards either item's text.
- Aborted file downloads and Raw TLV Range requests stop reading and close their files. HEAD, valid byte ranges, and normal downloads retain their existing response contracts.
- Encoding and source-position failures retain specific safe HTTP error details; existing HTTPException statuses and reasons must propagate unchanged.
- Transcoded recording and copy-HLS support `playlist?type=master|primary-audio|secondary-audio` and `segment?audio=primary|secondary`. Existing playlist and segment requests default to primary audio. The master playlist exposes audio tracks supported by that path; Raw TLV keeps its existing audio handling; copy-HLS exposes the same primary and secondary audio via the master playlist API.
- Changing HLS audio retains the playback position and selected track across seek/playlist refresh. Audio and video use the same segment timeline, session and buffering lifecycle. Offline playback derives secondary audio from stored TS and performs no server requests.
- Same-stem WebVTT subtitles are an optional fallback for recordings without embedded subtitles. Missing historical metadata or a missing sidecar does not prevent playback. Use the public DPlayer WebVTT `url`/`type` options and existing subtitle controls without a separate renderer or compatibility manager. Embedded subtitles take precedence; sidecars remain shared files and are not newly deleted with recordings.
- Existing player controls, notification location, focus and geometry remain stable. The iOS top-blur workaround has no visible content or hit target and does not hide required content.

## Validation

- 2026-10-23: A temporary localhost frontend using the current LiveDataBroadcastingManager, raw-TS decoder and Remocon received Mirakurun `192.168.6.253:40772` service 400791. Both browser inspection and the user's screenshot confirmed the WOWOW guidance BML page and automatically opened remote. This preview proxies TS directly to Mirakurun; it does not establish live acceptance of the Python streaming endpoint, other data services, or every remote action. The preview's channel initialization follows the application's frozen channel-data convention to avoid cloning Vue proxies.
- The same preview rendered CS101 (service 600101) after manual reload following an observed `DATA_STREAM_ENDED`. Direct and proxied HTTP 200 streams each delivered approximately 0.8 MB during a bounded 12-second check. The initial disconnection cause remains undetermined; recovery and BML rendering were observed, but sustained reception was not established. The temporary preview was stopped and its 7,280-byte launcher removed after acceptance.
- Standalone-data checks passed: client ESLint/TypeScript and 14 selected regression tests; server Ruff/Pyright and three data-broadcasting tests. Server tests use isolated ASGI requests and an in-memory database, not a running deployment or real EDCB tuner.

### Hybridcast integration

- Hybridcast 起動操作の直後と HTML 文書遷移中は、切替ボタンの同じ領域に進捗スピナーを表示し、重複起動・BML への切替・アプリへのキー送信を止める。現在文書の load・フォント・レイアウトが完了した時点で操作を戻す。失敗時はスピナーを終了し、理由・再試行・BML への復帰を表示する。
- 2026-10-25: 同じローカル Edge タブで NHK BML → Hybridcast → 番組表を確認。診断時に実 AIT の XHR だけを一時停止し、切替ボタンのスピナー・disabled=true・aria-busy=true と未表示 stage の opacity=0 / inert=true を確認。要求再開後に NHK ホームと番組表を表示し、BML への復帰ボタンが操作可能になった。診断用リクエスト停止とネットワーク遅延は解除済み。[読込中](images/hybridcast-loading-state.jpg) / [番組表の表示後](images/hybridcast-ready-state.jpg)。本番環境の受入完了は含まない。
- 同じタブの最終ソースで AIT 要求の接続リセットも診断し、スピナー終了・HYBRIDCAST_AIT_FETCH_FAILED の理由・再試行・BML への復帰を確認。再試行後は NHK ホームと映像が表示され、切替ボタンが再び操作可能になった。ESLint / TypeScript は成功。
- Hybridcast の起動・HTML 文書遷移中は通常のテレビ映像を継続表示し、切替ボタンをスピナーにする。黒画面を挟んでからテレビへ戻す遷移は許容しない。runtime の installed 通知だけで表示せず、同じ文書の load、フォント読込、最初のレイアウト完了後に HTML 面と既存映像を同じフレームで切り替える。途中退出・リダイレクト・失敗した文書は遅れて表示せず、読込失敗時はテレビ映像と再試行 / BML への復帰を維持する。
- Generic receiver APIs, BML state and key dispatch belong to libaribhtml5. HonomiTV owns AIT selection, network policy, lifecycle and broadcaster adaptations.
- Discover application URLs from received BML and matching XML AIT. Keep the existing internet setting, cancel startup on channel changes, and preserve return to BML.
- NHK の標準 AIT 起動は、BML が渡した ID・XML AIT・Greg を同じ宿主へ接続する。入力源判定はライブ宿主の宣言を使い、VFS 文書の location から派生した要求・navigation は実際の上流 namespace へ写像する。上流 JS の版・文字列位置や固定のアプリ入口には依存しない。Hybridcast 面は DPlayer の操作面・番組ヘッダーより後ろに置く。
- IPTV-F の放送映像 object は、VFS の先行 runtime で SDK の共通映像スロットへ接続する。MSE 接続済みの映像 DOM は同じ文書に残し、SDK の external media plane から位置・合成順を受け取る。BML と HTML5 の移動ではユーザーの再生 / 停止状態を維持する。
- 2026-10-25: ローカル Edge の `gr011` で、実放送 BML の Hybridcast ボタン → XML AIT → NHK ホーム → 番組表を操作し、実際の番組枠と現在番組の詳細を表示した。[番組表の画面証跡](images/nhk-hybridcast-program-guide.jpg)。`location.hostname` から生成された `127.nhk.jp` は実在する接続先ではなく、VFS の要求起点に含まれる上流ホストへ戻す。NHK ホームの映像窓も MIME スロット接続後に表示された。最終的な再生状態復元の再検証中、対象タブの CDP 接続が継続して timeout し、同一タブの再読込・退出後の再生継続・ヘッダー表示の最終確認は未完了。本番環境への反映・受入完了は含まない。
- Keep application navigation inside the proxy scope and the display at 16:9. Station-specific adaptations must remain narrowly scoped.
- VFS の要求・Response 境界を通常の TypeScript で処理し、生成済み SDK Worker のコード文字列を置換しない。JSON は原文のまま返し、NHK メニューは実際の遷移操作時だけ URL を VFS に写像する。既存の映像 DOM・合成順序・寸法は維持する。BS4K の d データ首画面と通信ページへの遷移、5 秒経過後の継続表示を実画面の受入条件とする。
- 2026-10-25: 独立したローカル Chromium で tlvdemux の bsp4k.mmts 由来 51 資源と実際の通信ページを使用し、d データ → 通信機能への遷移を確認。NHK の redirectIfPc が makerid `huggy` を十六進数として解析して PC と誤判定し、ライブラリ評価中に外部サイトへ遷移していた。公開オブジェクト定義時に受信機 API の能力判定を適用し、メーカー ID は保持。修正後は通信ページが VFS 内で runtime を導入し、12 秒後も表示継続、broadcast object は 0、映像枠は 720×405。[画面証跡](images/bs4k-vfs-commu-review.jpg)。実際の DPlayer media adapter を使ったページ・矩形の検証であり、映像入力は未接続。実動画合成および本番視聴の受入完了は含まない。古い録画の番組 JSON 2 件は上流 404、object 無効化時の CSP ログは残る。ESLint / TypeScript は成功。
- SkyPerfectTV の公開モードでは、上流 JS を版・ハッシュ・文字列位置で書き換えず、VFS 文書の起動前フックで公開番組表を初期化する。番組データはアプリ設定にある公開 JSON を GET し、カード番号・契約状態・API 成功応答は捏造しない。カード必須の契約 API には公開閲覧のために接続しない。FAQ からメニューの「番組表」へ進み、実際の番組枠が表示されることを受入条件とする。
- 2026-10-25: 通信ページの左メニューが黒くなる原因は、libaribhtml5 が foreground media にも背景透過を適用し、白い `#container` まで除去していたこと。SDK の `ExternalMediaHoleController` は `above-application` の場合に背景を保持し、占位 object / wrapper のみを透明化するよう修正。既存 DPlayer adapter、video の寸法・z-index は変更なし。SDK の前景→後景→前景の回帰検証と型検査を通過。ローカル Chromium の実通信ページでは白背景とメニュー文字を確認し、映像枠は (96, 1701), 720×405、visible=true のまま。[背景修正の画面証跡](images/bs4k-vfs-commu-background.jpg)。この診断に映像入力は接続していない。修正ソースは隣接 `libaribhtml5` 作業ツリーにあり、同 SDK のローカルビルドを client/node_modules に反映して frontend を再ビルドする。npm 版への反映は未実施のため、依存関係の再インストール時には修正版 SDK が必要。
- 公開番組表の決定操作は、同じ公開 JSON にある番組概要・詳細・放送時間を既存の番組詳細画面へ表示する。契約・購入・予約の状態を推測せず、番組表へ戻る操作とフォーカスの復帰まで検証する。
- Local interaction verified public-help navigation and NHK program-guide switching. Broadcast-triggered production launch and full station functionality remain unverified.
- 2026-10-25: BS8K 通信ページの利用調査パネルが外部 foreground video に隠される問題をローカル Chromium で確認。ページの `#vstream` は z-index 10、`#nav` は 20、その中の `#survey` は 50。SDK は browser の `elementsFromPoint()` による実際の順序と不透明な矩形の完全被覆を確認し、覆われた期間だけ外部 video を不可視とする。リモコンで調査項目を選択して決定、選択・閉じるボタンが表示されること、決定で閉じた後に元の (96,1701),720×405 の media report が復帰することを確認。[画面証跡](images/bs8k-survey-occlusion.jpg)。映像入力なしの DOM・合成状態検証であり、部分被覆・半透明 UI の完全な interleave や本番映像の受入完了は含まない。
- 2026-10-25: 上記の video-only 検証では HDR 変換 canvas を含めておらず、実視聴の調査パネル被覆を検出できていなかった。DPlayer の `HlgSdrPlayerRenderer.syncLayout()` は位置・寸法・z-index のみ同期し、video が hidden になっても WebGPU / WebGL canvas は visible のままだった。実際の renderer クラスを追加した同じ VFS 通信ページでこの差を再現し、外部映像面の visibility も同期する一行の修正後に両 canvas の hidden と閉じた後の visible 復帰を確認。backend 選択用 hidden 属性と元の映像矩形は保持。[HDR canvas を含む画面証跡](images/bs8k-survey-hdr-occlusion.jpg)。canvas に診断用の塗りを置いた合成検証であり、実放送の HDR デコード・変換入力は未接続。修正は隣接 DPlayer ソースとローカルビルドにあり、依存関係の再インストールには修正版 DPlayer の参照更新が必要。
- 2026-10-25 のローカル実画面 (`http://127.0.0.1:7011/tv/watch/cs101`、一時 API は port 7002) で、FAQ → メニュー → 番組表の実番組枠表示、方向キーによる番組フォーカス移動、BML への復帰と再入場を確認。サイドパネル開閉後の iframe と BML 表示領域の矩形はそれぞれ一致 (1294×727.875 / 1646×925.875)。ESLint / TypeScript は成功。証跡は [hybridcast-public-epg.jpg](images/hybridcast-public-epg.jpg)。公開番組表のローカル検証であり、契約機能や本番環境の受入完了は含まない。
- 同日の再検証で、決定操作による番組詳細がカード取得で失敗すること、他チャンネルの詳細から戻ると選択が現在チャンネルへ戻ることを確認して修正。CS101 と CS161 の公開詳細を実画面で表示し、QVC の「滝野順ピュアプラチナジュエリー」で画面の戻るボタン・リモコンの戻るキーの両方が元の番組へ復帰することを確認。複数ジャンルは主ジャンルを見出し、全件を説明欄に表示し、タイトルとの重なりを解消。900×700 と 390×844 でも表示領域を確認 (縦画面は SDK の整数寸法丸めによる 1px 未満の差)。ESLint / TypeScript は再実行成功。証跡は [hybridcast-public-detail.jpg](images/hybridcast-public-detail.jpg)。

- Run client ESLint, TypeScript and direct regression tests for quality selection, metadata readiness, PiP teardown, EPG preservation, HLS/offline audio and sidecar selection. Run server Ruff/Pyright plus affected API/file-response and audio/subtitle tests.
- Validate uv lockfiles and dependency synchronization for server and installer; audit executable commands and build definitions for superseded Poetry usage. Production installs exclude development-only dependencies; development and lint install the dev group.
- Request affected application consumer entry points where a current user-managed API and suitable recordings are available. Inspect desktop/narrow player interaction and recovery, including audio changes, seeks, PiP, leaving/re-entering playback, and subtitles.
- Report source/tests, application HTTP, browser interaction, iOS behavior, Docker/Windows/Nix builds and real broadcast/recording playback separately. Never start or restart the user-managed API without authorization.

## Legacy installer Poetry shim

The installer updater owns the temporary Poetry shim. A pre-v0.14.1 installer updates its source and then replaces `server/thirdparty` with the current release archive before it invokes `python -m poetry`; the archive must therefore contain both pinned uv and the shim that maps this legacy command to uv.

Remove `.github/workflows/scripts/poetry` when pre-v0.14.1 installers are no longer supported, no later than 2027-04-14. `build_thirdparty.yaml` is the regression boundary: each Windows and Linux archive build executes `python -m uv --version`, `python -m poetry --version`, and `python -m poetry env use <thirdparty Python>` after placing the archive contents. This verifies the bootstrap module and the only legacy command that runs before dependency synchronization.

## Integration evidence (2026-10-14)

- Client ESLint and TypeScript passed. 21 selected tests passed, covering MediaSession readiness, PiP pause/exit/teardown, EPG detail preservation, offline renditions, actual TS secondary AAC/PMT CRC/PCR extraction, and existing playback policies/notices.
- 10 server tests passed. Server ASGI tests exercise playlist/audio/errors, download/HEAD/Range/disconnect, sidecar selection and UTF-8 header boundaries; these are isolated test application requests, not a running user's server or real broadcast acceptance.
- Server Ruff passes. Repository-wide Pyright still reports the unchanged redundant Cookie isinstance guard in TwitterScrapeBrowser.py:1849, plus existing deprecation warnings.
- Server and installer uv locks and dependency synchronization passed. Installer Ruff/Pyright, Linux Nix derivation evaluation and Darwin flake evaluation passed. Docker images, Windows installer builds and Nix builds have not been executed.
- No KonomiTV API is running locally on port 7000 (the listener is macOS ControlCenter); the development HTTPS endpoint times out. Real playback, browser interaction and iOS blur behavior remain unverified. Existing Vite preview serves another session's dist, so it is not source acceptance for this change.
- WebVTT uses normal DPlayer options. TLV subtitle-mode handling and quality-switch subtitle reattachment belong to the separately requested DPlayer repair/bump; they require acceptance after that update. No local compatibility renderer was added.

### TLV broadband application entry (2026-10-28)

- BS テレ東の `/error/bsjhc/error.php` はバックエンドの外部通信プロキシで
  HTTP 403 として拒否する。HTTP / HTTPS、クエリ付き要求、リダイレクト先にも
  同じ制約を適用し、通常の放送アプリ資源は引き続き許可する。
- BS TV Tokyo 4K (1100171): live raw TLV MH-AIT contains HTTP transport
  `http://hc.bsjhc.jp/hc/` and `entry/html_mm/startup.min.html`; application
  type 0x11, organization 0x102e, application 1, AUTOSTART. Application-service
  signalling declares data-transmission=0. No carousel readiness can arrive.
- HonomiTV now resolves this transport independently of broadcast entryReady,
  uses the existing same-origin external VFS and allowlisted server proxy,
  preserves network-disabled settings, cancels pending requests on exit, and
  identifies the receiver as A-PAB/1.0. A ready broadcast entry remains preferred.
- The real proxy router was requested in-process against the public broadcaster:
  startup HTML, portal HTML, portal.js and x_bjhc.min.js returned HTTP 200;
  a private-IP destination returned HTTP 403. This is HTTP acceptance, not
  browser rendering or deployed-service acceptance.
- Other live 4K services inspected serially: NHK BSP4K (1100101), BS Fuji
  (1100181), QVC (1100221) advertise carousel application entries. BS Nippon TV
  (1100141), BS Asahi (1100151), BS-TBS (1100161) supplied media but no application
  signalling in the approximately 20-second samples. This is a bounded sample,
  not a claim that they never broadcast applications. Shop Channel (1100211)
  disconnected during capture and again before HTTP response on retry; its
  application availability remains unverified. No recording files were retained.
- Focused URL/transport tests (7) and frontend lint/typecheck passed. Server
  Ruff/Pyright passed with 10 existing deprecation warnings. Actual HonomiTV
  D-button rendering, navigation and service-switch acceptance remain unverified:
  no local user-managed HonomiTV API was running during this check.

## Full merge verification (2026-10-28)

- Source validation from the previous integration: client lint/typecheck and 30 focused tests passed. The replacement merge reuses those source resolutions, performs no frontend build, and preserves the pre-merge dist tree.
- Server Ruff/Pyright passed (10 deprecation warnings); 30 focused playback, recording, CM and data-broadcasting tests passed, plus 2 capture-router ASGI checks. Final recording lifecycle/progress/responsiveness rerun: 11 passed.
- Installer lint/typecheck and lock validation passed. Nix server derivation evaluates; frontend dependency hash refreshed. Workflow syntax passes actionlint; full ShellCheck reports existing/upstream script warnings.
- Upstream migrations are numbered 18–20 after the fork migrations. Populated in-memory SQLite upgrade preserves an existing user; merged Aerich offline model state reports no changes. No user database was modified.
- No local user-managed API was running. Live HTTP, browser UI, real broadcast playback, Windows/Docker builds and deployment were not verified.

### BS TV Tokyo 4K CSP initialization failure (2026-10-29)

- The local broadband experiment reproduced an uncaught EvalError at
  `x_bjhc.min.js:58:62588`: the broadcaster initializes Greg/Ureg accessors with
  eval(), but the VFS document CSP only enabled legacy evaluation for NHK.
- Apply the same document-scoped compatibility setting to `hc.bsjhc.jp`.
  Other hosts, carousel documents, proxy destination validation and frame
  restrictions retain their existing policies. HTTP 200 alone did not validate
  broadcaster JavaScript initialization. Portal rendering after this fix remains
  pending browser confirmation.
