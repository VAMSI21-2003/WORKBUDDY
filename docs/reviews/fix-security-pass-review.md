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

## Review round 3 (commit c766fa6)

**Verdict: DON'T MERGE.** No HIGH. R2-1, R2-2 and R2-3 are closed: every round 2 file is refused or passes as it should. Five blocking MEDIUMs, each a small upload that passes every limit and then needs over 1 GiB or many minutes: two regressions from the new reader-based check (R3-1, R3-2), one from the branch's own text cap (R3-4), and two pre-existing in the class `docs/security/asvs-l1.md` marks "Pass" (R3-3, R3-5). Small fixes for R3-1, R3-2, R3-5 and most of R3-3 are prototyped. Rules 1 and 2 held in every run.

Scope: the diff `211a823..c766fa6` (`fix-security-pass-fixes2.md`), the findings round 2 left open, and regression against base `89625e7`. New probes are in `scripts/review_harness/fix-security-pass/` (see its `README.md`, "Round 3"). Every bomb run uses `OPENPYXL_LXML=False`, as in the image. Turn memory is measured with `--no-facts`, because the harness's own openpyxl read of the original otherwise counts in the peak. Timings come from a 4-core machine that was often loaded, so take them as ±2×.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3639 passed, 5 skipped, 0 failed (1,343 s) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | Nothing new (17 vs 18; head has one fewer `E501` in `buddy/hands.py`). |
| 3 | `harness.py` (532), `rereview3.py` (1,078), `probe_a_pipeline.py` (19 × 3 speakers), head vs round 2's head | All 1,667 identical in reply, delivery and final facts. 0 errors, 0 originals changed. The 6 `claim-words` flags are the hostile header and value, quoted and capped, as in rounds 1 and 2. |
| 4 | Round 1 and 2 bombs (`probe_limit_bypass.py`, `probe_k_round2.py --read`) | All refused: K1–K5 and the round 1 files by the grid, rows or columns, and K7 by the 32,767-character cap. K6 now passes (R2-3 closed). The control passes. |
| 5 | New tests against the round 2 code (`9d17990`, with the 4 new constants shimmed in) | 20 of the new `test_hostile_workbooks.py` tests fail there, as they should. The new `shown()` tests pass there, because that code was already in `9d17990` (R2-4 was about missing tests). |
| 6 | `mutations_r3.py`: 15 mutations of the round 2 fixes, plus round 2's 4 `shown()` survivors re-run | Killed: the 4 `shown()` mutations and 9 of the 15. Survived: `walk-no-reset-dimensions`, `date-guard-inspector-off`, `date-guard-analyze-off` and `shown-round3-quotes-dropped` (R3-7). `entity-handler-off` and `doctype-handler-off` survive harmlessly, because each one covers the other. |
| 7 | `probe_m_round3.py`: 16 new workbooks, each through the upload route's `check_workbook` and then `check_workbook_limits` in a capped child | Each file fits the 2 MiB upload cap and the 64 MiB unpacked cap, and the upload route accepts it. Results are under R3-1 to R3-6. |
| 8 | `probe_n_turns.py`: whole dedupe and report turns on those files, head vs base, plus `prototype_fix_r3.py` | The table below. Every reply's numbers match my own read: M9 has 99,999 rows with 50,000 distinct keys, and the reply says "removed 49999 … The copy has 50000 rows". The M5 copy has 1 row. The M7 copy has 1,500 distinct rows, so 0 removed. |
| 9 | `prototype_fix_r3.py` (about 35 lines in `workbook.py` and `analyze.py`), in an export | Everything it addresses falls to ≤ 320 MiB and ≤ 6 s: the turns for M1, M4, M5 and M8, and the analysis alone for M6. It doesn't address M3, M7 or M9. 1,057 tests pass (`tests/skills/excel`, `test_text_cells`, `test_file_text_in_replies`, `test_inspector*`, `tests/backend/test_uploads.py`, `test_engine_factory`). |
| 10 | Original's SHA-256 before and after, every turn above (about 1,720) | Unchanged in every one. |

Whole dedupe turns, app only (`--no-facts`). Each file is ≤ 210 KB and the upload route accepts it.

| File | Head | Base | Prototype |
|---|---|---|---|
| M1: 2,000 sheets declared on one part holding `<row r="200000"/>` (22 KB) | 109 s ("the workbook has 2000 sheets") | 1 s, same reply | 1 s |
| M1: the same with 20,000 sheets (206 KB) | check alone still running at 900 s | 13 s | check 3.9 s |
| M8: 1.4 M `<definedName>` in `workbook.xml` (177 KB) | 32 s, **1,130 MiB**, "could not read" | 0 s, 165 MiB, same reply | 0 s |
| M5: one external link caching 2.3 M cells (158 KB) | 575 s, **2,290 MiB**, delivered "Done —" | 539 s, 2,289 MiB, same | 6 s, 320 MiB, same reply |
| M4: 1.05 M `<xf>` in `styles.xml` (191 KB) | 72 s, 818 MiB, "could not read" | 46 s, 818 MiB | 0 s |
| M3: 3.6 M shared strings (159 KB) | still running at 900 s | still running at 900 s | not addressed |
| M7: 1,500 cells of 32,767 characters (49 M in all, inside both caps; 89 KB) | 34 s, **1,824 MiB**, delivered | 46 s, 1,681 MiB | not addressed |
| M6: 200 rows, 30,000-character names (12 KB) | 9 s, **2,728 MiB**, dedupe step "could not read the workbook" | 10 s, 2,754 MiB, same | analysis alone 1.4 s, 196 MiB |
| M6: 10,000 rows, 20-character names, same first 3 letters (149 KB) | analysis alone killed at 6,302 MiB after 22 s | killed at 6,419 MiB | analysis alone 0.2 s, 127 MiB |
| M9: 1,000,000 numeric cells, at the cell cap (304 KB) | 570 s, 416 MiB, delivered | 511 s, 411 MiB | not addressed |

### R3-1 (MEDIUM, [Certain], regression): one sheet part declared as thousands of sheets makes the walk run for minutes, then hours

**Where.** `skills/excel/workbook.py` `_reader_verdict`: `for sheet in book.worksheets:`, with `MAX_ROWS` counted per sheet.

**Scenario.** `workbook.xml` declares N sheets, and the rels point every one of them at the same part, `<sheetData><row r="200000"/></sheetData>`. The upload route counts parts, so it sees one worksheet and accepts the file. The walk then pads 199,999 empty rows for each sheet, with a grid of 0, so no limit is hit. Afterwards the planner refuses the file anyway, honestly: "the workbook has 2000 sheets, and only single-sheet workbooks are supported for now". 2,000 sheets (22 KB): 109 s on head, 1 s on base. 20,000 sheets (206 KB): the check alone was still running at 900 s; base took 13 s. At the 2 MiB upload cap that is roughly 190,000 sheets, or about 3 h of CPU [Likely, extrapolated]. The turn's thread keeps one of the two turn slots after its 180 s deadline (round 2).

**Reachability.** Any signed-in person, with an ordinary request.

**Fix.** Either walk only `book.worksheets[:1]`, which is all pandas reads (the single-sheet rule then refuses the rest with its own true sentence; `test_the_grids_of_all_sheets_count_together` would have to change), or count the rows of all sheets together against `MAX_ROWS` (`prototype_fix_r3.py` item 2). With the second, the refusal must not say "the sheet has more than 200,000 rows", because no single sheet does: in the prototype the M1 reply says exactly that, so use a sentence that is true for both cases. Add a test with many sheets on one part that asserts the check is refused or finishes within a second or two.

### R3-2 (MEDIUM, [Certain], regression): the walk parses `workbook.xml` before `sheet_names()` caps its size, so a 177 KB file takes 1.1 GiB

**Where.** `check_workbook_limits` now runs first in `sheet_names()`, and its walk calls `openpyxl.load_workbook`, which parses the whole workbook part into objects (defined names included). On base, `_declared_sheet_names` → `_read_part` refused a workbook part over `_MAX_PART_BYTES` (16 MiB) before openpyxl ever saw it.

**Scenario.** M8: 1.4 M copies of `<definedName name="n">S1!$A$1</definedName>` (57 MiB unpacked, 177 KB zipped). On head, a dedupe turn ran 32 s at 1,130 MiB, then said "could not read the workbook". On base the same reply came in 0 s at 165 MiB.

**Reachability.** Any signed-in person.

**Fix.** Before any openpyxl load, refuse a workbook part whose declared size is over a small cap (the prototype uses 1 MiB; the existing comment says "a real workbook.xml is a few KB"), as in `prototype_fix_r3.py` item 3. Add a test.

### R3-3 (MEDIUM, [Certain], pre-existing, in the class ASVS marks Pass): parts openpyxl reads whole on every load make a 160–190 KB upload take 0.8–2.3 GiB or more than 15 minutes

**Where.** Every `openpyxl.load_workbook` in `workbook.py` (the walk, `_zip_sheet_names`, `_sheet_problem`, and `formula_cell_counts` twice) uses openpyxl's default `keep_links=True`. pandas passes `keep_links=False`. openpyxl also parses `styles.xml` and all of `sharedStrings.xml` on every load, before a single row.

**Scenario and result.** See the table: M5 (an external link) 2,290 MiB and 575 s, then a delivered "Done —"; M4 (styles) 818 MiB; M3 (shared strings) still running at 900 s. Base is the same, but the branch's ASVS row "Decompression bomb, huge sheets, huge strings" says Pass. Round 2 held R2-2 to the same standard.

**Reachability.** Any signed-in person.

**Fix.**
- Pass `keep_links=False` to all five loads (item 1 of the prototype; M5 falls to 6 s and 320 MiB). This also closes R3-6.
- Cap `styles.xml` by its declared size (the prototype uses 8 MiB).
- For shared strings (not prototyped): refuse more than `MAX_CELLS` `<si>` entries, counted in the pass that already streams every part, and measure a turn at that cap. A turn loads the workbook about 8 times (backlog: cost of the reader-based check), so the per-load cost multiplies.
- Correct the ASVS row for whatever is left open.

### R3-4 (MEDIUM, [Certain]): `MAX_TEXT_CHARS` (50 M) lets an 89 KB upload through to a 1.8 GiB turn

**Where.** `skills/excel/workbook.py` `MAX_TEXT_CHARS = 50_000_000`, a cap this branch chose.

**Scenario.** M7 has 1,500 distinct cells of 32,767 characters each (49 M characters, under both caps). A dedupe turn delivered correctly after 34 s, at a 1,824 MiB peak (base 1,681 MiB, no caps). That is about 30 bytes per character of cell text across a turn.

**Fix.** Lower the cap so that a turn at the cap measures well under the container's 1 GiB (about 10 M characters, by this measurement), and add the measurement to the ASVS row.

### R3-5 (MEDIUM, [Certain], pre-existing, in the class ASVS marks Pass): the near-duplicate check's pairs take more than 6 GiB from a 41–149 KB file

**Where.** `skills/excel/analyze.py` `NearDuplicateAnalyzer`. Splink materialises every pair that `substr(name, 1, 3)` or the key blocks together, with both full strings, and the analysis runs on every turn.

**Scenario.** A `name`/`email`/`phone` table:
- 10,000 rows whose names share their first 3 letters (149 KB): the analysis alone was killed at 6.3 GiB after 22 s, on head and on base;
- 2,000 rows of 1,000-character names (41 KB): over 7 GiB in 8 s;
- 200 rows of 30,000-character names (12 KB): 2.7 GiB. Under a 4 GiB address cap, the dedupe step then reported "could not read the workbook; it may be damaged", which is false about the file. It was a memory failure.

**Fix.** Prototyped in `prototype_fix_r3.py` item 4, about 10 lines. Give Splink at most 100 characters of each field, and skip the check when its two blocking rules would pair more than 250,000 rows, which can be counted with `value_counts` before Splink starts. All three files then take ≤ 196 MiB, and the tests above pass. Alternatively, record it as open in the ASVS row and the backlog: an owner's call, but the row must not say Pass.

### R3-6 (LOW, [Certain]; not reachable through a turn today [Likely]): "pandas can't read it either" isn't true when the walk and pandas load differently

M2 is the grid bomb plus an `<externalReference>` whose part is missing. The walk's `load_workbook` raises, so it returns no verdict and the check passes. `pd.read_excel` reads 19,999 × 1,000 (542 MiB). Today `_zip_sheet_names` raises the same way first, so every turn says "could not read the workbook" (3 kinds of turn, 0 s). If R3-3's `keep_links=False` goes into `sheet_names` but not into the walk, M2 becomes reachable. Fix: load with exactly pandas' arguments (`read_only=True, data_only=True, keep_links=False`) in the walk, and add M2 as a test.

### R3-7 (LOW): guard tests that a reverted fix still passes, and a claim in the fixes doc

- `walk-no-reset-dimensions` survives. No test has a `<dimension>` that understates the sheet; without `reset_dimensions()` a bomb with `<dimension ref="A1:B2"/>` walks as 2 rows (checked by hand). Add one.
- `date-guard-*-off` survive. `_matched_date_format("x" * 5_000_000)` fails fast without the guard. The crawl happens when `strptime` formats the long value into its error message 10 times per cell (round 2's profile). Assert that it is fast for many calls, or mock `strptime` and assert it is never called.
- `shown-round3-quotes-dropped` survives. `fix-security-pass-fixes2.md` says "Tests for 17 kinds of double quotation mark", but `QUOTE_MARKS` has 8, and none of the six marks added in this round (U+2033, U+2036, U+2039, U+203A, U+275D, U+275E) or the three CJK marks is tested.

### R3-8 (LOW, for the owner, pre-existing): a file at the cell cap takes about 9 minutes per turn

M9 is 1,000,000 numeric cells (304 KB). A dedupe turn delivered correctly after 570 s on head and 511 s on base, at 416 MiB. The cap admits it, and so would a real file of that size. With two turn slots and a 180 s deadline that doesn't stop the thread, two such uploads hold every slot for about 10 minutes.

### Earlier findings

| Finding | Status (my evidence) |
|---|---|
| R2-1 | Closed. K1–K5 refused (row 4). Their tests fail on `9d17990` (row 5). The class is now covered by the reader walk, apart from R3-1, R3-2 and R3-6. |
| R2-2 | K7's shape is closed (refused by the 32,767 cap; the turn test passes). The total-text cap is too high (R3-4). |
| R2-3 | Closed. K6 passes the check. |
| R2-4 | The 4 `shown()` mutations are killed. `$A$1` is moot, because the XML isn't parsed by hand any more. New gaps are in R3-7. |
| R2-5 | The refusal says "spans"; U+05F4 and U+02BA are left (backlog). |
| R1-2, R1-5 | Unchanged. Still the owner's call. probe_a S11 and S15 give the same replies as in round 2. |

### Deferred (added to `docs/backlog.md`)

R3-6, R3-7 and R3-8. R1-2 and R1-5 for the owner, as before.

### Not run

- Live runs with Groq: nothing in this round changes what the pipeline says.
- Docker and a real 1 GiB cgroup. The figures are process peak RSS (sampled for the killed runs), with a 3–4 GiB `RLIMIT_AS` child for most runs. Under that cap DuckDB's failures show up as "could not read", where a container would be killed.
- M3 and the 20,000-sheet M1 to completion (stopped at 900 s). M1 at the upload cap (about 190,000 sheets) is extrapolated, not run.
- A prototype of the shared-strings cap (R3-3) and of a lower `MAX_TEXT_CHARS` (R3-4).
- The bomb turns in the friend and focus speakers: the speaker doesn't touch the read path, and probe_a covered all three.
- A real Excel-written file with an external link, or with very large styles, for the size of legitimate parts. The prototype's 1 MiB and 8 MiB caps are guesses, to be checked against real files before they are adopted.

## Review round 4 (commit 43c9d1b)

**Verdict: DON'T MERGE.** No HIGH. Three blocking MEDIUMs, each a small upload that passes every check and then needs about 1 GiB or more, or many minutes: the round 3 caps go by part name, and openpyxl finds parts by content type (R4-1, partly a regression against base); other parts openpyxl parses whole on every load are still uncapped (R4-2, one of them a regression); and the text cap counts characters, not memory (R4-3, so R3-4 isn't closed). The caps-by-list approach has now been bypassed in four rounds running. The fix that closes the class is a resource limit on the scan itself, prototyped in `prototype_fix_r4.py`. Rules 1 and 2 held in every run.

Scope: the diff `cd04bef..43c9d1b` (`fix-security-pass-fixes3.md`, the owner's R1-2 option (b), and R3-1 to R3-7), the findings round 3 left open, and regression against base `89625e7`. Probes are in `scripts/review_harness/fix-security-pass/` (README, "Round 4"). Bomb runs use `OPENPYXL_LXML=False`, as in the image, and turn memory is the app's alone (`--no-facts`).

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3670 passed, 5 skipped, 0 failed (1,295 s) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | Nothing new (17 vs 18; head has one fewer `E501` in `buddy/hands.py`) |
| 3 | `harness.py` (532), `rereview3.py` (1,078), `probe_a_pipeline.py` (57), head vs round 3's head | Delivery and final facts are identical on all 1,667 turns. 0 errors, 0 Voice-model calls, 0 originals changed. 121 replies differ, all in the same way: the owner's option (b) turns "Partly done … I read part of your request as something I can't do yet" into "Removed 2 exact duplicate rows. Some of your wording overlaps what I did, so I can't tell if you asked for something more; if you did, I haven't done it, so check the copy is what you wanted." (e2, S11, S15, z05, z29, z31, z32, z42, rr2-s22). On z16 the sentence is added beside the existing "Your message mentions empty rows" sentence (R4-4). |
| 4 | Round 3's files (`probe_m_round3.py`, then `probe_n_turns.py` on M1, M4, M5, M8) | All closed as named: M1 with 20,000 sheets is refused in 3.8 s (was 900 s or more), M8 and M4 in 0 s, M3 by the strings cap, M7 by the text cap, M2 by the grid. An M5 turn now takes 4 s at 319 MiB (was 575 s, 2,290 MiB). The M6 analyses take ≤ 1.5 s and ≤ 196 MiB (were 2.7 to 6.3 GiB). |
| 5 | New tests against the round 3 code (`c766fa6`, with the new names shimmed in) | 56 fail there, as they should: 8 in `test_hostile_workbooks.py`, 2 in `test_analyze.py`, 1 in `test_outcome.py`, 27 in `test_residual_done_paths.py`, 18 in `test_engine_factory.py`. |
| 6 | `mutations_r4.py`: 18 mutations of the round 3 fixes, plus round 3's 4 survivors re-run | Killed: the 4 old survivors (R3-7 closed) and 16 of the 18. Survived: `pair-count-name-only` and `said-key-without-reason` (R4-5). |
| 7 | `probe_p_round4.py`: 14 new workbooks, through the upload route's check and then `check_workbook_limits` | The upload route accepts all 14. The table below has what the check lets through. |
| 8 | `probe_q_round4.py` U1–U8 (the neutral sentence where nothing changed, nothing came back, only a report was asked for, a later chain step failed, a real extra request), 3 speakers, head, round 3's head and base | No false claim. Where the neutral sentence appears, a copy was handed back each time (U1: 0 removed, but the copy was still delivered). With a report only (U2) or a failed chain (U3) it doesn't appear. The numbers match my own read (`plain`: 6 rows, 2 repeats, so 4 left, 1 of 4 missing = 25%). |
| 9 | `probe_r_neardup_limit.py` and probe_q N0–N3, head and base | R4-6. |
| 10 | `prototype_fix_r4.py --as-mib 512` | Refuses P1–P7b, P8–P8c and P10 (a 60 s limit), and still passes P0, P9, M5, M6 and M9 (1,000,000 cells, 10.9 s). 372 of 377 tests pass in `tests/skills/excel`, `test_text_cells`, `test_file_text_in_replies` and `tests/backend/test_uploads.py`. The 5 that fail monkeypatch a limit in the test process, which the child process doesn't see, so the fix needs a seam for them. At its weighted cap a turn takes 564 MiB (ASCII) and 475 MiB (emoji). |
| 11 | Original's SHA-256 before and after, every turn above (1,730) | Unchanged in every one. 2 bomb turns were stopped at 900 s, so neither has a second hash. |

Dedupe turns, app only. Each file is at most 224 KB, and the upload route accepts it.

| File | Check alone (head) | Turn, head | Turn, base |
|---|---|---|---|
| P1: round 3's M8 with the workbook part at `xl/book.xml` (177 KB) | 39.5 s, 1,094 MiB, passes | 29 s, **1,131 MiB**, "could not read the workbook" | 0 s, 164 MiB, same reply |
| P2: round 3's M3 with the shared strings at `xl/strings.xml` (159 KB) | 46.5 s, passes | still running at 900 s | not run (M3 on base: still running at 900 s) |
| P3: 1.4 M `<Override>` in `[Content_Types].xml` (169 KB) | 32 s, 927 MiB, passes | 24 s, **964 MiB**, "could not read" | 0 s, 165 MiB, same reply |
| P4: 1.3 M relationships in the workbook's rels (160 KB) | 15 s, 879 MiB, passes | 328 s, **1,103 MiB**, delivered "Done —" | 313 s, 1,102 MiB, same |
| P5: 1.3 M relationships in the sheet's rels (161 KB) | 14 s, 899 MiB, passes | 339 s, **1,123 MiB**, delivered | not run |
| P6: 600 k custom properties (224 KB) | 16 s, 651 MiB, passes | 388 s, 973 MiB, delivered | not run |
| P7b: 1.6 M `<xf/>`, styles 7.6 MiB, under the 8 MiB cap (13 KB) | 25 s, 851 MiB, passes | 79 s, 903 MiB, "could not read" | 59 s, 903 MiB, same |
| P8: 9.99 M ASCII characters, under the 10 M cap (19 KB) | 0.2 s, passes | 13 s, 790 MiB, delivered | 23 s, 758 MiB |
| P8b: the same in emoji (48 KB) | 0.4 s, passes | 41 s, **1,955 MiB**, delivered | 40 s, 1,746 MiB |
| P8c: the same in CJK (39 KB) | 0.4 s, passes | 29 s, **1,719 MiB**, delivered | 25 s, 1,620 MiB |
| P10: 20,000 sheets on one part whose rels have 2,000 entries (207 KB) | 396 s, passes | not run | still running at 900 s |

Every delivered reply's numbers match the file. P4 to P6 hold 3 rows with 1 repeat, and the reply says "removed 1 … The copy has 2 rows". P8 has 305 distinct rows, and the reply says "no exact duplicate rows … 305 rows".

### R4-1 (MEDIUM, [Certain]; R3-2 and the strings part of R3-3 not closed; P1 is a regression against base): the new caps go by part name, and openpyxl doesn't

**Where.** `skills/excel/workbook.py` `_scan`: `name.endswith("workbook.xml")`, `name.endswith("styles.xml")`; `_part_verdict`: `entry.filename.lower().endswith("sharedstrings.xml")`.

**Scenario.** openpyxl finds the workbook part and the shared strings by `[Content_Types].xml` (`reader/excel.py` `_find_workbook_part`, `read_strings`), so either can be called anything, and `_declared_sheet_names` follows the content types too. Round 3's M8 with the workbook part at `xl/book.xml` (P1) passes the 1 MiB cap. The walk's load then takes 1,131 MiB before `_read_part`'s 16 MiB cap refuses the file. On base that cap came first: 0 s at 164 MiB. Round 3's M3 with the strings at `xl/strings.xml` (P2) passes the 1,000,000-strings cap, and the turn was still running at 900 s. Only `styles.xml` is read by a fixed name (`ARC_STYLE`), so that cap holds.

**Reply.** Honest ("could not read the workbook"), after the memory is spent.

**Reachability.** Any signed-in person. It takes renaming two zip entries and one content-type line.

**Fix.** See R4-2: one fix covers both.

### R4-2 (MEDIUM, [Certain]; P3 is a regression against base, the rest is pre-existing in the class ASVS marks Pass): other parts openpyxl parses whole on every load are uncapped

**Where.** Each `openpyxl.load_workbook` in `workbook.py`, and pandas' own. A read-only load parses `[Content_Types].xml`, the workbook's rels, every sheet's rels (once per declared sheet), `docProps/core.xml`, `docProps/custom.xml` and `styles.xml` into objects. None of these is capped, apart from styles at 8 MiB.

**Scenario.** See the table: P3, P4, P5 and P6 take 964 to 1,123 MiB per turn, and P4 to P6 take 5.5 to 6.5 minutes, then deliver "Done —". P7b sits under the 8 MiB styles cap and takes 903 MiB. P10 declares 20,000 sheets (under the 1 MiB workbook cap) on one part, whose rels part is parsed once per sheet: the check alone takes 396 s. The R3-1 fix counts rows, not the load. P3 is a regression: base's `_read_part` refused the 55 MiB content-types part before openpyxl saw it, and head's walk now runs first. The others are the same on base.

**Reachability.** Any signed-in person, with an ordinary request.

**Fix.** Stop listing parts. Run `_scan` in a child process under an address-space cap and a wall-clock limit, and refuse with a fixed sentence when the child passes either (`prototype_fix_r4.py`, about 40 lines). The later loads of the same file in the server are the same openpyxl load, so the child's success bounds them, and no list of part names or element kinds has to be complete. Two details the prototype found:
- The scan's `except Exception` catch-alls also catch `MemoryError`. Under a cap that reads as "can't be read" and passes the file, so re-raise it.
- The tests that monkeypatch `MAX_*` need a seam, such as calling `_scan_here` directly or passing the limits to the child.

The 512 MiB and 60 s figures pass every ordinary and at-cap control here (M9 in 10.9 s). Measure them on the image before adopting them. A turn loads the workbook about 8 times, so a 60 s scan can still mean several minutes of turn (R3-8, the owner's call). Then correct the ASVS row. If the owner prefers caps, they must be keyed on what openpyxl reads (the part named by the content types or by the rels, or the root element seen in the expat pass) and must cover every part listed above, including an element-count cap for styles. That is the fifth patch of the same list.

### R4-3 (MEDIUM, [Certain]; R3-4 not closed): the 10 M text cap counts characters, and CPython stores emoji at 4 bytes and CJK at 2

**Where.** `_reader_verdict`: `chars += len(value)`, `MAX_TEXT_CHARS = 10_000_000`.

**Scenario.** 305 cells of 32,767 emoji (P8b, 48 KB) deliver correctly after 41 s, at **1,955 MiB**. In CJK (P8c, 39 KB) the turn takes 1,719 MiB. Even ASCII at the cap (P8) takes 790 MiB, where the fixes doc expected "about 300 MiB" without measuring it. Base is similar, but R3-4 asked for a cap with a measured turn well under 1 GiB.

**Fix.** Count what CPython stores, which is `len(value)` times 1, 2 or 4 by the widest character, and pick the cap from a measured turn. The prototype's 4,000,000 gives 564 MiB for ASCII and 475 MiB for emoji, against a 319 MiB control. That is still a lot with two turn slots in 1 GiB, so the owner may want a lower cap. Add a test with emoji text.

### R4-4 (LOW, [Certain]): the neutral sentence says "I can't tell if you asked for something more" when the message plainly does

"get rid of the repeated entries and delete the file" (rr2-s22), "… and then rename it" (z29) and "remove duplicates and email it to bob" (U4) were "Partly done … I read part of your request as something I can't do yet", which was accurate. When Brain's phrase for the dedupe spans the whole message, they now get the neutral sentence. It isn't false, because "if you did, I haven't done it" follows, and it is option (b) as the owner chose it. Clearer wording, true in all three cases: "Some of your wording overlaps what I did. Anything more it asks for, I haven't done, so check the copy is what you wanted." On z16 ("remove duplicates and empty rows"), the sentence now stands beside "Your message mentions empty rows, but I didn't run the empty-row removal", which already says it.

### R4-5 (LOW): guard tests that a reverted fix still passes

- `pair-count-name-only` survives: no test has a table whose key column alone passes the pair limit (many blank or repeated phones).
- `said-key-without-reason` survives: no test has an `UNSURE` entry and a generic `NO_SKILL` entry in one turn, which the reason in the key keeps apart.

### R4-6 (LOW, [Certain]): blank phone or email cells all block together, so an ordinary contact list skips the near-duplicate check

**Where.** `NearDuplicateAnalyzer`: blanks become the string `"nan"` (`astype(str)`), and the pair count and Splink's `block_on(key)` treat them as one value.

**Scenario.** 800 contacts, 710 of them with no phone: 263,939 pairs, so the check is skipped. With every phone present, it finds the "Jonathan / Jonathon Smithers" pair (`probe_r_neardup_limit.py`). Base found that pair in none of my frames, and no near-duplicate finding reached a report reply on either tree (N0), so nothing is lost against base. The check is skipped silently, as the row limit already is.

**Fix.** Make blank keys null before counting and blocking (Splink doesn't block on nulls), and leave them out of the pair count.

### Earlier findings

| Finding | Status (my evidence) |
|---|---|
| R1-2 | The owner's option (b) is in. The replies are true in every case I ran (row 3, U1–U8). Wording: R4-4. |
| R3-1 | Closed for the case reported (M1, 20,000 sheets, 3.8 s). P10 is the same idea through rels (R4-2). |
| R3-2 | Closed for a part named `workbook.xml`; R4-1. |
| R3-3 | `keep_links=False`: closed (M5 turn 4 s, 319 MiB). Styles cap: closed for M4, but P7b fits under it (R4-2). Shared-strings cap: bypassed by name (R4-1). |
| R3-4 | Not closed (R4-3). |
| R3-5 | Closed (row 4). R4-6 is a side effect. |
| R3-6, R3-7 | Closed (M2 refused; the 4 survivors are killed). |
| R3-8, R1-5 | Unchanged, the owner's call. |

### Deferred (added to `docs/backlog.md`)

R4-4, R4-5 and R4-6. R3-8 and R1-5 for the owner, as before.

### Not run

- Live runs with Groq. The R1-2 wording changes what the pipeline says, but no model call was involved.
- Docker and a real 1 GiB cgroup. The figures are process peak RSS, with a 4 GiB `RLIMIT_AS` per child.
- P2 to completion (stopped at 900 s), P10 as a head turn, and P5, P6 and P2 on base.
- The friend and focus speakers for the bomb turns (the speaker doesn't touch the read path). U and the shared harnesses ran in all three.
- The prototype's limits measured in the production image, and its effect on the 5 monkeypatched tests beyond the reason they fail.
