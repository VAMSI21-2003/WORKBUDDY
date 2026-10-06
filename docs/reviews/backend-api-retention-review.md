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

---

## Review round 2 (commit `6959db2`)

**Verdict (round 2, commit `6959db2`): MERGE.** M1 is fixed, and I confirmed the fix with my own evidence. Nothing is HIGH. The one open MEDIUM, M2, still can't be reached by an ordinary request. The fix adds three LOW findings, listed below.

Scope: the diff `4352d1c..6959db2` (`backend/retention.py` `remove_tree`, two tests, docs), the open findings from round 1 (M1 to L8), and regression against the base (`origin/main`, `dede5b8`). Probes and outputs are in `scripts/review_harness/feat-backend-retention/`, under "Round 2" in its `README.md` and in `out/r2_*`.

| Run | Result |
|---|---|
| `python -m pytest -q` (Python 3.11.15) | **2442 passed**, 280 s. That is round 1's 2440 plus the 2 new tests. |
| `ruff check buddy tests`, by file and rule against the base | Identical to the base. `ruff check backend tests/backend scripts/live_api_runs.py`: clean. |
| The 2 new tests on round-1 code (`4352d1c` worktree with head's `test_retention.py`) | Both fail. |
| `r2_mutations.py`: 5 mutations of the fix | 3 killed. 2 survived: "chmod every regular file", which is equivalent, and "chmod symlinks too" (R2-L2). `out/r2_mutations.txt` |
| `windows_readonly_sim.py` on head, without `--fixed` | The session is removed on the first sweep (`failed: 0`). Session and download are `404` afterwards. Round 1: `failed: 1` forever. `out/r2_windows_readonly_sim_head.json` |
| `r2_probe.py` w1 and w2, x 3 speakers, Windows rule, head and round 1 | Head: the two expired sessions are removed (`404` on session and download). A plan waiting across the sweep is kept, and after approval it is delivered. The session used at +23 h is kept: upload and delivered file still `0o444`, and a later turn on it is delivered. A sweep from disk by a second process removes the session. Round 1: `removed 0, failed 2`, and everything is still served. `out/r2_probe_head.jsonl`, `out/r2_probe_round1.jsonl` |
| `r2_probe.py` pf (Windows rule, plus one upload that can't be unlinked) and hl (a hard link) | See M2 (update) and R2-L1. |
| `r2_foreign_owner.py`: `remove_tree` run as `nobody` on a root-owned `0o444` file in its own folder | Head: `PermissionError` (EPERM from the chmod). Round 1: removed. See R2-L1. |
| `pr4_probe.py` (15 cases, 35 runs) again, compared with round 1 | Same results. The only differences are the friend opener picked from the catalog and the byte counts of c10 (±4 bytes, from zip timestamps). `out/r2_pr4_probe.jsonl` |
| `api_probe.py` (25 x 3), head and base | 73 of 75 the same. The other 2 differ only in polled status order (timing). `out/r2_api_probe_head_vs_base.txt` |
| `startup_probe.py` | Same as round 1, apart from the random ports. `out/r2_startup_probe.jsonl` |
| Rule checks (PR-3's `r2_rule_checks.py`) over the 120 head runs | 0 problems. `out/r2_rule_checks.txt` |

**Rule 1.** I checked every reply on a delivered turn against an openpyxl read of the delivered file (dedupe 4/0, chain 3/0/0, empty rows 5/0), and they all match. Undelivered replies claim nothing. The only misleading reply is M2's `integrity_failure` text. **Rule 2.** Every run that records the original's hash has the same SHA-256 before and after: 90 rows by the checker, plus every nested turn in `r2_probe`, which I checked by hand. The `pr4_probe` cases c05, c07, c08 and c10 to c15 record no hash, as in round 1: either retention deletes the stored upload on purpose, or the case runs no turn, or (c10) the probe doesn't record the hash. That last one is a gap in the probe. **Secrets.** No change since round 1: no token or password appears in any output.

**Design check.** The fix matches round 1's specification. One deviation: the design's PR-4 implementation note on the sweep (`backend-api-design.md`, "Uploads are read-only, so each folder is made writable first") doesn't mention that read-only files are now made writable too (R2-L3).

### Round-1 findings

- **M1: fixed** [Certain]. Evidence: the simulation, `r2_probe` w1 and w2 on head against round 1, and the new test failing on round-1 code. Not run on Windows itself.
- **M2: open, deferred** (still not reachable by an ordinary request). **Update** [Certain]: with M1 fixed, M2 can now happen on Windows from a sharing violation. A file in the session might be open in Excel straight from the data folder, or held by a scanner. Round-1 code stopped at the first read-only file and deleted nothing.
  - In `pf` (head, 3 speakers) the sweep deletes one upload, fails on the other, and puts the session back in service. The returning user's turn ends `integrity_failure` with "your original file no longer matches the one you uploaded", which is M2's misleading reply.
  - The surviving upload is also left `0o600`, no longer read-only (R2-L1).
  - Round 1 gives a delivered turn in the same case, because it deleted nothing.
  - The fix is unchanged. I'd take it next for a Windows deployment.
- **L1 to L7: open, deferred.** Their code is unchanged since round 1.
- **L8: fixed** [Certain]. The implementation notes now say base 2258, adds 182.
- **Correction to round 1's "For the owner":** backlog P2 (the `ydata-profiling` telemetry) is not open. `skills/excel/analyze.py` sets `YDATA_PROFILING_NO_ANALYTICS` (main's PR #11), and the backlog's own status line records that. [Certain]; I checked it by reading the code.

### New findings (LOW)

**R2-L1 ([Certain]; not reachable by ordinary use): chmod-before-unlink touches files it shouldn't, and leaves them writable when the unlink then fails.** Where: `backend/retention.py`, `remove_tree`, the non-directory branch.
- **Hard link** (`r2_probe` hl): a `0o444` file outside the data root that is hard-linked into a session's outputs is `0o600` after the sweep. The chmod changes the shared inode. Round 1 left it `0o444`.
- **Foreign owner** (`r2_foreign_owner.py`): on Linux, a read-only file that the server user doesn't own, in a folder it does own, can no longer be removed. The chmod raises EPERM, although the unlink alone would succeed (round 1 removed it). That session then fails on every sweep.
- **Failed unlink after the chmod** (`pf`): the file stays writable, in a session that M2 puts back in service.
- **Reach:** nothing in the app creates hard links or files owned by another user. The third case needs M2's I/O failure.
- **Fix** (prototyped in a scratch copy: `out/r2_proto_fix.diff`):
  1. Unlink first.
  2. Only on `PermissionError`, and only for a regular file without the write bit and with `st_nlink == 1`, chmod it to `S_IWRITE | S_IREAD` and retry.
  3. If the retry fails, restore the mode and re-raise.
- **Prototype results:**
  - `test_retention.py`: 53 passed.
  - w1 and w2: removed.
  - hl: the outside file stays `0o444`.
  - Foreign owner: removed.
  - pf: the surviving uploads stay `0o444`. M2 itself remains.

**R2-L2 ([Certain]): the symlink guard test can't fail on Linux.** `test_a_symlink_is_not_chmodded_when_its_target_is_read_only` passes with `stat.S_ISREG(mode) and` removed (mutation `chmod_symlinks_too`). A Linux symlink's own mode is always `0o777`, so `not mode & S_IWRITE` already excludes it. Fix: in the test, patch `backend.retention.os.lstat` so that the link reports `S_IFLNK | 0o444`, as a Windows link with the read-only attribute does. Or, with R2-L1's fix, test a hard link (`st_nlink == 2`) instead.

**R2-L3 ([Certain]): design note not updated.** In `docs/design/backend-api-design.md`, PR-4's implementation note on the sweep, add that read-only files are made writable before they are unlinked, because Windows refuses to delete a read-only file.

### Deferred

M2, L1 to L7 (round 1), and R2-L1 to R2-L3 above. R2-L1 to R2-L3 are added to `docs/backlog.md`.

### Not run

- **Windows.** M1's fix and R2-L1 were checked by simulating Windows' rule on Linux. Python 3.13, where `os.chmod` on Windows can follow symlinks, wasn't run.
- **Live runs with Groq.** They're the writer's job after this verdict (`scripts/live_api_runs.py`, including run S).
- **The full 46-mutation set from round 1.** It wasn't rerun because the code it covers is unchanged. Only the 5 mutations of the fix were run.
- **A real browser, a real reverse proxy, and stress runs of the sweep against in-flight requests.** As in round 1.
