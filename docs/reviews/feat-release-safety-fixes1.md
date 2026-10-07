# feat/release-safety: fixes for review round 1

Round 1 (`docs/reviews/feat-release-safety-review.md`, commit 0ea40c4): DON'T MERGE, 1 HIGH and 5 MEDIUM. Every finding is fixed below, with a test that fails on the reviewed code (`c1377ba`, which holds the review files and the code of 0ea40c4) and passes now. I didn't downgrade or skip any finding.

| Finding | Fix | Test (fails on the old code, passes now) |
|---|---|---|
| **H1** `restore.sh` deleted the archive it was restoring, then emptied `/data` | `restore.sh` rewritten: copies the archive to a private temp file first; the safety backup runs with `WORKBUDDY_BACKUP_PRUNE=0` (new in `backup.sh`) so it deletes nothing; the archive is mounted with `--mount type=bind,...,readonly` (fails on a missing source instead of creating a directory); it unpacks into `/data/.restore-staging` and replaces `/data` only after that succeeded; a failed unpack leaves `/data` alone and starts the app again; a failure after `/data` was touched prints the safety backup's name and the exact command that puts it back | `test_restoring_the_oldest_backup_works_when_the_folder_is_full` (real Docker volume, folder at its limit, restores the oldest: data is right, the archive still exists, no staging folder, owner 10001, the safety backup holds the replaced data), `test_the_pre_restore_backup_holds_what_was_there`, `test_backups_keep_only_the_newest_few_unless_told_not_to`, `test_a_file_that_is_not_an_archive_is_refused_and_nothing_changes`, `test_a_failed_unpack_leaves_data_alone_and_starts_the_app_again` |
| **M1** the saved-data shape guard failed on a fresh checkout (git keeps no empty folder) | `layout()` lists files only | `test_the_layout_ignores_empty_folders`; and the whole `tests/deploy` folder passes on a `git checkout-index` copy of the tree (the guard test failed there before) |
| **M2** a rerun after a failed first release said "already the live release" with the app down | The no-previous rollback deletes `current.env`; the same-commit shortcut now needs the app to be running and healthy, otherwise the release is done again (with no rollback target) | `test_after_a_failed_first_release_a_rerun_does_the_whole_release`, `test_the_same_commit_is_skipped_only_while_it_is_running_and_healthy` |
| **M3** a `.env` value that compose reads differently (`$name`, quotes, ` #`, trailing space, tab, backtick, `\r`) broke every release and compose printed part of the password | `check_env_syntax` in `lib.sh`, called by `require_settings` (release, smoke) and by `compose.sh`, refuses such a value naming the setting only; Section 7 step 6 says the same | `test_a_value_that_compose_would_read_differently_is_refused_by_name_only` (8 values, release and compose, nothing printed), `test_a_carriage_return_in_env_is_refused`, `test_an_ordinary_env_is_accepted` |
| **M4** Caddy's access log recorded session addresses | The `log` block is gone; the Caddyfile and `docs/deploy.md` Section 9 say why | `test_caddy_writes_no_access_log` |
| **M5** a release built the commit's image but ran the checkout's `deploy/` files | `release.sh` refuses when `git diff --quiet <commit> -- deploy/` fails, before changing anything, and says to check the commit out; Section 8 now says `git pull --ff-only`, then `./deploy/release.sh HEAD`, and the by-hand rollback checks the older commit out | `test_a_release_refuses_when_deploy_differs_from_the_commit` |

## Also changed (reviewer's LOW findings, cheap enough to fix with the above)

- **L1** `smoke.sh` checks the download differs from the original before saying so.
- **L3, L4** `release.sh` builds first, then backs up (reading the volume with the live image, or the new one if the live image is gone), so a failed build takes no backup and prunes nothing. The doc and a test (`test_a_failed_build_changes_nothing_and_takes_no_backup`) say so.
- **L5** the usage texts print the whole comment block (`usage` in `lib.sh`).
- **L6** the backlog now records the absolute-path limit, and that CI exists.
- The workflow's `tests` job pulls `python:3.11-slim` before the suite, so the real-Docker backup and restore tests run in CI instead of skipping. Not run on GitHub yet (see the implementation notes).

Left open and listed in `docs/backlog.md`: L2, L7, L8.

## What I ran

- Full suite and lint: see the final lines of this file, written after the run.
- The new tests on the old code: a worktree of `c1377ba` with the new test files copied in: 20 failed, 9 passed (the 9 are tests of behaviour that was already right, such as the ordinary-env case and a clean release).
- A real `release.sh HEAD` through Caddy with real Groq on the fixed code (`SMOKE OK`).
- shellcheck on all scripts: clean apart from the "can't follow source" notes.

## Note for the next reviewer

The fixes added a test module that starts real containers (`tests/deploy/test_release_scripts.py`, skipped without Docker or the `python:3.11-slim` image) and a stand-in `docker` for the release tests. The stand-in answers only the commands the scripts ask; where its answers matter, check them against real Docker.

## Suite and lint on the fixed code (written after the run)

- `python -m pytest -q` on the sandbox's Python 3.13: **2634 passed, 1 failed, 1 skipped**. The failure is `tests/backend/test_retention.py::test_the_upload_byte_total_is_enforced_exactly_and_rolls_back` (an upload answered 403 where 201 was expected). It passes 3 of 3 when run alone, it passed in the previous full run, and this branch changes nothing under `backend/` or in that file. I don't know why it failed once. [Possible] it depends on `len(xlsx_bytes())` being equal for each generated workbook; I did not verify that.
- `ruff check buddy tests`: 22 errors, the same as the baseline; `python scripts/check_ruff_baseline.py`: no errors beyond it. `ruff check tests/deploy scripts/*.py`: clean.
