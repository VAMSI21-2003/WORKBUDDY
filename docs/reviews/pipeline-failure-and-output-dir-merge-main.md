# feat/pipeline-failure-and-output-dir: merge of origin/main

Merge of `origin/main` (7bcf10f) into `feat/pipeline-failure-and-output-dir`. No rebase, no
force-push. Not merged to `main`.

## Precondition

`skills/excel/workbook.py` exists on `origin/main` (blob d61e59c), so the multi-sheet work is
in main.

## What conflicted

One file, `scripts/run_dedupe_manually.py`, in three hunks. Nothing under `buddy/`, `core/` or
`skills/` conflicted; git auto-merged `buddy/hands.py`, `skills/excel/dedupe.py`,
`skills/excel/remove_empty_rows.py`, `docs/backlog.md` and `tests/buddy/test_hands.py`.

| Hunk | This branch | main | Resolution |
|---|---|---|---|
| Imports | `from datetime import UTC, datetime` | `import time`, `import zipfile` | All three kept. |
| Start of the output check | `handed_back: Path \| None = None` | `print(f"original sheets: ...")` (declared-sheet check) | Both kept, sheet line first. |
| End of `_run_turn` | per-turn hash, `return handed_back` | "files written this run" listing | Both kept, with the adaptation below. |

Kept from main: the `two-sheets`, `damaged` and `password-protected` fixtures, `_sheet_names`
(declared sheets read from `workbook.xml`), and the "original sheets" / "output sheets" lines.
Kept from this branch: `--second-message`, the per-turn output folders, and withholding files
on a failed turn.

**One adaptation beyond picking sides.** Main's "files written this run" listing used
`started` and `original_before` from `main()` and a non-recursive `glob("*.xlsx")` on
`workspace/outputs/manual-workspace`. On this branch the work is in `_run_turn` and files sit
in `<session>/<run>/tNNNN/` folders, so the verbatim merge would have raised a `NameError` and
listed nothing. I moved `started = time.time()` into `_run_turn` (removed it from `main()`),
switched to `rglob`, and print paths relative to that folder, labelled "this turn". The
"original file hash" line in `_run_turn` is this branch's per-turn version.

## Other change (comment only)

`requirements.txt`, finding L1 of the review: the comment above `setuptools>=68,<81` now says
`pkg_resources` is missing from setuptools 82 and later, the failure happens when the analysis
runs rather than at import, and a fresh uv venv on Python 3.11 lacks setuptools too. The pin
itself is unchanged.

## Checks

- `python -m pytest -q`: **942 passed**, 11 warnings, 103.6 s.
- `ruff check buddy tests`: 24 output lines on both this branch and `origin/main` (checked out
  in a separate worktree); after stripping line and column numbers the two outputs are
  identical, so no new lint errors. `ruff check scripts/run_dedupe_manually.py` is clean.

## Live runs (real Groq, focus mode, 2 runs, no 429)

Original's hash is the whole-run hash before and after.

### Run 1: default fixture, `--second-message`

Turn 1, "remove duplicate rows from this file":
- Intent `remove_duplicate_rows` (0.99). `clarification_required`: False. Engine failure: none.
- Steps: `spreadsheet.analyze` (original) -> `spreadsheet.deduplicate` -> `spreadsheet.analyze`
  (re-run on the final file). All success; verification passed.
- File delivered: yes, `.../run-20261003T182335089653/t0001/manual_report_deduplicated_manual-session-task-1.xlsx`.
- Independent check: original 6 rows, 2 duplicates, 1 empty row; output 4 rows, 0 duplicates,
  1 empty row (dedupe doesn't touch it); sheets `['Sheet1']`; inside the turn's folder.
- Reply: "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s)
  remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing
  value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- False claim: none. Every number matches the independent check (the 25% is 1 of 4 rows, from
  the re-run on the output).

Turn 2, "remove the empty rows":
- Intent `remove_empty_rows` (0.99). `clarification_required`: False. Engine failure: none.
- Steps: `spreadsheet.remove_empty_rows` only. Success; verification passed.
- File delivered: yes, `.../t0002/manual_report_no_empty_rows_manual-session-task-0.xlsx`.
- Independent check: output 5 rows, 0 empty rows, sheets `['Sheet1']`; inside turn 2's folder.
- Reply: "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s)
  remaining. Your cleaned copy of manual_report.xlsx is ready."
- False claim: none.

Two-turn checks: different paths True; different folders True; turn 1 file hash
`cfa1071041e17e32e4d78d22b2e018a65e81502bfce735ca6603c1b115f099be` before and after turn 2
(unchanged).

Original hash: `a5086822e376225022eca605615a7803561e027a9f6a9912eb45059ac1a8b965` ->
same (unchanged).

Observation, not a defect: turn 2 ran on the original upload, not on turn 1's output, so the
turn 2 file still has the 2 duplicates (the check printed `duplicate rows found: 2`). The
reply makes no claim about duplicates, so it is accurate. A user who expects the turns to
stack would be surprised; that is a product decision, not something this merge changes.

### Run 2: two-sheets fixture, "remove the duplicate rows"

- Intent `remove_duplicate_rows` (0.99). `clarification_required`: False. Engine failure: none
  (the stages all ran; the step failed).
- Steps: `spreadsheet.deduplicate` -> failure: "the workbook has 2 sheets, and only
  single-sheet workbooks are supported for now". Chain stopped there; verification
  `passed=False`.
- File delivered: no. Independent check: files handed back 0; original sheets `['Q1', 'Q2']`;
  no files written this turn.
- Reply: "I ran into a problem partway through. The deduplicate step: the workbook has 2
  sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate
  step and no file was handed back; your original file is unchanged."
- False claim: none. "No file was handed back" and "original unchanged" both verified.
- Original hash: `a115f555cd1bac8022b8b62bd3ff0c9c891a6c0184e36e10c39c41228324321c` -> same
  (unchanged).

## Not covered

The `damaged` and `password-protected` fixtures were merged in but not run live; only the two
requested runs were done. Two runs on one fixture each is not a representative sample of the
model's behaviour.
