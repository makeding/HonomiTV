# Device pairing page

- The binding page uses a plain, centered responsive column; it has no enclosing card, elevation or nested action tray.
- Reuse the account settings page's current user avatar, name and User ID. Sign out uses the existing user store and preserves the pairing code and login return path.
- Reserve an account slot while fetching the profile. Loading, signed-in, signed-out and profile-fetch failure are visible; failure has an adjacent retry.
- The approval action is a filled, full-width button. Invalid codes, pending requests and completed approval cannot submit. A successful approval retires the editable code; failures retain it for correction/retry.
- Verify profile/sign-out, login return, pending duplicate prevention and terminal behavior in component regression tests; inspect narrow and desktop layouts separately.

## Commit validation (2026-10-14)

- Client ESLint and TypeScript passed. All four Pair component tests passed, covering profile-fetch failure, sign-out/code/login-return preservation, pending/terminal duplicate prevention and approval-failure recovery.
- Desktop/narrow browser inspection and real device approval have not been verified in this commit check. Component tests use mocked user and approval services.
