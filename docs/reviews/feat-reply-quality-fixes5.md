# feat/reply-quality: fixes for round 5 (H1, RQ-R5-3)

Round 5 (`docs/reviews/feat-reply-quality-review.md`, "Review round 5 (commit d6ab115)") was DON'T MERGE: one HIGH, H1, both halves with the exact fix the reviewer gave. This file records what I did, nothing beyond it. Source of the round: `0eb950c`; the head reviewed was `d6ab115`; the previous head is `5fc73c6`. Round 6 is a new reviewer agent on this head, scoped to the diff since `5fc73c6`, H1, and regression against `origin/main`.

## H1(a): the mention cross-check ran blind on the report's phrase

- `buddy/brain.py`, `MentionCrossCheck.__call__`: `except_inside=report_phrases(understanding)` is gone; `mentioned_actions(request.user_message)` reads the whole message, as on the base. The `report_phrases` import and the class docstring (section 3.2) are updated.
- **Cost, as the reviewer said:** "give me a report on the duplicate rows" now gets "Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: report on the file." True, reads as if a removal was asked. This is RQ-R5-1 and RQ-R4-2; both are now "by design". (I did not edit `docs/backlog.md`; closing them there is the owner's or the next session's call.)
- Tests: the one test that pinned the exclusion (`test_what_the_report_is_about_is_not_a_request_for_it_when_brain_gave_it_the_phrase`, three speakers) is replaced by `test_a_removal_named_inside_the_reports_phrase_is_still_found_by_the_cross_check` (the new expectation above, and the removal is in the ledger). Added `test_a_removal_swallowed_by_the_reports_phrase_is_not_claimed_as_done`: "remove the duplicate rows, remove the empty rows and give me a report" with Brain's report phrase "remove the empty rows and give me a report"; the copy is delivered, the reply does not start with "Done" and says "Your message mentions empty rows, but I didn't run the empty-row removal".
- Left alone, not asked for: the `except_inside` parameter of `buddy.actions.mentioned_actions` and its two unit tests (`tests/buddy/test_actions.py`, `tests/buddy/test_reply_quality_units.py`). Nothing in the pipeline passes it any more. The reviewer may want it removed.

## H1(b): an extra folded into the report's phrase

- `buddy/brain.py`, `_SYSTEM_PROMPT`: the reviewer's sentence, word for word, after the `report_on_file` sentence: *Anything the user asks for around a report that Buddy cannot do (a file format such as pdf, a language, a place to put it, someone to send it to) is its own "unsupported" entry; the "source_phrase" of "report_on_file" is only the words that ask for the report itself.*
- Test: `test_brains_prompt_makes_an_extra_around_a_report_its_own_unsupported_entry` in `tests/buddy/test_reply_quality_units.py`, next to the existing prompt test.
- **What this does not do:** it is a prompt sentence, so it only lowers the chance that the real Brain folds "in pdf" into the report's phrase. Nothing in code catches it when it does. I did not measure it (no live runs in this session, by the owner's instruction). The reviewer's note stands: a live check of "remove the duplicate rows and give me a report in pdf" is needed before merge, and a "Done" that omits "in pdf" there is a stop.

## RQ-R5-3: a requested report whose analysis skill fails

- `buddy/voice.py`, `TemplateSpeaker.compose`, the `_no_changes_message` branch: when `request.report_requested`, the reply uses `report_unavailable_sentence(build_outcome_report(...))` (so "I couldn't produce the report you asked for: the analysis of your file failed."). `_no_changes_message` takes it as `report_unavailable` and uses it in place of the generic "I wasn't able to confirm that worked. The analysis reported failure." when the only failed verdicts are the analysis's. If another verdict failed too, the generic failure sentence stays and the report sentence is added after it.
- Before: "I wasn't able to confirm that worked. The analysis reported failure. No changes were made to any file this turn." After: "I couldn't produce the report you asked for: the analysis of your file failed. No changes were made to any file this turn." in all three speakers.
- Test: `test_a_report_whose_analysis_skill_fails_uses_the_report_sentence` (exact reply, three speakers).

## `CLAUDE.md`

"Decided behaviour" now says a report is Brain's closed action `report_on_file` and no word list decides whether a request asks for one, that the report's `source_phrase` covers only the words asking for it, that the cross-check reads the whole message, and records the owner's confirmation that "remove the duplicate rows and then give me a report" delivers the copy and the report.

## Checks

- The 10 new test cases (3 + 3 + 3 + 1) fail on the previous `buddy/` code and pass on this one (run with `buddy/` stashed).
- `python -m pytest -q`: 3090 passed, 6 skipped (3083 before: 3 tests replaced by 10 new cases, net +7). No test skipped or xfailed.
- `ruff check buddy tests`: 18 errors, the same as round 5's head; `scripts/check_ruff_baseline.py`: no errors beyond the baseline.
- `origin/main` has not moved since the round-5 merge (`c9592ca`), so there is nothing to merge in.
- Not run: live runs (none in this session, by the owner's instruction).
