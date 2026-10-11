# feat/reply-quality: fixes for review round 1

Round 1 (`docs/reviews/feat-reply-quality-review.md`, head `8f0d708`): **DON'T MERGE**, one MEDIUM (M1). This file records what was changed for it, and for the LOW findings that were cheap and safe to close now. I did not edit the review, downgrade a finding or write a verdict. Anything below that the next reviewer finds wrong stays open.

## M1: a delivered report was also "something I can't do yet"

**Cause (as the reviewer found it):** two stages decided "this phrase is only a report request" with two different word lists. The planner and Voice used `is_only_a_report_request` (`buddy/actions.py`); the outcome renderer stripped the report from an `unsupported` phrase with `strip_report_request` and then judged what was left with `_FILLER_WORDS`, which deliberately has no "file", "data", "it", "my" or "of". So "analyze my file" or "summarize my data" was a report for the first and a leftover content word for the second.

**Change:**
- `buddy/outcome.py` `_no_skill_outcome`: `if report_stated and is_only_a_report_request(phrase): return None` before any word-list reasoning. One predicate for both stages.
- `buddy/actions.py`: `_REPORT_REQUEST` now includes the thing the report is about (`of|on|about|for` + determiners + file, sheet, spreadsheet, workbook, data, table, issues, problems, duplicates, rows, columns, errors), so `strip_report_request` and `is_only_a_report_request` see "analyze my file" as one request. `_REPORT_FILLER` gained "be great thanks thank short brief quick full detailed written tell". The nouns gained "rundown" and "overview", and the pattern gained "check / look at / look over / take a look at / review / inspect the file (for problems)" and "what's wrong with the file": the four wordings in the reviewer's list that the detector didn't recognise ("give me a rundown", "tell me what's wrong with the file", "check the file for problems", "take a look at the file") showed "I can't do that yet" next to the analysis findings.
- Tests (`tests/buddy/test_reply_quality.py`): all 34 wordings from the reviewer's probe (`probe_b_report_phrases.py`) are asserted to be one report request for the detector, the planner's predicate and `build_outcome_report(report_stated=True)` (no "can't do" action left); 7 representative wordings run through `build_engine` in all three speakers, alone (the reply starts "Report on your original file:" and has no "can't do") and after "remove the duplicate rows and …" (the reply has "Report on the copy:", "Done —", no "can't do", no "Partly done"). With the one-line outcome change removed, 12 of those 55 cases fail (the widened pattern already carries most wordings; the predicate is what makes the two stages agree).

**Not changed on purpose:** a wording the detector still doesn't recognise (a paraphrase) is still reported as "I can't do that yet" when Brain flags it `unsupported`, and the analysis, if it ran, is still shown as "Here's what I noticed when I looked it over". That is stated, not silent, and not a false claim; widening the patterns further trades it against more unrequested report blocks (L2).

## LOW findings closed now

| ID | Change | Test |
|---|---|---|
| L1 | Four of the five unguarded paths now have a test that fails when the guard is removed: the "analysis passed" check in `_analysis_findings` (a failed analysis's finding is never given as the report), the "report not available" branch for an unchanged file, case-folding in `display_names`, and the Inspector's singular "1 row repeats an earlier row exactly". The fifth (`download_name`'s `sanitize_name`) was removed with L5(b): the stem is already sanitised when it is uploaded. | `test_findings_of_an_analysis_that_did_not_pass_…`, `test_a_report_on_an_unchanged_file_says_when_the_analysis_did_not_run`, `test_names_that_differ_only_in_case_are_numbered_too`, `test_the_verifiers_finding_uses_the_singular_for_one_repeated_row` |
| L3 | A Think that failed now also says "No changes were made to any file this turn." Only a greeting or a question omits it (design D1). | `test_a_think_that_failed_still_says_nothing_was_changed` |
| L5 | `backend/names.py` `download_name` parses the generated shape `<stem>(_<operation>_<session>-task-<n>)+.<ext>` and rewrites only those parts: the person's own `no_empty_rows` in a stem is kept (`my_no_empty_rows_data_cleaned.xlsx`), and a long stem is cut so the operation words and the extension always show (total ≤ 100). Ids in any other shape are still dropped. | `test_only_the_generated_part_of_the_name_is_rewritten`, `test_a_long_name_is_cut_in_its_stem_so_the_operation_still_shows` |
| L6 | The new E501 in `skills/excel/analyze.py` is wrapped; `ruff check skills backend core` is clean again. | the lint run |

## Left open (still deferred, in `docs/backlog.md`)

L2 (the bare noun "report", "summary" or "analysis" in a message about something else gets an unrequested, true and labelled report block), L4 ("thanks!" gets the greeting), L7 (another branch's scripts still name `NOTHING_RAN_REPLY`; I don't edit another branch's folder), L8, L9 (a skill-level analysis failure gets the older wording), L10, L11 (pre-existing: column-header text reaches replies; exception text in two Inspector findings).

## Numbers after the fixes

See the "Results" section of `docs/reviews/feat-reply-quality-impl.md`, updated for the head the next reviewer is given.
