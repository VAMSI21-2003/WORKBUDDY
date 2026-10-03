# Fourth fixes for `feat/verified-outcome-b` (re-review 3: R1), and the merge of `origin/main`

**Review:** "Re-review 3 (fixes commit `688f62b`)" in `docs/reviews/verified-outcome-b-review.md` (verdict: DON'T MERGE), finding R1 and its section 6. Implemented as specified. The session that wrote this must not review it.
**Not merged.** A new reviewer agent, round 5, last reviewed commit `688f62b`, reviews it next.
**Suite:** `python -m pytest -q` gives **1568 passed** after the merge and the fix (1513 passed on the merge commit alone; +55 new cases). `ruff check buddy tests`: 22 errors, the same count as before the change; none are new.

## Merge of `origin/main` (no rebase, no force-push)

- One conflict, in the `buddy/voice.py` imports. Kept this branch's `buddy.actions` import (the `buddy.planner._INTENT_CAPABILITIES` that `main` imported no longer exists and nothing in `voice.py` uses it) and `main`'s `StageFailure` and `failure_reply`.
- A turn with `state.failure` set never reaches the outcome block. `Voice.__call__` returns `failure_reply(...)` before any speaker runs. `_verified_delivery` requires `request.failure is None`, so `TemplateSpeaker` and `PersonalityVoice` called directly give the failure reply too. `main`'s tests in `tests/buddy/test_pipeline_failure_and_output_dir.py` pin all three (`test_verified_delivery_is_false_whenever_a_stage_failed`, `test_a_speaker_called_directly_with_a_failure_gives_the_failure_reply`, `test_voice_gives_the_failure_reply_without_calling_its_speaker`) and pass on the merge commit. I added no duplicate test.
- After the merge the skills refuse a workbook with more than one sheet (`WorkbookRefused`), so nothing is delivered for one.

## R1: cause and change

**Cause.** `_LOCATION_PHRASE` in `buddy/outcome.py` took `across` as a preposition and `workbook` as a noun, so Brain's correct `unsupported` flag on "remove duplicates across the workbook" was stripped as a location, nothing was left, and the entry was dropped.

**Change (`buddy/outcome.py`, only this).** `across` and `workbook` removed from `_LOCATION_PHRASE`, exactly as section 6 gives it. A comment above it says the location strip must never take a phrase that names more than the one sheet the skills read ("across", "workbook", "every", "all"). No other code changed.

## Tests (+55 cases)

- `tests/buddy/test_outcome.py::test_r1_the_dropping_word_lists_are_pinned` (1): pins `_REMOVAL_VERBS`, `_DETERMINERS` and the full `_LOCATION_PHRASE` pattern to their current closed lists, and checks that "across the workbook", "in the workbook", "in every sheet" and "in all sheets" don't match it. This is the L-d guard.
- `tests/buddy/test_engine_factory.py::test_r1_a_two_sheet_workbook_delivers_nothing_and_the_reply_never_says_done` (18 = 3 speakers × 6 session ids): a real two-sheet workbook (`Q1`, `Q2`), through `build_engine`. Nothing is delivered, the reply has no "Done —", the Voice model isn't called, and the original is byte-identical.
- `tests/buddy/test_engine_factory.py::test_r1_a_flagged_workbook_scope_is_partly_done_not_done_on_a_one_sheet_file` (36 = 2 scopes × 3 × 6): a one-sheet file, Brain flags "remove duplicates across the workbook" / "…in the workbook" as unsupported. The reply contains "Partly done — removed 2 duplicate row(s)." and the not-done sentence, and never "Done —". The file is checked independently (4 rows, 0 duplicates, 1 empty row). `_run_ledger_turn` gained a `second_sheet` option for the first test.

**Pre-fix check** (`buddy/outcome.py` put back to the pre-fix version): the guard and all 36 one-sheet cases fail. The two-sheet cases pass on the old code too, because after the merge the skills refuse the workbook before any reply is built. They pin that behaviour so a future change to the skills can't reopen R1's scenario.

## `_no_skill_outcome`: the two-rounds rule

`_no_skill_outcome` has had a HIGH in four review rounds. R1 is the fifth, so `docs/process/review-loop.md`'s rule applies from the first round here. I applied R1 as specified, and this round did not change the function's logic. **If the round-5 reviewer finds another HIGH in `_no_skill_outcome`, it won't be patched again.** The dropping logic will be removed, so that an `unsupported` entry from Brain always renders a not-done sentence, even though replies get flatter.

## Known cost (from the review, accepted)

A one-sheet file that was fully deduplicated now says "Partly done" when Brain flags the workbook scope as unsupported. That's a false "can't do", reachable only after Brain's flag (MEDIUM-u in the review), and the safe side until backlog item 1a (multi-sheet) is built.

## Not run

Live runs with Groq are required only after a MERGE verdict, on the final head (`review-loop.md`, step 5).
