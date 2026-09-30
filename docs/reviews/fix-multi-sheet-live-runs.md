# Live runs: `fix/multi-sheet` (backlog item 1a, review M3)

**Date:** 2026-09-30. **Provider:** Groq (`GroqProvider`, key attached by the cloud proxy). **Script:** `python scripts/run_dedupe_manually.py --mode focus --fixture two-sheets --message "<request>"`, which runs `build_engine` with `PersonalityVoice`.

**Runs completed:** 3 of 3 planned. No 429 or other provider error.

**Fixture (`two-sheets`, new):** `scratch/uploads/manual_report.xlsx`, written fresh for each run. It has two sheets:
- `Q1`: the default fixture's 6 rows (`name`/`amount`), with 2 exact duplicates (Alice, Bob) and 1 fully-empty row mid-file.
- `Q2`: 2 distinct rows (Zed, Yan).

**Independent check:** after each run, the script:
- re-reads the original's sheet names with pandas (`pd.ExcelFile(...).sheet_names`);
- counts the files handed back;
- lists every `.xlsx` written under `workspace/outputs/manual-workspace/` during the run (by modification time);
- hashes the original with SHA-256 before and after.

The fixture is re-written each run, and `.xlsx` files embed a timestamp, so the hash differs between runs. Within a run it must not change.

**LLM calls:** the script wraps the provider and prints the first line of each call's system prompt.

## Summary

| # | Mode | Message | Intent | `clarification_required` | Delivered | Reply states the real reason | False claim | Original unchanged | Voice model called |
|---|---|---|---|---|---|---|---|---|---|
| 1 | focus | "remove the duplicate rows" | `remove_duplicate_rows` | false | no | yes | no | yes | no |
| 2 | focus | "remove the empty rows" | `remove_empty_rows` | false | no | yes | no | yes | no |
| 3 | focus | "remove the duplicate rows and then remove the empty rows" | `multi_step_request` | false | no | yes | no | yes | no |

**Result:**
- 0 false claims. No reply says or implies that anything was removed, cleaned, saved or handed back.
- No reply contains "cleaned copy", "ready" or "the copy".
- Every reply says no file was handed back and the original is unchanged.
- No output file was written, and the original was byte-identical in all 3 runs.

The mode doesn't change these replies: a turn that delivers nothing gets `TemplateSpeaker`'s factual reply from either speaker.

**One observation (not a false claim, deferred):** each reply leads with analysis findings computed on sheet `Q1` only, and they don't say so. See the note after run 3.

## Runs

### Run 1: focus, "remove the duplicate rows"
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false (Think confidence 0.98).
- **Planned:** `spreadsheet.analyze` (original), then `spreadsheet.deduplicate`.
- **Steps executed:**
  - `spreadsheet.analyze` on the original: success, read-only.
  - `spreadsheet.deduplicate` on the original: failure. Its `failure_reason` is "the workbook has 2 sheets, and only single-sheet workbooks are supported for now".
  - No re-check ran, because the chain stopped (`stopped_at = spreadsheet.deduplicate`).
- **Verification:** `passed=False`. The deduplicate verdict's only finding is the skill's message. There are no measurements.
- **Delivered:** no. Files handed back: 0. Files written under `workspace/outputs/manual-workspace/` this run: none.
- **Independent check:** the original's sheets are `['Q1', 'Q2']`. That's 2 sheets, matching the "2 sheets" in the reply.
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- **False claim:** none. Nothing is claimed as done. The reason is the skill's real message, and its count (2) is correct. The analysis numbers are correct for `Q1` (1 empty row of 6 is 16.7%), but they cover only that sheet; see the note below.
- **LLM calls:** Brain, Think.
- **Original hash (SHA-256):** `c0f8677d92626d2e4c798d7b630689468be5827fadd3036763f324f529521f71` → `c0f8677d92626d2e4c798d7b630689468be5827fadd3036763f324f529521f71` (unchanged).

### Run 2: focus, "remove the empty rows"
- **Intent:** `remove_empty_rows` (0.99). **`clarification_required`:** false (Think confidence 0.99).
- **Planned:** `spreadsheet.analyze` (original), then `spreadsheet.remove_empty_rows`.
- **Steps executed:**
  - `spreadsheet.analyze` on the original: success.
  - `spreadsheet.remove_empty_rows` on the original: failure, with `failure_reason` "the workbook has 2 sheets, and only single-sheet workbooks are supported for now".
  - The chain stopped there (`stopped_at = spreadsheet.remove_empty_rows`), so no re-check ran.
- **Verification:** `passed=False`. The only finding is the skill's message.
- **Delivered:** no. Files handed back: 0. Files written this run: none.
- **Independent check:** the original's sheets are `['Q1', 'Q2']`.
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? I ran into a problem partway through. The remove empty rows step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the remove empty rows step and no file was handed back; your original file is unchanged."
- **False claim:** none.
- **LLM calls:** Brain, Think.
- **Original hash (SHA-256):** `97fc5435286e4178fb355e865f79bb85d9781ff7c34147306596244a324fd1d0` → `97fc5435286e4178fb355e865f79bb85d9781ff7c34147306596244a324fd1d0` (unchanged).

### Run 3: focus, "remove the duplicate rows and then remove the empty rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false (Think confidence 0.99).
- **Planned (ClaudePlanner):** `spreadsheet.analyze` (original), then `spreadsheet.deduplicate` (step-1), then `spreadsheet.remove_empty_rows` (step-2, depends on step-1). That's the stated order.
- **Steps executed:**
  - `spreadsheet.analyze` on the original: success.
  - `spreadsheet.deduplicate` on the original: failure, with the refusal as its `failure_reason`.
  - `spreadsheet.remove_empty_rows`: skipped. It would have read step 1's output, but there was none.
- **Verification:** `passed=False`. The deduplicate verdict has the refusal. There's no `final_output` verdict, because no final file exists.
- **Delivered:** no. Files handed back: 0. Files written this run: none.
- **Independent check:** the original's sheets are `['Q1', 'Q2']`.
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step, so remove empty rows never ran and no file was handed back; your original file is unchanged."
- **False claim:** none. The reply correctly says the second step never ran.
- **LLM calls:** Brain, Think, Planner.
- **Original hash (SHA-256):** `d2bc9b53733d1dc47f4c383337a87f76bf7ca2873b8a67efab240a8a6bbf690d` → `d2bc9b53733d1dc47f4c383337a87f76bf7ca2873b8a67efab240a8a6bbf690d` (unchanged).

## Note: the analysis covers the first sheet only

In every run, the reply's "here's what I noticed" findings come from `spreadsheet.analyze`, which reads only the first sheet (`Q1`). The numbers are true for `Q1`, but the reply doesn't name the sheet. Across the workbook, 1 of 8 rows is empty (12.5%), not 16.7%.
- This isn't an outcome claim, and nothing was delivered.
- But it's a statement about the user's file that's incomplete by omission.
- It's followed by "Want me to tackle any of these?", which the row-cleaning skills can't do on this workbook.

It's out of scope for this branch (the user chose the smallest fix: refuse in the two row-cleaning skills). It's listed under "Deferred" in `docs/reviews/fix-multi-sheet-impl.md` and in `docs/backlog.md` item 1a.
