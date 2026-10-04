# Review: `fix/values-only-copy`, round 1

**Verdict: DON'T MERGE.** Head `850a27d` against base `origin/main` (`63427e9`). Two HIGH findings (R1-1, R1-2) share one cause and one fix: cells that pandas reads as missing but that aren't empty (error results such as `#N/A`, and text such as `NA`/`N/A`/`null`). The reply's new "last saved values" and "values only" sentences are false for them. Details, evidence and the exact fix are below. No finding touches rule 2: the original was byte-identical in every run.

Reviewer: independent agent, no memory of writing the change. Every claim in `docs/reviews/fix-values-only-copy-impl.md` was treated as unverified.

## 1. Suite and lint

- `python -m pytest -q`: **1684 passed**, 11 warnings, 200 s. Matches the implementer's number.
- `ruff check buddy tests`: 22 errors on head, 22 on `origin/main`. Compared by file and rule (line numbers stripped, sorted `diff`): **identical, nothing new**.

## 2. Check against `docs/design/values-only-copy.md`

Verified by reading the code and by probes. Everything in the design is implemented as written:

- Detector (`plain_table_sheet_name`, `plain_table_problem`, openpyxl read-only `data_only=True`, streaming `expat` scan for `<mergeCell>`, four fixed refusal texts in the stated order, empty sheet not refused). Both skills call it; the Inspector re-runs it on every successful file-changing step's input; the planner uses it to skip the analysis.
- `OriginalMeasurer` reads the first file-changing step's input fresh, records `OriginalFacts`, never changes `passed`.
- Values-only sentence on every verified delivery, after the file sentence; formulas sentences from the measurement only; "couldn't check" when the read fails (probed, all three speakers, with a secret in the exception text: the reply carries none of it).
- P-2: `YDATA_PROFILING_NO_ANALYTICS` is assigned before the import. Probed with `analytics_probe.py` (counts `requests.get`, sends nothing): on `origin/main` one call to `packages.ydata.ai` whether the variable is unset or empty; on head none in both cases.

Deviations and gaps in the design's own claims:

1. **The design's claim that the formulas sentence is "from a measurement" of what the copy gets is wrong for error results** (R1-1).
2. **The design assumes the openpyxl read sees the same cells as pandas.** It does not when `<dimension>` is wrong (R1-3).
3. No other deviation. The duplicate-header rule, the unconditional formatting sentence and "disclose instead of refuse" for unsaved formulas are all as the design says.

## 3. Findings

Severity as in the brief. [Certain] = reproduced here.

### R1-1 (HIGH, [Certain]): a formula whose result is an error is emptied in the copy, and the reply says the copy has "their last saved values"

- **Where:** `skills/excel/workbook.py` `formula_cell_counts` counts a formula as "without value" only when `saved.value` is `None` or `""`; `buddy/outcome.py` `_values_only_sentences` then says "The copy has their last saved values". pandas (`read_excel`, openpyxl engine) turns every error cell (`t="e"`: `#N/A`, `#DIV/0!`, `#REF!`, `#VALUE!`, ...) into NaN, so the skills write an empty cell.
- **Scenario:** `adversarial_probe.py na_error_results`: one sheet, formulas in column C with saved results `#N/A`, `#DIV/0!`, `""`, `40`; "remove the duplicate rows".
- **Reply (template):** "Done — there were no duplicate rows to remove. ... The copy has the values only; ... Your file has 4 formula cell(s). The copy has their last saved values, not the formulas. **1 of them had no saved value, so they're empty in the copy**, and rows were judged with those cells empty. ..."
- **What is true (my read of the delivered file with openpyxl):** `lookup` column of the copy is `None, None, None, 40`. **Three** of the four formula cells are empty, not one, and two of them had a saved value that the copy does not have. Both the "last saved values" claim and the count are wrong. The same holds for friend and focus (same sentences).
- **Reachability:** ordinary. Any Excel file with a `VLOOKUP`/`INDEX` miss or a division by zero. On `main` the cells were emptied silently too; the branch now adds an affirmative false sentence.
- **Fix:** see R1-2; one rule covers both.

### R1-2 (HIGH, [Certain], pre-existing on `main`, but the new sentence now misdescribes it): text that pandas reads as missing is emptied, and "fully-empty" rows are removed that weren't empty

- **Where:** `pd.read_excel(...)` in `skills/excel/dedupe.py:105` and `skills/excel/remove_empty_rows.py:106` (and every verifier read) uses pandas' default NA strings: `NA`, `N/A`, `n/a`, `NULL`, `null`, `None`, `nan`, `NaN`, `#N/A`, `<NA>`, ... in data cells (headers are untouched).
- **Scenario:** `na_strings` fixture: rows `("NA","N/A")`, `("Namibia","null")`, `("NA","None")`, `("US","nan")`; "remove the empty rows". Identical result on `origin/main` and on head (the diff between the two replies is only the new sentence).
- **Reply (template, head):** "Done — removed 2 fully-empty row(s). The copy has 2 row(s), 0 fully-empty row(s) remaining. ... The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I also noticed: note: 2 missing value(s) (100.0%)."
- **What is true:** the original has no empty row at all. Two real rows (country code "NA" = Namibia is the obvious ordinary example) were deleted as "empty", and the "null" and "nan" cells in the surviving rows were blanked. Dedupe does the same: `country: [None, 'Namibia', 'US']`, "removed 1 duplicate row(s), including 1 repeated blank row".
- **Why it blocks this change:** the change exists to make the reply honest about what the copy does not keep. Its unconditional sentence says the copy "has the values". For these cells it does not, and nothing says so. The Inspector cannot catch it because it reads with the same pandas defaults.
- **Reachability:** ordinary. `N/A` and `NA` are common in notes and code columns.
- **Fix (exact, one rule, prototyped):** make the plain-table check (which both skills, the Inspector and the planner already run) refuse a sheet that has a cell pandas would read as missing although it isn't empty. In `_layout_problem`, inside the loop over non-empty cells, before `seen_first_row`:

  ```python
  if cell.data_type == "e" or (
      cell.row != 1 and isinstance(cell.value, str) and cell.value in _NA_TEXT
  ):
      return _READS_AS_EMPTY
  ```

  with `_NA_TEXT = frozenset(pandas._libs.parsers.STR_NA_VALUES) - {""}` (or the 18 literal strings, to avoid a private import) and a fixed `_READS_AS_EMPTY = "a cell holds an error or text such as NA or N/A that would be read as empty"`. `data_type == "e"` is what openpyxl `data_only=True` gives an error cell, formula result or typed. In a scratch copy (`scratchpad/fixproto`) this refuses `na_strings` and `na_error_results` for dedupe and empty-rows, leaves `plain`, `formulas_saved` and `formulas_wrong_dim` delivered, and the existing tests in `tests/buddy/test_values_only_copy.py`, `tests/skills/excel`, `test_multi_sheet.py`, `test_planner.py`, `test_inspector.py` still pass (486). Over-refusal is the design's stated safe side. The alternative (read with `keep_default_na=False, na_values=[""]` in both skills and every verifier read) keeps more files cleanable but touches eight read sites; if the owner prefers it, the next reviewer must probe all of them.
- Add tests: `NA`, `N/A`, `null` text in a data cell, an `#N/A` formula result and a typed `#DIV/0!`, each refused by both skills and by the Inspector with a skill that doesn't refuse (same shape as the existing merged-cell tests). A header cell holding `NA` must stay allowed.

### R1-3 (MEDIUM, [Certain], rare reach): a wrong `<dimension>` hides cells from the detector and from the formula count

- **Where:** `_sheet_problem` and `formula_cell_counts` iterate `sheet.iter_rows()` of a read-only openpyxl sheet, which honours the `<dimension>` element. pandas resets it, so the skill reads cells the detector never saw.
- **Scenario A (`gap_dim_a1`):** header `name, <empty>, amount`, `<dimension ref="A1"/>`. Detector: no problem. Delivered copy has a new `Unnamed: 1` column; reply: "Done — removed 1 duplicate row(s). ... The copy has the values only; ..." plus the analysis line naming `Unnamed: 1`. The same file with a correct dimension is refused.
- **Scenario B (`formulas_wrong_dim`):** a plain table with 4 formulas in column C, `<dimension ref="A1:B6"/>`. Delivered; the reply says **nothing about formulas** (count read as 0), while the copy holds their values only.
- **Reachability:** needs a generator that writes a wrong dimension (some non-Excel writers do; openpyxl, including write-only mode, does not). Not an everyday file.
- **Fix:** call `sheet.reset_dimensions()` right after `book[sheet_name]` in `_sheet_problem`, and on both `shown_book[name]` and `saved_book[name]` before iterating in `formula_cell_counts`. Verified in the scratch copy: `A1`, `A1:A2`, `A1:B2` all refused; the count in scenario B becomes 4. Add a test per scenario (patch `<dimension>` in the XML).

### R1-4 (LOW, Deferred): "had no saved value" is the wrong reason for formulas that return `""`

An Excel-saved formula such as `=IF(x,"",y)` saves an empty string; `_is_empty` counts it as "no saved value". The consequence in the sentence ("empty in the copy, rows judged with those cells empty") is true, the reason isn't. Also the count includes formula cells in rows the turn then removed ("4 of them ... empty in the copy" next to a copy of 3 rows). Reword to "K of them are empty in the copy".

### R1-5 (LOW, Deferred, design-accepted): unsaved formulas still skew the judged rows, disclosed

`formulas_partly_saved`: "Done — there were no duplicate rows to remove" for rows Excel would call duplicates; `formula_only_row_unsaved`: "removed 1 fully-empty row(s)" for a row whose only content was a formula. Each reply then discloses "rows were judged with those cells empty", and `docs/design/values-only-copy.md` section 3 and backlog 1b record the decision to disclose rather than refuse. I did not count this as blocking for that reason; it needs a never-calculated file (written by a script). Backlog 1b already lists the stronger option.

### R1-6 (LOW, Deferred): cost, wording, private API, untested guard

- **Cost.** One plain-table pass is about as slow as the pandas read; a formula count about twice. 100,000 rows x 5 columns (measured while another job used the CPU): `pandas.read_excel` 8.9 s, `plain_table_problem` 10.1 s, `formula_cell_counts` 17.6 s. A one-step turn adds roughly skill + planner + Inspector + measurer, about 48 s on that file; a two-step chain more. The design lists this under costs; measure again before a public launch.
- **Wording.** An up-front refusal reads "I ran into a problem partway through ... I stopped at the deduplicate step". Nothing ran. The same wording exists on `main` for multi-sheet refusals. "A column in the sheet has no header" is what a title row gets.
- **Private API.** `sheet._get_source()` in `_has_merged_cells` is private to openpyxl; `requirements.txt` says `openpyxl>=3.1`. Pin the range or guard with a test that fails loudly if it disappears (the refusal falls back to "damaged", so it is safe, not silent).
- **Untested guard.** Mutation `_is_empty` ignoring `""` survives the whole suite. A header cell that is an empty inline string (`<is><t></t></is>`) is refused today (`Unnamed: 1` otherwise); add a test.
- **Pre-existing, seen again.** Numeric headers (`2023`, `1` and `"1"`) fail verification: "Something went wrong while checking the result ..." while `outputs` still holds a file (backlog "Failure reply and delivered files"). Integers above 2^53 lose precision through float (`9007199254740993` becomes `...992`). `text_equals` (a text cell starting with `=`) is refused by the verifier, honestly.

## 4. Probes through the real pipeline

Real `build_engine`, real skills and verifiers, fake LLM (`scripts/review_harness/harness.py` as the base). 27 fixtures x dedupe / empty-rows / two-step chain x template, friend (3 session ids) and focus = 405 turns on head and the same 405 on a worktree of `origin/main`; then 4 fixtures (`plain`, `merged`, `formulas_wrong_dim`, `gap_dim_a1`) x five requests (adding `analyze_only` and `wrong_step`) = 100 more on head; then the adversarial cases. **Rule 2: the original's SHA-256 was identical before and after in every run (0 changes in all 910 probe turns and in the adversarial runs). Voice-model calls: 0.**

Automatic checks over all 405 head turns: every reply that says "The copy has the values only" is a delivery with a file sentence; no reply says it for a refusal; "Your file has N formula cell(s)" equals my independent XML count of `<f>` elements in every case except the error-value and wrong-dimension cases above.

| Fixture (one sheet) | main | head |
|---|---|---|
| plain, `extras` (filter, validation, comment, hidden column, `%` format, freeze panes), `excel_table`, `date_cells`, `whitespace_header`, `header_only`, `empty_sheet` | delivered | delivered, plus the values-only sentence |
| `formulas_saved` (4 formulas, bold header, fill, width) | delivered, silent | delivered; "Your file has 4 formula cell(s)" (correct); copy holds 20/40/20/60 |
| `formulas_unsaved`, `formulas_partly_saved`, `formula_only_row_unsaved` | delivered, silent | delivered; counts 4, 3, 3 (correct); disclosure sentence present |
| `merged`, `title_row`, `header_row3`, `dup_header`, `header_gap`, `data_right_of_header`, `col_a_empty`, `formula_header`, `no_dimension` | delivered with `Unnamed: n` / `Total.1` columns and a bare "Done —" | **refused**, nothing handed back, original unchanged, fixed text, all speakers |
| `wrong_dimension` (data in column D, dimension A1:B6), `gap_dim_a1`, `formulas_wrong_dim` | delivered | delivered (R1-3) |
| `na_strings`, `na_error_results` | delivered, silent loss | delivered, silent loss (R1-1, R1-2) |
| `numeric_headers`, `int_and_str_header`, `big_ints`, `text_equals` | as head | as main |
| `analyze_only` request (planner runs only the analysis) | no file | no file, no values-only sentence ("No changes were made to any file this turn.") |

Adversarial cases (`adversarial_probe.py`):

- **Formula count read raises** (message `sk-SECRET-TOKEN-123 at /etc/passwd`), template, friend, focus: delivered; reply says "I couldn't check whether your file has formulas; if it does, the copy has their last saved values, not the formulas." No exception or secret text in any reply (the secret does appear in the log line `logger.exception`, as everywhere else in the code base).
- **Skills that don't refuse** (both skills patched to the old single-sheet check), merged / title row / duplicate header / header gap, single step and chain, template and focus: the Inspector fails the step ("the sheet it read isn't a plain table: ..."), nothing is handed back, the chain stops ("remove empty rows never ran"), the original is unchanged.
- **Shared formulas** (`<f t="shared">`): counted correctly (4).
- **Entity-declaring XML in the sheet part:** both the new scan and pandas refuse it as unreadable; no difference from `main`.

## 5. Tests: do they fail on the old code, and are the guards real?

- The new test modules and the changed expectations fail on `origin/main` source at collection (5 modules cannot import `VALUES_ONLY_SENTENCE` or the new detector). That is true but weak; the mutations below are the real check.
- **Mutations** (`scripts/review_harness/mutations.py`: export `HEAD`, change one line, run the eight relevant test files; 24 mutations): **22 caught, 2 survived.** Caught: merged scan off; header-not-first off; gap check off; right-of-header check off; duplicate-header rule off; each skill not refusing; Inspector check off; Inspector check on the first sheet only; `OriginalMeasurer` unwired; measurer sets no facts; measurer reads the last step's input instead of the first; failed read silently dropped; values-only sentence removed; formulas sentence removed; unsaved sentence removed; "couldn't check" sentence removed; unsaved count counts everything; formulas counted from the `data_only` book; planner check off; analytics `setdefault`; analytics removed.
- **Survived:** (a) `_reads_a_workbook` without `step.recheck`: equivalent in practice, the analysis steps are read-tier and already excluded (inherited from `_sheet_list_problem`); (b) `_is_empty` ignoring `""`: a real gap (R1-6).
- **No test covers** error-valued formula results, NA-like text, or a wrong `<dimension>` (R1-1 to R1-3). The exact-reply tests in `test_voice.py`, `test_outcome.py` and `test_multi_sheet.py` were updated only by adding the sentence in the right place; I found no weakened assertion.

## 6. What the next round must check

1. R1-1 and R1-2 fixed by a rule that refuses (or by reads that keep the strings), with the tests above, and my `na_strings` / `na_error_results` probes producing a refusal or a faithful copy.
2. R1-3: `gap_dim_a1` refused, `formulas_wrong_dim` says "4 formula cell(s)".
3. Regression against the base and the 405-turn table above (`values_only_probe.py`), the mutation list, rule 2 hashes, lint by file and rule.

## Deferred

R1-4, R1-5 (already backlog 1b), R1-6 (cost, wording, private API, untested `""` guard). Added to `docs/backlog.md`.

## Not run

- Live runs with Groq (the writer's step 5, after a MERGE verdict).
- Files saved by real Excel or LibreOffice: neither is installed. Fixtures are written with openpyxl and patched at the XML level (saved formula results, error results, `<dimension>`). The error-value and NA-text findings do not depend on this: they follow from pandas' reader on the XML Excel writes (`t="e"`).
- `.xls` input (`xlrd` not installed).
- The performance numbers were taken while a mutation run used the CPU; use them for ratios, not absolutes.
- The full suite on the scratch fix prototype (only the five relevant test directories, 486 passed). The prototype is a proof of the fix, not the fix.


---

# Review round 2 (commit 8222c7f)

**Verdict: MERGE.** Head `8222c7f` against base `origin/main`; diff reviewed since `850a27d` (`skills/excel/workbook.py`, tests, fixtures, docs) plus every open finding and regression against the base. R1-1, R1-2 and R1-3 are fixed, confirmed with my own probes. No HIGH, and no MEDIUM that an ordinary request reaches. Three LOW items are deferred below and added to `docs/backlog.md`. Rule 2: the original's SHA-256 was identical before and after in every run (0 changes in 725 head turns, 725 base turns, 400 round-2 turns, 20 re-run turns and 30 Inspector-only turns). Voice-model calls: 0.

Reviewer: independent agent, no memory of writing the change. `docs/reviews/fix-values-only-copy-fixes1.md` was treated as unverified.

## 1. Suite and lint

- `python -m pytest -q`: **1767 passed**, 11 warnings, 195 s. Matches the writer's number.
- `ruff check buddy tests`: 22 errors on head and on `origin/main`. Compared by file and rule (line numbers stripped, sorted `diff`): **identical, nothing new**.

## 2. Earlier findings, confirmed with my own evidence

| Finding | Result | Evidence |
|---|---|---|
| R1-1 (HIGH) error results emptied, "last saved values" false | **Fixed** | `na_error_results`, `r2_formula_error_unsaved_mix` (one saved `#DIV/0!` among saved numbers), `r2_formula_text_na` (a formula saving the text `N/A`), `r2_error_header`: refused for dedupe / empty / chain, template, friend, focus. Nothing handed back, fixed text, original unchanged. |
| R1-2 (HIGH) NA-like text emptied, real rows deleted as "empty" | **Fixed** | `na_strings` (`NA`, `N/A`, `null`, `None`, `nan`), `r2_inline_na` (inline and rich-text `N/A`), `r2_dim_missing_na` (no `<dimension>`), `r2_wrongdim_small_na` (NA outside the stated dimension): all refused. On `origin/main` the same files were delivered with the loss. |
| R1-3 (MEDIUM) wrong `<dimension>` hides cells | **Fixed** | `gap_dim_a1` refused; `formulas_wrong_dim` now says "Your file has 4 formula cell(s)" (4 `<f>` in the XML); `wrong_dimension` (data in column D, dimension `A1:B6`) is now refused where `main` delivered it with `Unnamed` columns; `wrong_dimension_rows` (dimension smaller than the data) still delivered correctly. |
| R1-6 survivor (`_is_empty` ignoring `""`) | **Closed** | Mutation now caught by `test_an_empty_string_header_written_as_a_string_cell_is_refused`. |

The replies for the refusals say: "a cell holds an error value, or text such as NA or N/A, that would be read as empty, and keeping such cells isn't supported for now ... no file was handed back; your original file is unchanged." That is true in every case I produced (nothing in `outputs`, original hash unchanged). When the skills are patched not to refuse, the Inspector refuses on its own ("the sheet it read isn't a plain table: ..."), single step and chain, three speakers (`round2_skill_doesnt_refuse.py`, 5 fixtures x 2 requests x 3 speakers, all `delivered=False`, original unchanged, the chain's second step skipped).

## 3. Check against `docs/design/values-only-copy.md`

The implementation matches the design as reviewed in round 1. One deviation: the design note does not describe the new refusal (error values and NA-like text) or the dimension reset; it still says the detector has four refusal texts. It is a fifth. LOW, see R2-3.

## 4. Probes

Real `build_engine`, real skills and verifiers, fake LLM (`values_only_probe.py` as the base).

- **725 turns on head and the same 725 on a worktree of `origin/main`** (29 fixtures x 5 requests x template / friend x3 / focus). Automatic checks: no reply says "The copy has the values only" without a delivery (0); no delivered turn has an NA-like or error cell in the original (0). Differences from `main`, all intended: every non-plain layout (merged, title row, header row 3, duplicate header, header gap, data right of header, column A empty, formula header, no dimension, wrong dimension with data outside it, `gap_dim_a1`) and `na_strings` are now refused where `main` delivered a damaged copy; plain / formulas / `extras` / `excel_table` / `date_cells` / `header_only` / `empty_sheet` / `whitespace_header` / `big_ints` / `wrong_dimension_rows` delivered on both, with the values-only sentence on head. Unchanged from `main`: `numeric_headers`, `int_and_str_header` (verification fails; backlog), `text_equals` (refused by the verifier on both).
- **Round-2 fixtures** (`round2_probe.py`, 400 turns): `r2_na_header_only` (header cells `NA`, `null`: delivered, header kept as written in the copy; correct), `r2_ws_na` (`" NA"`, `"NA "`, `"Na"`, `"n/a."`, `"NAN"`: pandas keeps them, delivered, copy identical; correct), `r2_ragged` (rows with gaps; delivered, copy correct), `r2_huge_dim` (`A1:XFD1048576`: delivered in under a second), `r2_error_in_empty_row` (an error value alone in the "empty" row: refused), `r2_num_nan` (`<v>nan</v>` in a number cell: refused as unreadable; honest).
- **Differential check** (`differential_nan_probe.py`): 25 odd cell kinds in a data cell (bool, ISO date, empty inline / `str` / shared strings, `<v/>`, `nan`/`inf`/`#IND` numbers, whitespace NA, Unicode-built `NA`, error and formula-error cells, formula text `n/a`). For each: does pandas read the cell as missing while the check neither refuses nor treats it as empty? **None.** Where pandas reads a cell as missing the check refuses it or counts it empty; unreadable numbers raise in openpyxl and the check refuses ("could not read the workbook").
- **Formula count after `reset_dimensions`:** ragged rows with formulas in sparse columns give 3 formulas (independent count 3); the `strict=True` zip of the two books did not raise on any fixture.

## 5. Tests and mutations

`scripts/review_harness/mutations.py` (exports HEAD, one change, runs the eight relevant test files). Round 2 added nine mutations: **8 caught, 1 survived.**

Caught: `reset_dimensions` removed at each of its three sites; error-type check off; NA-text check off; NA check applied to the header row; one string dropped from the NA set (the pandas-equality test); the new refusal given the "plain tables" suffix; `_is_empty` ignoring `""`.
Survived: the NA text check restricted to row 2 only (`cell.row == 2`): no test puts NA text or an error value below row 2. The real code is right (probes put them at rows 4 and 5); the tests don't pin it. LOW, R2-2.

The new tests fail without their fix (each mutation above). The fixture helpers (`error_result_sheet`, `with_dimension`, `na_text_sheet`) are the ones the earlier probes needed; I found no weakened assertion.

## 6. Findings

### R2-1 (LOW, [Certain], deferred): "what is in this file" on a refused sheet gets "I wasn't able to confirm that worked. No verifier has been configured."

- **Where:** `buddy/planner.py` `_cleaning_skills_would_refuse` now covers every refusal, including the new NA / error rule; the planner then drops the analysis, the plan has no steps, and the engine's no-steps reply is used.
- **Scenario:** an analysis-only request on `na_strings`, `gap_dim_a1`, `r2_formula_text_na`. Reply: "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn."
- **Why LOW:** the same reply is produced on `origin/main` for a two-sheet workbook (I ran both trees); the planner skip is a decided behaviour. It claims nothing false about files ("No changes were made"), but "No verifier has been configured" is wrong as a statement about the system and says nothing about why nothing ran. The new rule widens how often it appears (any file with an `N/A`).
- **Fix:** when the planner skips the only step because the file would be refused, say so with the refusal text (as the chain refusal does) instead of the no-verifier fallback.

### R2-2 (LOW, [Certain], deferred): the NA / error rule is tested only at row 2

Mutation `cell.row == 2` survives. Add a parametrised case with the NA text and an error value at row 5 and in a later column.

### R2-3 (LOW, [Certain], deferred): the design note is stale

`docs/design/values-only-copy.md` doesn't mention the error / NA refusal, the dimension reset or the over-refusal it accepts. Over-refusal is real: any file with a data cell of `NA`, `N/A`, `null` or an error value can't be cleaned until the cell is changed; the reply says why, truthfully. A later option is to read with `keep_default_na=False` at every pandas read site (backlog).

## 7. Deferred

R2-1, R2-2, R2-3 (added to `docs/backlog.md`), plus round 1's R1-4, R1-5, R1-6 (already there; the `""` guard part of R1-6 is closed).

## Not run

- Live runs with Groq (the writer's step 5, after this MERGE verdict).
- Files saved by real Excel or LibreOffice (not installed): fixtures are openpyxl files patched at the XML level.
- `.xls` input (`xlrd` not installed).
- Timing on a large sheet (round 1's numbers stand; `reset_dimensions` changes only how rows are padded, and the `A1:XFD1048576` case ran in under a second).
- Other pandas or openpyxl versions: tested on pandas 2.3.3 and openpyxl 3.1.5. `test_the_na_text_set_is_pandas_own` guards the NA list against a pandas upgrade; nothing guards the error-cell behaviour of the pandas reader.
