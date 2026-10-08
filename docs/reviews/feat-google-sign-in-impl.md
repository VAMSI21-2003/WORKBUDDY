# feat/google-sign-in: implementation record

Written by the writer, before review round 1. Design: `docs/design/google-sign-in.md`. Base: `origin/main` at `4b7dec2`. Nothing here is verified by anyone but the writer.

## What changed, by file

| Area | Files |
|---|---|
| Identity | `backend/identity.py` (the `Principal`: `google:<sub>`, `operator`, `demo`, `dev`) |
| Settings | `backend/settings.py`: `WORKBUDDY_GOOGLE_CLIENT_ID/SECRET`, `WORKBUDDY_ALLOWED_EMAILS`, `WORKBUDDY_OPERATOR_TOKEN`, `WORKBUDDY_OIDC_ISSUER`; production needs Google and ignores the demo password (one warning); three new rate buckets |
| The flow | `backend/oidc.py` (state/nonce/PKCE, discovery, JWKS cache, token checks), `backend/web/auth.py` (`/auth/google/start`, `/auth/google/callback`, `/auth/signout`), `backend/web/templates/{gate,signed_in,workspace,_copy}.html`, `static/{app.js,styles.css}` |
| Sign-ins | `backend/signin_store.py` (hash of the token under `/data/auth/signins/`, loaded at start), `backend/access.py` (`AccessControl.check` returns a principal; `sign_in`, `sign_out`; the demo password is a credential outside production only) |
| Ownership | `backend/sessions.py` (`SessionRegistry.authorize`, `get(id, principal)`, `create(mode, owner)`, `latest_for`), `backend/store.py` (`owner.json`, `migrate_ownership`), `backend/gate.py` (the 404 for every `{session_id}` route), `backend/routes.py`, `backend/web/routes.py` (`GET /` and the pointer) |
| Per-person limits | `backend/ratelimit.py` (`user_turns`, `user_uploads`, `signin`; a charge may carry a key), `backend/gate.py` |
| Logs | `backend/logging_filter.py` masks the callback's `code`/`state`, the login cookie, JWT-shaped strings, `GOCSPX-…` and `/ui/s/<id>` |
| Deploy | `deploy/{.env.example,docker-compose.yml,lib.sh,smoke.sh,install.sh,compose.sh}`, `docs/deploy.md`, `README.md` |
| Dependencies | `requirements-runtime.txt` + both locks: `pyjwt[crypto]` (adds `cryptography`, `cffi`, `pycparser`; every other pin unchanged, checked) |

## Deviations from the design note, and decisions made while writing

- **The session id is now on disk** (`owner.json`). The old rule "never written to disk" was a property of the id being the only credential. Two tests that pinned it were changed to say "only in `owner.json`" (`test_sessions.py`, `test_store.py`). This is the one change to an earlier invariant; the design note says why (landing in the most recent session needs the id).
- **Sign-out is an htmx request**, found by the browser test: a plain form POST sends `Origin: null` under `Referrer-Policy: no-referrer` and got `403 forbidden_origin`.
- **A refusal before the body**: the ownership 404 now comes before the body-size 413 and before a route's own 4xx for a session that isn't the caller's, because the gate answers first. `test_a_body_over_the_cap_is_refused_413_and_still_charged` was changed to use a real session of its own.
- **`ErrorCode.UNAUTHORIZED`'s message** is now "You aren't signed in, or your sign-in has ended." (it said "The demo password is missing or wrong."). `backend/openapi.json` was regenerated.
- **`test_the_access_class_is_the_only_place_that_knows_the_password`** allows `sessions.py` and `oidc.py` to use `compare_digest` (owner, id, nonce, binder).
- **`locked_settings` (tests) is no longer production**: the password isn't a credential there. `production_settings` is the new production helper.
- A probe of someone's session spends the prober's `user_turns` token, not the owner's (the limiter charges every request whatever the answer).
- Not changed: `scripts/live_api_runs.py`, `scripts/live_frontend_runs.py` and `scripts/measure_container.py` authenticate with the demo password against a production-mode server, so they no longer log in. Recorded in the backlog (it was not, until round 1's L4). `scripts/live_stack/` is not new: it is on `main`, and `stack_flow.py` was changed in round 1 to use the operator token.

## Saved data

`sessions/<key>/owner.json` (new, `{version, owner|null, session_id|null}`) and `auth/signins/<sha256>.json` (new). `session.json` and turn records are byte-for-byte the old format (`FORMAT_VERSION` stays 1), so an older server still opens every session and ignores both new things. A session folder with a `session.json` and no `owner.json` is marked `owner: null` at start (`SessionStore.migrate_ownership`): nobody can open it, nothing else in it is touched, and the retention sweep deletes it when idle. `tests/deploy/fixtures/data-v1` is unchanged (not touched by this branch: `git diff origin/main -- tests/deploy/fixtures/data-v1` is empty); `data-v2` was made with `python scripts/make_data_fixture.py 2`.

## Tests

New: `tests/backend/test_signin.py` (60 cases: every flaw in `tests/idp/provider.py` `FLAWS`, wrong state, binder, replay, expiry of a pending login, key cache and rotation, allowed list, sign-out, restart, expiry, production without a password, the operator), `test_ownership.py` (the matrix built from the route table; a probe route that forgets the check; broken owner files; landing), `test_person_limits.py`, `test_signin_settings.py`, `tests/web/test_signin_page.py`, `tests/e2e/test_signin_browser.py` (Chromium, the fake Google on `localhost` and the app on `127.0.0.1`, so the way back is cross-site), `tests/deploy/test_smoke_auth.py`; `tests/idp/` is the fake provider (real RS256 tokens, single-use codes, PKCE, client secret).

Mutations tried by the writer, each caught: gate never checks ownership; `authorize` always passes; owner compared by prefix. Not tried: the OIDC checks one by one (each has a case in `test_a_token_that_is_wrong_in_any_way_signs_nobody_in`).

## Numbers at the time of writing

Baseline on `origin/main` (clean worktree): 2657 passed, 5 skipped, 1 failed (`test_a_process_that_is_not_root_can_delete_read_only_uploads`, which cannot pass as root). This branch before round 1: 2814 passed, 5 skipped, 1 failed (the same root-only test). Ruff: no error beyond `ruff-baseline.txt` (22 known) *as of the full run*; two errors were added afterwards in `tests/deploy` (round 1, L1).

## Not done / not verifiable here

Real Google. The owner's checklist is `docs/deploy.md` Section 7, step 9.
