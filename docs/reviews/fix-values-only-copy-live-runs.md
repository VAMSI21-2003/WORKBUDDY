# fix/values-only-copy: live runs with Groq

Head: code as reviewed at `8222c7f` (round-2 verdict MERGE); later commits only add review files and docs, plus this live-run script's fixtures. Run with `python scripts/run_dedupe_manually.py --mode <mode> --fixture <fixture> --message "<request>"`, real Groq for Brain and Think (Voice is code; the model is never asked for reply text). 6 runs, no 429. Every number below was checked against an independent read in the script's output: the row counts with pandas, the formula and merged-range counts with a regex over the sheet XML (no pandas, no openpyxl), styles with openpyxl.

Fixtures (all written fresh into `scratch/uploads/manual_report.xlsx`):
- `formulas`: six data rows (Alice 10, Bob 20, an empty row, Alice 10, Carol 30, Bob 20), `double` = `=B*2` in the five non-empty rows, saved results stored as Excel would (XML-patched; LibreOffice can't load xlsx in this container), bold red header, yellow fill, column A 40 wide.
- `formulas-unsaved`: the same, formulas with no saved result (a file written by a script).
- `merged-cells`: the default 6 rows (2 duplicates, 1 empty row) plus a merged note cell `E1:F1`.

| # | Fixture, mode | Request | Intent | clarification_required | Steps executed | File delivered | Independent check | False claim in the reply? | Original hash before -> after |
|---|---|---|---|---|---|---|---|---|---|
| 1 | formulas, focus | remove the duplicate rows | remove_duplicate_rows | False | analyze, deduplicate, analyze (re-check) | yes | rows 6 -> 4, 0 duplicates; original XML 5 formula cells (0 unsaved), output 0; `double` column [20, 40, empty, 60]; header no longer red, fill gone, column A 13 wide | no | f7b2cf9be30f… -> f7b2cf9be30f… (unchanged) |
| 2 | formulas, friend | remove the empty rows and then remove the duplicates | multi_step_request | False | analyze, remove_empty_rows, deduplicate, analyze | yes | rows 6 -> 5 -> 3, 0 empty, 0 duplicates; 5 formula cells -> 0; `double` [20, 40, 60] | no | af8941c6524c… -> af8941c6524c… (unchanged) |
| 3 | formulas-unsaved, focus | remove the duplicate rows | remove_duplicate_rows | False | analyze, deduplicate, analyze | yes | rows 6 -> 4; original XML 5 formula cells, 5 unsaved; output 0 formulas, `double` all empty | no | 89da19931b59… -> 89da19931b59… (unchanged) |
| 4 | formulas-unsaved, friend | remove the duplicate rows and then remove the empty rows | multi_step_request | False | analyze, deduplicate, remove_empty_rows, analyze | yes | rows 6 -> 4 -> 3; 5 formula cells (5 unsaved) -> 0; `double` all empty | no | 0fa5ead610f9… -> 0fa5ead610f9… (unchanged) |
| 5 | merged-cells, focus | remove the duplicate rows | remove_duplicate_rows | False | deduplicate (failed: refused; no analysis prepended) | no | 0 files handed back, nothing under `workspace/outputs/` | no | 372a798cca4a… -> 372a798cca4a… (unchanged) |
| 6 | merged-cells, friend | remove the duplicate rows and then remove the empty rows | multi_step_request | False | deduplicate (failed: refused), remove_empty_rows (skipped) | no | 0 files handed back | no | cb3e718be798… -> cb3e718be798… (unchanged) |

## The replies

**Run 1**
> Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. Your file has 5 formula cell(s). The copy has their last saved values, not the formulas. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%); double: 1 missing value(s) (25.0%). Want me to tackle any of these?

**Run 2**
> Okay, here's the rundown. Done — removed 1 fully-empty row(s). Removed 2 duplicate row(s). The copy has 3 row(s), 0 fully-empty row(s) and 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. Your file has 5 formula cell(s). The copy has their last saved values, not the formulas. None of the issues I check for were found. Anything else you want me to look at?

**Run 3**
> Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. Your file has 5 formula cell(s). The copy has their last saved values, not the formulas. 5 of them had no saved value, so they're empty in the copy, and rows were judged with those cells empty. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%); double: 4 missing value(s) (100.0%). Want me to tackle any of these?

**Run 4**
> Okay, here's the rundown. Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. Your file has 5 formula cell(s). The copy has their last saved values, not the formulas. 5 of them had no saved value, so they're empty in the copy, and rows were judged with those cells empty. I also noticed: double: 3 missing value(s) (100.0%). Want me to tackle any of these?

**Run 5**
> I ran into a problem partway through. The deduplicate step: the sheet has merged cells, and only plain tables (one header row at the top, then the data) are supported for now. I stopped at the deduplicate step and no file was handed back; your original file is unchanged.

**Run 6**
> I ran into a problem partway through. The deduplicate step: the sheet has merged cells, and only plain tables (one header row at the top, then the data) are supported for now. I stopped at the deduplicate step, so remove empty rows never ran and no file was handed back; your original file is unchanged.

## Reading them against rule 1

- Runs 1-4: every count in the reply (rows removed, rows in the copy, formula cells, formula cells with no saved value) equals the independent read above. "The copy has the values only; formatting, column widths and other sheet settings aren't carried over." is true of each delivered file (header colour, fill and column width are gone; no formulas remain). "The copy has their last saved values" is true in runs 1-2 (20, 40, 60); in runs 3-4 the five formulas had no saved result, and the reply says so ("5 of them had no saved value, so they're empty in the copy, and rows were judged with those cells empty"), which matches the empty `double` column.
- Runs 5-6: nothing is delivered, no "Done", no "copy", no "Unnamed" column, no analysis of the refused sheet. "I ran into a problem partway through" is the existing failure wording (review R1-6, deferred): the step did fail, so it's not false, but it reads oddly for a refusal made before any work.
- Rule 2: the original's SHA-256 is identical before and after every run. 0 changes.
- Not run: real Excel or LibreOffice files (LibreOffice can't open xlsx here; saved results are XML-patched), `.xls`.
