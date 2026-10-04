# Fixes for review round 2: `feat/backend-api-core`

**Review:** `docs/reviews/backend-api-core-review.md`, "Review round 2 (commit 0276e7c)", verdict DON'T MERGE.
**Fixes start from:** `9e4866a` (the round-2 review committed as written).
**State:** the round-2 fixes below are complete. The next step is review round 3 with a new reviewer agent.

The writer's account. Treat it as unverified. I disagree with no finding.

## R2-M1: the from-original sentence followed a delivery from any upload

- **Change.** `backend/turns.py`, `TurnRunner.start`: `earlier_deliveries` now counts only the files handed back by earlier finished, delivered turns whose `target_file_id` is the upload this turn works on (the given `file_id`, else the active file). That's the counting rule the review gives. The same `target_id` is now stored on the new turn, so the rule and the record can't disagree.
- **Effect.**
  - A copy the user downloaded and uploaded again is an upload of its own, with no earlier deliveries, so its delivered reply has no sentence.
  - A delivery on an unrelated second upload has no sentence.
  - A turn that delivered nothing never counts.
  - The same-upload follow-up still gets the sentence, including after a restart (`target_file_id` is stored on every turn record).
- **Tests** (`tests/backend/test_turns.py`, through the API, all three speakers):
  - (a) `test_a_re_uploaded_copy_gets_no_from_original_sentence`: dedupe, download, upload the copy, "now remove the empty rows". Delivered, no sentence, and the file has 3 rows with 0 duplicates (it built on the copy).
  - (b) `test_a_first_delivery_after_a_turn_that_delivered_nothing_gets_no_sentence`: after a greeting, and after a failed turn (Inspector raising after a real output). No sentence.
  - (c) `test_a_delivery_on_another_upload_gets_no_sentence`: dedupe on the first upload, then dedupe on `budget.xlsx`. No sentence.
  - (d) The existing `test_a_follow_up_delivery_says_it_was_made_from_the_original` and the `followup_after_delivery` rule-1 scenario: the sentence is still there.

## Recommended in the same round

| Item | Change | Test |
|---|---|---|
| R2-L1 | `backend/logging_filter.py`, `_redact_record`: the format string is never redacted on its own. A record whose formatted message has no secret is left alone. Otherwise its string args are redacted in place, and the shape is kept only if re-formatting then equals the redacted message; else the record collapses to the redacted message with no args. If a record can't be formatted at all, its `msg` and string args are both redacted, since logging's error path prints them. | `test_a_secret_split_between_format_and_argument_formats_and_stays_masked`, for `"Authorization: Bearer %s"`, `"api_key=%s"` and `"GET /api/v1/sessions/%s/files"`: the line is written redacted, and nothing raw and no "Logging error" reaches stderr. The uvicorn `AccessFormatter` test from round 1 still passes, and `scripts/review_harness/l1_placeholder_probe.py` prints every line redacted. |
| R2-L2 | `backend/turns.py`, `_save_finished`: catches `BaseException`, so the turn finishes as failed with the fallback instead of staying `running`. | `test_a_base_exception_from_the_finished_save_still_finishes_the_turn`: `failed`, `FALLBACK_PIPELINE_ERROR`, no files, session not busy. |
| R2-L3 | `docs/design/backend-api-design.md`, "Decisions (2026-10-04)", item 2: `earlier_deliveries` and its per-upload counting rule, labelled as an implementation note, not an owner decision. | — |

R2-L4 stays deferred (`docs/backlog.md`).

## Do the new tests fail on the old code?

- **R2-M1.** Each mutation below was applied to the fixed `TurnRunner.start`, and each made at least one test fail:
  - not setting `earlier_deliveries` at all fails the follow-up test and the `followup_after_delivery` scenario, in all 3 speakers (6 failures);
  - counting every earlier turn (the review's `m2_counts_every_turn`) fails tests (a), (b) and (c) (12 failures);
  - counting every delivery in the session, which is round 2's code, fails (a) and (c) (6 failures).
  
  `scripts/review_harness/mutations.py --only m2`: the three `m2` mutations that still apply are killed. `m2_not_set_at_start` and `m2_counts_every_turn` report "NOT APPLIED", because the line they target changed; the hand-run equivalents above cover them.
- **R2-L1 and R2-L2.** With `backend/logging_filter.py` from `0276e7c`, all 3 split-secret cases fail. With `backend/turns.py` from `0276e7c`, the `BaseException` test fails.

## Tests and lint at the head of this round

- `python -m pytest -q`: 1317 passed (Python 3.11.15).
- `ruff check buddy tests`: 22 errors, identical to a fresh `origin/main` worktree by file and rule. `ruff check backend core skills scripts tests/backend` and the new buddy test file: clean.
- `python -m backend.export_openapi --check`: in sync.
