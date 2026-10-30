# EPGStation recording API integration

## Current scope

- The server exposes the configured EPGStation API through fixed routes under `/api/recording/epgstation`. EPGStation owns reservation and rule IDs, matching, duplicate detection, scheduling, recording and encoding options.
- The bridge covers configuration/channels, reservation listing and state changes, rule CRUD and enable/disable, schedule search/detail and current recordings. Native request and response fields are preserved; upstream failures retain their status and a sanitized reason with a stable `EPGSTATION_*` code.
- Program resolution requires an exact network/service/channel and start/end-time match. Ambiguous matches fail explicitly rather than selecting a guessed program ID.
- The EPGStation frontend added in this task was rolled back at the user's request on 2026-10-30. Its route, components, service client, tests and changes to existing screens were removed. Existing frontend behavior remains as before this task; the retained API does not imply frontend recording support.
- No database schema changes, user database writes, service restarts or deployment are part of this integration.

## Verification

- `nix-shell -p uv --run 'uv run python -m unittest tests.test_epgstation_router'` passed five ASGI/controlled HTTP tests. Native payloads/statuses, exact program identity, ambiguous matches, error reasons/redaction and malformed upstream JSON are covered.
- `uv run task lint` passed with no errors; eight existing FastAPI deprecation warnings remain.
- Live application acceptance remains unverified: the local HTTPS API on port 7000 timed out during the implementation. No API service was started/restarted and no real reservation/rule was modified.
