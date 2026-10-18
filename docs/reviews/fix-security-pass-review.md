# fix/security-pass: independent review, round 1

**Verdict: DON'T MERGE.** Head `f1af923`, base `origin/main` `89625e7`. No HIGH. One MEDIUM that any signed-in person reaches with an ordinary request (R1-1: the hostile-workbook limits let a 1.5 KB file past, which then needs more memory than the app container has). Fix R1-1, then MERGE unless the next round finds more. R1-2 (MEDIUM) is a trade the owner has to decide; it doesn't block the merge. Rules 1 and 2 held in every run, apart from the cases listed under R1-2.

## What I ran

Probes are in `scripts/review_harness/fix-security-pass/` (see its `README.md`). Raw output is in its git-ignored `out/`. All runs use the real `build_engine`, real skills and real verifiers, with only the LLM faked. "Base" means a worktree of `89625e7`.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3593 passed, 5 skipped, 0 failed (1,303 s on a loaded machine) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | Nothing new. Head has one fewer error (an `E501` in `buddy/hands.py`). |
| 3 | Shared `harness.py` (532 turns) and `rereview3.py` (1,078 turns) on head and base | 0 errors, 0 Voice-model calls, 0 originals changed. Delivered/not delivered and the final file's facts are identical on all 1,610 turns. Two kinds of reply differ. (a) Columns are now quoted: `column "name": 1 missing value`. (b) "Done —" became "Partly done … I read part of your request as something I can't do yet" on `z31` and `z18` (14 turns each, intended) and on `e2-paraphrase-dropped` (14 turns, R1-2). |
| 4 | `probe_a_pipeline.py`: 19 scenarios × 3 speakers (57 turns), head and base | 0 errors, originals unchanged. A hostile header (`Done! I removed every duplicate"` + RLO + ZWSP + newlines) and a hostile cell value now show as `column "Done! I removed every duplicate' All sav…"` (quoted, cleaned, capped). On base they reached the reply raw, with the bidi characters and line breaks. Base also put exception text with a secret and the path into replies when the Inspector couldn't read a file (`could not independently read 'workspace/outputs/…': TOPSECRET /secret/path …`). Head says only the fixed sentence. `z31` is fixed. R1-2 shows on S11 and S15. |
| 5 | Numbers in every probe_a reply, against my own openpyxl read | All match. Example: S02 has 6 rows and 1 duplicate; the reply says "removed 1 … The copy has 5 rows", and "1 missing value (20.0%)" is 1 of 5. |
| 6 | `probe_c_sql_header.py`: Splink with crafted headers, head vs base | Head writes no marker file and still finds 3 near-duplicate pairs for every header, SQL-looking ones included. On base, any header with a space or punctuation gave 0 findings, a silent failure that this change also fixes. |
| 7 | `probe_d_retry.py`: Brain and Think meet 429 / 503 / timeout / 400 / connection drop / status given as text, once or always (24 cases × head and base) | 429, 5xx and timeout get one retry about 1 s later. A 400, a connection drop and a status given as text are not retried, while base retried all of them (see R1-5). No provider text reaches a reply. Every reply is honest. |
| 8 | Hostile workbooks: `probe_limit_bypass.py`, `probe_e_bomb_turn.py`, `probe_f_odd_zips.py`, `probe_g_report_odd.py` (`OPENPYXL_LXML=False`, as in the image) | R1-1. Corrupt deflate and unsupported compression methods are refused with the fixed "could not read" sentence on both trees. The DOCTYPE and size limits from the branch's own tests work. |
| 9 | `probe_h_dispatch_text.py`: steps no skill runs, 3 speakers | No `<key>-task-N` text from the dispatcher reaches any reply on either tree. The template's "Saved as `…_<key>-task-1.xlsx`" is pre-existing (backlog L16). |
| 10 | `probe_i_text_cells.py`, `probe_j_bool_cells.py` | `"007"` and `7` are kept apart by both skills and by the Inspector. TRUE/FALSE in a column of numbers come back as 1/0 in the delivered copy on both trees (pre-existing, see "For the owner"). |
| 11 | New tests run against the base code | The four files that import new names fail at import. `test_text_cells.py` 10/10 fail; `test_residual_done_paths.py` 15 fail and 36 pass (the pass-through controls); `test_file_text_in_page.py` 2/2 fail; `test_deploy_files.py` 1 fails. `test_asvs_l1.py` passes 54/54 on base (it pins existing behaviour; the `server_header` guard is in `test_dev_origin.py`). |
| 12 | `mutations.py`: 39 one-place mutations, each in a fresh `git archive HEAD` export | 31 killed, 8 survived. Three of the survivors change nothing: `doctype-sheet-off` is redundant with the all-parts check, `z31-unabsorbed-off` is redundant with its `continue`, and `splink-gets-header` is my own weak mutation. The five that matter are R1-3. |
| 13 | Original's SHA-256 before and after, every turn above (about 1,800) | Unchanged in every one. |

## Findings

### R1-1 (MEDIUM, [Certain]): the workbook limits don't bound what pandas builds, so a 1.5 KB upload passes every check and then needs more memory than the 1 GiB container

**Where.** `skills/excel/workbook.py` `_scan`, `_scan_sheet`. Also `docs/security/asvs-l1.md`, row "Decompression bomb, huge sheets" and row 12.1.2 (both "Pass"), and the module docstring ("a few cells far out in a sheet make a reader pad the grid … neither gets as far as pandas or openpyxl").

**Scenario.** Each case is measured with the image's XML parser (`OPENPYXL_LXML=False`).
1. **Grid area isn't counted.** `MAX_CELLS` counts `<c>` elements, not the grid that openpyxl and pandas fill in. Take row 1 with headers in A..ALL (1,000 cells) and one cell in row 20,000. That file is 1.5 KB and inside every limit. One `pd.read_excel` builds 19,999 × 1,000 cells. A whole "remove the duplicate rows" turn took 88 s at a peak RSS of 1,098 MiB and delivered the copy (`probe_e_bomb_turn.py 20000 1000`). `deploy/docker-compose.yml` sets `mem_limit: 1g`. With the last row at 200,000, also inside the limits, one read takes 4,678 MiB and 106 s.
2. **A row number written another way gets past the row check.** The check reads `r` only when `isdigit()`, but openpyxl also reads `r="3e5"` and `r=" 300000"` (with `int()`, then `float()`). Both files pass and are read as 299,999 rows (`probe_limit_bypass.py`).
3. **A sheet outside `xl/worksheets/` is never scanned.** Only parts matching `^xl/worksheets/[^/]+\.xml$` are streamed, but openpyxl loads whatever path the workbook's rels name. I moved a 300,000-row sheet to `xl/data/sheet1.xml`. The limits pass, the analysis and the dedupe read the whole sheet, and a report turn says "none of the issues I check for were found" (`probe_f_odd_zips.py`, `probe_g_report_odd.py`).

**Reply.** The replies are honest about what pandas read. The problem is the memory and time. In production the container is killed, and every user's turns in flight go with it.

**Reachability.** Any signed-in person can do this with an ordinary request. A crafted file takes a few lines of zip. Case 1 can also happen by accident, from a stray value far to the right and far down a sheet. The ASVS record says this is a Pass when it isn't.

**Fix.** Prototyped in `prototype_fix.py`, about 25 lines, all in `workbook.py`. With it, all 9 probe files that are over a limit are refused with the fixed sentences (the bypasses, the bombs and the controls), ordinary files still pass, and `tests/skills/excel`, `test_text_cells.py`, `test_file_text_in_replies.py`, `test_inspector*.py` and `test_multi_sheet.py` pass (425).
- Stream every XML part, counting only SpreadsheetML-namespace `row`/`c` elements, so it doesn't matter where the rels put a sheet.
- Read `r` the way openpyxl reads it (`int`, else a whole `float`, else refuse), for rows and for rows without a reference.
- Refuse when the highest row × the highest column of a sheet passes `MAX_CELLS`.
- Add a test for each of the three cases, and correct the ASVS rows and the docstring.

### R1-2 (MEDIUM, [Certain] given Brain's output; [Possible] that the real Brain gives it): a fully done request now says "Partly done … I read part of your request as something I can't do yet"

**Where.** `buddy/outcome.py` `_no_skill_outcome`, the new `mention.search(said)` branch.

**Scenario.** "get rid of the repeated entries". Brain says `remove_duplicate_rows` "get rid of the repeated entries" and also lists `unsupported` with the same words (harness `e2`, probe_a S11). "remove duplicates, i.e. get rid of repeats" behaves the same (S15). Both happen in all three speakers.

**Reply.** "Partly done — removed 2 exact duplicate rows. I read part of your request as something I can't do yet. …" Base said "Done — removed 2 exact duplicate rows." That was true, because the whole request was carried out. So this is a false "can't do" about work that was done.

**Reachability.** It needs Brain to list a paraphrase twice. Nobody has measured how often the real Brain does that; there were no live runs. The writer flagged this as the owner's trade. It is the safer direction (z31 used to be a false "Done —"), but it is new on this branch.

**Fix options, for the owner.**
- (a) Keep it, and measure the double-listing rate in a live run.
- (b) Use a sentence for this branch that is true in both z31 and e2, such as "Some of your wording didn't match anything I can do, so check the copy is what you wanted." Keep "Partly done" only for entries that the mention patterns don't cover at all.

### R1-3 (LOW): guard tests that a reverted fix still passes

- `test_a_header_cannot_inject_sql_into_the_near_duplicate_check` passes on base, and also when the whole Splink fix is reverted (`splink-revert-to-headers` survives). Add an assertion that a header with a space (`"full name"`, the `_MESSY_NAME_PHONE_ROWS` rows) gives at least one finding. That fails on base: 0 findings.
- `test_a_cell_value_in_a_finding_is_quoted_and_capped` loops over findings that are empty for its frame (0 near-duplicates), so it checks nothing. Assert `findings` first.
- `hands-dispatch-msg-back` survives: no test checks that the dispatcher's text stays out of `ExecutionResult.errors` and the step summary.
- `row-attr-off` survives: no test refuses a far row by its `r` alone.
- `bytes-read-off` survives because the code can't be reached: `zipfile` never returns more than an entry's declared size.
- `analysis-missing-str-alert` survives. The skill's own missing-value text is never tested, though it doesn't reach replies, because the Inspector's recount is used.

### R1-4 (LOW): `check_workbook_limits` lets non-refusal errors out

**Where.** `_scan` catches `BadZipFile`, `OSError`, `ExpatError`, `ValueError`, `RuntimeError` and `EOFError`, but not `zlib.error` (corrupt deflate) or `NotImplementedError`. In `SpreadsheetAnalysisSkill.execute` the call sits outside the `try`.

**Reachability.** In a turn the planner's `sheet_names()` refuses the file first, so the reply is the fixed "could not read" sentence ([Likely] that it isn't reachable through a turn).

**Fix.** Catch `Exception` in `_scan` and return `None`.

### R1-5 (LOW): fewer retries than before

**Where.** `buddy/brain.py`, `buddy/think.py`.

**What changed.** A connection drop (`APIConnectionError`) no longer gets the stage's second try. Base retried it and delivered (probe_d `*-connection-x1`). This follows the instruction "429, timeout or 5xx", and the SDK retries twice underneath, so it's noted for the owner only.

### R1-6 (LOW): wording and docs

- `docs/backlog.md`, the status line under PR-B re-review 3, says the z31 reply "quotes 'delete the file'". It uses the generic sentence, as the implementation notes and tests say.
- `shown()` keeps curly quotes (`”`) and combining marks, so a header can look as if it closes the quote. Consider mapping `“”„` to `'` as well.
- In `inspector._read_cells`, the docstring "no cell converted to another type" doesn't hold for booleans (see "For the owner").

## For the owner (pre-existing on `main`, not caused by this branch)

- **TRUE/FALSE in a column of numbers come back as 1/0 in the delivered copy, and the reply says "Done —" with nothing about it** (`probe_j_bool_cells.py`, identical on base). The original column B is `[1, True, 0, False, 1]`; the delivered copy has `[1, 1, 0, 0]`. pandas converts the booleans even with `dtype=object`, and the Inspector compares `True == 1`, so a row `("b", True)` would also count as an exact duplicate of `("b", 1)`. This is HIGH-class on `main` and should be a backlog item of its own.
- A header that is a number (`2024`) makes a report turn say "Something went wrong while checking the result" on both trees (S06; `_KEY_COLUMN_PATTERN.search` is given an int).

## Deferred (added to `docs/backlog.md`)

R1-3, R1-4, R1-5 and R1-6. R1-2 if the owner keeps it as it is. The two pre-existing items above.

## Not run

- Live runs with Groq (the owner checks on the server, and this review didn't need them). So R1-2's reachability is unmeasured.
- Docker, Caddy, and a real 1 GiB cgroup. The memory figures are process peak RSS outside a container.
- `scripts/run_pip_audit.py` against the live advisory databases. The advisory text of PYSEC-2026-3447 isn't verified.
- The retry, bomb and odd-zip probes ran in the template speaker only (the speaker doesn't affect those paths). probe_a and the shared harnesses ran in all three.

## Review round 2 (commit 9d17990)

**Verdict: DON'T MERGE.** Two blocking MEDIUMs, R2-1 and R2-2: a hostile workbook still gets past `check_workbook_limits` and then needs more memory than the 1 GiB container. R1-1 is not closed. Its three round 1 cases are fixed, but the same attack still works through other files, including one of 22 KB. No HIGH. Rules 1 and 2 held in every run.

Scope: the diff `f1af923..9d17990` (the fixes in `fix-security-pass-fixes1.md`), the findings round 1 left open, and regression against base `89625e7`. New probes are in `scripts/review_harness/fix-security-pass/`: `probe_k_round2.py`, `probe_l_turns.py`, `mutations_r2.py`, `prototype_fix_r2.py` (see its `README.md`). The bomb probes use `OPENPYXL_LXML=False`, as in the image.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3613 passed, 5 skipped, 0 failed (1,222 s) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | Nothing new. Head still has one fewer `E501` (`buddy/hands.py`). |
| 3 | `harness.py` (532 turns), `rereview3.py` (1,078), `probe_a_pipeline.py` (19 × 3 speakers), head vs round 1's head | All 1,667 identical in reply, delivery and final facts. 0 errors, 0 Voice-model calls, 0 originals changed. |
| 4 | Round 1's R1-1 files (`probe_limit_bypass.py`, `probe_f_odd_zips.py`) | All refused on head: the 10k × 1,000 grid, `r="3e5"`, `r=" 300000"`, and the sheet outside `xl/worksheets/`. Corrupt deflate and method 99 now pass the check and are refused by `sheet_names()` (R1-4 fixed). |
| 5 | `probe_k_round2.py`: 13 new workbooks, check, then one pandas read in a child process capped at 3 GiB | 9 files pass the check and are bombs: 8 for R2-1 (K1–K5) and 1 for R2-2 (K7). K6 is refused for the wrong reason (R2-3). The controls behave: an ordinary file passes, and the 20k × 1,000 grid is refused. A grid of exactly 1,000 × 1,000 passes; its whole turn took 18 s at 374 MiB. |
| 6 | `probe_l_turns.py`: whole turns on K0, K1, K2, K5, K6, K7 (dedupe and report; K6 in all three speakers) | K1, K2 and K5: delivered after 81–87 s at a 1,099 MiB peak (round 1 measured 1,098). K7: a dedupe turn was still running at 15 min of CPU when I stopped it. Every reply's numbers match pandas' read. |
| 7 | New tests against f1af923 code | 9 of the new `test_hostile_workbooks.py` tests fail there, as they should. The new `test_file_text_in_replies.py` tests pass on f1af923, because the code they guard is older than that commit. R1-3 asked for them to fail on base: the Splink test and the dispatcher test are now killed by their mutations (row 8). |
| 8 | `mutations.py` + `mutations_r2.py`: 21 one-place mutations of the fixes | 11 killed, 10 survived. 4 of the survivors are in `shown()` (R2-4). `dollar-ref-old-regex` survives (R2-4). `entity-decl-off` and `doctype-off` each survive alone, but both together are killed. `row-unreadable-off` and `cellref-unreadable-off` survive harmlessly, because the reader then refuses with the same sentence. `analysis-missing-str-alert` still survives (known). |
| 9 | `prototype_fix_r2.py` (about 20 lines, `workbook.py` only), in an export | Refuses K1–K5 and passes K6 and the controls. 514 tests pass (`tests/skills/excel`, `test_text_cells`, `test_file_text_in_replies`, `test_multi_sheet`, `test_inspector*`, `tests/backend/test_uploads.py`). It doesn't address K7. |
| 10 | Original's SHA-256 before and after, every turn above (about 1,700) | Unchanged in every one. |

### R2-1 (MEDIUM, [Certain]; R1-1 not closed): five 7 KB workbooks pass the limits and read as 19,999 × 1,000

**Where.** `skills/excel/workbook.py` `_scan` and `_scan_part`.

**Scenario.** Each file has 1,000 headers in row 1 and one more cell 20,000 rows down. Head refuses this layout on its own (K0).
- **K1, the row number and the cell reference disagree.** The sheet has `<row r="20000"><c r="A1">`. pandas reads with openpyxl `read_only`, which puts a row's cells at the row's `r` (`_read_only.py` `_cells_by_row`). The grid check only counts the cell's own reference (`state["top_row"] = max(state["top_row"], row)`), so it sees a 1 × 1,000 grid. A dedupe turn delivered after 87 s at 1,099 MiB. With `r="150000"` (still inside `MAX_ROWS`), one read ran out of a 3 GiB cap.
- **K2, K3, K4: one part the scan can't finish ends the whole scan with no verdict.** `_scan_part` lets any exception escape, and `_scan`'s `except Exception` then returns `None`, so the workbook passes. Three ways to trigger it, each with an extra part placed first in the zip that openpyxl never opens:
  - malformed XML (`<a>`), or a VML part with an unclosed `<br>`, as legacy Excel form controls are reported to write [Possible];
  - a cell reference with 5,000 digits, where `int()` raises past Python's digit limit;
  - a damaged deflate stream.

  At f1af923 a malformed part outside `xl/worksheets/` was harmless (`_has_doctype` caught `ExpatError`). The R1-4 catch-all is what lets it through now.
- **K5, a part name that doesn't end in `.xml`.** Only `\.(xml|rels|vml)$` parts are streamed, but openpyxl loads whatever path the rels name. A sheet stored at `xl/worksheets/sheet1.bin`, or with no extension, is never scanned. A dedupe turn delivered after 81 s at 1,099 MiB.

**Reply.** It is honest about what pandas read: "Done — removed 19997 exact duplicate rows, including 19997 repeated blank rows. The copy has 2 rows". The problem is the memory, as in round 1.

**Reachability.** Any signed-in person, with an ordinary request. Each file takes a few lines of zip.

**Fix.** Apply `prototype_fix_r2.py` items 1–3:
- count the row's `r` in the grid: `max(state["top_row"], row, state["row"])`;
- stream every part whatever its name ends in, and catch exceptions per part, skipping only that part, since a reader can't get past that point either;
- make the element handler refuse (`_UNREADABLE`) instead of raising.

Add a test for each case (row/reference mismatch, junk part first, long digit reference, damaged deflate first, `.bin` sheet), and correct the ASVS rows.

### R2-2 (MEDIUM, [Certain]): a 22 KB workbook made of one long shared string takes minutes of CPU and about 10 GB per turn

**Where.** `skills/excel/workbook.py`: nothing bounds the text that cells point at. `_matched_date_format` in `skills/excel/analyze.py:179` and `buddy/inspector.py:459` runs `strptime` on every value whatever its length.

**Scenario.** K7 is 1,000 rows whose cells all point at one 10,000,000-character shared string. It is 22 KB zipped and 10 MB unzipped, inside every limit.
- On head, a "remove the duplicate rows" turn was still running at 15 min of CPU when I stopped it. The profile of a 200,000-character version spends its time in `_strptime`, called from both date matchers.
- With a prototype length guard in both matchers, the turn took 323 s at a **9,933 MiB** peak. It ended with "The deduplicate step: output has 1000 rows … 1000 output rows don't appear in its input", which is honest: the Inspector caught the changed copy and nothing was delivered.

Base has no limits at all, so this is pre-existing. It belongs here because the branch's `docs/security/asvs-l1.md` marks 12.1.2 ("compressed files can't exhaust the server") as Pass. In production the container is killed. Even short of that, a thread past its 180 s deadline keeps one of the two `max_concurrent_turns` slots, so two uploads lock out every user.

**Reachability.** Any signed-in person, with an ordinary request.

**Fix.**
- In the scan, refuse any shared or inline string over 32,767 characters, which is Excel's own cell limit, so no file Excel wrote is refused.
- Refuse when the characters the cells reference add up to more than a cap, for example `MAX_UNZIPPED_BYTES`. Record each `<si>` length from the shared-strings part, then add the length for each `t="s"` cell.
- Add `if len(value) > 32: return None` to both `_matched_date_format`s, since no listed format is longer.
- Add a test with K7's shape.

### R2-3 (LOW, [Certain]): calcChain.xml is counted as cells, so a two-sheet workbook is refused for a false reason

**Where.** `_scan_part` counts every spreadsheet-namespace `<c>`. `xl/calcChain.xml`, which Excel writes for any workbook with formulas, lists each formula cell as `<c r="B2" i="1"/>`. Its grid spans every sheet.

**Scenario.** K6 is 962 KB, with 120,001 cells: 60,000 rows with a formula column, and a summary formula in `Z1` of a second sheet. Every speaker replied "I can't work on this file: the workbook has more than 1,000,000 cells". At f1af923 the reply was "the workbook has 2 sheets, and only single-sheet workbooks are supported for now". The file is refused either way, so this is LOW. In one sheet, the double count only refuses a file with more than 500,000 formula cells, and that is [Likely] over the 2 MiB upload cap (my estimate: 4.9 MB).

**Fix.** Count a `<c>` only inside a `<row>` (`prototype_fix_r2.py` item 4).

### R2-4 (LOW): guard tests the fixes doc says exist, or that are missing

- `fix-security-pass-fixes1.md` says the new `shown()` behaviour has "tests added". No test exercises it: `shown-curly-quotes-kept`, `shown-leading-mark-kept`, `shown-trailing-mark-kept` and `shown-no-nfc` all survive. Add asserts for `“x”`, `«x»`, `＂x＂`, and a leading or trailing U+0301.
- `dollar-ref-old-regex` survives. No test has a `$A$1` reference, and without the `$` in the regex such a file would be refused as unreadable even though openpyxl reads it.

### R2-5 (LOW): wording

- The grid refusal uses `TOO_MANY_CELLS`, so a file with 1,001 cells is told "the workbook has more than 1,000,000 cells" (K0, and the 1,001 × 1,000 turn). Something like "the sheet spans more than 1,000,000 cells" would be true.
- `shown()` leaves look-alikes such as `″` (U+2033), `❞`, `״` and `ʺ`. None of them ends a straight-quote quotation, so this is cosmetic.

### Earlier findings

| Finding | Status (my evidence) |
|---|---|
| R1-1 | Its three cases are fixed (row 4). The class is not closed: R2-1 and R2-2. |
| R1-2 | Unchanged. Still the owner's call, not blocking. The probe_a S11 and S15 replies are identical to round 1. |
| R1-3 | Splink: fixed (`splink-revert-to-headers` killed). Dispatcher text: fixed (`hands-dispatch-msg-back` killed). Cell-value quoting: the test now asserts findings first. Row attribute: fixed (`row-number-isdigit` killed). Bytes-read budget: removed. The skill's own missing-value text is still untested (known). |
| R1-4 | Fixed (row 4), but the catch-all is now part of R2-1. |
| R1-5 | Unchanged. Owner's note. |
| R1-6 | The backlog line and the `_read_cells` docstring are fixed. The `shown()` change is in, but untested (R2-4). |

### Deferred (added to `docs/backlog.md`)

R2-3, R2-4 and R2-5. R1-2 and R1-5 for the owner, as in round 1. Seen in passing and pre-existing: a report on K1 lists 1,000 "column … missing values" findings in one reply.

### Not run

- Live runs with Groq. No change in this round alters what the pipeline says.
- Docker and a real 1 GiB cgroup. The memory figures are process peak RSS, plus a 3 GiB `RLIMIT_AS` child for single reads.
- A real Excel-written calcChain or VML file. Both were built by hand from the format.
- K7 to completion on head (stopped at 15 min). K7 and the K1/K2/K5 turns in the friend and focus speakers: the speaker doesn't touch the read path, and K6 ran in all three.
- A prototype of R2-2's scan-side text cap. Only the date-matcher guard was prototyped.
