# Bangumi playback completion

## Contract

- Reuse existing Web settings/history cloud sync and TV history checkpoints. Do not add a separate progress request, timer or client completion gate. Only an explicitly attached current playback time axis may trigger Bangumi updates; importing or merging historical lists alone does not. Save history before syncing Bangumi and isolate provider failures from history persistence.
- The backend owns completion policy. Web and Komorebi send the current playback position and resolved duration periodically; neither client calculates 90%, CM boundaries, channel rules or episode slots.
- CS333 / AT-X animation with an identified integer episode and a normal half-hour slot becomes eligible at 20:00. Anime identification requires the animation EPG genre; other channels and non-animation programmes retain the existing CM/90% policy.
- An explicitly identified contiguous range such as 1-6 or 7-12 on CS333 animation uses 30-minute slots. Only the current slot's episode is marked after its 20th minute: 20, 50, 80, 110, 140 and 170 minutes for six episodes. Seeking does not backfill skipped episodes. Unknown/ambiguous ranges or non-half-hour slots do not use this rule.
- Every episode must resolve uniquely against Bangumi before an external update. Range recordings keep their range metadata; no single-episode ID is written over it. Existing user/episode completion records deduplicate updates.
- Recording chase playback, unlinked accounts and ambiguous mappings never write watched state. Progress success for one episode must not stop reporting for subsequent episodes. No historical backfill, playback jump or next-episode timing change is included.
- The existing recorded_video.playback_completion_threshold response field remains for deployed clients using that public field. Owner: HonomiTV playback API. Dependency: released Web/TV clients. Remove only after those versions are no longer supported, review by 2027-04-14; CM/90% regression coverage remains at that compatibility boundary. Updated clients do not consume it.

## Acceptance

### Live viewing history

- Web and Android TV send the canonical broadcast channel ID and the wall-clock time of the advancing live picture as optional `live_playback_progress: {channel_id, playback_time}` (Unix seconds). Web attaches it to the existing `PUT /api/settings/client` and reads history through the existing settings pull; TV uses `PUT /api/settings/client/watched-history`. No second progress upload is added. Checkpoints run every 10 seconds of active playback, with the last observed checkpoint saved on pause/exit/channel switch. Paused, buffering and failed playback do not advance history.
- The server resolves recordings on that channel whose programme interval contains the picture time. Both `Recording` and `Recorded` rows qualify; a recording inserted after live playback starts is discovered by the next checkpoint. Unrecorded programmes create no history. Programme transitions use the picture time, including live buffer delay, rather than the latest EPG card.
- Resume positions are relative to the actual `recording_start_time`, including recording margins and partial recordings, and require a known start time. An unfinished recording's scanned duration must not truncate live progress. Completed recordings clamp to their actual recorded end/duration. Existing history limits, per-user ownership and last-update merge rules apply. Old clients and bulk-history payloads retain their existing behavior.
- This saves recording history only. Live checkpoints do not invoke Bangumi completion or fabricate a completed-recording duration. No schema migration, external provider write, new UI or historical backfill is included.
- The initial picture wall-clock is estimated from device time minus buffered media; subsequent checkpoints follow actual media-time advancement. This does not claim frame-accurate broadcast-clock synchronization and assumes a correctly synchronized device clock.
- Web respects the existing account/settings-sync opt-out. A playback session's pending checkpoint and response must not move into another account after logout or account switching.
- Verify authenticated settings/history PUT/GET using real SQLite rows: no recording, late insertion, recording margins/partial recording, missing start, completed recording, channel/programme transitions, invalid reports and independent users. Verify Web/TV media-time tracking, payloads and final checkpoints. Isolated HTTP regression is distinct from user-managed server, browser live-stream and physical-TV acceptance.

- Exercise authenticated PUT /api/settings/client and PUT /api/settings/client/watched-history (plus the retained compatibility POST /api/bangumi/videos/{id}/progress) for CS333 single episodes and explicit 1-6 / 7-12 ranges at just below and exactly 20-minute slot thresholds, slot transitions, seeks, duplicate updates and unresolved mappings.
- Preserve ordinary CM/90% results, authentication, provider failure propagation and cross-season resolution.
- Run server lint/type checks, direct policy/API regression tests, Web lint/type checks and TV history payload tests/compile. Report isolated HTTP tests separately from user-managed API and actual Bangumi-account/device acceptance.

- The old Bangumi progress POST remains only for released clients (same owner/dependency/removal condition/deadline as the threshold field). Updated Web and TV clients have no call to it; all entry points delegate to the same owning backend service.

## Live history verification (2026-10-18)

- Passed 6 isolated server HTTP regressions (5 live-history cases plus the existing history/Bangumi isolation case), using real in-memory SQLite, real JWT authentication and both Web settings / TV history PUT entry points with complete readback.
- Passed 12 focused Web tests covering payloads, media-time clocks, lifecycle checkpoints, pending-save conflation and account/sync boundaries; `yarn lint` and `yarn typecheck` passed.
- Ruff passed; focused Pyright for `WatchedHistory.py`, `SettingsRouter.py` and `schemas.py` passed. Full `uv run task lint` still reports the existing unrelated `TwitterScrapeBrowser.py:1849` unnecessary `isinstance` error.
- The user-managed API was not running locally. No service was started/restarted, and browser live-stream / physical Android TV acceptance and deployment remain unverified.
