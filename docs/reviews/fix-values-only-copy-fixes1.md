# fix/values-only-copy: fixes for review round 1

Review: `docs/reviews/fix-values-only-copy-review.md`, round 1 (head `850a27d`): DON'T MERGE. Writer's notes; the next reviewer judges them.

## Fixed

- **R1-1 (HIGH) and R1-2 (HIGH), one rule.** `_layout_problem` in `skills/excel/workbook.py` now refuses a sheet that has a cell pandas reads as missing although it isn't empty: an error value (`data_type == "e"`, anywhere, including a formula's saved `#N/A` and a typed `#DIV/0!`), or, below row 1, text in pandas' default NA set (`NA`, `N/A`, `n/a`, `NULL`, `null`, `None`, `nan`, `NaN`, `#N/A`, ...). Fixed text: "a cell holds an error value, or text such as NA or N/A, that would be read as empty, and keeping such cells isn't supported for now" (no suffix about plain tables; it contains none of the words the nothing-delivered replies must avoid). It is the same `plain_table_problem` the skills, the Inspector and the planner already use, so all four refuse together. Headers with that text stay allowed (pandas keeps a header as written). The reviewer's alternative (reading with `keep_default_na=False` at about eight sites) was not taken: the refusing rule is the design's stated safe side and touches one place. `_PANDAS_NA_TEXT` is a literal set, and a test keeps it equal to pandas' `STR_NA_VALUES` minus the empty string.
- **R1-3 (MEDIUM).** `sheet.reset_dimensions()` after `book[sheet_name]` in `_sheet_problem`, and on both books' sheets in `formula_cell_counts`, so a wrong `<dimension>` can't hide cells pandas reads.

## Tests added (each fails without its fix; mutations: reset removed, error check removed, NA check removed, `_is_empty` ignoring `""`)

- `tests/skills/excel/test_plain_table.py`: every pandas NA string refused in a data cell, allowed in a header; near-misses (`n/a.`, `na`, `NAN`) not refused; typed error cell and formula with a saved error result refused; the NA sheet refused by both skills; wrong `<dimension>` (`A1`, `A1:A2`, `A1:B2`) no longer hides a header gap; the formula count with `A1:B6` is 4; an empty-string header cell (crafted XML) is refused (the reviewer's surviving mutation 2).
- `tests/buddy/test_values_only_copy.py`: `na_text`, `formula_error_results` and `header_gap_wrong_dimension` added to the refused fixtures: through `build_engine`, three speakers, dedupe / empty rows / chain, nothing delivered, and the Inspector refuses them too when the skills don't.

## Not changed

- Reviewer's surviving mutation 1 (`_reads_a_workbook` without `step.recheck`) is, as the reviewer says, equivalent.
- Deferred by the reviewer, unchanged: R1-4 (reason wording for formulas that return `""`), R1-5 (design-accepted), R1-6 (cost, wording, private `_get_source`); in `docs/backlog.md`.

## Results

Suite: 1767 passed (round 1 head: 1684). Lint: same errors as `origin/main` by file and rule.
