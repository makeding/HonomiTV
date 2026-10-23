# Selective upstream integration

## Scope

- Port playback stability fixes from KonomiTV through `ddbc50c4`: default 60fps selection, MediaSession metadata readiness, subtitle teardown, Document Picture-in-Picture recovery, EPG detail preservation, disconnect-aware file responses, and HLS failure details.
- Add online and offline HLS secondary audio, update mpeg2toh264 without replacing this fork's DPlayer, and support same-stem WebVTT sidecars when a recording has no embedded subtitles.
- Move server, installer, Docker, CI, Nix packaging and development instructions from Poetry to uv. Preserve the Python 3.11 runtime and current application dependencies; require Python 3.11.4 or newer for filtered tar extraction.
- Keep tracked `client/dist` under the upstream release workflow; do not generate or commit a local dist in this integration.
- NFS polling is deferred. DPlayer version changes are a separate task. Preserve device-local time display, Raw TLV as the initial BS4K choice, recording chase playback, and the existing TLV notification contract.

## Observable playback contract

- Standalone data services (`service_type=0xC0`) outside terrestrial one-seg are selectable in the broadcast channel list, including BS SID 791. Their type comes from the last successful Mirakurun/EDCB channel discovery, refreshed before startup accepts traffic and on normal channel updates; unavailable metadata defaults to ordinary broadcast behavior without changing stored records or requiring a database migration.
- Data-only viewing receives the original decoded MPEG-TS directly from Mirakurun/EDCB into the existing web-bml decoder. It never starts an encoder, waits for a video frame, or subscribes to video-task events. BML remains enabled for this channel even when optional data broadcasting on video channels is disabled. The existing remote and player restart control remain usable; loading ends on BML load, and receive/render timeouts show a reason and adjacent retry action without automatic video restarts. Leaving or switching channels cancels reception and releases the owned tuner.
- Entering a standalone data channel automatically opens the viewing panel and remote on the Program tab. This does not overwrite saved panel preferences. Leaving restores the previous tab/remote state, and the user can still close the remote or panel while viewing the data service.
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
