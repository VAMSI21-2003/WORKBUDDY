# Implementation: `fix/multi-sheet` (backlog item 1a, review M3)

**Source:** `docs/backlog.md` item 1a; `docs/reviews/verified-outcome-a-review.md` finding M3.
**Base:** `origin/main` at `310f660`.
**Rule-touching:** yes, rule 1 (a delivered file silently lost sheets while the reply called it "the copy"). It needs an independent review by a separate session (`docs/reviews/fix-multi-sheet-review.md`) before merging. That review hasn't been done. This document is the implementer's account, not a review.
**Live runs:** `docs/reviews/fix-multi-sheet-live-runs.md`. 3 of 3 runs completed with Groq. Nothing was delivered, no reply made a false claim, and the original was byte-identical in every run.

## What changed, in one paragraph

`ExcelDeduplicateSkill` and `ExcelRemoveEmptyRowsSkill` now count the workbook's sheets before they read anything. If there's more than one, they fail with the fixed message "the workbook has N sheets, and only single-sheet workbooks are supported for now" and write nothing. Hands now records a failing chain step's own errors as its `failure_reason`, so that message reaches the reply instead of the verifier's generic "execution did not produce an output file". The Inspector gains a structural check: every successful file-changing step that read a workbook must hand on a workbook with the same sheet names in the same order. A skill that drops or renames a sheet fails verification, whatever it reported. A one-sheet workbook gets the same reply, rows and verdict as on `main`. The only difference is that its sheet keeps its name instead of becoming `Sheet1`.

## Design constraint applied

The user decided on the smallest honest fix: refuse, don't clean per sheet. Per-sheet cleaning would need per-sheet measurements in the Inspector and Outcome code, which another branch is changing, and it would risk new false counts. Nothing in this branch adds per-sheet reading, cleaning or measurement.

## Files

| File | Change |
|---|---|
| `skills/excel/workbook.py` (new) | `single_sheet_name(path)` returns the only sheet's name, or raises `WorkbookRefused` with a user-facing reason. It picks the reader from the file's content. A zip (`.xlsx` content) is opened with `openpyxl.load_workbook(handle, read_only=True)`, and `sheetnames` includes chart sheets. An OLE2 file (a legacy `.xls`) uses `xlrd` if it's importable. Otherwise it refuses with "the sheets in an .xls workbook can't be counted because the xlrd library isn't installed". Anything else gets "could not read …: it isn't an Excel workbook". |
| `skills/excel/dedupe.py`, `skills/excel/remove_empty_rows.py` | Call `single_sheet_name` first. On `WorkbookRefused`, return `FAILURE` with the message as the only error, before any read or write. Read that sheet with `sheet_name=`, and write the output with the same `sheet_name=`. |
| `buddy/hands.py` | When an in-chain step's Skill reports `FAILURE` with errors, set the step's `failure_reason` to `"; ".join(result.errors)`. That's 4 lines. |
| `buddy/models.py` | The `StepResult.failure_reason` docstring now covers that case. |
| `buddy/inspector.py` | `_sheet_names(path)` and `_sheet_list_problem(step)` are new. `CompositeVerifier._verify_step` calls the check before the capability's verifier. That's 3 lines in the class, plus about 50 lines of module-level helpers placed just above `FINAL_OUTPUT_CAPABILITY`, away from the verifiers and `_InputCheck` that the other branch is editing. |
| `scripts/run_dedupe_manually.py` | A `--fixture two-sheets` option. The script now prints the original's and the output's sheet names, the full SHA-256 (it printed the first 16 characters before), and any file written under `workspace/outputs/manual-workspace/` during the run. |
| `tests/buddy/test_multi_sheet.py` (new) | End-to-end tests through `build_engine` (see "Tests"). |
| `tests/buddy/test_inspector_sheets.py` (new) | Unit tests for the sheet-list check. |
| `tests/skills/excel/test_workbook.py` (new) | Unit tests for `single_sheet_name` and both skills' refusal. |
| `tests/buddy/test_hands.py` | 2 tests for `failure_reason`. |
| `tests/buddy/test_inspector.py` | 2 existing tests now write real one-sheet workbooks (see "Changed tests"). |
| `docs/backlog.md` | Item 1a: status line and the open follow-ups. |

## How the pieces cover the acceptance criteria

1. **A delivered file never silently loses a sheet.** It's covered twice:
   - The skills refuse any workbook with more than one sheet, so they never produce a one-sheet copy of a multi-sheet workbook.
   - Independently, the Inspector fails any file-changing step whose output's sheet names differ from its input's. This runs inside Hands (the per-step gate that stops a chain) and again in Inspector.
2. **The chain case.** Step 2 reads step 1's output, so step 2's sheets are compared with *that* file, not the original. Step 1's are compared with the original. Each link is checked, so the final file's sheet names equal the original's. `_verify_final_file` needs no change: the final file is the last step's own output, already checked. Test: `test_a_chain_step_that_drops_a_sheet_of_the_previous_steps_output_fails`.
3. **No reply calls a one-sheet file "the copy" of a multi-sheet workbook.** A refused or failed step means nothing is delivered. `_verified_delivery` is then false, so neither speaker renders the outcome block ("The copy has …", "Your cleaned copy of … is ready."). Both use `TemplateSpeaker._stopped_message`, which ends with "…no file was handed back; your original file is unchanged."

## Replies (two-sheet workbook, faked LLM, all three speakers give the same text)

- **Single dedupe:** "I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- **Chain, dedupe then empty rows:** "… I stopped at the deduplicate step, so remove empty rows never ran and no file was handed back; your original file is unchanged."
- **On `main`, for comparison:** "Done — removed 1 duplicate row(s). The copy has 2 row(s), … Your cleaned copy of report.xlsx is ready." `Q2` was gone.

If the first sheet has analysis findings, the reply leads with "Before anything else, here's what I noticed: …" (see "Deferred", item 1).

## One-sheet workbooks: unchanged

I ran a scratch harness on `main` (`310f660`) and on this branch with the same faked LLM replies. It covered 3 requests (dedupe, empty rows, chain) × 3 speakers × 2 sheet names (`Sheet1`, `Data`), 18 runs per tree. It compared the reply, the output paths, the output rows and `verification_result.passed`.
- All 18 are identical.
- The only difference: with a sheet named `Data`, `main`'s output sheet was `Sheet1`, and this branch's is `Data`.

That rename would otherwise fail the new sheet-list check, which is why the skills now write the sheet under its own name. `test_a_one_sheet_workbook_behaves_exactly_as_before` pins 6 of `main`'s replies (dedupe and chain × 3 speakers) for both sheet names.

## Tests

`tests/buddy/test_multi_sheet.py`, all through `build_engine` with a faked LLM:
- **`test_a_multi_sheet_workbook_is_refused_and_nothing_is_delivered`:** 2 and 3 sheets × {dedupe, empty rows, chain} × {template, friend, focus}, 18 cases. Each asserts:
  - nothing is delivered and the first file-changing step is where it stopped;
  - in the chain, step 2 was skipped;
  - the reply contains the skill's exact message with the real N, "no file was handed back" and "your original file is unchanged";
  - the reply contains no "cleaned copy", "ready" or "the copy", no `_COMPLETION_CLAIM` match, no raw capability id, and no "did not produce an output file";
  - no `.xlsx` was written under `workspace/`;
  - the original's SHA-256 is identical before and after, and its sheet names are intact;
  - the Voice model is never called.
- **`test_a_one_sheet_workbook_behaves_exactly_as_before`:** 12 cases, described above. Each also checks the original's hash.
- **`test_a_skill_that_drops_a_sheet_fails_verification`:** 3 speakers. A fake dedupe with `main`'s behaviour (no refusal, first sheet only, written as `Sheet1`). Verification fails with "the output's sheets (Sheet1) don't match its input's (Q1, Q2), so a sheet was lost or renamed", nothing is delivered, and the hash is unchanged.
- **`test_a_skill_that_renames_the_only_sheet_fails_verification`:** a one-sheet `Data` workbook, and the fake writes `Sheet1`.
- **`test_a_chain_step_that_drops_a_sheet_of_the_previous_steps_output_fails`:** 3 speakers. The fake step 1 keeps both sheets and passes. The fake step 2 drops `Q2` from step 1's output and fails. The chain stops at step 2, there's no `final_output` verdict, and nothing is delivered.

**Unit tests:**
- `test_workbook.py`: 1, 2 and 3 sheets; a chart sheet counts; an `.xls` name holding `.xlsx` content; a legacy `.xls` with `xlrd` missing (`sys.modules["xlrd"] = None`) and with a fake `xlrd` (1 and 2 sheets, resources released); a file that isn't a workbook; a missing file. For both skills: refusal with no output and the original unchanged, `.xls` without `xlrd`, and the one-sheet name kept.
- `test_inspector_sheets.py`: matching lists pass through to the capability verifier. A dropped, renamed or reordered sheet fails without calling it. An `.xls` input holding `.xlsx` content is compared. An unreadable output fails. Read-only, re-check and input-less steps aren't compared.
- `test_hands.py`: the failing step's errors become `failure_reason`. A failure with no errors, and a failing read-only step, leave it empty.

**Checked against `main`:** I copied `test_multi_sheet.py` into a `main` worktree. 31 of its 37 tests fail there. The 6 that pass are the one-sheet `Sheet1` cases, which are meant to be identical.

## Changed tests

`test_composite_verifier_does_not_fail_the_turn_on_a_failed_re_check` and `test_composite_verifier_does_not_fail_the_turn_on_a_failed_read_only_step` (`tests/buddy/test_inspector.py`) use fake verifiers with `orig.xlsx` → `final.xlsx` paths that didn't exist on disk. The new check correctly fails a step whose workbook it can't read, so both tests now write real, identical one-sheet workbooks first. What they assert is unchanged.

## Results

- `python -m pytest -q`: **776 passed**, 11 warnings (`main`: 710 passed).
- `ruff check buddy tests`: **22 errors, the same 22 as `main`** (compared by file and rule). None are in new or changed lines. `ruff check skills scripts`: clean.

## Deviations and judgment calls (for the reviewer)

1. **The reader is picked from the content, not the extension.** The instruction was: `.xlsx` with openpyxl read-only; `.xls` with `xlrd` if importable, otherwise fail honestly. That's what happens for real files. But two existing tests (`test_full_slice_deduplicates_an_xls_upload`, `test_full_slice_chains_two_steps_from_an_xls_upload`) upload `.xls` files holding `.xlsx` content, which pandas reads by content. An extension-only rule would have refused them for lack of `xlrd` even though openpyxl can count their sheets. So a zip is always opened with openpyxl (through a file handle, because openpyxl rejects the `.xls` name), and only a real OLE2 `.xls` needs `xlrd`.
2. **Hands relays a skill's failure reason.** Without this, the reply said "The deduplicate step: execution did not produce an output file", not the refusal. The change applies to every in-chain step whose Skill reports `FAILURE` with errors, so other failures now show the Skill's own reason too. For example, "could not read '…': …" replaces "execution did not produce an output file". The existing suite has no test whose reply depends on that wording. The Skill's text is a reason for failing, never a success claim, and the turn delivers nothing. But it is self-reported: the N in "the workbook has N sheets" is the skill's count, not re-counted by the Inspector. The live runs checked it independently.
3. **The sheet-list check is fail-safe.** It applies to any successful, non-read, non-re-check step whose input path ends in `.xlsx`, `.xlsm` or `.xls`. If either file's sheet names can't be read, the step fails. So a future write skill that turns a workbook into, say, a `.csv` or a report would fail this check. That's deliberate: that would also be a sheet silently lost. Such a skill would need an explicit exemption.
4. **The order counts.** Sheets that are the same but reordered fail too. Neither skill reorders, and a reorder is a visible change the reply wouldn't mention.
5. **One-sheet output names changed.** A one-sheet workbook's sheet keeps its name (it was always `Sheet1`). This is needed for the check. It changes no reply, row or verdict.

## Deferred

1. **The analysis of a multi-sheet workbook describes its first sheet only, and doesn't say so.** `spreadsheet.analyze` and `SpreadsheetAnalysisVerifier` read `pd.read_excel(path)`, the first sheet. On a refused workbook the reply still leads with "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%)…". That's true for `Q1` (6 rows), but the percentage isn't the workbook's (1 of 8 rows is 12.5%). It then asks "Want me to tackle any of these?", which the skills can't do on this workbook. It isn't an outcome claim, and no file is delivered, but it's a statement about the file that's incomplete by omission. The smallest follow-ups: skip the analysis on a multi-sheet workbook, or label the findings with the sheet name. Out of scope here by the user's decision; added to `docs/backlog.md` item 1a.
2. **Per-sheet cleaning** (keep every sheet, clean each one or the named one). It needs per-sheet measurements in the Inspector and Outcome code. Wait for the other branch.
3. **"I ran into a problem partway through."** This opens the reply even for a single-step turn that stopped at its first step. The wording predates this branch (`_stopped_message`), and it's imprecise, not false.
4. **Real `.xls` uploads can't be cleaned at all without `xlrd`.** This was already true on `main` (`pd.read_excel` needs it). Now the message says why. Adding `xlrd` is a dependency decision.
