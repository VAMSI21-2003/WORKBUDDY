> **State at the round 3 checkpoint (commit after `c766fa6`).** Round 3 ended DON'T MERGE (five blocking MEDIUMs, R3-1 to R3-5, no HIGH; `docs/reviews/fix-security-pass-review.md`, "Review round 3"). By `docs/process/review-loop.md` I did not start round 4 or fix anything after it: the owner's advisor decides the next step. Nothing is fixed for R3-1 to R3-5 on this branch. The reviewer's prototype is `scripts/review_harness/fix-security-pass/prototype_fix_r3.py` (about 35 lines, 1,057 related tests pass; it does not cover shared strings or the text cap). The PR is still a draft.

# fix/security-pass: fixes for review round 2

Round 2 (`docs/reviews/fix-security-pass-review.md`, reviewed head `9d17990`): DON'T MERGE, two blocking MEDIUMs. I did not edit the review file.

**Mechanism replaced, not patched.** R1-1 and R2-1 are the same mechanism failing in two rounds in a row: a check that predicts what the table's reader will build by reading the XML itself. `docs/process/review-loop.md` says not to patch it a third time. `check_workbook_limits` now walks the workbook with the table's own reader and counts what it yields (`_reader_verdict` in `skills/excel/workbook.py`). It can't disagree with the reader about where a sheet is, what a row's `r` means, or what a cell holds, because it is the reader. The reviewer's prototype (`prototype_fix_r2.py`) patched the guess and was not used.

| Finding | What I did |
|---|---|
| **R2-1 (MEDIUM)** a row number that disagrees with its cells' references; one part the scan can't finish ends the scan; a sheet whose part name doesn't end in `.xml` | All three are gone with the XML counting. The reader follows the workbook's own relationships to the sheet, whatever it is called; it places cells at the row's `r`; a junk, malformed or damaged part the reader never opens affects nothing (the reader pass doesn't read it, and the entity pass reads every part and ignores one that isn't XML). Tests (real workbooks): the mismatch (`test_a_row_number_that_disagrees_with_its_cell_references_is_followed`), a junk part placed first (4 kinds: malformed XML, unclosed VML, a 5,000-digit reference, binary), a damaged deflate part placed first, a sheet at five other part names. |
| **R2-2 (MEDIUM)** one 10-million-character string that 1,000 cells point at | The walk refuses a cell with more than 32,767 characters (Excel's own limit) and more than 50 million characters of text in all (`TOO_LONG_CELL`, `TOO_MUCH_TEXT`); the date matchers in `buddy/inspector.py` and `skills/excel/analyze.py` try no format on text over 32 characters. Tests: K7's shape, a cell at exactly the limit, an inline string, the total cap, a whole turn (refused in under a minute with the fixed sentence), and the matchers on 5 million characters. |
| R2-3 (LOW) `calcChain.xml` counted as cells | Gone: the XML is no longer counted. |
| R2-4 (LOW) no test of `shown()`'s new handling; `$A$1` | Tests for 17 kinds of double quotation mark, a leading and a trailing combining mark, and NFC. The four mutations the reviewer listed are killed (checked). The `$A$1` reference is parsed by openpyxl now. (They were meant to be in the round 1 fixes; the edit that added them had silently not applied.) |
| R2-5 (LOW) wording | The refusal says "the sheet spans more than 1,000,000 cells (its rows times its columns)"; `shown()` also maps U+2033, U+2036, U+2039, U+203A, U+275D and U+275E. U+05F4 and U+02BA are still left alone (backlog). |
| R1-2, R1-5 | Still the owner's. |

## What changed in cost and in behaviour

- The first walk of a file costs time: 6.7 s for 61,277 rows by 14 columns (858,000 cells); pandas' own read of that file takes 8.2 s here. Once per file, then cached.
- A workbook the reader can't read gets no verdict from the check; it is refused by the callers' own reads, with the existing sentence.
- A cell reference that disagrees with its row no longer moves the cell (the reader doesn't move it): `<row><c r="A1048576">` is one row, as pandas reads it, and is not refused.

## Run

Lint: nothing beyond the baseline. Full suite on the fixed head in the report. No live runs.
