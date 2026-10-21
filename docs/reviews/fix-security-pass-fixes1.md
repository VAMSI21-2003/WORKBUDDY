# fix/security-pass: fixes for review round 1

Round 1 (`docs/reviews/fix-security-pass-review.md`, reviewed head `f1af923`): DON'T MERGE, one blocking MEDIUM (R1-1). I fixed it and the LOW findings; R1-2 and R1-5 are the owner's. I did not edit the review file.

| Finding | What I did |
|---|---|
| **R1-1 (MEDIUM)** the workbook limits don't bound what pandas builds | Fixed in `skills/excel/workbook.py` (`_scan`, `_scan_part`), written from the finding, not copied from the reviewer's prototype. (1) The grid counts: per XML part, the highest row times the highest column may not pass `MAX_CELLS`, so 1,000 headers plus one cell at row 20,000 is refused (`TOO_MANY_CELLS`). (2) A row's `r` is read as openpyxl reads it (`int`, else a whole `float`, else refused as unreadable): `3e5`, ` 300000`, `300000.0`, `+300000` are refused for their rows; `2.5`, `abc`, `nan`, `inf` and an empty `r` are refused as unreadable. (3) Every XML part is scanned, not only `xl/worksheets/`, and only elements of the spreadsheet namespaces (transitional and strict) count. The separate DOCTYPE pass is gone: the one scan refuses on the parser's DOCTYPE and entity events. Tests (`tests/skills/excel/test_hostile_workbooks.py`, now 57): the grid case, the grid limit exactly, the grid counted per sheet, the odd row numbers, a far row by its `r` alone, a sheet kept in three other places, the strict namespace, foreign-namespace rows adding nothing. `docs/security/asvs-l1.md` row corrected to say what the first version missed. |
| R1-2 (MEDIUM, owner's call) a double-listed paraphrase now says "Partly done" | Not changed. It is the trade in `docs/reviews/fix-security-pass-impl.md` item 2 and for the owner in the report. |
| R1-3 (LOW) guard tests that pass on the old code | SQL header: new tests that a header with a space finds near-duplicates (0 findings on the base) and that Splink's `Linker` is handed only `unique_id`, `name_field`, `key_field` and no header text in its settings. Cell-value quoting: the fixture now yields findings, asserted before the loop. Dispatcher text: my test never reached the branch (an undeclared capability gets a message without the task id); it now registers a skill that declares the capability and refuses the task, and fails when `errors.append(str(exc))` is put back (checked). Row attribute: `test_a_far_row_is_refused_by_its_number_alone`. Bytes-read budget: unreachable because `zipfile` stops at the declared size, so I removed it rather than test it. The missing-value text of the skill's own analyzer is still untested; it never reaches a reply (the Inspector's recount is what is shown). |
| R1-4 (LOW) `zlib.error` and `NotImplementedError` escape the check | `_scan` now catches `Exception` and leaves the file to the callers' own refusals. `test_corrupt_deflate_data_is_left_to_the_callers_not_raised_from_the_check` fails with the old tuple (checked: `zlib.error`). |
| R1-5 (LOW) a dropped connection is no longer retried | Owner's note; follows the instruction. Unchanged. |
| R1-6 (LOW) wording | The backlog's z31 status now says the generic sentence. `shown()` maps every kind of double quotation mark (straight, curly, low, guillemets, full-width) to a single quote and drops a combining mark that would cling to its opening or closing quote (tests added). `_read_cells`' docstring no longer says "no cell converted" and points at the boolean item in the backlog. |

## Pre-existing on `main`, from the review's "For the owner"

- TRUE/FALSE in a column of numbers come back as 1/0 in the delivered copy, with "Done —" (HIGH-class, rule 1). Not caused by this branch and not in its scope; it is in `docs/backlog.md` with the reviewer's probe. It needs its own branch (the skills write booleans as numbers and the Inspector compares `True == 1`).
- A numeric header (`2024`) makes a report turn fail with "Something went wrong while checking the result".

## Run

Lint: nothing beyond the baseline. The full suite on the fixed head is in the report. No live runs.
