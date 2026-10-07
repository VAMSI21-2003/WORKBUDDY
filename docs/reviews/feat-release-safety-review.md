# Review: `feat/release-safety` (round 1, commit 0ea40c4)

**Verdict: DON'T MERGE.** One HIGH: `restore.sh` can delete the backup it was asked to restore, then empty `/data` and leave the site down (H1). There are also five MEDIUMs (M1 to M5), and M1 means the suite fails on a fresh checkout, including the CI this branch adds.

Base `origin/main` (47ef562). No design document. Nothing under `buddy/`, `core/`, `skills/` or `backend/` changed. Probes, how to rerun them, and full outputs: `scripts/review_harness/feat-release-safety/` (README, `out/`).

## What I ran

| Check | Result |
|---|---|
| `python -m pytest -q` in this checkout | 2613 passed, 1 skipped |
| The same suite on a **fresh clone from GitHub** at 0ea40c4 | **1 failed**, 2612 passed: `test_what_the_server_writes_today_has_the_shape_of_the_newest_fixture` (M1) |
| `ruff check buddy tests` vs base, per file and rule | Identical, 22 errors. `check_ruff_baseline.py` with the locked ruff 0.16.10 finds no new errors |
| Locks | The runtime lock equals the recorded image freeze, plus `setuptools` and 3 platform-marked packages. A `--no-cache` image build installs from the lock, `pip check` is clean, and its `pip freeze` is byte-identical to the freeze (86 lines). The dev lock installs into a clean 3.11 venv and `pip check` is clean |
| In-process pipeline, 25 scenarios x template/friend/focus, head vs base (`ui_probe`) | No difference apart from poll timing in `03_dedupe_timeout`, which also varies between runs on the head. `rule_checks`: 150 rows, 0 problems. The original was unchanged in 75 of 75 rows |
| The deploy stack (compose + Caddy + image), LLM faked: 12 cases through Caddy in friend and focus (smoke file, no duplicates, hostile text and formulas, a rejected plan: 8 turns; two sheets, not a workbook: 4 uploads) | Every reply's counts match my openpyxl read of the delivered file (2 removed / 4 left; 0 / 3; 1 / 2; missing-value percentages correct). Two sheets and the bad file got `422`. A rejected plan delivered nothing. Every upload's SHA-256 in the volume was unchanged. No file text reached a reply. Rule 1: 0 false claims. Rule 2: 8 of 8 originals unchanged |
| `release.sh`: first release, same commit again, build fails, app can't start, smoke fails, good release | Correct, except M2. Build failure: nothing changed. Start or smoke failure: rolled back, smoke OK, exit 1, `releases.log` right |
| `check-proxy.sh` | 6 of 6 with hops 1. Fails 3 checks with hops 0 and with hops 2 |
| `backup.sh` / `restore.sh` | Normal round trip OK (sessions back, owner 10001, smoke OK). A corrupt archive is refused with nothing changed. **H1** |
| Guard mutations (`mutations.py`, 15) | With the fixture's empty folder recreated, all 14 real mutations fail a test (session or turn key added, type changed, `FORMAT_VERSION` bumped, reader stricter, a saved context key, Dockerfile unlocked or unhashed, unpinned requirement, range past the lock, hash mismatch, lock kept out of the build context, a new ruff error, baseline compare blind to new files). On a plain `git archive` copy, the unmutated tree already fails (M1) |
| New tests on base | The lock and deploy-file tests fail (19 failed, 6 errors). The saved-data tests pass, as they should: the format didn't change |

## Findings

**H1 (HIGH) [Certain]: `restore.sh` deletes the archive it's restoring, then empties `/data`.** `deploy/restore.sh` lines 24 to 31 and `deploy/backup.sh` lines 36 to 40.
- **Scenario:** the backup folder holds `WORKBUDDY_BACKUP_KEEP` archives (7 by default; the nightly cron in Section 7 step 10 plus one per release attempt fills it within a week), and the owner restores the oldest one, for example the last good one before a bad release. `restore.sh` first runs `backup.sh`, whose pruning deletes that oldest archive. Then `docker run -v "$archive:/backup.tar.gz:ro"` finds no file, so Docker creates an empty directory in its place. Then `find /data -mindepth 1 -delete` runs, and `tar` fails.
- **Result** (`out/d_restore_oldest.txt`, with KEEP=2): the asked-for archive is gone (a directory has its name), `/data` has 0 entries, the app is stopped, and the script exits 2 with tar's "Cannot read: Is a directory". It doesn't say that `/data` is empty, that the backup is gone, or which archive holds the pre-restore data.
- **Fix:**
  - Copy the archive to a private temporary file before the pre-restore backup.
  - Make the pre-restore backup skip pruning, for example with a `WORKBUDDY_BACKUP_PRUNE=0` that `backup.sh` honours.
  - Mount with `--mount type=bind,src=...,readonly`, which fails on a missing source instead of creating a directory.
  - Unpack into a staging folder on the volume, and replace `/data`'s contents only after `tar` succeeds.
  - On any failure, print the pre-restore archive's name and the exact `restore.sh` command that puts it back.
  - A prototype of the first and third points restored the oldest archive correctly (`out/d06b_restore_oldest_with_fix.txt`). It still pruned the archive afterwards, hence the second point.

**M1 (MEDIUM) [Certain]: the saved-data guard fails on every fresh checkout, CI included.** `tests/deploy/data_scenario.py`, `layout()`.
- **Cause:** the scenario's rejected turn leaves an empty folder, `outputs/<key>/t0002`. Git doesn't store empty folders, so a clone's fixture lacks it, and `layout()` lists folders. The test passes only in the implementer's working tree.
- **Result:** on a fresh GitHub clone, 1 failed and 2612 passed. The `tests` job in `.github/workflows/tests.yml` is a fresh checkout, so it would be red. The implementation notes' "2613 passed" holds only in the implementer's working tree.
- **Fix:** have `layout()` list files only (`if not path.is_file(): continue`). Prototype: the copy passes 7 of 7, and the guard still fails when the server writes an extra file.

**M2 (MEDIUM) [Certain]: after a failed first release, rerunning it says "already the live release. Nothing to do." and exits 0 while the site is down.** `deploy/release.sh` lines 27 to 30 and 56 to 60.
- **Cause:** the no-previous rollback stops the app but leaves `current.env` naming the failed tag.
- **Scenario:** Section 7 step 8 says "Fix the cause and run the same command again", and its table lists the usual first-release causes: DNS, ports, and the key.
- **Result** (`out/d_first_release_fails.txt`): exit 0, the app exited, `/healthz` doesn't answer.
- **Fix:** after `compose stop app` in that branch, `rm -f "$DEPLOY_DIR/current.env"`. Also make the same-tag shortcut require the app container to be running and healthy.

**M3 (MEDIUM) [Certain]: a `.env` value that compose rewrites breaks every release, and a piece of the password is printed.**
- **Cause:** `env_value` (`deploy/lib.sh`) reads `.env` literally. Compose interpolates `$name` and strips quotes, inline ` #` comments, trailing spaces and `\r` (checked with Compose v5.6.0).
- **Scenario:** `WORKBUDDY_DEMO_PASSWORD=Correct$horse-battery-staple-91`. Step 6 forbids spaces and quotes, but not `$`, and the owner may choose their own password.
- **Result** (`out/d_dollar_password.txt`):
  - The app's password is `Correct-battery-staple-91`, so the password in `.env` is refused on the page.
  - `smoke.sh` gets 401, the release rolls back, and the previous release's smoke also gets 401 (exit 2).
  - Every compose command prints `The "horse" variable is not set`, which is part of the password.
- **Fix:** `require_settings` refuses (naming the setting, never the value) any value containing `$`, a quote, `\r`, leading or trailing whitespace, or ` #`. Step 6 says the same.

**M4 (MEDIUM) [Certain that it's logged; Possible that it's misused]: Caddy's access log records session ids.** `deploy/Caddyfile` lines 15 to 18.
- **Cause:** `backend/logging_filter.py` treats a session id as a bearer secret and masks it in paths. The new `log` block makes Caddy write every request URI to the Docker log, 50 MB kept.
- **Result:** 22 lines with `/api/v1/sessions/s_…` in Caddy's log after one smoke run, against 0 in the app's log.
- **Risk:** anyone who gets that log (Section 7 sends the owner to `compose.sh logs caddy` for troubleshooting) plus the shared password can open another visitor's session and download their files.
- **Fix:** drop the `log` block (Caddy still logs errors). Or keep it with `format filter` and replace `request>uri` with a regexp masking `/sessions/[^/]+` and `/ui/s/[^/]+`.

**M5 (MEDIUM, not reachable until `deploy/` changes) [Certain]: a release builds the commit's image but runs the checkout's `deploy/` files.**
- **Cause:** `release.sh`, `compose.sh`, `smoke.sh`, the compose file and the Caddyfile come from the working tree. The documented flow is `git fetch` then `./deploy/release.sh origin/main` (Section 8), which never updates the working tree.
- **Result:** a later release that adds a setting, a volume or a Caddy rule under `deploy/` silently runs under the old files, and a rollback doesn't undo `deploy/` changes. That's the owner's stated goal ("later upgrades must not break it") left uncovered.
- **Fix:** `release.sh` refuses when `git diff --quiet HEAD "$full" -- deploy/` fails, and tells the operator to `git checkout <commit>` (or `git pull`) first. Section 8 says the same.

## Deferred (LOW; added to `docs/backlog.md`)

- **L1:** `smoke.sh` line 110 prints "it's different from the original's" before line 111 checks it.
- **L2:** `smoke.sh`'s check of the original compares the local fixture, which the server never touches. The server's copy of the upload is only checked by the server's own `uploads_unchanged`. Hash it in the volume after the turn.
- **L3:** `release.sh` skips the backup when the live release's image is missing, and prints "no live release or no data yet" although data exists. Back up after the build, with the new image.
- **L4:** every release attempt takes a backup before building, including one whose build fails, and prunes the oldest. A few failed attempts push the nightly backups out of the 7 kept.
- **L5:** `restore.sh`'s usage text prints two lines of code: `sed -n '2,8p'` should be `2,6p`.
- **L6:** `docs/deploy.md` Section 8 says the absolute-path limit is "recorded in `docs/backlog.md`", but the backlog had no such entry. Backlog item 6 still says "there is no CI yet".
- **L7:** `check_ruff_baseline.py` prints every finding in a file that has a new error, including the known ones.
- **L8 [Likely]:** the shape guard sees only the keys the one scenario writes (a delivered and a rejected turn). A key written only on a failed, chained or clarification turn changes the format without failing it. Add those turns when the next fixture is made.

## Not run

- The GitHub workflow itself (it isn't on the default branch). Its commands were run here instead: the dev-lock install on 3.11, the baseline check with the locked ruff, and the `--no-cache` image build.
- The suite on Python 3.11 with Chromium: Playwright isn't installed here, so the browser tests didn't run.
- `install.sh`, which needs a root Ubuntu VM.
- `refresh_locks.py`, which re-resolves the locks and would change them.
- The Azure appendix.
- Real Groq. Every turn here used the faked LLM, and the live runs with Groq are due after a MERGE verdict.
- Note: the probes' successful release pruned `workbuddy:4ac1fddcb676` (`release.sh` keeps two releases). The implementer's `workbuddy-data` volume was saved before the probes and restored after them.

---

# Review round 2 (commit 7cda772)

**Verdict: MERGE.** All six round-1 findings (H1, M1 to M5) are fixed, each confirmed with my own runs; no new HIGH or MEDIUM. Six new LOWs are deferred.

Scope: `0ea40c4..7cda772` (only `deploy/`, `.github/workflows/tests.yml`, `tests/deploy/`, docs). Nothing under `buddy/`, `core/`, `skills/` or `backend/` differs from `origin/main` (47ef562, unchanged since round 1). Probes, how to rerun them, and outputs: `scripts/review_harness/feat-release-safety/round2/` (README, `out/`).

## What I ran

| Check | Result |
|---|---|
| `python -m pytest -q` (this checkout) | 2635 passed, 1 skipped. `tests/deploy`: 64 passed, none skipped (the real-Docker backup and restore tests ran) |
| `tests/deploy` in a `git archive` copy of 7cda772 (no empty folders, like a clone) | 64 passed (M1 fixed; `mutations2.txt` N00) |
| `ruff check buddy tests` vs base, by file and rule | Identical, 22 errors. `check_ruff_baseline.py`: nothing new. `ruff check tests/deploy scripts/*.py`: clean |
| This head's `tests/deploy` on the round-1 code (0ea40c4) | 21 failed, 43 passed: every new test of a fix fails on the old code (`new_tests_on_0ea40c4.txt`) |
| Mutations undoing each fix (`mutations2.py`, 18) | 16 caught. 2 survived (R2-L5): the "data from before is in" message, and an uncommitted `deploy/` edit (the code refuses it, P3, but no test makes one) |
| In-process pipeline, 25 scenarios x template/friend/focus vs round 1's base output | Only poll timing differs (`03_dedupe_timeout`, `08_chain_timeout`, as in round 1). `rule_checks`: 75 rows, 0 problems; the original unchanged in 75 of 75 |
| Real stack (compose + Caddy + image, LLM faked): round 1's d01 to d10, then `stack_round2.sh` e00 to e05 | d01/d02 OK. d07: build failure changes nothing and takes no backup; start and smoke failures roll back with smoke OK; good release OK. **H1:** d06 and e01 restore the oldest archive with the folder at its limit: exit 0, the archive kept, 21 of 21 sessions back, owner 10001, no staging folder, smoke OK, a turn afterwards OK. **M2:** d08 rerun after a failed first release does the whole release, `SMOKE OK`; e03 same commit with the app stopped is released again, then skipped while healthy. **M3:** d09/e04 `$` in the password: `compose.sh`, `release.sh` and `smoke.sh` refuse by name; "horse" printed 0 times. **M4:** d10/e05: 0 session ids in Caddy's log (25 lines) or the app's. **M5:** e02 a commit whose `deploy/` differs is refused, `current.env` and the app container unchanged |
| Turns through Caddy, friend and focus (smoke, hostile, no-duplicates, two sheets, not a workbook, a rejected plan): 15 rows | Rule 1: every count in every reply matches my openpyxl read (`check_stack_turns.py`); a rejected plan delivered nothing and claimed nothing; no file text reached a reply. Rule 2: 9 of 9 accepted uploads unchanged in the volume; the 4 bad-file uploads got 422. (Two of d03's 15 rows met the sign-in limit's 429, by design; e00 reran them) |
| `restore.sh` adversarial, real Docker (`restore_probes.py`) | Relative path in a folder named with a space and a quote, KEEP=2: OK. `../` member: unpack refused, `/data` untouched, app started. Full disk during unpack (4 MB tmpfs): refused, `/data` untouched. Left-over staging folder: OK. Top-level name with a space: R2-L2; the printed undo command put the data back |
| `release.sh` edge cases, stand-in docker (`release_probes.py`) | Local edit under `deploy/`: refused. Git-ignored override: allowed. Older commit with the same `deploy/`: allowed. Live commit unhealthy: R2-L1 |
| `.env` reading, compose vs scripts (`env_parse_probe.py`, 23 values, 6 line forms) | Every value compose reads differently is refused, by name. R2-L3, R2-L4 for malformed lines |

## Round-1 findings

| Finding | Status | My evidence |
|---|---|---|
| H1 | Fixed [Certain] | d06, e01, `test_restoring_the_oldest...` on real Docker; N02 to N06 caught; R1, R4, R6 |
| M1 | Fixed [Certain] | `git archive` copy passes `tests/deploy`; N08 caught |
| M2 | Fixed [Certain] | d08, e03; N09, N10 caught |
| M3 | Fixed [Certain] | d09, e04, env probe; N11 to N15 caught |
| M4 | Fixed [Certain] | d10, e05; N16 caught |
| M5 | Fixed for a release [Certain]; the rollback half is R2-L6 | e02, P3; N17 caught |

## New findings

All LOW. None is reachable by a WorkBuddy user request; they're about the operator's scripts.

- **R2-L1 [Certain]: rerunning the live commit while it isn't healthy loses the rollback target.** `deploy/release.sh` lines 33 to 39 set `previous=""`.
  - **Scenario** (`release_probes.txt` P1, P2): the live commit is "starting" or unhealthy and the owner runs `release.sh` on it again.
  - **Result:** if it then fails, it prints "There was no earlier release to go back to", stops the app and deletes `current.env`, although an older release's image is on the machine. If it succeeds, it prunes that older image.
  - **Fix:** take `previous` from `previous.env` when that image exists and differs from the tag. Otherwise say "no other release image to go back to".
- **R2-L2 [Certain]: `restore.sh`'s replace step splits names on spaces.** `for entry in $(ls -A)`.
  - **Scenario** (R3): an archive with a top-level name that holds a space fails halfway, after `/data` was emptied. It says so and prints an undo command, which worked (R3b). Archives from `backup.sh` hold only `sessions`, `uploads` and `outputs`.
  - **Fix:** `find /data -mindepth 1 -maxdepth 1 ! -name .restore-staging -exec rm -rf -- {} +`, then `find /data/.restore-staging -mindepth 1 -maxdepth 1 -exec mv -t /data -- {} +`.
- **R2-L3 [Certain]: `check_env_syntax` can print a line's content.** In `deploy/lib.sh`, a line without `=` becomes the "name".
  - **Scenario:** a password wrapped onto its own line, with a `$` in it (`secret$tail`), gives "the value of secret$tail has a $...". The same happens for an indented comment.
  - **Exposure:** compose's own error prints that line too, so this adds no exposure over the base. It does contradict "its content is never printed".
  - **Fix:** skip `^[[:space:]]*#` lines. For a line without `=`, or with a name not matching `^[A-Za-z_][A-Za-z0-9_]*$`, say "line N" only.
- **R2-L4 [Certain]: some line forms get a misleading "has no value for".** Compose reads `export NAME=v`, `NAME: v` and `  NAME=v`, but `env_value` doesn't, so `require_settings` says "deploy/.env has no value for: NAME". It still refuses, so nothing breaks.
  - **Fix:** refuse those forms in `check_env_syntax`, by line number.
- **R2-L5 [Certain]: two guards aren't tested.**
  - `test_the_pre_restore_backup_holds_what_was_there` still passes when the final message no longer names the safety backup, because `backup.sh`'s own line carries the path (N07).
  - No test makes an uncommitted edit under `deploy/`, so a `git diff HEAD <commit>` mutant passes (N19). The code does refuse such an edit (P3).
  - **Fix:** assert on "The data from before is in <path>", and add the uncommitted-edit test.
- **R2-L6 [Certain by reading; not run]: what remains of M5 after the fix.**
  - `rollback()` runs the previous image under this checkout's `deploy/`, which is the failed commit's.
  - `docs/deploy.md` Section 8's by-hand rollback ends with `git checkout main`, so a later `./deploy/compose.sh up -d app` (Section 9) applies main's compose file to the older image.
  - **Reachable** only when a release changes `deploy/`.
  - **Fix:** after a by-hand rollback, stay on the older commit until the next `git pull`. When the failed release changed `deploy/`, `rollback()` says to `git checkout <previous commit>` and run `./deploy/compose.sh up -d`.

## Deferred (LOW; added to `docs/backlog.md`)

R2-L1 to R2-L6 above. Round 1's L2, L7 and L8 stay open in the backlog; L1, L3, L4, L5 and L6 are fixed (checked: `smoke.sh` order, d07 BAD_BUILD "no backup was taken", the usage text, the backlog entries).

## Not run

- The suite on a fresh clone from GitHub: the sandbox refused it (code from an external source). The `git archive` copy stands in for it: it has no empty folders, like a clone.
- The GitHub workflow, including its new `docker pull python:3.11-slim` step.
- Python 3.11, and the browser tests with Chromium.
- shellcheck, which isn't installed. `install.sh`, the Azure appendix and `refresh_locks.py`.
- Real Groq. Every turn here used the faked LLM; the live runs come after a MERGE verdict.
- Note: the probes ran on the fixed volume and image names. The implementer's `workbuddy-data` volume was saved before the probes and put back after them (12 files, owner 10001), and the `workbuddy:5967924664c7` and `workbuddy:locktest` images were kept.
