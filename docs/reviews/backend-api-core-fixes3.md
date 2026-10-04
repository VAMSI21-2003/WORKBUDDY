# Changes before review round 4: `feat/backend-api-core`

**Last reviewed commit:** `7be9949` (round 3, MERGE). Owner instructions of 2026-10-04 followed in order: move the probes, merge `origin/main`, lower the upload cap, run the suite.
**State:** done. The next step is review round 4 with a new reviewer agent.

The writer's account. Treat it as unverified.

## 1. The round reviewers' probes moved to this branch's folder

- **What changed:** the updated `docs/process/review-loop.md` (PR #12) puts each branch's probes in `scripts/review_harness/<branch>/`. Every file the three reviewers added was moved with `git mv` into `scripts/review_harness/feat-backend-api-core/`, their README included (as `README.md` there).
- **The edits:** in each script, the repo root went from `Path(__file__).resolve().parents[2]` to `parents[3]`, and the documented commands now name the new folder. Imports between the scripts are unchanged, because each script puts its own folder on `sys.path`.
- **Checked:**
  - every file compiles and passes `ruff`;
  - `l1_placeholder_probe.py` runs and prints every line redacted;
  - `api_probe.py --speaker template --only dedupe`, started from another working directory, ran a full turn;
  - `mutations.py --only l8` copied the right repo root and killed both mutations.
- `scripts/live_api_runs.py` stays where it was.
- **Commit:** `9dea54a`.

## 2. `origin/main` merged in (`git merge`, no rebase, no force-push)

- **What came in:** `origin/main` at `1462ce4` brought PR #11 (values-only copy, the PR-B reply ledger and related pipeline and skill changes, and analytics disabled) and PR #12 (the review-loop update).
- **Conflicts: none.** `buddy/voice.py`, `buddy/voice_catalog.py` and `docs/backlog.md` changed on both sides and merged automatically. Nothing else under `buddy/`, `core/`, `skills/`, `backend/` or `tests/` was changed by both.
- **In the merged `buddy/voice.py`,** this branch's additions sit unchanged next to main's: `NOTHING_RAN_REPLY`'s early return, `FROM_ORIGINAL_SENTENCE`, `SpeakingRequest.earlier_deliveries` and `is_verified_delivery`. Main's reply ledger is used on the delivered path, and main didn't change the undelivered path the early return sits in.
- **`scripts/review_harness/`:** the files directly in it are main's, byte for byte (same file list, `git diff origin/main` empty there). The only addition is this branch's folder.
- **Commit:** `510ed06`.

## 3. The default upload cap is 2 MiB

- **Why:** the design's evidence rule ("Decisions (2026-09-30, part 2)", item 3) applies. Live run 9 took 256 s for one step and 308 s for a two-step chain on a 5 MB fixture, so the default is lowered to 2 MB.
- **`backend/settings.py`:** `DEFAULT_MAX_UPLOAD_BYTES = 2 * MIB`, with the reason in a comment. `WORKBUDDY_MAX_UPLOAD_BYTES` still overrides it. The `413` message and `limits.max_upload_bytes` already read the setting.
- **`docs/design/backend-api-design.md`:** "Decisions (2026-10-04)" gains item 3, the new default and its evidence.
- **`backend/openapi.json`:** unchanged. The default isn't part of the schema; `python -m backend.export_openapi --check` passes.

## Tests changed, and why

Only these three, and all for the cap. **No test's expected reply changed**: after the merge the whole suite passed with no other edit, including every API test that runs the real pipeline.

| Test | Change | Why |
|---|---|---|
| `tests/backend/test_app_basics.py::test_the_413_message_is_built_from_the_cap` | The default's message is now "That file is larger than 2 MB." The 5 MiB case stays as an explicit value ("…5 MB."), so the size formatting is still covered. | The default changed. |
| `tests/backend/test_app_basics.py::test_settings_defaults_match_the_design` | `max_upload_bytes == 2 * 1024 * 1024` | The default changed, per the design's new item 3. |
| `tests/backend/test_uploads.py::test_the_413_message_names_the_configured_cap` | expects "That file is larger than 2 MB." | It uploads one byte over the default cap. |

## Tests and lint at this head

- `python -m pytest -q`: **2142 passed**, 0 failed (Python 3.11.15, 208 s). That's 1317 from this branch plus main's new tests.
- `ruff check buddy tests`: 22 errors, identical to a fresh `origin/main` worktree by file and rule. `ruff check backend core skills scripts/live_api_runs.py scripts/review_harness/feat-backend-api-core tests/backend`: clean.
- `python -m backend.export_openapi --check`: in sync.
