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

# Fixed head: live runs after the review fixes

Five runs with real Groq (`python scripts/run_dedupe_manually.py --mode focus --fixture <fixture> --message "<request>"`, key attached by the cloud proxy), on the working tree that adds `docs/reviews/fix-multi-sheet-fixes.md`. **No 429; all five runs completed.** The script writes a fresh fixture each run, so each run has its own hash. Each reply is quoted in full. Voice mode is `focus`; on a turn that delivers nothing, the reply is `TemplateSpeaker`'s factual one in any mode.

The script gained two fixtures for these runs (`damaged`, `password-protected`) and now reads a zip workbook's sheet list from the declared `<sheet>` entries of `workbook.xml` for its independent check. The `password-protected` fixture is a stand-in (OLE2 magic bytes plus the UTF-16-LE stream name `EncryptedPackage`), not a file Excel encrypted.

| # | Fixture | Message | Intent | `clarification_required` | Steps executed | Delivered | Independent check | False claim | Original hash before → after |
|---|---|---|---|---|---|---|---|---|---|
| 1 | `two-sheets` (Q1 6 rows, Q2 2 rows) | "remove the duplicate rows" | `remove_duplicate_rows` | false | deduplicate ✗ (refused). **No analyze step.** | no (0 files, none written) | original sheets `['Q1','Q2']` | none | `af78f20f…a3dd` → same |
| 2 | `two-sheets` | "remove the empty rows and then remove duplicates" | `multi_step_request` | false | remove empty rows ✗ (refused), deduplicate skipped. **No analyze step.** | no | `['Q1','Q2']` | none | `b40d8c9c…f1ea` → same |
| 3 | `damaged` (declares Q1 and Q2, `sheet2.xml` removed) | "remove the duplicate rows" | `remove_duplicate_rows` | false | deduplicate ✗ (refused). No analyze step. | no | declared sheets `['Q1','Q2']` (openpyxl and pandas would list only `Q1`) | none | `168e60c2…05ee` → same |
| 4 | `password-protected` (stand-in) | "remove the duplicate rows" | `remove_duplicate_rows` | false | deduplicate ✗ (refused). No analyze step. | no | pandas can't read it (`ImportError`, no `xlrd`), so the check can only say "unreadable" | none | `fb3fb841…4248` → same |
| 5 | `default` (one sheet, 6 rows, 2 duplicates, 1 empty row) | "remove the duplicate rows" | `remove_duplicate_rows` | false | analyze ✓, deduplicate ✓, re-check analyze ✓ | yes, 1 file, `['Sheet1']` | 6 rows → 4 rows, 0 duplicates left; 1 empty row left, which dedupe doesn't remove | none | `34baf36e…2f81` → same |

Replies:

1. "I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
2. "I ran into a problem partway through. The remove empty rows step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the remove empty rows step, so deduplicate never ran and no file was handed back; your original file is unchanged."
3. "I ran into a problem partway through. The deduplicate step: the workbook looks damaged: it lists sheets that can't be read. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
4. "I ran into a problem partway through. The deduplicate step: the workbook is password-protected, so it can't be opened. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
5. "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"

What the runs show:
- **M4 is fixed on real Groq.** Runs 1 and 2 are the same two-sheet requests as before (`here's what I noticed … (16.7%). Want me to tackle any of these?` led the replies then). The replies now hold only the refusal.
- **H2 and M2 are fixed.** The damaged workbook in run 3 is refused as damaged. Before the fix it was delivered with one sheet and "Done — removed 2 duplicate row(s)…" (review §3.2).
- **M1 is fixed for the stand-in.** Run 4 says "password-protected", not "an .xls workbook … xlrd isn't installed".
- **One-sheet workbooks are unchanged** (run 5): analyzed, cleaned, delivered, 6 → 4 rows, the same reply as in the earlier live runs.
- In every run the original's hash is unchanged, and runs 1 to 4 wrote nothing under `workspace/outputs/manual-workspace/`.
- LLM calls: Brain and Think (and the planner in run 2). The Voice model wasn't called on the refused turns; run 5 is a delivered turn, where code renders the reply.

**Not covered by a live run:** a real `msoffcrypto`/Excel-encrypted file (only the stand-in), a legacy `.xls`, and a crafted `[Content_Types].xml` (those three are covered by the faked-LLM tests through `build_engine`).

# Re-review: live runs by the independent re-reviewer

Two runs with real Groq on head `04315d3`, by the session that wrote `fix-multi-sheet-review2.md` (full per-run details are in its §6). **No 429; 2 of 2 completed.**

| # | Fixture | Mode | Message | Intent | `clarification_required` | Steps executed | Delivered | Independent check | False claim | Original hash before → after |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `two-sheets` | focus | "remove the duplicate rows and then remove the empty rows" | `multi_step_request` | false | deduplicate ✗ (refused: "the workbook has 2 sheets…"), remove empty rows skipped. No analyze step. | no (0 files, none written) | original sheets `['Q1','Q2']` | none | `0b38afd3…41ea` → same |
| 2 | `password-protected` (stand-in) | friend | "remove the duplicate rows" | `remove_duplicate_rows` | false | deduplicate ✗ (refused: "the workbook is password-protected, so it can't be opened"). No analyze step. | no (0 files, none written) | script: `(unreadable: ImportError)`; reviewer: 608-byte stand-in, not a valid OLE2 container (real `msoffcrypto` files get the same reply in the re-review grid) | none | `fb3fb841…4248` → same |

Replies:
1. "I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step, so remove empty rows never ran and no file was handed back; your original file is unchanged."
2. "I ran into a problem partway through. The deduplicate step: the workbook is password-protected, so it can't be opened. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
