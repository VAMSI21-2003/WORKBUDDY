# feat/release-safety: live runs

Run after the round 2 verdict (MERGE, commit 7cda772), on this branch's head (only documents and the live-run driver changed after the reviewed commit; nothing under `buddy/`, `core/`, `skills/`, `backend/` or `deploy/` did). Raw output of every run: `docs/reviews/feat-release-safety-live-runs/`. Driver: `scripts/live_stack/stack_flow.py`.

**What these runs are.** The real `deploy/docker-compose.yml` stack (the app image and Caddy) on this sandbox machine, driven over **HTTPS through Caddy** with the **real Groq LLM**. **What they are not:** an Azure VM, a public name, or a Let's Encrypt certificate. The stack was named `localhost`, so Caddy used its own private certificate authority (the driver and `smoke.sh` trust it, for the run only). The container reaches Groq through this sandbox's egress proxy by way of a relay (`scripts/live_stack/`, sandbox only; a real VM talks to `api.groq.com` directly). No Groq 429 appeared in the app's log. Real-LLM turns in all: 8 (4 smoke tests inside releases, 4 driver turns), every one delivered.

## Run 1: first release of the head, fresh stack

`./deploy/release.sh HEAD` on a machine with no stack and no volume: build, start, healthy, smoke. `RELEASED 6b452b595beb`, 15 s with a warm build cache. Smoke: health OK (an LLM key is set), sign-in exchanged for the cookie, upload (the server's hash equals the file's), the plan shown (`spreadsheet.deduplicate`), the turn delivered, the download's hash equals the recorded one and differs from the original's. (`run1-first-release.txt`)

## Run 2: the full flow through Caddy (real Groq)

Fixture `deploy/smoke.xlsx`: 6 rows, 2 exact duplicates, 1 empty row. Original SHA-256 `45f300dd1801ec0a…` (local, as the server recorded it, and the server's stored copy read from the data volume) **before and after every turn: identical.**

| | 2a: "remove duplicate rows" (focus) | 2b: "remove duplicate rows, then remove empty rows" (friend) |
|---|---|---|
| intent | `remove_duplicate_rows` | `multi_step_request` |
| `clarification_required` | false | false |
| steps run | `analyze`, `deduplicate`, `analyze` (recheck), all `success`, `verified` | `analyze`, `deduplicate`, `remove_empty_rows` (reads step 1's output), `analyze` (recheck), all `success`, `verified` |
| plan | shown once, approved | shown once (both steps), approved |
| file delivered | 1 | 1 |
| independent check (pandas) | 6 rows to 4; contents equal to `drop_duplicates()`; 0 duplicates left | 6 rows to 3; contents equal to dedupe then `dropna(how="all")`; 0 duplicates left |
| download hash = recorded hash | yes | yes |
| reply | "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. … I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%)." | "… Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. … None of the issues I check for were found." |
| false claim? | none: the 4 rows are Alice, Bob, an empty row, Carol, so one missing name and one missing amount of 4 is 25.0% | none: 3 rows (Alice, Bob, Carol) match the file |

The first 2b printed "contents equal: False". I opened the file: Alice/Bob/Carol with amounts 10/20/30, exactly right; my driver compared pandas dtypes (the original's empty row makes its numbers floats, the delivered file holds integers). I fixed the driver to compare values and ran 2b again (a second real turn; both outputs are in `run2-flows.txt`). The same driver's comparison then failed on a NaN cell in run 4a's file; I checked the file, found the data identical, fixed the NaN handling, and re-checked that file (`run4a-recheck.txt`). Both were faults in my checker, not in the server; they're stated here because the first output said "False".

## Run 3: checklist item 7 on the stack (`./deploy/check-proxy.sh`)

Three throwaway clients with their own addresses on a temporary network that Caddy joins; wrong-password sign-ins (the `access` bucket: 10 in a burst, then 429). (`run3-check-proxy.txt`)
- **As shipped (`WORKBUDDY_TRUSTED_PROXY_HOPS=1`): `CHECK-PROXY OK`, 6 of 6.** A: 10 answered (401), then 429. B (another address): all 401. C claiming A's address in `X-Forwarded-For`: all 401 (A's bucket isn't charged to C). A claiming a fresh address, or sending a made-up chain: still 429. B claiming a fresh address: still 401.
- **Controls, the setting wrong on purpose: the check fails.** Hops 0: 3 of 6 fail (B, C and B again are all 429: one shared bucket, Caddy's address). Hops 2: the same 3 fail. Put back to 1: OK again.
- The app isn't published: `docker port` is empty, `http://127.0.0.1:8000` is refused from the host, port 80 answers `308` to `https://`; both containers have `restart=unless-stopped`, the app a 1 GiB and Caddy a 256 MiB memory limit, `json-file` logs of 5 files of 10 MB (`run3b-exposure.txt`).

## Run 4: an upgrade, with data kept

The previous version is what `main` is today (47ef562, the old Dockerfile with unpinned requirements): built as `workbuddy:47ef56242629`, started under the stack, and a real session made on it (run 4a: upload, "remove duplicate rows", approved, delivered, 6 rows to 4, original unchanged; session `s_MOj30F…`, delivered file `d_t0001_1`, SHA-256 `9dd4bddf…`). Then `./deploy/release.sh HEAD` (`run4b-upgrade.txt`):
- build, **backup** (`workbuddy-data-20261007-091927.tar.gz`), start, healthy, smoke OK; `RELEASED 2051e554d16f`; the running image is `workbuddy:2051e554d16f`, the old one is kept for rollback, `releases.log` says `OK (was 47ef56242629)`.
- **The session and its file are still there:** `GET session` 200, the file registry lists `report.xlsx` with the same hash, `t0001 finished delivered`; the delivered file downloads with SHA-256 `9dd4bddf…`, identical to the one before the upgrade, and the pandas check on it passes (6 rows to 4).

What this does and doesn't show: the application code of `main` and of this head is the same (nothing under `backend/`, `buddy/`, `core/` or `skills/` changed on this branch), so this shows the mechanism (the volume, the backup, the image swap, health and smoke) with data written by the previous release, not a data-format migration. Format compatibility is what the fixture tests cover (`tests/deploy/test_saved_data_survives.py`).

## Run 5: failed releases roll back by themselves

Two deliberately broken commits, made in a throwaway worktree on local branches that were never pushed and are deleted now. Both start from the live release `2051e554d16f` with the site answering `{"ok":true,"llm_configured":true}` `HTTP 200`.
- **5a, an app that exits at start** (`raise SystemExit(3)` in `backend/__main__.py`): the build and backup succeeded, "dependency failed to start: container workbuddy-app-1 is unhealthy", **`!! RELEASE a4f990d9684c FAILED at: starting the containers`, "Rolling back to 2051e554d16f"**, the old image's smoke (a real turn) OK, **`!! ROLLED BACK to 2051e554d16f … The site is serving the previous release.`** Exit status 1, 26 s. After: the site answers `HTTP 200`, the running image is `2051e554d16f`, `releases.log` has the failure and "rolled back … smoke ok". (`run5a-failed-release-crash.txt`)
- **5b, an app that is healthy but can't create a session** (`create_session` raises): healthy, then smoke fails at `POST /api/v1/sessions` ("HTTP 500 internal_error"), **rolled back to `2051e554d16f`**, smoke OK, exit status 1, the site `HTTP 200` after. (`run5b-failed-release-smoke.txt`)

## Rule checks

- **Rule 1** (no false claim): 4 turns' replies read against the delivered files (2a, 2b twice, 4a): no false claim. The 3 smoke turns inside releases were checked by hash and outcome only.
- **Rule 2** (the original is never modified): the local fixture, the upload's recorded hash and the server's stored copy had the same SHA-256 before and after every driver turn (2a, 2b twice, 4a).

## Not run, and what these runs left unobserved

- Azure, a public name, Let's Encrypt, `install.sh` on a real VM (it was run in an `ubuntu:24.04` container with the kernel-level commands stubbed, in the implementation notes), the Azure appendix.
- The workflow on GitHub, the 2 MB two-step chain on a host (checklist item 6).
- A data-format migration (none exists yet).
- A failed release leaves its image on the machine (only a successful release removes older images): after run 5 the two broken images were removed by hand. Noted in `docs/backlog.md`.
