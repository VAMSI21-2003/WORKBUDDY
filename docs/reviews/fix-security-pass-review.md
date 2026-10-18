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
