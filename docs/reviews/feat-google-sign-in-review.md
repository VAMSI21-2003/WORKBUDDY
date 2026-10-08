# Review: `feat/google-sign-in`, round 1 (commit 948ec6c, base origin/main 4b7dec2)

**Verdict: DON'T MERGE.** One HIGH (H1: outside production, a second holder of the demo password lands in the first holder's session). The fix is a one-line condition and a test; fix L1 (the CI lint job is red) in the same round.

Probes, outputs and how to rerun them: `scripts/review_harness/feat-google-sign-in/` (README there).

## What was run

| Run | Result |
|---|---|
| `python -m pytest -q` (head) | 2814 passed, 5 skipped, 1 failed: `test_a_process_that_is_not_root_can_delete_read_only_uploads`, which is on base too and can't pass as root (this sandbox is uid 0). `out/suite_and_lint.txt` |
| `ruff check buddy tests`, per file and rule, head vs base | 2 new: `tests/deploy/test_deploy_files.py` UP031, `tests/deploy/test_smoke_auth.py` E501. `scripts/check_ruff_baseline.py` (the CI job) exits 1. `ruff check backend`: 5 new E501 (base is clean). L1 |
| 25 scenarios x 3 speakers through the page, dev mode, head vs base (`../feat-frontend-demo/ui_probe.py`) | 75/75 rows each. Replies, outcomes, plans, delivered names and links are identical. The only difference is the statuses a poll happened to catch in `03_dedupe_timeout`, which is timing. Rule checks: 0 problems. The original is unchanged in 75/75 |
| Same 75 runs as a person signed in with Google on production settings, then a second person against the session (`signed_in_probe.py`) | 0 probe errors, 30 delivered, 45 not delivered. Rule checks: 0 problems. The original is unchanged in 75/75. Bob got the unknown-id answer, byte for byte, on all 12 session routes in 75/75 runs. None of Alice's reply, file names or message appeared in anything Bob got. With Alice's pointer planted, Bob's `GET /` showed nothing of hers and cleared the pointer. Replies equal base's except the same timing-only statuses |
| 23 adversarial cases (`adversarial.py`) | 22 held, 1 failed (c18, H1). Held: alg confusion (HS256 with the public key), RS512, aud lists with and without azp, a missing or prefixed nonce, `email_verified: 1`, nbf in the future, Google's issuer spelling on a non-Google issuer, a kid with `../`, a wrong key. Also held: login CSRF, replay, callback junk with no reflection, the allowed list (including removal across a restart), restart paths with Bob first, damaged owner files (all mean nobody), v1 data (nobody, operator included; swept; files untouched at start), rollback to base and forward (c09: base opens head's sessions by id as it always did; sessions made during the rollback belong to nobody afterwards), operator isolation, the password refused in production, the per-person limit across two addresses, log redaction, a hostile email (escaped), sign-out from a foreign or missing Origin (403), Bob answering Alice's plan (404, the plan still waits, Alice's turn delivers: reply "4 row(s), 0 duplicate" against openpyxl 4 rows, 0 duplicates; original unchanged), file ids across sessions, a recycled email, 12 h expiry, landing after a sweep |
| Mutations (`mutations.py`): 34 one-line mutations in `git archive` copies | M00 passes. 27 killed by the 13 sign-in and ownership files. M06 survives as an equivalent mutant (PyJWT's `algorithms=["RS256"]` still applies). M32 is killed by `tests/backend` as a whole. M08, M12, M19, M26 and M30 survive even `tests/backend` + `tests/web` + the saved-data test (L2). `out/mutations*.txt` |
| New test files on base code | 58 failed, 8 errors, 3 passed. The 3 that pass are generic layout checks in `test_saved_data_survives.py` (a fixture exists, no path outside `session.json`, empty folders ignored), which should pass on base. `out/new_tests_on_base.txt` |
| Rule 2 | The original's SHA-256 was unchanged in every run that had an upload: 75 + 75 + 75 page runs, c06, c09, c15 |

**Design deviations** (`docs/design/google-sign-in.md`):
1. The design doesn't say that `latest_for` applies to the shared `demo` and `dev` principals. `backend/identity.py` says these modes are one owner, "what those modes always were". That isn't so: before this change, one holder of the password couldn't reach another's session (H1).
2. "The page" says Sign out is "a plain form POST". The Routes table and the code use htmx (L5).
3. `WORKBUDDY_OIDC_ISSUER` is described as for the tests and live runs only, but production accepts any https issuer (L5).
4. `azp` is checked whenever it's present, not only when there are several audiences. That's stricter, which is fine.

## Findings

**H1 [Certain]: outside production, a second holder of the demo password is put into the first holder's session.** `backend/web/routes.py`, `index()`: `session_id = registry.latest_for(principal.owner)` runs for every principal, including the shared `demo` and `dev`.
- Scenario (c18): `WORKBUDDY_ENV` empty, `WORKBUDDY_DEMO_PASSWORD` set. Person 1 enters the password, uploads `payroll_private.xlsx` and runs a turn. Person 2, in a fresh browser, enters the same password and opens `/`.
- Result: `/` renders person 1's session. The file name and session id are in the page, with the conversation and download links, and person 2's pointer cookie is set to it. On base, `/` with no pointer renders an empty workspace and the page makes a new session.
- Reachability: production can't reach it, because there are no `demo` or `dev` principals there [Certain]. A non-production server binds 127.0.0.1, so it takes people sharing one machine, or a proxy the project doesn't ship. `README.md` and `docs/deploy.md` still describe the mode for "local trials and scripts".
- Why HIGH: one person's data reaches another.
- Fix: use `latest_for` only when `principal.kind == "google"` (prototype: `out/proto_fix_h1.diff`).
  - With the prototype, `tests/backend`, `tests/web` and the saved-data test pass (927).
  - c18 then holds, and c06, c20 and c23 still pass.
- Add a test: two demo-password browsers, and two dev browsers. The second one's `GET /` must show none of the first's conversation and set no pointer.
- Correct the `identity.py` docstring.

## Deferred (LOW; in `docs/backlog.md`)

- **L1 [Certain]: new lint errors.** The CI job `ruff (no errors beyond the baseline)` fails.
  - In tests: `tests/deploy/test_deploy_files.py:52` UP031 and `tests/deploy/test_smoke_auth.py:65` E501.
  - In backend: E501 at `backend/gate.py:174`, `backend/retention.py:236`, `backend/sessions.py:271` and `:292`, and `backend/web/common.py:1`.
  - The implementation record says there are no new errors.
  - Fix: an f-string and wrapped lines. CI is red until then, so fix it with H1.
- **L2 [Certain]: guard tests miss five mutations.** Each survives `tests/backend` + `tests/web` + the saved-data test. Add these tests:
  - M08: a pending login is no longer single use. The replay test only fails because the fake provider's code is single use. Assert that the second callback makes no `POST /token`.
  - M12: the callback keeps the last person's pointer. Assert that the callback's response deletes `__Host-wb_session`.
  - M19: `latest_for` ignores the owner. Add a landing test where Bob's session is newer than Alice's and Alice lands in hers.
  - M26: the discovery document's `issuer` isn't checked. Serve a discovery document with another issuer and assert that it's refused.
  - M30: the email isn't lower-cased. Make the provider return an upper-case email and assert that the stored email is lower case.
- **L3 [Certain]: a refused callback breaks a sign-in still in progress.** `backend/web/auth.py` `_refused` clears `__Host-wb_login` on every refusal, `no_pending_login` included.
  - Scenario (c03): "Continue with Google" is started twice in one browser (two tabs), or a stray link to `/auth/google/callback` is opened. Finishing the older one fails and deletes the binder, so the newer one fails too: both answer 400, "Sign-in didn't work. Try again."
  - A retry works.
  - Fix: don't clear the login cookie on `no_pending_login`.
- **L4 [Certain]: the implementation record misstates two things.**
  - It says the breakage of `scripts/live_api_runs.py`, `live_frontend_runs.py` and `measure_container.py` is "recorded in the backlog". It isn't.
  - It calls `scripts/live_stack/` new. It's on main, and `stack_flow.py:72` still signs in with the demo password, so it can't drive a production stack of this branch.
  - Fix: add the backlog entry, and switch `stack_flow.py` to the operator token.
- **L5 [Certain]: the design note is out of step with the code.** Correct the sign-out sentence in "The page". Say that production accepts `WORKBUDDY_OIDC_ISSUER`, or refuse a non-Google issuer in production.

## Not run

- Real Google: the owner's checklist.
- Live runs with Groq: this branch doesn't touch `buddy/`, `core/` or `skills/`, and the writer does them after MERGE.
- The compose stack and `deploy/smoke.sh` against a built image of this branch. Only the suite's `tests/deploy/test_smoke_auth.py` ran.
- My own Chromium probes of the new header, sign-out and New session. The suite's `tests/e2e/test_signin_browser.py` ran and passed (11 with `test_smoke_auth.py`).
- Mutations of `app.js` (the New-session swap exception).
- The full suite on base.
