# Implementation notes: `feat/backend-retention` (PR-4 of the backend API design)

**Branch:** `feat/backend-retention`, from `origin/main` (`dede5b8`, PR-3 merged as #13; `backend/confirmation.py` is on it).
**Design:** `docs/design/backend-api-design.md`, PR-4 and PR-4a (Section 8.3 item 4), with every "Decisions" section applied (later sections win): "Decisions (2026-09-30)" item 1; "(2026-09-30, part 2)" item 1 and ask 4; "(2026-10-01)" items 1, 2, 3 and 7. The implementation notes added to the design with this PR record how.
**Also in this PR:** two items from the PR-3 review's Deferred list, R2-L1 (an approved step could start after the provider closed) and R2-L5 (`WORKBUDDY_TURN_DEADLINE_S` had no upper bound), and a line in `CLAUDE.md`'s "Decided behaviour" recording the owner's confirmation of PR-3's chain change.
**Rule-touching:** yes. The credential, the Origin check and the limits decide who reaches the API that carries both rules; retention deletes the user's files, and the product advertises it (`retention_hours`), so an unenforced promise would be a false claim.
**Not in this PR:** the HTML routes and the frontend (it provides `require_access`, `limits(...)` and the `page` bucket for them), real sign-in (`AccessControl` is the one place to replace), and the rest of the PR-3 review's Deferred list.

Everything below is the writer's account. Treat it as unverified.

## What changed

| File | Change |
|---|---|
| `backend/settings.py` | `env`, `demo_password` (never in `repr`), `public_origin` (normalized), `access_ttl_hours`, `access_max_tokens`, `trusted_proxy_hops`, `cors_origins`, `rate_limits` (`BucketSpec` per bucket), `session_ttl_hours`, `sweep_interval_s`, per-session quotas, `max_total_bytes`; every startup refusal; `bind_host`, `retention_hours`; R2-L5's cap on the turn deadline; env variables for all of it |
| `backend/access.py` (new) | `check_credential`, `AccessControl` (`check`, `exchange`), `TokenStore`, the Origin check, `require_access` (the router-wide dependency) and the `POST /api/v1/access` handler |
| `backend/ratelimit.py` (new) | `RateLimiter` (token buckets), `limits(...)` route declarations, `check_route_declarations`, `charges_for` / `after_response`, `client_ip` |
| `backend/gate.py` (new) | The ASGI middleware that charges, checks the credential and the Origin before any body is read |
| `backend/retention.py` (new) | `RetentionSweeper`, `remove_tree`, `StorageMeter`, `MaintenanceLoop` |
| `backend/routes.py` | `require_access` on the router; every route declares its buckets; the access route; the 401, 403, 429 and 503 declarations; upload quotas and the storage cap |
| `backend/sessions.py`, `backend/store.py` | Last-use time (kept in memory, written to disk at most once a minute); the registry's claim and release calls for the sweep |
| `backend/turns.py` | The turn quota |
| `backend/confirmation.py` | R2-L1 |
| `backend/main.py`, `backend/middleware.py`, `backend/__main__.py` | The gate, CORS, HSTS and `frame-ancestors 'none'`, the maintenance loop at startup; binding by environment |
| `backend/errors.py`, `backend/schemas.py`, `backend/views.py`, `backend/logging_filter.py` | New error codes (`unauthorized`, `forbidden_origin`, `rate_limited`, `session_quota_exceeded`, `storage_full`); `retention_hours` from the settings; redaction of the cookie and a password |
| `backend/openapi.json` | Regenerated: the access route, bearer and cookie schemes, 401/403/429/503 on the routes |
| `scripts/live_api_runs.py` | Production-like server settings and the cookie flow for every run; runs A, W, O, L and S |
| `README.md`, `docs/backlog.md`, `docs/design/backend-api-design.md`, `CLAUDE.md` | Documentation, and the design's "Implementation notes, recorded with PR-4" |

## Design requirements and where they're tested

See the design's implementation notes for the interpretations. The tests are in `tests/backend/test_access.py` (credential, access route, token store, Origin, redaction, startup refusals), `test_ratelimit.py` (the ten tests of part 2 item 1 under their numbers, and item 3's), `test_retention.py` (sweep, quotas, cap, binding, CORS, docs off, headers), `test_openapi_sync.py` (the contract), `test_confirmation_provider.py` (R2-L1) and `test_app_basics.py` (R2-L5).

## Deviations from the design, and interpretations

1. **The gate is a middleware as well as a dependency** (FastAPI parses a body before it resolves dependencies; measured). Design notes, item 1.
2. **A wrong credential costs only 2 `general` tokens**, not the route's own buckets. The access route's wrong password is charged after the handler (the limiter can't read the form first), which gives the same total.
3. **The storage cap also gates uploads**, not only new sessions, and its default (2 GiB) is a placeholder: the design leaves the number to the host.
4. **`retention_hours` is `integer | number | null`**, because the TTL may be fractional (the live run uses a short one).
5. **Docs endpoints are never served**, in any environment.
6. **`session_quota_exceeded` is `403`**; the design names no code for the quotas.
7. **`Settings` defaults to `env="dev"` when built in code**; `from_env` defaults to unset, which needs a password.
8. **Part 2's "third poll stream" test** uses the 2 s cadence the revised frontend polls at.
9. **The `page` bucket has no route in this PR.** Its declaration and a test route stand in for the frontend's.
10. **The existing API tests run on `RELAXED_LIMITS`**, because they poll every 20 ms on the real clock, which the real buckets would refuse. The tests of the limits use the real numbers and an injected clock.

## Results

- `python -m pytest -q` (Python 3.11.15): **2440 passed**, 285 s. `origin/main` (`dede5b8`) gave 2258 passed in the reviewer's run (this note first said 2241, copied from PR-3's notes; review round 1, L8), so this branch adds 182 tests.
- `ruff check buddy tests`: 22 errors, the same count as `origin/main`. `ruff check backend tests/backend scripts/live_api_runs.py`: clean.
- `python -m backend.export_openapi --check`: in sync (tested by `test_openapi_sync.py`).
- The new R2-L1 test fails on `origin/main`'s `backend/confirmation.py` and passes on this branch's.
- Not run by the writer: live runs (they follow a MERGE verdict), Python 3.13, Windows. The one non-root test (`test_a_process_that_is_not_root_can_delete_read_only_uploads`) runs only as root, by dropping to uid 65534.
