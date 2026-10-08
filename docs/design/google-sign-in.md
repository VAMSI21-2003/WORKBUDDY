# Sign in with Google

Status: design for `feat/google-sign-in`. Replaces the shared demo password in production. Security work: any path by which one person's data reaches another is HIGH.

Owner decisions (not reopened): Google only (OpenID Connect, authorization-code flow with PKCE, done on the server, a maintained library pinned in the locks); anyone with a verified Google email may sign in, unless the optional `WORKBUDDY_ALLOWED_EMAILS` is set; store only Google's subject id and the email; files are still deleted after the configured idle time; one VM behind Caddy.

## Library

`PyJWT[crypto]` (signature, `exp`, `iss`, `aud`) and `httpx` (already a runtime dependency) for Google's discovery document, JWKS and token endpoint. State, nonce and PKCE are our own few lines (`backend/oidc.py`), tested by a fake identity provider that signs real RS256 tokens. Both pinned in `requirements-runtime.lock` with hashes.

## Routes (HTML flow, not in `openapi.json`, all declare their rate-limit buckets)

| Route | What it does |
|---|---|
| `GET /auth/google/start` | Makes `state`, `nonce` and a PKCE verifier; keeps them in memory for 10 minutes (bounded, single use); sets `__Host-wb_login` (Secure, HttpOnly, SameSite=Lax, a random binder whose hash is kept with the pending login); `302` to Google (`scope=openid email`, `prompt=select_account`, `code_challenge_method=S256`). |
| `GET /auth/google/callback` | Takes the pending login (so a second use of the same `state` fails first), checks `state` and the binder cookie, exchanges the code (with the verifier and the client secret), then validates the ID token: RS256 only, signature against Google's JWKS (cached 1 h; one refetch per 60 s for an unknown `kid`), `iss`, `aud` (and `azp` if several), `exp`/`iat`, `nonce`, `sub`, `email`, `email_verified is true`, and the allowed list. Every failure is the same page ("Sign-in didn't work"), except an email not on the list. Success sets the access cookie, clears the login cookie, and answers `200` with a page that moves to `/` (a redirect would be a cross-site chain and the SameSite=Strict cookie would not be sent on it). |
| `POST /auth/signout` | Ends the server-side sign-in, clears the access and session-pointer cookies, `303` to `/`. Cookie-authenticated, so the Origin check applies; works with an already-dead cookie. |

The redirect address is `WORKBUDDY_PUBLIC_ORIGIN` + `/auth/google/callback`, built from the setting only, never from `Host` or any header. No route takes a "return to" value: after sign-in the browser always goes to `/`. There is no open redirect to build.

## The cookie and the sign-in session

The existing `__Host-wb_access` (Secure, HttpOnly, SameSite=Strict, Path=/, a session cookie) carries a random 256-bit token. `AccessControl.check` stays the one decision behind `check_credential`; it now returns a `Principal` with the answer (`kind`, an `owner` id, and for a person the email). Owner ids: `google:<sub>`; `operator`; `demo` (non-production password); `dev` (no credential, dev only).

A sign-in is a file `<data>/auth/signins/<sha256(token)>.json` (`owner`, `email`, `issued_at`, `expires_at`; the token itself is never stored), loaded into memory at start-up, so a release does not sign anyone out. It expires after `WORKBUDDY_ACCESS_TTL_HOURS` (12 h, not renewed), at most `WORKBUDDY_ACCESS_MAX_TOKENS` are kept (soonest-expiring evicted), expired files are swept with the existing maintenance loop, and sign-out deletes the file. With an allowed list set, a sign-in is valid only while its email is on the list, checked on every request.

`Authorization: Bearer <WORKBUDDY_OPERATOR_TOKEN>` (new, at least 32 characters) is the operator: scripts and `deploy/smoke.sh`. It is a principal like any other and reaches only sessions it created.

**Production.** Requires the Google client id and secret and an https origin. `WORKBUDDY_DEMO_PASSWORD` is not read for authentication: `POST /api/v1/access` always answers the one `401`, a Bearer demo password is refused, and a set value logs one warning at start-up (refusing to start would turn an upgrade with an old `.env` into a rollback). **Not production:** the demo password still works, and `WORKBUDDY_ENV=dev` with nothing configured is open as today (owner `dev`).

## Ownership

Every session has one owner. Where it is kept: `sessions/<key>/owner.json` = `{"version": 1, "owner": "<id>" | null, "session_id": "<id>"}`, written before `session.json` at creation and never rewritten. `session.json` is unchanged (version 1), so an older server still opens the sessions and ignores the file (rollback works).

`SessionRegistry.authorize(session_id, principal)` is the one check: id shape, not being deleted, and the owner equal (constant-time) to the principal's. `registry.get` needs a principal and starts with it, so no code path gets a record from an id alone. A missing, unreadable or `null` owner means nobody. The same `404 session_not_found` comes back for someone else's session, a missing one and a badly shaped id.

Because the gate sees the matched route's path parameters, `RequestGate` calls `authorize` for every route with a `{session_id}` in its path, API (`/api/v1/sessions/...`) and page (`/ui/s/...`), after the credential and before the body is read, so a route cannot forget it. `session_of` checks again. The guard test builds its cases from the app's route table and fails for any `{session_id}` route that answers person B differently from a session that does not exist, including a route added only inside the test that never calls `session_of`. `GET /` treats another person's pointer cookie as a missing session.

The session id is now written to `owner.json` (it was never on disk). That is a deliberate change: the id used to be the only credential, now a request also needs its owner, so the file is a way back to "your most recent session". `GET /` without a usable pointer asks `registry.latest_for(owner)` (an in-memory index built from the `owner.json` files, then kept up to date) and lands the person in the session used last, else makes a new one.

## Limits per person

New buckets `user_turns` and `user_uploads`, same shape as the per-IP ones (10/h and 20/h by default, `WORKBUDDY_RATE_USER_TURNS_*`, `WORKBUDDY_RATE_USER_UPLOADS_*`), keyed by the owner id and charged next to the per-IP buckets in the same all-or-nothing step. A refusal costs nothing, as before.

## Settings

`WORKBUDDY_GOOGLE_CLIENT_ID`, `WORKBUDDY_GOOGLE_CLIENT_SECRET` (never in `repr`, logs or errors), `WORKBUDDY_ALLOWED_EMAILS` (comma-separated, case-insensitive; empty = any verified email), `WORKBUDDY_OPERATOR_TOKEN`, `WORKBUDDY_OIDC_ISSUER` (default `https://accounts.google.com`; for the fake provider in tests and live runs only).

## Saved data and the migration

Sessions written before this change have no `owner.json`. **Decision: they belong to nobody.** At start-up the migration writes `owner.json` with `"owner": null` for each (explicit, so the state is visible and counted in the log), leaves `session.json` and every file untouched, and the registry never loads them. No one, including the operator, can open them; claiming by session id is refused, because a demo-era id may have been shared. The ordinary retention sweep deletes them once idle for `WORKBUDDY_SESSION_TTL_HOURS`; nothing can touch them, so they are gone within that time of the release. `tests/deploy/fixtures/data-v1` stays as it is; `data-v2` (`make_data_fixture.py 2`) holds an owned session and a sign-in record. `test_saved_data_survives` proves that v1 data still parses, is readable by nobody and is swept, and that v2 opens for its owner and for no one else. Sign-in files are new, so they need no migration.

## The page

The gate is a "Continue with Google" link (a link, not a form: `form-action 'self'` would block the redirect to Google); outside production a password form is shown too when a demo password is set. The header shows "Signed in as <email>" and a "Sign out" button (a plain form `POST`), and a "New session" button (`POST /ui/sessions`; the swap guard lets this one request replace the session). These are labels only; frontend design Section 4 holds and `ui_copy_snapshot.txt` is updated. A `401` notice says "Sign in again". After sign-in the person lands in their most recent session, or a new one.

## Out of scope

Other providers, Google Workspace domain rules, per-person storage quotas, a list of a person's sessions, deleting an account, refresh tokens or calling Google APIs after sign-in, claiming pre-change sessions, multiple workers (still one process), real-Google testing from the build environment (the owner does it, steps in `docs/deploy.md`).
