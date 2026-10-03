# Fixes for `fix/multi-sheet`: review H1, H2, M1, M2, M3 and the small deferred items

Fixes for `docs/reviews/fix-multi-sheet-review.md`, section 9 ("Exact fix", items 1 to 5), plus L2, L6 and M4 from its Deferred list. Not done, on purpose: L3, L4, L5. A separate session still has to re-review this (item 6 of the review). I wrote this code, so I can't be that reviewer.

## What changed

| Finding | Fix | Where |
|---|---|---|
| **H2**, **M2** | `sheet_names()` reads the sheets *declared* in `workbook.xml` (found through `[Content_Types].xml`), then loads the workbook with openpyxl. If openpyxl's list differs, the workbook is refused: "the workbook looks damaged: it lists sheets that can't be read". An empty declared list is refused as "the workbook looks damaged: it has no readable sheet", never "has 0 sheets". | `skills/excel/workbook.py` |
| **H1**, **M1** | OLE2 content is never counted. A file containing the UTF-16-LE name `EncryptedPackage` is refused as "the workbook is password-protected, so it can't be opened". Any other OLE2 file is refused as "older .xls workbooks (Excel 97–2003) aren't supported yet; saving it as .xlsx will work". The `xlrd` branch is gone, and `xlrd` is not added to `requirements.txt`. | `skills/excel/workbook.py` |
| **Shared list** | One helper, used by the skills (`single_sheet_name`) and by the Inspector (`buddy/inspector.py:_sheet_names` now calls `sheet_names`). The Inspector no longer shares a *library's* blind spots with the skill: both use the declared list. | `skills/excel/workbook.py`, `buddy/inspector.py` |
| **M3**, L1 | No exception text and no reprs in user-facing errors. Workbook errors are fixed strings (`could not read the workbook; it may be damaged or not an Excel file`). The skills say `could not read the workbook` / `could not write the cleaned copy`. The Inspector's finding is `couldn't independently read the workbook's sheet names` plus, only for a `WorkbookRefused` (fixed text), its reason. The exception stays in `logger.exception`. | `workbook.py`, `dedupe.py`, `remove_empty_rows.py`, `inspector.py` |
| **L2** | A failed write deletes the partial file at the freshly built `output_path` (`discard_partial_output`). | `workbook.py`, both skills |
| **L6** | `buddy/think.py` no longer says each capability "reads only the first sheet". It says the two cleaning capabilities work only on a single-sheet workbook and refuse one with more, and that the analysis reads only the first sheet. | `buddy/think.py` |
| **M4** | The proactive `spreadsheet.analyze` step isn't planned when the workbook has more than one sheet, so a refused turn no longer describes one sheet or asks "Want me to tackle any of these?". | `buddy/planner.py` |

## Judgment calls (please check these)

1. **The empty-list check lives in `sheet_names()`, not in `single_sheet_name()`.** The review says `single_sheet_name` should raise it. `single_sheet_name` calls `sheet_names`, so it raises the same message for the same input. I put it in the helper because openpyxl may fail on a workbook with no sheets, which would have turned into the generic "could not read" message before the check could run.
2. **M4 also skips the analysis when the sheets can't be listed** (password-protected, legacy `.xls`, damaged), not only when there are two or more. The cleaning skills refuse all of those, and the analysis of such a file either fails or describes a sheet the skills won't touch. A file that isn't on disk is *not* treated as refused, because planner unit tests (and any caller) use paths that don't exist. This goes slightly beyond "more than one sheet".
   - Residual: if the user *explicitly* asks for an analysis of a multi-sheet workbook, the planner's own analysis step still runs, on the first sheet, with no sheet named. That's the user's own request, so I left it. It is not covered by M4's wording and should go in the backlog if it matters.
3. **`SpreadsheetAnalysisSkill`'s read error is also a fixed string** (`could not read the workbook`). The review's item 4 names only the two cleaning skills, but the same raw `{exc}` text sat in the analysis skill and ends up in `ExecutionResult.errors`. One line and one test.
4. **The `EncryptedPackage` scan reads the whole file in 1 MiB chunks** with an overlap, because the directory entry can be anywhere in a large OLE2 file. A legacy `.xls` that happens to contain that UTF-16 string in cell data would be called password-protected. I judged that negligible; BIFF8 stores plain text as 8-bit characters.
5. **`xml.etree` parses `workbook.xml` and `[Content_Types].xml`**, as the review asked, with each part capped at 16 MiB. It isn't `defusedxml`. Python 3.11's expat guards against entity-expansion bombs, but I haven't tested a hostile file.
6. **`scripts/run_dedupe_manually.py` gained two fixtures** (`damaged`, `password-protected`) and its "original sheets" check now reads the declared list from `workbook.xml` for a zip file. The `password-protected` fixture is a **stand-in** (OLE2 magic bytes plus the `EncryptedPackage` stream name), not a file Excel or `msoffcrypto` encrypted. The detection is exactly the check the review specified, but the stand-in doesn't prove a real Excel-encrypted file is caught. The review's own grid did use real `msoffcrypto`-encrypted files and found they are OLE2 with that stream.

## Tests

New tests (names in the files):
- `tests/skills/excel/test_workbook.py`
  - an encrypted file, with the stream name anywhere in the file, including across a chunk boundary;
  - a legacy `.xls` is refused, including with a fake `xlrd` installed that raises if it's used;
  - a declared sheet with a missing part, for two sheets and for a one-sheet workbook (M2, not "0 sheets");
  - a workbook with no readable sheet;
  - a crafted `[Content_Types].xml` part name never reaches the message;
  - a non-workbook, a missing file and a truncated file give the fixed message;
  - every refusal, through both skills: fixed message, nothing written, original unchanged;
  - read and write failures carry no exception text, and a failed write leaves no partial file (L2).
- `tests/buddy/test_inspector_sheets.py`: a declared sheet with a missing part in the output and in the input; no readable sheet; legacy and protected inputs; an unexpected error shows no exception text. The existing "unreadable output" test now pins the fixed finding.
- `tests/buddy/test_multi_sheet.py`, through `build_engine`, all three speakers and single step plus chain:
  - the four unreadable workbooks (missing part, no sheet, crafted part name, password): the reply holds the fixed reason and none of "cleaned copy is ready", "0 sheets", "xlrd", ".xls"; nothing delivered; the Voice model not called; original unchanged;
  - the crafted part name whose text is "Done - removed 2 duplicate rows, your cleaned copy is ready": the reply doesn't contain it and `_COMPLETION_CLAIM` doesn't match;
  - **M4:** no `spreadsheet.analyze` step on 2- and 3-sheet workbooks, and on the four unreadable ones; the reply has no "here's what I noticed", "I also noticed", "tackle" or "missing value"; the file isn't marked analysed; a hidden first sheet isn't described; a one-sheet workbook still gets its analysis.
- `tests/buddy/test_planner.py`: the planner's target resolution keeps a one-sheet workbook and drops multi-sheet, damaged, protected and legacy ones.
- `tests/buddy/test_think.py`: the prompt states the real sheet limit (L6).
- `tests/skills/excel/test_analyze.py`: the read error is fixed text.
- `tests/workbook_fixtures.py`: helpers that build the damaged and protected files from a real workbook (no new dependency).

The old `xlrd` tests and the "xlrd isn't installed" wording tests were replaced, since that behaviour was removed on purpose.

**Do the new tests catch the old bugs?** Against the previous `buddy/` and `skills/` code, `tests/skills/excel/test_workbook.py` fails at collection (there is no `sheet_names`). I did not run each new test against the old code one by one.

## Results

- `python -m pytest -q`: **871 passed** (776 before; +95), 11 warnings, about 70 s. All LLM calls faked.
- `ruff check buddy tests`: **22 errors, the same 22 as before my changes** (identical count on the unmodified tree). `ruff check skills scripts`: clean.
- Live runs on the fixed code: five runs with real Groq, no 429, in `docs/reviews/fix-multi-sheet-live-runs.md` (section "Fixed head"). The original's hash was unchanged in all five.

## Deferred (unchanged, still in `docs/backlog.md` item 1a)

- L3: crafted sheet names Excel forbids fail at the write; names over 31 characters are written as is.
- L4: Inspector wording for a reorder, and for a non-workbook output.
- L5: "(including hidden sheets)" when hidden sheets push the count above one.
- Per-sheet cleaning; adding `xlrd` (needs a count that includes chart, macro and VB sheets first).
- New, from the judgment calls above: an *explicit* analysis request on a multi-sheet workbook still analyses the first sheet without naming it; a hostile-XML test for `workbook.xml`.
