# Values-only copy: refuse what isn't a plain table, and say what the copy doesn't keep

Branch `fix/values-only-copy`. Closes finding P-1 (and P-2) of `docs/reviews/verified-outcome-b-review.md`, round 5. Rule-touching (rule 1), so it goes through `docs/process/review-loop.md`.

## Problem

`ExcelDeduplicateSkill` and `ExcelRemoveEmptyRowsSkill` read one sheet with `pandas.read_excel` and write it back with `to_excel`. The copy therefore holds values only:

- formulas become their last saved value, or an empty cell when the file has none saved (a file written by a script, never opened in Excel);
- formatting, number formats, column widths, filters and everything else beyond the values are gone;
- on a sheet that isn't a plain table, pandas misreads the layout: a merged note cell became an `Unnamed: 3` column, a header on row 3 turns row 1 into the header, two columns called `Total` come back as `Total` and `Total.1`.

The reply still said "Done — … Your cleaned copy … is ready." (`scripts/review_harness/formulas_probe.py`: the `double` column is empty and an `Unnamed: 3` column appeared.)

## Decisions

### 1. A sheet that isn't a plain table is refused (like a multi-sheet workbook)

A plain table: row 1 is the header and every column up to the last header has one; the data starts on row 2; no merged cells; no two headers equal. Detection is `plain_table_sheet_name` / `plain_table_problem` in `skills/excel/workbook.py`, next to `single_sheet_name` / `sheet_names`:

- It reads the file with **openpyxl** (read-only, `data_only=True`: the cell values the skills' pandas read sees), never from a skill's DataFrame. Row and column positions come from openpyxl's cells.
- Merged cells aren't exposed by openpyxl's read-only mode, so the sheet's own XML (`sheet._get_source()`) is scanned for `<mergeCell>` with a streaming parser that builds no tree.
- Refused, in this order, each with fixed text and nothing from the file: `the sheet has merged cells`; `the sheet's header isn't on the first row` (row 1 is empty and something is below it); `a column in the sheet has no header` (a gap in row 1 before the last header, or data to the right of it); `two columns in the sheet have the same header`. Each ends `, and only plain tables (one header row at the top, then the data) are supported for now`.
- **Added after review round 1 (R1-1, R1-2): a cell pandas reads as missing although it isn't empty.** An error value (`data_type == "e"`: a typed `#DIV/0!`, a formula's saved `#N/A`) anywhere, or text in pandas' default NA set (`NA`, `N/A`, `n/a`, `NULL`, `null`, `None`, `nan`, `NaN`, `#N/A`, ...) in a cell below row 1, makes the sheet refused: `a cell holds an error value, or text such as NA or N/A, that would be read as empty, and keeping such cells isn't supported for now`. Without it the skills blanked those cells and, for "remove the empty rows", deleted real rows as "empty" (a country code `NA`), and the "copy has their last saved values" sentence was false for error results. A header holding such text is allowed (pandas keeps headers as written). This over-refuses on purpose: any file with `NA`, `N/A`, `null` or an error in a data cell can't be cleaned until the writer keeps cells as they are (backlog 1b). A test keeps the literal set equal to pandas' `STR_NA_VALUES`.
- **Added after round 1 (R1-3):** the read-only sheets' `<dimension>` is reset (`reset_dimensions()`) in the detector and the formula count, because pandas ignores a wrong one and would read cells the detector never saw.
- A sheet with no values at all isn't refused (there is nothing to lose; `main` delivers an empty copy too).
- **Addition to the request: duplicate headers.** pandas renames them (`a` → `a.1`) and nothing says so, so I count it as "not a plain table". It's one branch (`_DUPLICATE_HEADER`) and one test; remove it if you'd rather deliver those.
- Known limit: a one-column table with a title in A1 and the real header in A2 can't be told from a table whose header is "title" and whose first value is the header's name. Not detected.

Both skills call it right after the sheet-count check (nothing delivered on a refusal; the chain stops as it does for a multi-sheet workbook). The **Inspector checks the same thing independently**: for every successful file-changing step it re-runs `plain_table_problem` on the step's input (every sheet it lists), so a skill that stops refusing, or a fake that never did, still fails verification and nothing is handed back. The planner's "don't prepend the analysis on a file the skills refuse" check uses it too, so a refused file isn't also analysed (the analysis reported the `Unnamed: 3` column).

### 2. For a plain table the copy is delivered, and the reply says what it doesn't keep

Two fixed sentences, rendered by code in `buddy/outcome.py`; no model text.

**Formatting: on every delivered copy, unconditionally.** "Has formatting" can't be measured reliably: the set of things the writer drops is open-ended (fonts, fills, number formats, widths, filters, validation, conditional formats, freeze panes, comments, images, charts…), and a detector that misses one makes the sentence's absence a false "nothing was lost". The writer never keeps any of it, so the sentence is true every time: *"The copy has the values only; formatting, column widths and other sheet settings aren't carried over."* (When a writer that does keep formatting is added, this sentence must become per-capability; backlog.)

**Formulas: only when the original has formulas, and from a measurement.** `OriginalMeasurer`, a turn-level wrapper around the Inspector's verifier (like `ResidualMeasurer`), reads the file the first file-changing step read (the user's file, also in a chain) fresh with openpyxl twice (formulas shown, and `data_only=True`), and records `OriginalFacts(formula_cells, formula_cells_without_value)` on `VerificationResult.original_facts`: the cells holding a formula, and how many of those have no saved value (the copy has an empty cell there). Rendered:

- `Your file has N formula cell(s). The copy has their last saved values, not the formulas.`
- when some have no saved value: `K of them had no saved value, so they're empty in the copy, and rows were judged with those cells empty.` (Dedupe and empty-row removal compare values, so a row of unsaved formulas looks empty. The reply says so rather than hiding it.)
- when the measurement can't be made (`formula_cells=None`): `I couldn't check whether your file has formulas; if it does, the copy has their values only.`
- nothing when `N == 0`, or when there's no measurement (no `OriginalMeasurer`).

The sentences come after the file sentence ("Your cleaned copy of … is ready." / "Saved as …"), so they qualify it directly.

### 3. Not in this change

Keeping formulas and formatting (an openpyxl-based writer that edits a copy of the workbook in place) is a backlog item. Also deferred: refusing when formula cells have no saved value, instead of judging rows with those cells empty.

### 4. P-2

`skills/excel/analyze.py` sets `YDATA_PROFILING_NO_ANALYTICS=1` (assigned, not `setdefault`: ydata tests `bool(value)`, so an inherited empty string would leave analytics on) immediately before `ydata_profiling` is imported in the analyzer. A test checks it's set after the analyzer runs, including when the variable was empty or removed beforehand.

## Costs and risks

- Each plain-table check streams the sheet once more (skill, Inspector, planner, measurer: about four extra passes, roughly the cost of the pandas read each). Large files take longer.
- Over-refusing is the safe side of rule 1; the cost is that a sheet with a title row, a merged banner or duplicate column names is no longer cleaned at all until per-layout support exists.
- Every delivered-copy reply gets one more sentence, so the exact-reply tests change.

## Tests

Through `build_engine`, all three speakers (template, friend, focus), single step and chain: a formatted sheet with formulas (with and without saved values), merged cells, a header on row 3, a plain sheet. Each reply is checked against an independent read (XML, not openpyxl), plus the original's hash before and after. Unit tests for the detector, the measurer, the Inspector check with a skill that doesn't refuse, and the renderer. Live runs with Groq on a formula fixture and a merged-cell fixture.
