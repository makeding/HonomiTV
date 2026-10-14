# Bangumi personal token rotation

## Contract

- Linking exchanges the supplied valid personal token for a dedicated 30-day token using `POST https://next.bgm.tv/demo/access-tokens` with Bearer authentication and `{name, days: 30}`. Verify the successor belongs to the same Bangumi user before encrypting it in the existing credential field. Never return or log tokens.
- Check linked accounts at startup and hourly using `POST https://bgm.tv/oauth/token_status`. Renew at seven days remaining or less; also replace historical tokens with more than 30 days remaining. Every successor issues the next token. No OAuth app secret, cookie, browser session, schema migration or client update is required.
- Failed checks, issuance, validation or persistence preserve the saved credential for the next hourly attempt. Isolate accounts, serialize local renewals, and conditionally replace the exact saved credential so concurrent unlink/relink wins. Other user writes must not overwrite the rotated credential.
- Do not revoke supplied or previous tokens: another application or an in-flight request may use them. They expire naturally. Already expired/revoked credentials, or a server offline beyond the token lifetime, require relinking.

## Acceptance

- Cover 30-day issuance, repeated successor issuance, renewal thresholds, restart with existing encrypted credentials, network/provider/invalid-response failures, concurrent renewal and unlink/relink, encrypted persistence and absence from User responses.
- Exercise authenticated link/logout and the existing progress/history HTTP boundaries. Distinguish mocked provider proof from real Bangumi-account issuance and user-managed API acceptance.

## Provider sources

- [Issuance handler](https://github.com/bangumi/server-private/blob/master/routes/demo/token/index.ts): `name`, `days`, JSON string response; expiry is calculated from issuance time, independently of the caller's expiry.
- [Bearer authentication](https://github.com/bangumi/server-private/blob/master/routes/hooks/pre-handler.ts): demo routes accept access tokens before session authentication.
- [Token status](https://github.com/bangumi/api/blob/master/docs-raw/How-to-Auth.md): `POST /oauth/token_status` returns `user_id` and Unix `expires`.
