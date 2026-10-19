# fix/security-pass: implementation notes

Branch `fix/security-pass`, base `origin/main` (`89625e7`). Security and rule-touching, so the reviewer agent is Opus. Draft PR: <https://github.com/VAMSI21-2003/WORKBUDDY/pull/25>. `feat/pdf-extract` (PR 24) is parked after its round 4 and is neither merged nor continued; two pieces were salvaged from it (item 7) and the rest is described in `docs/backlog.md` (item 8).

No live runs, by instruction: the owner checks on the server.

## What changed, in the owner's order

### 1. RQ-L11: file text and exception text never read as Buddy's words

- `skills/excel/shown_text.py` `shown()`: the only way text from a file gets into a sentence. It removes control, format (bidirectional marks included), surrogate, private-use and unassigned characters, folds white space (line and paragraph separators too) to one space, cuts to 40 characters with an ellipsis, turns a double quote inside into a single quote, and wraps the result in double quotes.
- Every finding that names a column or quotes a value uses it, in the skill (`skills/excel/analyze.py`) and in the Inspector's independent re-derivations (`buddy/inspector.py`): `column "name": 1 missing value (25.0%)`, `column "k" … linked to more than one value in column "n" (e.g. "k" = "v")`, the near-duplicate pair. The missing-value finding no longer uses `str(alert)` from ydata-profiling.
- The Inspector's sheet-list finding no longer names sheets (it said `the output's sheets (Q1) don't match its input's (Q1, Q2)`).
- Exception text: the four `could not independently read … {exc}` sites (and their server paths) are the fixed sentences `COULD_NOT_READ_OUTPUT`, `COULD_NOT_READ_INPUT`, `COULD_NOT_READ_ANALYSED`; the detail was already logged by `logger.exception`. `buddy/hands.py` kept the dispatcher's message (`No skill supporting 'x' can handle task '<session key>-task-0'`) in `ExecutionResult.errors` and a step's `summary`; it now stores `"<capability> couldn't be run because no available skill can do it"` and logs the message. The re-check warning no longer carries it either.
- The web page: no change needed, and a test now says so. Every template is autoescaped, `|safe`/`Markup(` are banned by `tests/web/test_rule1.py`, and the script writes with `textContent`. New end-to-end test: `tests/web/test_file_text_in_page.py` (a header `<img src=x onerror=alert(1)>…` through the real routes).
- Found on the way: `NearDuplicateAnalyzer` built Splink/DuckDB SQL from two column headers. They are now replaced by the fixed names `name_field` and `key_field` before Splink sees them (ASVS 5.3.4, MEDIUM, in `docs/security/asvs-l1.md`).
- Existing tests that pinned the old finding formats were updated (`test_multi_sheet.py`, `test_reply_quality.py`, `test_conversation_repair.py`, `test_inspector_sheets.py`, `test_analyze.py`).

### 2. The residual false "Done —" paths (z31, z18, MEDIUM-u)

Run through the real pipeline on this base (`tests/buddy/test_residual_done_paths.py`):

| Path | Still reachable? | What now |
|---|---|---|
| z31: Brain files "delete the file" under duplicate removal and also flags it unsupported | Yes (needs that Brain misreading) | Fixed in `buddy/outcome.py` `_no_skill_outcome`: Brain's phrase for a done action covers an `unsupported` entry's words only if the phrase itself matches that action's mention pattern. The reply is "Partly done … I read part of your request as something I can't do yet." (generic, not quoted: a quote could hold the part that ran). |
| z18: Brain flags "don't keep the empty rows" as unsupported | Yes (same) | Fixed: a named action that didn't run and has no entry of its own is said as not done whether or not a negation word is present. |
| MEDIUM-u, near-miss action names (`dedupe`, `remove_duplicates`, z27, z36–z39) | No | Outside Brain's closed vocabulary they become `unsupported`/`unclear`; nothing runs, nothing is claimed, no "Partly done". Test proves it for five messages in three speakers. |
| MEDIUM-u, a whole-message `unsupported` flag on a fully carried-out request (z05, z24, z30, z35, z42) | Yes | Not changed. It is the flat "Partly done … can't do yet" (a false "can't do", never a false "Done"). Absorbing it would absorb z31 too, and nothing but a word list tells the two apart. Pinned by a test as the intended direction. |

Cost, for the owner: the fix is two small changes to a mechanism that has had five rounds of word-list patches (`docs/process/review-loop.md`, "Stop patching a mechanism that keeps failing"). Its word lists are still there. A paraphrase that Brain double-lists as unsupported ("get rid of the repeated entries") and a keep-clause that Brain lists as unsupported ("keep the empty rows") used to be dropped silently and now get "Partly done". The simpler mechanism (never drop an `unsupported` entry, delete `_REMOVAL_VERBS`, `_DETERMINERS`, `_LOCATION_PHRASE`, `_FILLER_WORDS`) is described in `docs/backlog.md` as the owner's option; I did not take it without a live measurement of how often the real Brain double-lists.

### 3. Hostile workbooks

`skills/excel/workbook.py` `check_workbook_limits`, called first inside `sheet_names()` (so the planner, both cleaning skills and the Inspector's sheet checks reach it) and explicitly by the analysis skill, the Inspector's cell reads (`_read_cells`) and the analysis verifier. In order: at most 1,000 parts and 64 MiB unpacked (`zipfile` never hands over more of a part than its directory declares); no DOCTYPE or entity declaration in any part, whatever its name (read with the parser); then a walk of the workbook with the table's own reader (`openpyxl.load_workbook(read_only=True, data_only=True)`, `reset_dimensions()` as pandas does it) one row at a time, counting what that reader yields: at most 200,000 rows (padded ones included), 1,000 columns, rows times the widest row at most 1,000,000 cells (summed over sheets), 32,767 characters in a cell and 50 million characters of text in all. A workbook the reader can't read gets no verdict from the check and is refused by the callers' own reads (pandas can't read it either). The verdict is cached per (path, size, mtime, inode). Refusals are fixed sentences naming the limit; they reach the reply through the existing "I can't work on this file:" path. The upload route's declared-size cap is the same 64 MiB, with a message (a test keeps the two in step). No date format is tried on text longer than 32 characters. Cost: 6.7 s for the first walk of 858,000 cells, once per file.

History, for the owner: this check was written three times. The first two counted what the XML says (first `<c>` elements, then rows and the grid with my own expat parser); the round 1 review bypassed the first (R1-1: a far cell beside 1,000 headers, `r="3e5"`, a sheet outside `xl/worksheets/`) and the round 2 review bypassed the second (R2-1: a row whose `r` disagrees with its cells' references, a part the scan can't finish ending the whole scan, a sheet whose part name doesn't end in `.xml`; R2-2: one 10-million-character string that a thousand cells point at). By the review loop's own rule (two rounds, one mechanism: don't patch a third time) the third version replaces the guess with the real thing: it counts what the table's reader yields, so it cannot disagree with that reader about where a sheet is, what a row number means or what a cell holds. The reviewer's prototypes (`prototype_fix.py`, `prototype_fix_r2.py`) patched the guess and were not used.

Tests: `tests/skills/excel/test_hostile_workbooks.py` (73), every one on a real workbook the reader opens (the earlier version used hand-made zips the reader couldn't have opened, which is how a check could pass them). I did not run a hostile file through pandas to measure its memory; the limits come from the sizes in the backlog (the 5 MB file: 61,277 rows, 14 columns, 858,000 cells) and the reviewer's measurement (about 1,100 MiB for 20 million cells).

### 4. ASVS level 1

`docs/security/asvs-l1.md`: each item Pass, Fail or N/A with evidence; guard tests in `tests/backend/test_asvs_l1.py` (54 tests). One Fail rated MEDIUM, fixed (SQL from a header). Four rated LOW, not fixed, each with its reason: JSON duplicate keys (5.1.1), no antivirus scan (12.4.2), no `Content-Disposition` on API JSON (14.4.2), HSTS without `includeSubDomains` (14.4.5; a pinned design decision, the owner's call). Also changed: uvicorn no longer sends `Server: uvicorn` (`server_header=False`).

### 5. Model retries

`buddy/llm.py`: `is_transient_failure(exc)` (a 429, a timeout or a 5xx, read from the exception's type and `status_code`, never its message) and `retry_pause_seconds()` (1 s ± 25%). Brain and Think wait before their second try and make it only after a transient error. Judgement call: a reply that was not JSON or had the wrong shape is still tried once more (after the same pause); the instruction named three kinds of provider error, and this retry is not about the provider failing. The SDKs' own two retries underneath, and the planner's single call, are unchanged. `tests/buddy/test_retry.py` (75 tests); `tests/buddy/test_engine_factory.py`'s fake 429 now carries `status_code`.

### 6. pip-audit in CI

Job `audit` in `.github/workflows/tests.yml` runs `scripts/run_pip_audit.py` over both lock files. `pip-audit-ignores.toml` lists accepted advisories, each with a reason and a `review_by` date; an incomplete entry or one past its date fails the run. One entry: `setuptools 79.0.1` (PYSEC-2026-3447), reviewed until 2026-12-18. The job reads live advisory databases, so it can go red on a day nothing changed. pip-audit itself is installed in the job at a pinned version, outside the lock files. Run here against both locks: no vulnerability beyond that one (it reports twice, once per database).

### 7. Salvaged from `feat/pdf-extract`, no PDF code

`dtype=object` in `ExcelDeduplicateSkill`, `ExcelRemoveEmptyRowsSkill`, the Inspector's reads (`_read_cells`) and the analysis' duplicate checks, so a text "007" stays text and "007" and 7 are different cells everywhere; `tests/buddy/test_text_cells.py` (10 tests, all fail on the old code, including a skill that converts and is caught). The Dockerfile's `pip install --retries 10 --timeout 60` with its comment, and a test.

### 8. Backlog

`docs/backlog.md`: "PDF table extraction parked at review round 4" (R4-H1, R4-H2, R4-M1 to R4-M3, R4-L1, the reviewer's prototype path, the unexplained GitHub failures of run 38598877920: "docker build" and "tests (full suite)"). The residual-path status is under the PR-B re-review 3 bullet.

### After review round 3 (see `fix-security-pass-fixes3.md`)

R1-2 option (b) (`NO_SKILL_UNSURE`, a neutral reply, no lead word) and R3-1 to R3-7: rows counted across sheets, part-size caps for `workbook.xml` and `styles.xml`, a shared-strings cap, `keep_links=False`, a 10 M text cap, bounded near-duplicate input, more tests. The new review is round 4 in the review file.

## Not run, and open

- No live runs (instruction).
- Caddy, Docker and the image were not run here (no runtime), so the `Dockerfile` change and the compose and Caddy rows of the ASVS document are read, not run.
- Full suite on `e64bab1` (before the review): 3,593 passed, 5 skipped (`tests/buddy tests/skills tests/core`: 2,347 passed; `tests/backend tests/web tests/deploy tests/e2e tests/idp`: 1,246 passed, 5 skipped). An earlier run on `407ab0b` had found three failures in `tests/buddy/test_engine_factory.py` caused by the item 2 change (a quoted flag that held the done part); fixed in `e64bab1`, which also makes the flag generic instead of quoted for z31.
- The reviewer's result is in the report.
- Lint: `scripts/check_ruff_baseline.py` reports nothing beyond the baseline (17 known here; CI's ruff version may differ).

## For the owner (nothing under "Decided behaviour" in `CLAUDE.md` was changed)

- Item 2's trade (above): the flat direction widens "Partly done" for a Brain that double-lists. Say if you want the simpler mechanism.
- HSTS `includeSubDomains` (ASVS 14.4.5) is yours to decide.
- The retry of a malformed model reply (item 5) is kept; say if "only 429, timeout or 5xx" should be literal for that too.
