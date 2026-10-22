# Recording chase playback

## Observable contract

- Recording list/detail duration follows observed media growth rather than the initial metadata duration or elapsed wall-clock time. Playback exposes only the safely readable media boundary; completed recordings retain their final duration.
- Dynamic progress and early covers use the existing MPEG-TS PCR/DTS tracking path. MP4 and MMT/TLV retain their existing metadata fallback until a format-specific readable-media probe is available; they must not be treated as verified growing timelines.
- Around five minutes into the program, excluding recording lead-in, generate an early representative cover once the corresponding media has actually been written. Five minutes is a selection heuristic, not proof that commercials have ended.
- Extract only a few candidate frames from a stable written region behind the growing edge, with bounded input and execution time. Do not copy the entire recording, lock the writer, or run the full completed-file analysis on a growing recording.
- Allow one early-cover job per recording. Retry transient extraction failures automatically; retain a successful cover until final recording analysis replaces it. Publish complete images atomically.
- Completion, source replacement, and hash changes must prevent stale early-cover jobs from overwriting the final cover. Completed-file analysis continues to produce the final cover and seek thumbnails.
- Async duration/image updates preserve card geometry and focus. Existing playback position is retained during metadata refresh.

## Validation

- Exercise increasing and stalled media progress, initial metadata fallback, completed recordings, and relevant list/detail responses.
- Exercise the five-minute threshold with lead-in, incomplete tails, bounded extraction, retries, duplicate scheduling, and completion/publication races.
- Run server lint/type checking and relevant regression tests; run client lint/type checking if client code changes.
- Request affected endpoints on the existing user-managed application when available, and verify growing-file playback/cover behavior with an available recording. Do not start or restart the API without authorization. Report unavailable real-file or browser evidence separately from automated checks.

## Local verification (2026-10-22)

- Server focused regression: 11 tests passed, covering media progress/fallback/replacement, safe playlist edge, thumbnail candidates, actual FFmpeg extraction, and scanner task lifecycle.
- The extraction fixture switches from red to blue and checks that a later target produces blue. It appends data after the recorded input boundary and verifies the decoder input remains bounded. This is synthetic media evidence, not acceptance against a live broadcast recording.
- Mounted client regression: 4 tests passed, covering refresh overlap, unmount cleanup, late initialization, and stale playback-session responses.
- Server Ruff/Pyright and client ESLint/TypeScript passed; server has existing deprecation warnings.
- Real application HTTP, growing broadcast recording playback, and browser interaction remain unverified: no local API process was running, and the local database was empty. No server was started or restarted, and no release build or deployment was performed.
