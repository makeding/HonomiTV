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

- Exercise authenticated PUT /api/settings/client and PUT /api/settings/client/watched-history (plus the retained compatibility POST /api/bangumi/videos/{id}/progress) for CS333 single episodes and explicit 1-6 / 7-12 ranges at just below and exactly 20-minute slot thresholds, slot transitions, seeks, duplicate updates and unresolved mappings.
- Preserve ordinary CM/90% results, authentication, provider failure propagation and cross-season resolution.
- Run server lint/type checks, direct policy/API regression tests, Web lint/type checks and TV history payload tests/compile. Report isolated HTTP tests separately from user-managed API and actual Bangumi-account/device acceptance.

- The old Bangumi progress POST remains only for released clients (same owner/dependency/removal condition/deadline as the threshold field). Updated Web and TV clients have no call to it; all entry points delegate to the same owning backend service.
