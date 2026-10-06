# Review: `feat/backend-retention` (PR-4: access, rate limits, retention, quotas)

**Verdict (round 1, commit `4352d1c`): DON'T MERGE.** One MEDIUM that ordinary use can reach is M1: on Windows the retention sweep never deletes a session, while every session response advertises `retention_hours: 24`. The fix is one line, given under M1. There is nothing HIGH.

Base: `origin/main` (`dede5b8`). Design: `docs/design/backend-api-design.md` (PR-4 rows, the "Decisions" sections, and "Implementation notes, recorded with PR-4"). The probes and their outputs are in `scripts/review_harness/feat-backend-retention/`; its `README.md` says how to rerun them.

## What I ran

| Run | Result |
|---|---|
| `python -m pytest -q` (Python 3.11) | Head: **2440 passed**, 271 s. Base: 2258 passed, 257 s. |
| `ruff check buddy tests`, by file and rule against the base | Identical to the base: nothing new. `ruff check backend scripts/live_api_runs.py`: clean. |
| PR-3's `api_probe.py`: 25 turns x 3 speakers, head and base, buckets relaxed (`old_probe.py`) | 70 of 75 the same after stripping the seeded catalog lines. The other 5 differ only in polled status order (timing). All 75 originals byte-identical. `out/api_probe_head_vs_base.txt` |
| `pr4_probe.py`: 15 cases (10 of them x 3 speakers), production-like settings, real buckets, injected clocks | Every flow (cookie, Bearer, reject, chain) has no `429` at the 2 s cadence, a delivered file that matches the reply's counts (openpyxl: 4 rows/0 duplicates; chain 3/0/0), and an unchanged original in every run. The cookie with no, foreign, `null` or trailing-slash Origin gets `403` on every attempt. Retention after delivery, during a waiting plan (kept) and across restarts (kept at +25 h, removed at +48 h) all behave as specified. Quotas, the cap, guessing (2 `general` per wrong password, the 11th exchange `429`, `Retry-After: 180` exact), a spoofed `X-Forwarded-For` (ignored), hostile cookies and Bearer values (`401`), paths around the routes (no handler runs) and a gate that raises (`500`, nothing created) all behave as designed. Findings M2 and L1 come from c08 and c14. `out/pr4_probe.jsonl` |
| `windows_readonly_sim.py`: Windows' read-only rule applied to `os.unlink` | Three sweeps 25 h apart: `failed: 1` each time, every folder still there, session `200`, download `200`. With the one-line fix: removed on the first sweep. `out/windows_readonly_sim*.json` |
| `startup_probe.py`: `python -m backend` under 15 environments | Refused with a named setting and no value or traceback: no password, a public `--host` in dev (`0.0.0.0`, `::`), production without an origin, an `http` origin, an origin with a path, `Production`, an empty password, CORS `*`, a deadline of 3601, a poll refill below the cap. Production binds `0.0.0.0`, everything else `127.0.0.1`. The password is never printed. `WORKBUDDY_SWEEP_INTERVAL_S=1e10` starts, then shows a traceback (L1). `out/startup_probe.jsonl` |
| R2-L1, deterministic (PR-3's `r2_l2_probe.py --only unit_close`) | Head: step 1 is refused after the close. Base: step 1 is allowed. Fixed. |
| New tests on the old code (head's `test_confirmation_provider.py` and `test_app_basics.py` on a copy of the base) | The R2-L1 test and both R2-L5 deadline cases fail on the base. The new modules (`access`, `ratelimit`, `retention`) don't exist on the base. |
| `mutations.py`: 46 one-line mutations of the new guards, `tests/backend` | 41 killed, 5 survived. 3 of the survivors are redundant on purpose: the two Origin checks on the access route, which back each other up, and `_remove_one`'s key check, whose keys are always key-shaped already. The other 2 are test gaps (L3). `out/mutations.txt` |

**Rule 1.** I checked every reply the probes produced (the 75 `api_probe` turns and every `pr4_probe` turn, the failure paths included) against an independent openpyxl read of the delivered file, or against what ran. None claims anything that didn't happen. The reply text is the same as the base's. The one misleading reply is M2's, and it isn't reachable by ordinary use. The API's own messages are accurate: the quota, storage, rate-limit and `401`/`403` texts. **Rule 2.** Every run's original upload has the same SHA-256 before and after. **Secrets.** The placeholder password and every token stay out of every response body. The password never appears in startup output, and only its SHA-256 is held in memory.

## Design check

The ten deviations listed in `backend-api-retention-impl.md` are all recorded in the design's PR-4 implementation notes, and none contradicts an owner decision. I found no undocumented deviation. Two notes on how the promises hold in practice:
- Deletion happens within the TTL plus one sweep interval (5 min by default).
- The advertised retention holds only while the maintenance thread runs. See L1.

## Findings

### M1 (MEDIUM, [Likely]; reachable in ordinary use on Windows, blocking): on Windows the sweep never deletes anything, while `retention_hours: 24` is advertised

- **Where:** `backend/retention.py`, `remove_tree`. The file branch calls `os.unlink(path)` on the file as it is. Every upload is stored `0o444` (`backend/uploads.py:131`), and so is every delivered file (`backend/delivery.py:116`).
- **Why it fails:** on Windows, `os.chmod(f, 0o444)` sets the read-only attribute, and `DeleteFileW`, so `os.unlink`, refuses a read-only file (`PermissionError`, WinError 5) until the attribute is cleared. That is the documented behaviour of Python's `os.chmod` on Windows and of `DeleteFile`.
- **What happens:** `remove_tree` makes the folders writable but not the files. So the first upload file stops the removal, and `sweep()` reports `failed` on every pass, forever. Nothing is deleted. The session stays restorable and downloadable, and `retention_hours: 24` is served. The design says this is the false claim to avoid: "a retention promise that isn't enforced is a false claim in the product's own voice".
- **Reach:** any session on a server run on Windows. That is the owner's machine (`CLAUDE.md`), and it is the README's `WORKBUDDY_ENV=dev python -m backend` path. Live run S (`scripts/live_api_runs.py`) would show it there.
- **Evidence:** `windows_readonly_sim.py`, which applies the Windows rule on Linux. Not run on Windows (see "Not run"). The writer's notes also list Windows as not run.
- **Fix:** in `remove_tree`'s non-directory branch, clear the read-only bit on a regular file before unlinking it. Never chmod a symlink:
  `if stat.S_ISREG(mode) and not mode & stat.S_IWRITE: os.chmod(path, stat.S_IWRITE | stat.S_IREAD)`
  Then add a test that applies the Windows rule (as the simulation does) and asserts the session is removed. `windows_readonly_sim.py --fixed` shows this fix removing the session on the first sweep.

### M2 (MEDIUM, [Certain]; not reachable by an ordinary request, deferred): a sweep that fails partway puts a half-deleted session back in service

- **Where:** `RetentionSweeper._remove` (`finally: self._registry.release(key)`) together with `SessionRegistry.claim_idle`, which drops the record from memory.
- **Scenario:** case c08, 3 speakers. The uploads are removed, then removing the outputs raises.
- **What happens:** the next request restores the session from disk.
  - `GET …/files` still lists `report.xlsx` as an upload.
  - A new turn ends `integrity_failure` with "I can't vouch for this result: your original file no longer matches the one you uploaded. I've held back every file from this turn." The server deleted the upload, so this misleads, and the user's original was never changed.
  - The use counts as activity, so the next sweep, an hour later, keeps the session: `removed 0`. The half-deleted session survives another full TTL after every visit.
- **Reach:** needs an I/O or permission error during removal.
- **Fix:** on a failed removal, keep the key out of service. Don't `release`; keep a set of failed keys that every sweep retries whatever their activity. Alternatively, write a tombstone in `sessions/<key>` before deleting anything, and have `load_session` refuse a tombstoned session. Test: fail the outputs removal, then show that `GET` is `404` and the next sweep finishes the job.

### LOW (deferred)

- **L1 ([Certain]) `sweep_interval_s` has no upper bound.**
  - Above `threading.TIMEOUT_MAX`, the maintenance thread runs one pass and then dies with `OverflowError` (c14; `startup_probe` `sweep_interval_1e10`). Retention then stops silently while `retention_hours` is still advertised. This is the same class as R2-L5, which this PR fixed for the deadline.
  - An interval above the TTL is also accepted: with `1e6` and a TTL of 1 h, the server advertises 1 h and sweeps every 278 h.
  - Fix: refuse `sweep_interval_s` above `min(3600, session_ttl_hours * 3600)`. Optionally, report `retention_hours: null` when the maintenance thread isn't alive.
- **L2 ([Certain]) Every rate-limit bucket is per full IPv6 address** (`client_ip` returns the `/128`). A caller with an ordinary `/64` has 2^64 fresh sets of `turns`, `uploads` and `access` buckets, so the per-IP cost control doesn't hold for IPv6. Fix (an owner call): key IPv6 clients by their `/64`.
- **L3 ([Certain]) Two guard tests are missing** (mutations):
  - Removing `require_access`'s own `401` survives. The gate covers it today, but for a router route declared `credential=False` the dependency is the only check. Add a test that a route on `router` declared `credential=False` is still `401` without a credential.
  - Allowing one byte past `max_session_upload_bytes` survives. Add the exact-boundary test.
- **L4 ([Certain], code reading) The route checks see only `APIRoute`s.** `check_route_declarations` and the gate skip any other route (`_is_api_route`). A Starlette `Route` or `Mount` added under `/api/v1` (`app.add_route`, `app.mount`), or a websocket route, would get no credential check and no startup refusal. So "a new route can't forget it" holds only for `APIRoute`s. Fix: refuse any non-`APIRoute` route whose path starts with `/api/v1`, and any websocket route.
- **L5 ([Certain], code reading) The dev public origin is fixed.** It is `http://127.0.0.1:8000` whatever the `--port` or host name, and the access route checks the Origin even in dev. So a browser on `http://localhost:8000`, or on any other port, gets `403` from `POST /api/v1/access`. This matters to FE-1. Fix: derive the dev default from `--port` and accept `localhost`, or document it.
- **L6 ([Certain]) The storage cap's coverage.**
  - Outputs aren't checked against the cap. A turn on a full server still writes (c10: 14.4 KB used on a 7 KB cap). This is as documented, so it is noted only.
  - `StorageMeter.used_bytes` walks the whole data root inside the `async` upload handler, which blocks the event loop at most once every 2 s. Move the walk to a thread, or into the maintenance loop.
- **L7 ([Certain]) Deployment footgun.** With `WORKBUDDY_TRUSTED_PROXY_HOPS=0` behind a reverse proxy, every user shares the proxy's buckets (10 turns an hour for the whole demo), and nothing warns. Add a startup log line in production when the hop count is 0, or a deployment-checklist item.
- **L8 ([Certain]) A wrong count in the writer's notes.** `backend-api-retention-impl.md` gives the base as 2241 tests and "adds 199". The base runs 2258, so the branch adds 182.

## Deferred

M2 and L1 to L8 above. They are added to `docs/backlog.md` under "backend-api-retention review (round 1), deferred".

**For the owner:** backlog P2 (the `ydata-profiling` telemetry) is still open. It says "before any shared deployment", and PR-4 is what makes a shared deployment possible.

## Not run

- **Windows.** M1 is simulated, not executed on Windows. Python 3.13 also wasn't run.
- **Live runs with Groq.** They are the writer's job after a MERGE verdict, and `scripts/live_api_runs.py` was not run.
- **A real browser.** Not run: the `__Host-` prefix, `Secure`, `SameSite=Strict`, and whether `Origin` is sent on same-origin POSTs. The TestClient sends the headers by hand.
- **A real reverse proxy.** Not run: uvicorn's own `proxy_headers` handling (it trusts `127.0.0.1` by default) combined with `WORKBUDDY_TRUSTED_PROXY_HOPS`.
- **Races between the sweep and in-flight requests.** These were checked by reading the code (every lookup touches the session under the registry lock, and a claim needs the record's lock and an idle record), not by stress runs.
