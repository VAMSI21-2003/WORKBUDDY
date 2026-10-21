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

## Review round 5 (commit 2ac4f8a)

**Verdict: DON'T MERGE.** No HIGH. One blocking MEDIUM (R5-1): the Inspector's formula count runs in the server, outside the new child and its limits, and it is the only read that parses formulas. A 376 KB upload passes the check in 2.6 s, then a dedupe turn peaks at 1,840 MiB and is delivered "Done —". The fix is one line, prototyped: run `formula_cell_counts` in the child too. With it, a file at the 1,000,000-cell cap needs a time limit above 60 s (it passed at 120 s). Every round 4 file is now refused, the controls pass, and rules 1 and 2 held in every run.

Scope: the diff `2ea20cf..2ac4f8a` (`fix-security-pass-fixes4.md`: the child-process check, the owner's conditions, R4-4), the findings round 4 left open, and regression against base `89625e7`. Probes are in `scripts/review_harness/fix-security-pass/` (README, "Round 5"). Bomb runs use `OPENPYXL_LXML=False`, as in the image. Turn memory is the app's alone (`--no-facts`), and the checking child is not included. Runs outside pytest use the real child; `tests/conftest.py` stubs it only inside the suite.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3,689 passed, 5 skipped, 0 failed (1,405 s, on a loaded machine) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | Nothing new (17 vs 18; head has one fewer `E501` in `buddy/hands.py`) |
| 3 | `harness.py` (532), `rereview3.py` (1,078), `probe_a_pipeline.py` (57), head (real child) vs round 4's head (round 3's for probe_a) | Delivery and final facts are identical on all 1,667 turns. 0 errors, 0 Voice-model calls, 0 originals changed. 112 harness replies differ, all only by the R4-4 sentence. 9 probe_a replies are round 4's option (b) change, now worded as R4-4. The 3 `claim-words` flags are the hostile cell value, quoted, as in every earlier round. |
| 4 | Round 3 and 4 files (`probe_m_round3.py`, `probe_p_round4.py`), check with the real child | All closed. P1, P2, P4–P7b, M3, M4 and M8 get "needs more memory or time" in 4–33 s. P10 is refused at the 60 s limit. P3 is refused in 0 s by the `[Content_Types].xml` size, as on base. P8–P8c, M6 (long names) and M7 hit the byte-weighted text cap in about 1 s, M1 the row count, M2 the grid. These pass: P0, M0, P9, P11, P11b, M5, M6 (short names), M1e and M9 (41.6–46.7 s against the 60 s limit, with the machine loaded). |
| 5 | Whole turns on files that pass (`probe_n_turns.py`, dedupe and report) | P0 319 MiB, P9 549 MiB, P11 552 MiB, P11b 475 MiB, all delivered or reported correctly. Every number matches my own pandas read (3 rows with 1 repeat; 1; 122; 30, none repeated). In the owner's 1 GiB, add a checking child of up to about 410 MiB resident for a second user. That is the unmeasured headroom the fixes doc and the backlog already name. |
| 6 | `probe_t_round5.py`: the check answers busy, crash, timeout or can't start, on the upload or only on the copy the turn wrote (11 cases × 3 speakers) | No false claim, no file handed back on any failure, originals unchanged. The real child runs through `build_engine` (T0: delivered, numbers right). R5-2 and R5-4 come from these runs. |
| 7 | `probe_s_round5.py` and the turns on its files, head and base | R5-1. |
| 8 | `prototype_fix_r5.py` (one line: `formula_cell_counts` in the child) | Refuses S1, S2, S4 and S5, and passes P0 and S0. M9 is refused at 60 s (60.4 s) and passes at 120 s (66.8 s). 467 tests pass in `tests/skills/excel`, `test_inspector.py`, `test_text_cells.py`, `test_file_text_in_replies.py` and `tests/backend/test_uploads.py`. |
| 9 | New tests against round 4's code (`43c9d1b`) | `test_scan_child.py` and `test_hostile_workbooks.py` fail at import there (the new names), so the mutations are the evidence (row 10). |
| 10 | `mutations_r5.py`: 23 one-place mutations | 18 caught. 17 make a test fail; `busy-wait-forever` hangs the tests until the runner's 1,500 s timeout. The fixes doc's list holds: re-raise, status, unknown answers, tables, limits, semaphore, timeout and memory error are all caught. 5 survive: `memerr-read-as-skills`, `blas-threads-off`, `isolated-off` and `r4-4-old-sentence` (R5-3), and none of these changes behaviour on this machine. |
| 11 | Original's SHA-256 before and after, every turn above | Unchanged in every one. |

Turns on shared-formula files. Each file is ≤ 532 KB and passes the check on head.

| File | Check (head) | `formula_cell_counts` alone (head) | Dedupe turn, head | Dedupe turn, base |
|---|---|---|---|---|
| S0: 20,000 rows, one short shared formula (control) | 3.7 s, passes | 2.4 s | not run | not run |
| S1: 50,000 rows, one shared formula of 1,500 references (7,400 characters, which Excel allows) | 7.6 s, passes | 182 s | 286 s, 348 MiB, delivered | not run |
| S2: 20,000 rows, one shared formula of 4,000 references | 3.9 s, passes | 192 s | not run | not run |
| S4: 2,000 shared-formula masters of 4,000 references each (376 KB) | 2.6 s, passes | 64 s, **1,600 MiB** | 79 s, **1,840 MiB**, delivered | 73 s, 1,841 MiB, delivered |
| S5: 4,500 masters of 1,500 references (Excel length, 309 KB) | 3.1 s, passes | 53 s, **1,370 MiB** | not run | not run |

### R5-1 (MEDIUM, [Certain]; pre-existing on base, in the class the ASVS row marks Pass): the formula count runs outside the child, and it is the only read that parses formulas

**Where.** `buddy/inspector.py:1196` (`OriginalMeasurer`) calls `skills/excel/workbook.py` `formula_cell_counts` on the original after every delivered, verified turn. It loads with `data_only=False`. That is the only openpyxl mode that parses `<f>`, and it translates each shared-formula dependent and keeps every master's tokenized `Translator` for the whole sheet (`openpyxl/worksheet/_reader.py` `parse_cell`, `parse_formula`). The child's walk and its pandas reads all use `data_only=True`, so they skip formulas entirely. `_read_as_the_skills_do` leaves the formula count out, and the fixes doc (condition 1) and the ASVS row say it adds "time, not memory".

**Scenario.** See the table. One master of 23,000 characters becomes 0.7 MB of objects and takes 0.19 s, and each dependent takes 2–9 ms to translate. S4 passes the check in 2.6 s, then a "remove the duplicate rows" turn peaks at 1,840 MiB in the server and replies "Done — there were no exact duplicate rows to remove. The copy has 2000 rows … Your file has 2000 formula cells." The reply is true. The problem is memory: in the 1 GiB container the kernel kills the server, not the child, because the child has finished by then and its `oom_score_adj` no longer helps [Likely, not run in a cgroup]. Every user's turn in flight goes with it. S1 uses formulas Excel itself allows, and a turn takes 286 s past a check of 7.6 s. That gets round the 60 s limit the owner set.

**Reachability.** Any signed-in person, with an ordinary request on a crafted file of a few hundred KB. Base behaves the same (S4: 1,841 MiB), but the round 4 fix, the owner's condition 1 and the ASVS row all rest on "what the child read bounds the server's own later reads", and this read is the exception. Rounds 2 and 3 held pre-existing items in this class to the same standard (R2-2, R3-3).

**Fix.** In `_read_as_the_skills_do`, after the two `pd.read_excel` calls, add `formula_cell_counts(input_path)`. It is inside the existing `try`, so `MemoryError` is re-raised and anything else is left to the callers (`prototype_fix_r5.py`). Add a test with S4's shape (many shared-formula masters) that asserts the check refuses it, and one with S1's shape that asserts it is refused within the time limit. That puts the formula count's time inside the child, so a file at the 1,000,000-cell cap took 60.4 s here. Raise `SCAN_SECONDS` (it passed at 120 s, in 66.8 s), or have the owner accept a lower practical cell cap, and record which in the ASVS row. Also correct the "time, not memory" sentence in the ASVS row and the module docstring.

### R5-2 (LOW, [Certain]): a busy slot fails a turn after the work is done, with "before I could change anything"

**Where.** `check_workbook_limits`: `_scan_slot.acquire(timeout=SCAN_WAIT_SECONDS)`, with 10 s against a running check of up to 60 s. The Inspector checks the copy the turn wrote as a new file (`_sheet_names`, `_read_cells`).

**Scenario.** User A's check takes 40 s (any file near the cell cap). User B's dedupe has already run and written its copy, and the Inspector's check of that copy waits 10 s, then fails (T2). Reply, in all three speakers: "I ran into a problem before I could change anything. The deduplicate step: couldn't independently read the workbook's sheet names: the server is busy checking another workbook, so I couldn't check this one; please try again in a moment. I stopped at the deduplicate step and no file was handed back; your original file is unchanged." Every part is true except the opener. The opener is pre-existing for any verification failure (base S17 says the same), but now an ordinary two-user overlap reaches it, with no fault at all. A timeout on the copy (T4) says "the workbook needs more memory or time to open than I allow", which is about the app's own copy.

**Fix.** Make `SCAN_WAIT_SECONDS` at least `SCAN_SECONDS` plus a margin. The running check is bounded by its limit anyway, so a wait then only fails behind a queue. Separately, in the backlog: the opener should not say "before I could change anything" when a step succeeded and its verification failed.

### R5-3 (LOW): guard tests that a reverted fix still passes

- `memerr-read-as-skills` survives. With the `except MemoryError: raise` in `_read_as_the_skills_do` removed, a pandas read that runs out of memory in the child is swallowed by `except Exception`, and the file passes. `test_running_out_of_memory_in_any_reader…` doesn't cover this reader. Add a case that makes `pd.read_excel` raise `MemoryError` in `_scan_in_this_process` and asserts `REFUSE:` and the `TOO_COSTLY` index.
- `r4-4-old-sentence` survives. No test pins the R4-4 wording.
- `blas-threads-off` and `isolated-off` survive. They are harmless on this 4-core machine. The BLAS setting matters on a VM with more cores, where each thread reserves address space under the 512 MiB cap [Possible]. The owner's `measure_workbook_check.py` run in the image covers it.

### R5-4 (LOW, for the owner): a timeout or crash under load is kept as the file's verdict for the process's life

`_scanned` keeps `TOO_COSTLY` and `CHECK_STOPPED`. M9 (1,000,000 cells) took 41.6–46.7 s here, against 60 s. On a busy VM it can pass 60 s once, and from then on that file is refused ("needs more memory or time to open than I allow") until 64 other files have been checked or the server restarts, even when the server is idle. A `CHECK_STOPPED` from an OOM kill caused by another turn's memory is kept the same way. The fixes doc chose this. Fix if the owner wants: keep only the verdicts the child computed from the file (`PASS` and the five limit refusals), not the timeout or the crash.

### R5-5 (LOW): wording and least privilege

- When the child can't be started at all (T5), the reply says "the check of the workbook was stopped before it finished". It never started. Something like "I couldn't run the check of the workbook" would be true for both cases.
- `_scan_in_child` passes the whole server environment to the child, API keys included, although the child only needs `PATH`, the locale and the thread variables. Nothing reaches a reply, and the child runs no code from the file. This is least privilege only.

### Earlier findings

| Finding | Status (my evidence) |
|---|---|
| R4-1 | Closed. P1 and P2 are refused by what they need (4.4 s, 32.5 s). |
| R4-2 | Closed for every file reported (P3–P7b, P10, row 4). One read was left out of the child: R5-1. |
| R4-3 | Closed. P8b and P8c are refused by the byte-weighted cap. Turns at the cap: P11 552 MiB, P11b 475 MiB (row 5). |
| R4-4 | The new sentence is in. No test pins it (R5-3). |
| R4-5, R4-6 | Deferred, in `docs/backlog.md` (now listed twice there, under round 4's own heading and the implementer's). |
| R3-8, R1-5 | Unchanged, the owner's call. R5-1's fix moves R3-8's cost into the child's time limit. |

### Deferred (added to `docs/backlog.md`)

R5-2, R5-3, R5-4 and R5-5.

### Not run

- Live runs with Groq: the change is under `skills/`, and only the R4-4 sentence changes what the pipeline says. No model call is involved.
- Docker, the production image and a real 1 GiB cgroup, so the owner's `scripts/measure_workbook_check.py` was not run. The figures are process peak RSS. The turn probes run under a 4 GiB `RLIMIT_AS`, which the child lowers to 512 MiB for itself.
- S2, S3 and S5 as whole turns, and S1 on base. `formula_cell_counts` alone was measured on head for S0, S1, S2, S4 and S5. S3 (100,000 dependents) wasn't run.
- A real Excel-written file with many shared-formula masters. S1 and S5 use formulas of a length Excel allows, built by hand.
- The prototype's whole suite: only the 467 related tests above.

## Review round 6 (commit 8705ad4)

**Verdict: MERGE.** No HIGH, and no MEDIUM that an ordinary request can reach. R5-1 is closed, and so are R5-2 to R5-5 apart from one starvation case (R6-1). Every place the server parses an uploaded or delivered workbook is either inside the child or measured to be below the child's own run (table below). Five LOW items are deferred. Rules 1 and 2 held in every run.

Scope: the diff `12039a6..8705ad4` (`fix-security-pass-fixes5.md`), the findings round 5 left open, and regression. Probes are in `scripts/review_harness/fix-security-pass/` (README, "Round 6"), and their output is in `out/r6/`. Every run uses `OPENPYXL_LXML=False`, as in the image, and the real child outside pytest. The machine was loaded by parallel runs, so times are ±2×.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3,705 passed, 5 skipped, 0 failed (1,009 s) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | Nothing new (17 vs 18; head has one fewer `E501` in `buddy/hands.py`). `ruff check skills/excel/workbook.py` gives 1 error at both 12039a6 and head. |
| 3 | `harness.py` (532), `rereview3.py` (1,078), `probe_a_pipeline.py` (57, 3 speakers), head vs round 5's head | All 1,667 identical in reply, delivery and final facts. 0 errors, 0 Voice-model calls, 0 originals changed. The 6 `claim-words` flags are the hostile header, quoted, as in every round. |
| 4 | `probe_t_round5.py` (11 cases × 3 speakers): the check answers busy, crash, timeout or can't start | 33 of 33 originals unchanged. Only the T0 controls deliver. T5 now says "I couldn't run the check of the workbook, so I won't open it" (R5-5). The other replies are as in round 5. |
| 5 | `probe_u_round6.py`: whole turns on round 5's formula files and the controls, counting the checking children | S1 is refused at 30.0 s, S4 and S5 at about 10 s ("needs more memory or time"), and M9 (1,000,000 cells) by the cell count in 4 s. Each turn on a refused file runs 2 children, because nothing is kept (R6-1). S0, P0 and M0 are delivered or reported in all 3 speakers. Every number matches my own read: S0 has 20,000 rows, 0 repeats and 20,000 formulas; M0 has 50 rows, 10 repeats, so 40 left; P0 has 3 rows, 1 repeat. |
| 6 | `probe_v_round6.py`: ordinary tables near the new cap | 24,999 × 10 numbers pass in 12.9 s. The same with 5 columns of unshared formulas passes in 14.7 s. 25,001 rows are refused by the cell count. 250,000 short text cells are refused by the 4 MB text cap (R6-5). Whole dedupe turns on the two at-cap files took 135 s and 99 s, and both were delivered correctly (V2: "124995 formula cells", which is 24,999 × 5). |
| 7 | `probe_w_round6.py`: each server read alone vs the child's whole run, on 8 files that pass | The table below. |
| 8 | `probe_x_round6.py`: two users, one too-costly file | R6-1. |
| 9 | Head's new tests against round 5's code (`2ac4f8a`, with `CHECK_NOT_RUN` and `_xml_reader_mode` shimmed in) | 14 fail there, as they should. The 9 that pass pin behaviour round 5 already had (the R4-4 sentence, `MemoryError` in the table reads, the short-formula control). |
| 10 | `mutations_r6.py`: 15 one-place mutations of the fixes, plus 2 re-runs | 16 of 17 killed: the formula count in the child (off, and without the tables), kept verdicts (everything kept, timeout kept, crash kept), the environment (whole, no `LC_*`), the XML-parser mode (unset, fixed), the "couldn't run" sentence, the wait, both limits, round 5's `memerr-read-as-skills` (re-anchored) and `r4-4-old-sentence`. `limit-not-kept` survives (R6-4). |
| 11 | Original's SHA-256 before and after, every turn above | Unchanged in every one. |

### Every place the server process reads an uploaded or delivered workbook

This list comes from my own search of `backend/`, `buddy/`, `core/` and `skills/` for `load_workbook`, `read_excel`, `ExcelFile`, `ZipFile`, `openpyxl`, `iter_rows`, `_get_source`, `open(`, `read_bytes` and the callers of each. `core/` and `buddy/eyes.py` read none.

**Bytes only, no parsing (not the child's business).**
- `backend/uploads.py:114-127` streams the upload to disk and hashes it, capped at `max_upload_bytes`.
- `backend/uploads.py:168-172` (`check_workbook`) reads the zip magic and the central directory (`infolist`) and decompresses nothing.
- `backend/delivery.py:104` (`check_turn_file`) and `:132` (`uploads_unchanged`), `backend/routes.py:429` (download) and `backend/turns.py:437-451` read whole files to hash, send or copy them. The copy of a delivered file at `turns.py:450` is a new path, so it gets its own check: the cache key at `workbook.py:338` includes the path.

**Parsing in the server, before the child, by design.**
- `workbook.py:365-384` (`_scan`) reads the directory and the declared sizes, and `:391-415` (`_declares_entities`) streams every part through expat in 1 MiB chunks. Bounded by 1,000 parts and 64 MiB declared. Nothing is built.
- `workbook.py:285, 687-695` (`_has_stream`, OLE2 only) reads in 1 MiB chunks of a file of at most 2 MiB.

**Parsing reads, each after `check_workbook_limits` on the same path** (`sheet_names` → `:278`; `_read_cells` → `inspector.py:280`; `analyze.py:466`; `inspector.py:689`).

| Read (file:line) | Called from | In the child? | My evidence |
|---|---|---|---|
| `_zip_sheet_names` `workbook.py:634-649` (ElementTree of the content types and workbook part ≤ 16 MiB each, `:654,663`, plus a load at `:641`) | `sheet_names` ← `planner.py:268`, `inspector.py:1005/1035`, `workbook.py:196, 250, 817` | Yes, `:561` and again inside the formula count `:817` | Code. `no-sheet-listing-in-child` was killed in round 5. |
| `formula_cell_counts` `workbook.py:821-842` (two loads at once, one with `data_only=False`) | `inspector.py:1196` (original only) | Yes, `:567` (new) | S1, S4 and S5 refused; `formula-count-off` killed. Caveat: R6-2. |
| `pd.read_excel(dtype=object)` | `dedupe.py:105`, `remove_empty_rows.py:106` (the only sheet), `inspector.py:281` via `_read_cells` ← `:149, 227, 407, 691, 1131` | Yes, `:562` (first sheet; a cleaning skill reads only a single-sheet file whose declared and loaded names match) | Code. Row 7: 95–195 MiB, at most the child's peak. |
| `pd.read_excel()` default and `dtype=object` held at once | `analyze.py:471,474`; `inspector.py:690,691` | Yes, `:562` holds both | Code. Row 7. Caveat: R6-2. |
| `_sheet_problem` `workbook.py:698-719` (a load identical to the walk's, `_layout_problem` `:727` per row, `_has_merged_cells` `:762` expat over the sheet part) | `plain_table_sheet_name` ← `dedupe.py:100`, `remove_empty_rows.py:101`; `plain_table_problem` ← `planner.py:274` (only for one sheet), `inspector.py:1064` (after a step that needed one sheet) | **No.** The docstring says it is (R6-3). | Bounded by measurement, not by being the same read: alone it takes more than the walk on every file, and less than the child's whole run (table below). |
| The walk `_reader_verdict` `workbook.py:578-623` | – | Child only | – |

Not a workbook read, but outside the child: ydata-profiling (`analyze.py:89`) and Splink (`:301`) on the frames, as the ASVS row says.

Each read alone, in its own uncapped process (`probe_w_round6.py`; peak RSS with a 79 MiB baseline, and seconds):

| File | Child, whole run | Walk | `_sheet_problem` | Formula count | Default + object read |
|---|---|---|---|---|---|
| P9 (one 60 M-character unused string) | 424 MiB, 1.6 s | 195, 0.2 | **252**, 0.5 | 309, 0.7 | 253, 0.5 |
| S0 (20,000 short shared formulas) | 89, 2.8 | 83, 0.5 | 87, 0.7 | 85, 1.4 | 89, 1.3 |
| V1 (249,990 numbers) | 103, 11.8 | 84, 1.7 | 91, **3.0** | 85, 4.7 | 104, 4.5 |
| V2 (half unshared formulas, at the cap) | 98, 13.1 | 84, 2.4 | 91, 3.7 | 86, 5.4 | 98, 4.8 |
| M5, P11, P11b, M6 (2,000 × 1,000 characters) | 80–105, ≤ 0.7 | ≤ 84 | ≤ 89 | ≤ 94 | ≤ 89 |

Every server read is at or below the child's whole run, in memory and in time. A turn makes many of these reads in sequence, so its time is several times the child's: S0's dedupe turn took 32 s against a 3.9 s check, and V1's took 135 s against 12.5 s (R6-5, and R3-8 before it).

### R6-1 (LOW, [Certain]; the rest of R5-2): a second user waiting behind a too-costly file is told the server is busy

**Where.** `workbook.py:343` (`_scan_slot.acquire(timeout=SCAN_WAIT_SECONDS)`), with `_NOT_KEPT` at `:307`.

**Scenario.** One turn on a file that is refused as too costly checks it twice in a row: once in the planner (`cleaning_refusal`, from `_resolve_analysis_target`) and once in the skill, 30 s each, with almost no gap between them (`probe_u`: S1, `30.0s:refuse 30.0s:refuse`). `threading.Semaphore` isn't first-come-first-served, so the refusing thread takes the slot back before the waiter wakes. In `probe_x_round6.py`, user B checked an ordinary file 1 s after A started. B got "the server is busy checking another workbook … please try again in a moment" after 45.0 s. With a 0.5 s gap between A's checks, B passed after 29.7 s.

**Reply.** True ("busy, try again"). If B is at the Inspector stage, the reply opens with round 5's "before I could change anything", which is in the backlog.

**Reachability.** It needs a file refused for time or memory, which takes a crafted file or a large one on a slow VM, and a second user at the same moment. This is the cost the backlog already records for the owner's R5-4 decision.

**Fix.** Keep `TOO_COSTLY` for the same key for a short time (for example 120 s), so a turn's second check doesn't run again, or make the slot a first-come-first-served ticket lock.

### R6-2 (LOW, [Possible]): one `try` around the child's four reads, so the first that fails skips the rest

**Where.** `workbook.py:560-571` (`_read_as_the_skills_do`).

**Scenario.** If `sheet_names` refuses, or the default `pd.read_excel` raises (anything but `MemoryError`), the `dtype=object` read and `formula_cell_counts` are skipped, and the child answers PASS. In the server, `formula_cell_counts` (`inspector.py:1196`) runs only after `sheet_names` succeeds (the same code on the same file) and after the cleaning skill's and the Inspector's `dtype=object` reads succeed. It does not need the default read to succeed. So a file whose default read fails while its `dtype=object` read succeeds would get the formula count outside the child's limits. The analysis reads (`analyze.py:471`, `inspector.py:690`) are not gated by `sheet_names`, so when only that refuses in the child, they are bounded by the walk's identical load rather than by a read in the child. I tried big integers, out-of-range dates and out-of-range durations: both reads behaved the same on all of them. I didn't search further, so reachability is not shown.

**Fix.** Give each of the four reads its own `try`, each re-raising `MemoryError`. That is about 6 lines. Cheap enough to do before merge, if the owner wants.

### R6-3 (LOW, [Certain]): the docstring says `_sheet_problem` runs in the child

`workbook.py:32-35` says `_sheet_problem` is "called in the child". The fixes doc and `_read_as_the_skills_do`'s docstring say it streams "over the same load" as the walk. It doesn't run in the child. It is a separate load in the server, and it measures heavier than the walk (P9: 252 vs 195 MiB; V1: 3.0 vs 1.7 s), though below the child's whole run. **Fix:** call `_sheet_problem(input_path, names[0])` in the child after `sheet_names`, or correct the docstring and the ASVS row.

### R6-4 (LOW): `limit-not-kept` survives

Only a row-limit verdict is tested as kept. Adding `TOO_MANY_CELLS` to `_NOT_KEPT` passes every test.

### R6-5 (LOW, for the owner): what the new limits mean for ordinary files

An ordinary 250,000-cell table passes the check in 13–15 s, a little under half the limit. A whole dedupe turn on it took 99–135 s here, against the 180 s turn deadline, with the machine loaded. 250,000 short text cells (about 18 characters each) are refused by the 4 MB text cap, so for text tables the cell cap is lower in practice. Both need confirming in the image with `scripts/measure_workbook_check.py`. Its case builders run here (I built S0, S4 and M9c), but the whole script was not run.

### Earlier findings

| Finding | Status (my evidence) |
|---|---|
| R5-1 | Closed. S1, S4 and S5 are refused, S0 passes (row 5), and `formula-count-off` is killed. The ASVS sentence about the formula count is corrected. Caveat: R6-2. |
| R5-2 | Closed for an ordinary overlap: the wait is 45 s, and `wait-ten` is killed. Behind a too-costly file: R6-1. |
| R5-3 | Closed. `memerr-read-as-skills` (re-anchored) and `r4-4-old-sentence` are killed. |
| R5-4 | Done as the owner decided. `kept-everything`, `timeout-kept` and `crash-kept` are killed. Consequence: R6-1. |
| R5-5 | Closed. The child's environment holds only the listed names (`env-whole` and `env-no-lc` killed). T5 says "I couldn't run the check" (row 4). The XML-parser mode is derived (`lxml-mode-*` killed). |
| R4-5, R4-6, R3-8, R1-5 | Unchanged, deferred or the owner's call. R3-8 is now R6-5's 99–135 s. |

### Deferred (added to `docs/backlog.md`)

R6-1 to R6-5.

### Not run

- Live runs with Groq. The change is under `skills/`, and only the "couldn't run the check" sentence changes what the pipeline says. No model call is involved.
- Docker, the production image and a real 1 GiB cgroup, and `scripts/measure_workbook_check.py` in full. The figures are process peak RSS on a loaded 4-core sandbox.
- The new probes (u, v, w, x) on base. Base has no child, and the shared harnesses compare head with round 5's head, which earlier rounds compared with base.
- A wider search for a file that reaches R6-2. I tried only the few ordinary cell shapes named there.
