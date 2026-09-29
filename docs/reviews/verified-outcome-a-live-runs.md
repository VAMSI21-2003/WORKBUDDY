# Live runs: `feat/verified-outcome-a` (PR-A of the verified-outcome design)

**Date:** 2026-09-29. **Provider:** Groq (`GroqProvider`, key attached by the cloud proxy). **Script:** `python scripts/run_dedupe_manually.py`, which runs `build_engine` with `PersonalityVoice`.

**Runs completed:** 7 of 7 planned (design table rows 1, 2, 3, 4, 5, 9 and 10). No 429 or other provider error.

**Fixtures:**
- `default`: 6 rows, `name`/`amount`, 2 exact duplicates (Alice, Bob) and 1 fully-empty row mid-file.
- `no-duplicates` (new `--fixture` option): 4 distinct, non-empty rows.

**Independent check:** after each run, the script re-reads the original and the delivered file with pandas. It counts the original's rows, its duplicates among the non-empty rows, and its fully-empty rows, and the delivered file's rows, duplicates and fully-empty rows. It hashes the original before and after. After all runs, the intermediate chain files still on disk were read the same way (see the note under "Checks of the counts").

**LLM calls:** the script wraps the provider and prints the first line of each call's system prompt, so each run shows which stages called the model.

## Summary

| # | Mode | Fixture | Message | Intent | Delivered | Counts match pandas | False claim | Original unchanged | Voice model called |
|---|---|---|---|---|---|---|---|---|---|
| 1 | focus | default | "remove the duplicate rows" | `remove_duplicate_rows` | yes | yes | no | yes | no |
| 2 | friend | default | "remove the duplicate rows" | `remove_duplicate_rows` | yes | yes | no | yes | no |
| 3 | focus | no-duplicates | "remove the duplicate rows" | `remove_duplicate_rows` | yes | yes | no | yes | no |
| 4 | focus | default | "remove the duplicate rows and then remove the empty rows" | `multi_step_request` | yes | yes | no | yes | no |
| 5 | friend | default | "remove the empty rows and then remove the duplicate rows" | `multi_step_request` | yes | yes | no | yes | no |
| 9 | friend | default | "hi, what can you do?" | `unclear` | no | n/a (no counts) | no | yes | no |
| 10 | focus | default | "remove duplicates, and delete the blank rows" | `multi_step_request` | yes | yes | no | yes | no |

**Acceptance (design 5.2):** 0 false claims; every count in every reply matches the independent check; the original was unchanged in all 7 runs.

**Not applicable in PR-A:**
- The selection-acceptance rate: decision D1 removed the model selection, so there's no model output to accept.
- Brain-ledger accuracy: the ledger is PR-B.

## Runs

### Run 1: focus, default, "remove the duplicate rows"
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false.
- **Steps executed:** `spreadsheet.analyze` (original) → `spreadsheet.deduplicate` → `spreadsheet.analyze` (re-check of the final file). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Measurements:** deduplicate: input_rows=6, removed=2, output_rows=4, remaining=0.
- **Independent check:** original has 6 rows, 2 duplicates and 1 empty row. The delivered file has 4 rows, 0 duplicates and 1 empty row. The dedupe skill doesn't remove empty rows.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **Phrases:** Focus mode has no opener. The closer was left empty because the analysis findings already ask a question.
- **False claim:** none. Each number is correct: 2 removed, 4 rows, 0 duplicates. The analysis describes the copy: 1 of its 4 rows is empty, which is 25.0%.
- **LLM calls:** Brain, Think.
- **Original hash:** `673fa430692a8291` → `673fa430692a8291` (unchanged).

### Run 2: friend, default, "remove the duplicate rows"
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false.
- **Steps executed:** analyze → deduplicate → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Measurements:** deduplicate: input_rows=6, removed=2, output_rows=4, remaining=0.
- **Independent check:** original has 6 rows, 2 duplicates and 1 empty row. The delivered file has 4 rows, 0 duplicates and 1 empty row.
- **Reply:** "Okay, here's the rundown. Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **Phrases:** opener `friend.opener.rundown`. The closer was left empty because the findings already ask a question.
- **False claim:** none.
- **LLM calls:** Brain, Think.
- **Original hash:** `81efd5321fe992c4` → `81efd5321fe992c4` (unchanged).

### Run 3: focus, no-duplicates, "remove the duplicate rows" (N2 live)
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false.
- **Steps executed:** analyze → deduplicate → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Measurements:** deduplicate: input_rows=4, removed=0, output_rows=4, remaining=0.
- **Independent check:** original has 4 rows, 0 duplicates and 0 empty rows. The delivered file has 4 rows, 0 duplicates and 0 empty rows.
- **Reply:** "Done — there were no duplicate rows to remove. The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **False claim:** none. The reply invents no count; N2's "Removed 3" can't occur.
- **LLM calls:** Brain, Think.
- **Original hash:** `82ca8ec6da14f424` → `82ca8ec6da14f424` (unchanged).

### Run 4: focus, default, "remove the duplicate rows and then remove the empty rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false.
- **Steps executed:** analyze → deduplicate → remove_empty_rows (read dedupe's output) → analyze (re-check). All succeeded.
- **Delivered:** yes, `…deduplicated_manual-session-task-1_no_empty_rows_manual-session-task-2.xlsx`.
- **Measurements:**
  - deduplicate: input_rows=6, removed=2, output_rows=4.
  - remove_empty_rows: input_rows=4, removed=1, output_rows=3.
  - final file: 3 rows, 0 duplicates, 0 empty rows.
- **Independent check:** original has 6 rows, 2 duplicates and 1 empty row. The delivered file has 3 rows, 0 duplicates and 0 empty rows.
- **Reply:** "Removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **Lead word:** none. The design table expected "Done —" here, but decision D4 gives a `multi_step_request` no lead word until PR-B's ledger can confirm every requested part ran. This is intended, not a regression.
- **False claim:** none.
- **LLM calls:** Brain, Think, Planner.
- **Original hash:** `9479dd1b26d3f88f` → `9479dd1b26d3f88f` (unchanged).

### Run 5: friend, default, "remove the empty rows and then remove the duplicate rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false.
- **Steps executed:** analyze → remove_empty_rows → deduplicate (read the empty-rows output) → analyze (re-check). All succeeded. The order is the user's order.
- **Delivered:** yes, `…no_empty_rows_manual-session-task-1_deduplicated_manual-session-task-2.xlsx`.
- **Measurements:**
  - remove_empty_rows: input_rows=6, removed=1, output_rows=5.
  - deduplicate: input_rows=5, removed=2, output_rows=3.
  - final file: 3 rows, 0 empty rows, 0 duplicates.
- **Independent check:**
  - Original: 6 rows, 2 duplicates, 1 empty row.
  - Intermediate: 5 rows, 2 duplicates, 0 empty rows.
  - Delivered: 3 rows, 0 duplicates, 0 empty rows.
- **Reply:** "Okay, here's the rundown. Removed 1 fully-empty row(s). Removed 2 duplicate row(s). The copy has 3 row(s), 0 fully-empty row(s) and 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found. Anything else you want me to look at?"
- **Phrases:** opener `friend.opener.rundown`, closer `friend.closer.anything_else`. The closer was eligible because there were no open findings.
- **False claim:** none.
- **LLM calls:** Brain, Think, Planner.
- **Original hash:** `95bd270f3c72c69b` → `95bd270f3c72c69b` (unchanged).

### Run 9: friend, default, "hi, what can you do?"
- **Intent:** `unclear` (0.97). **`clarification_required`:** false.
- **Steps executed:** analyze (original) only.
- **Delivered:** no.
- **Independent check:** no file was handed back. The original is unchanged.
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn." This is the same text the final review recorded for `main` on this path (`chaining-wip-final-review.md`, V1 "Undelivered turns"), so the undelivered path is unchanged.
- **False claim:** none. The reply doesn't answer the greeting. That's L-5, backlog item 2, and out of scope.
- **LLM calls:** Brain, Think. There was no Voice call.
- **Original hash:** `5132b1e9d5a66962` → `5132b1e9d5a66962` (unchanged).

### Run 10: focus, default, "remove duplicates, and delete the blank rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false.
- **Steps executed:** analyze → deduplicate → remove_empty_rows → analyze (re-check). All succeeded. The planner couldn't confirm the order: the order sentence appears in the reply. "and" alone isn't in `_FORWARD_CONNECTORS`, which is the likely reason. The script doesn't print the planner's `source_phrase`s, though, so which check declined the order wasn't confirmed.
- **Delivered:** yes, `…deduplicated_manual-session-task-1_no_empty_rows_manual-session-task-2.xlsx`.
- **Measurements:**
  - deduplicate: input_rows=6, removed=2, output_rows=4.
  - remove_empty_rows: input_rows=4, removed=1, output_rows=3.
  - final file: 3 rows, 0 of each.
- **Independent check:**
  - Original: 6 rows, 2 duplicates, 1 empty row.
  - Intermediate: 4 rows, 0 duplicates, 1 empty row.
  - Delivered: 3 rows, 0 duplicates, 0 empty rows.
- **Reply:** "Removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. I couldn't tell for sure from your message what order you wanted, so the steps ran in this order: deduplicate, then remove empty rows. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **Order sentence:** present with `PersonalityVoice`. This closes the backlog's F4-with-`PersonalityVoice` item.
- **False claim:** none.
- **LLM calls:** Brain, Think, Planner.
- **Original hash:** `e9fb7243240e3c4e` → `e9fb7243240e3c4e` (unchanged).

## Checks of the counts

Every number in each delivered reply was compared with the pandas read:
- "removed N" was checked against the original's (or the intermediate's) duplicate or empty-row count.
- "The copy has N row(s)" and "N … remaining" were checked against the delivered file.
- The analysis percentages were checked against the analyzed copy (runs 1 and 2: 1 empty row in 4 is 25.0%).

All of them match.

**One caveat on the intermediate files.** Output names are unique only within a turn (F5, backlog item 4), so runs that shared a name overwrote each other's files:
- The dedupe-first intermediate `manual_report_deduplicated_manual-session-task-1.xlsx` (runs 1–4 and 10) was last written by run 10. Reading it after the runs therefore checks run 10's intermediate, not run 4's.
- Run 4's step counts are still consistent with its own checks: the original had 2 duplicates and 1 empty row, and 6 − 2 − 1 = 3, the delivered row count. They're also identical to run 10's, whose inputs and chain were identical.
- Runs 1–3's delivered files were each checked by the script right after the run, before the next run overwrote them.

**Code changed after these runs.** Two things changed after these runs:
- The delivered-reply analysis notes now skip text from a *failed* analysis verdict. That text is already stated by `INITIAL_ANALYSIS_FAILED_SENTENCE`.
- Three docstring/comment edits.

No analysis step failed in any of the 7 runs, so none of these replies would change. The runs weren't repeated.
