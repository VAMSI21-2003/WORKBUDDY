# feat/release-safety: implementation notes

Branch `feat/release-safety`, from `origin/main` (47ef562). The goal, from the owner: the full project live on one Ubuntu VM, and later upgrades must not break it. Not a design-document change; no code under `buddy/`, `core/`, `skills/` or `backend/` was modified. It is rule-touching in the "saved data" sense (Part D adds a rule to `CLAUDE.md`) and in the deployment sense (rollback, backups, the rate-limit proxy setting), so it goes through the review loop.

## What changed

| Part | Files | What |
|---|---|---|
| A | `requirements-runtime.lock`, `requirements-dev.lock`, `scripts/refresh_locks.py`, `Dockerfile`, `.dockerignore`, `tests/deploy/test_dependency_locks.py` | Exact pins with hashes, Python 3.11, `--universal`. The runtime lock pins what `docs/reviews/frontend-deploy-image-freeze.txt` recorded, plus `setuptools==79.0.1` (the base image's; the freeze omits it because `pip freeze` hides it). The Dockerfile installs with `--require-hashes --no-deps` then `pip check`. The dev lock holds the same runtime pins plus the dev tools. One command refreshes: `python scripts/refresh_locks.py [dev]` |
| B | `.github/workflows/tests.yml`, `ruff-baseline.txt`, `scripts/check_ruff_baseline.py`, `tests/deploy/test_ruff_baseline.py` | Three jobs: full suite (Python 3.11, from the dev lock, Chromium installed so the browser tests run), ruff against the baseline (22 known errors, counted per file and rule), and `docker build` plus starting the image and asking `/healthz`. Triggers: pull requests, pushes to `main`, and `workflow_dispatch` (added so it could be run by hand; GitHub answered 404 to a dispatch because the file isn't on the default branch yet) |
| C | `deploy/*` | See `docs/deploy.md` Section 9 |
| D | `tests/deploy/fixtures/data-v1/`, `tests/deploy/data_scenario.py`, `tests/deploy/test_saved_data_survives.py`, `scripts/make_data_fixture.py`, `CLAUDE.md` | A `/data` folder written by the real app at 71511b8 (session, upload, a delivered turn, a rejected turn). Tests: it lists, the turns read back as recorded, the delivered file downloads with the recorded hash, the upload is intact on disk, a new turn runs on it; plus a guard that fails when what the server writes today stops having the shape of the newest fixture (the format changed with no new fixture). New rule in `CLAUDE.md` under "Working method" |
| E | `docs/deploy.md` | Decisions recorded; checklist items 5, 6, 7 updated; Sections 7 to 9 and Appendix A; `.gitignore` entries |

## Things I found while doing it

- **The saved session record holds absolute paths** (`context.uploaded_files`, `context.active_file`), and the store refuses a session whose paths don't equal `<data_dir>/uploads/...` (`_restore_context`). So a data folder is restorable only at the path it was written at. Inside the image that's always `/data`, so deployment is unaffected; the fixture test relocates the paths in `session.json` only (`data_scenario.relocate`, no other file is touched), and `docs/deploy.md` says to restore only into the VM's `/data`. Not changed in `backend/`; recorded for the backlog.
- Caddy's `header_up X-Forwarded-For {remote_host}` replaces whatever the caller sent. With `WORKBUDDY_TRUSTED_PROXY_HOPS=1` the app reads the last (only) entry. With `0` or `2` the check fails (all visitors share Caddy's address), which is the control that shows `check-proxy.sh` can fail.
- `workflow_dispatch` is refused by GitHub for a workflow that isn't on the default branch (404). The workflow was therefore **never run on GitHub**; every command in it was run locally (below).

## What I ran (all on this sandbox machine)

- Lock install in a clean Python 3.11 venv from `requirements-dev.lock` with `--require-hashes --no-deps`: installs, `pip check` clean.
- Full suite on the sandbox's own Python 3.13 (no Playwright installed there, so the browser tests are skipped): 2613 passed, 1 skipped. Full suite in the Python 3.11 venv from the lock (browser tests included): 2608 passed, 1 failed (`test_a_process_that_is_not_root_can_delete_read_only_uploads`: it executes `sys.executable` as uid 65534, and the venv sits in a root-only scratch folder; the same test passes with the system 3.11 interpreter and the venv's packages).
- Image built from the lock: `pip freeze` of the image is byte-identical to the recorded freeze (86 lines); `pip check` passes.
- Mutations (each reverted): lock version outside the range, a package dropped from the dev lock, the Dockerfile reverted to an unlocked install, a lock entry with its hashes stripped, a dev lock version differing from the runtime's: each fails a test. For Part D: adding a key to `session.json` fails the shape guard; requiring a new key when reading turn records fails four open-the-fixture tests.
- `deploy/` on this machine, as `localhost` with Caddy's own certificate, real Groq through the sandbox's egress proxy (`scripts/live_stack/`, sandbox only): `release.sh` (the first attempts failed on my own YAML and Caddyfile mistakes and left the stack stopped, as designed), `smoke.sh` OK, `check-proxy.sh` 6/6 with hops 1 and 3 failures each with hops 0 and 2, `backup.sh`, `restore.sh` (2 sessions back to 1, ownership 10001), `install.sh` in an unprivileged `ubuntu:24.04` container (real Docker apt repository; `swapon`, `ufw`, `systemctl` stubbed; second run changes nothing).
- shellcheck on every script: no warnings (only "can't follow source" notes).

## Not done / limits

- The workflow has not run on GitHub (see above). Actions are pinned by major tag (`@v4`, `@v5`), not by commit.
- The 2 MB two-step chain on the target VM (checklist item 6) is not automated and not measured.
- `check-proxy.sh` and the whole stack were run on one machine, not on Azure; the Azure appendix is written from knowledge of the portal and was not exercised.
- Lint scope is what `CLAUDE.md` names (`buddy tests`); `backend/`, `core/`, `skills/` and `scripts/` aren't in the baseline.
- `release.sh` replaces the single app container: a few seconds of downtime per release.
- Live runs (compose + Caddy + real Groq, an upgrade, a failed release) are done after a MERGE verdict, in `docs/reviews/feat-release-safety-live-runs.md`.
