# EPGStation integration in existing recording flows

## Current scope

- The server exposes the configured EPGStation API through fixed routes under `/api/recording/epgstation`. EPGStation owns reservation and rule IDs, matching, duplicate detection, scheduling, recording and encoding options.
- The bridge covers configuration/channels, reservation listing and state changes, rule CRUD and enable/disable, schedule search/detail and current recordings. Native request and response fields are preserved; upstream failures retain their status and a sanitized reason with a stable `EPGSTATION_*` code.
- Program resolution requires an exact network/service/channel and start/end-time match. Ambiguous matches fail explicitly rather than selecting a guessed program ID.
- Use the existing `/recording/reservations`, `/recording/conditions` and `/programs/search` contracts with EPGStation backend branches. The existing reservation homepage supplies upcoming, the existing list supplies all reservations and the existing detail drawer supplies recording actions. Do not add pages, routes, forms, a separate dashboard or a parallel frontend service.
- Keep EDCB-specific preset acquisition and settings on the EDCB branch. On EPGStation, the same settings component exposes the supported enable/disable and data-broadcast settings; existing native save/encode/tag options remain owned by EPGStation and survive common-field edits. Unsupported search/rule options must produce an explicit reason rather than silently changing their meaning.
- Preserve canonical channel/program identity, native rule options and actual recording state across the branch. Request every affected existing API entry point during verification; mocked green tests alone do not establish live-service acceptance.
- Program attributes absent from native time reservations remain unknown: `Program.is_free` permits null instead of inventing free/paid status, and EID 65535 marks unavailable event identity. Real canonical program metadata retains its values. Missing size estimates occupy the existing size slot as unknown.
- No database schema changes, user database writes, service restarts or deployment are part of this integration.

## Verification

- Existing-flow verification: five controlled ASGI checks in `tests.test_epgstation_common_api` requested the existing reservation list/detail/add/update/cancel, condition CRUD, search and timetable entry points. They verify complete program/channel responses, native recording ID separation, preservation of rule options and missing attributes. Together with the five native bridge checks, all ten passed.
- Three mounted checks in the existing `ReservationRecordingSettings` component verify EPGStation manual/automatic applicability, retained EDCB settings and the EDCB-only preset boundary. Client ESLint and TypeScript passed. The router and reservation homepage/list source files remain unchanged.
- `nix-shell -p uv --run 'uv run python -m unittest tests.test_epgstation_router'` passed five ASGI/controlled HTTP tests. Native payloads/statuses, exact program identity, ambiguous matches, error reasons/redaction and malformed upstream JSON are covered.
- `uv run task lint` passed with no errors; eight existing FastAPI deprecation warnings remain.
- Live application acceptance remains unverified: the local HTTPS API on port 7000 timed out during the implementation. No API service was started/restarted and no real reservation/rule was modified.
