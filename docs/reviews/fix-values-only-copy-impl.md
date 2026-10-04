# fix/values-only-copy: implementation notes

Writer's notes for the reviewer to check, not to trust. Design: `docs/design/values-only-copy.md`. Base: `origin/main` at `63427e9`.

## What changed

- `skills/excel/workbook.py`: `plain_table_sheet_name` (single sheet, then plain table; both skills call it), `plain_table_problem` (every sheet; the Inspector and the planner call it), `formula_cell_counts` and `FormulaCounts`. Detection reads the file with openpyxl (read-only, `data_only=True`); merged cells come from a streaming `expat` scan of the sheet part, because openpyxl's read-only mode doesn't expose them. Refusal texts are fixed.
- `skills/excel/dedupe.py`, `remove_empty_rows.py`: call `plain_table_sheet_name` instead of `single_sheet_name`.
- `buddy/inspector.py`: `_plain_table_finding` (an independent re-check of each successful file-changing step's input, next to `_sheet_list_problem`) and `OriginalMeasurer` (turn-level wrapper that records `OriginalFacts` from the file the first file-changing step read).
- `buddy/models.py`: `OriginalFacts`; `VerificationResult.original_facts`.
- `buddy/outcome.py`: `OutcomeReport.original_facts`; `VALUES_ONLY_SENTENCE` on every rendered outcome (after the file sentence); the formulas sentences from the measurement only.
- `buddy/planner.py`: `_cleaning_skills_would_refuse` also true for a non-plain sheet, so a refused file isn't analysed.
- `buddy/engine_factory.py`: wires `OriginalMeasurer`.
- `skills/excel/analyze.py`: P-2, `os.environ["YDATA_PROFILING_NO_ANALYTICS"] = "1"` just before the `ydata_profiling` import.

## Deviations from the request, and judgement calls

- **Duplicate headers are refused too** (a fourth rule; pandas renames `a` to `a.1`). Easy to remove: one branch in `_layout_problem`, one test.
- **A title above the header** is refused as "a column in the sheet has no header" (row 1 has a value in column A only), and a blank row 1 as "the sheet's header isn't on the first row".
- **The formatting sentence is unconditional**, per the design note.
- **Formulas with no saved value** are disclosed ("rows were judged with those cells empty") rather than refused.
- The values-only sentence is rendered whenever `render_outcome` is called, which is only for a verified delivery, not only when a file sentence is present.

## Tests

- New: `tests/skills/excel/test_plain_table.py` (detector, skills, formula counts against a raw XML read), `tests/buddy/test_values_only_copy.py` (through `build_engine`, three speakers, dedupe / empty rows / chain, on formulas saved, formulas unsaved, plain, merged cells, header under a title; numbers checked against an openpyxl/XML read that doesn't use pandas or the skills; Inspector independence with skills that don't refuse; measurement failure; renderer), a P-2 test in `tests/skills/excel/test_analyze.py`.
- Changed: exact-reply tests in `test_voice.py`, `test_outcome.py`, `test_multi_sheet.py` now include the values-only sentence.
- Mutation checks done by the writer (each made a new test fail): merged-cell scan off; Inspector check off; `OriginalMeasurer` unwired; sentence removed; analytics `setdefault`; duplicate-header rule off; planner check off; unsaved count off.
- `scripts/review_harness/formulas_probe.py` on this branch: the dedupe step is refused ("the sheet has merged cells, …"), no file is handed back (the probe itself then fails at `outputs[0]`, as it should).

## Results

Suite: 1684 passed (`origin/main`: 1568). Lint (`ruff check buddy tests`): same errors as `origin/main` by file and rule (24 lines, none new).

## Not done here

Keeping formulas and formatting; refusing files whose formulas have no saved value (`docs/backlog.md` item 1b).
