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
