# feat/pdf-extract: independent review

## Review round 1 (commit e841088)

**Verdict: DON'T MERGE.** Three HIGH (a delivered table that isn't the PDF's while the reply says every cell is the PDF's text, in two ways; an exception's text in a reply) and two ordinary-path MEDIUMs.

Base `origin/main` `89625e7`. Probes: `scripts/review_harness/feat-pdf-extract/` (README there). Real `build_engine`, real skills, real sandboxed worker and verifiers; only the LLM faked.

### What was run

| What | Result |
|---|---|
| `python -m pytest -q` (head) | 3552 passed, 7 skipped, 917 s |
| `ruff check buddy tests`, by file and rule, head vs base | identical (13 file/rule groups each); `scripts/check_ruff_baseline.py`: nothing beyond the baseline |
| New tests on base code (`tests/skills/pdf`, `test_pdf_extract.py`, `test_pdf_turns.py` copied onto base) | all 6 files fail (the modules don't exist) |
| `probe_a_worker.py`: the skill's reading vs the verifier's reading on 26 PDFs (reportlab, hand-drawn, LibreOffice exports) | 13 agree with what was drawn; 8 disagree (refused: `/Rotate`, landscape, fake bold, overprint, Hebrew/Arabic, overflow into a ruled cell); **5 agree with each other but not with the PDF** (H1, H2, M2) |
| `probe_b_pipeline.py`: 21 scenarios x template/friend/focus, with follow-ups (65 turns) | originals' SHA-256 unchanged in all; 0 Voice-model calls; speakers agree on facts; wrong tables delivered under the "text layer" sentence in p02, p03, p04, p21; exception text in p13 |
| `probe_c_sandbox.py`: code inside the real lockdown | sockets refused; files of other users readable, writable, chmod-able; `io_uring_setup` not filtered; x32 bit not checked; fork allowed (M4) |
| `probe_d_dtype.py`: decision 8, head vs base | base turned text `007`/`2.50` into 7/2.5 (fixed on head); head's dedupe and analysis now disagree (M3) |
| `probe_e_api.py`: odd uploads, worker places all taken | busy upload gets "isn't a valid ... PDF" (L1); turn while busy is honest |
| Shared `harness.py`, 532 turns, head vs base | 518 identical, 14 differ only by the capability line naming PDFs; delivered files identical; originals unchanged |
| `mutations.py`: 24 one-place mutations | 20 killed; survived: m04 verifier ignores `plain`, m13 Inspector reads without `dtype=object`, m14 empty-rows skill reads without it, m23 worker cap 3 (L8) |

### Findings

**H1 HIGH: text that runs past its cell's border is cut off, or moved into the next cell, and the reply says every cell is the PDF's text.** [Certain]
- Where: `skills/pdf/_worker/main.py` `extract` (pdfplumber assigns characters to cells by position) and `verify` (`get_text_bounded` on each cell's box). Both readings clip to the cell's box the same way, so `PdfTableVerifier` passes.
- Scenario (LibreOffice Calc export, the ordinary way a spreadsheet becomes a PDF; rendered, the text is visible in full past the border): Comment `Paid in full on delivery` is delivered as `Paid in full`; `café – naïve` as `café – naïv`; a long Note whose right-hand neighbour is empty is delivered as Note `a fairly lon`, Qty `g note that` (the empty Qty cell now holds part of the note).
- Reply (focus, p02/p03): "Done — extracted the table: 3 rows and 2 columns. The table from report.pdf is in a new spreadsheet, ready for you. ... Every cell holds the text read from the PDF's text layer; numbers and dates are not converted."
- Reachable: yes, any table with a cell wider than its column.
- Fix: in `verify`, refuse with a new reason (for example `text_overflows`: "some text in the table runs past its cell's border, so a cell can't be read whole") when a character on the page lies on the same line as text inside a cell (baseline within the tolerance, gap under about one em) but outside that cell, or straddles one of the grid's rules (PDFium `FPDFText_GetCharBox` over the page's characters). Add tests on the three LibreOffice files from `probe_a_worker.py` (`libreoffice_calc_last_col_overflow`, `libreoffice_calc_unicode`, `libreoffice_calc_mid_col_overflow_next_empty`): each must be refused, and the FRUIT table must still be delivered.

**H2 HIGH: NFKC changes the values in cells, and the reply says nothing was converted.** [Certain]
- Where: `skills/pdf/_worker/textnorm.py` `normalize_cell` (`unicodedata.normalize("NFKC", ...)`). The skill and the verifier share this code, so they agree on the changed value.
- Scenario: cells `2½`, `12 m²`, `10³ W`, `3³`, `¼`, `Acme™`, `①` are delivered as `21⁄2`, `12 m2`, `103 W`, `33`, `1⁄4`, `AcmeTM`, `1`. Same result from a LibreOffice export (`2½ in`, `12 m²`).
- Reply (p04, all speakers): "Every cell holds the text read from the PDF's text layer; numbers and dates are not converted." `2½` read as `21⁄2` and `10³` read as `103` are different numbers.
- Reachable: yes, fractions, units and superscripts are common in tables.
- Fix: normalise with NFC, not NFKC. If ligatures must still become letters, map only U+FB00–U+FB06 explicitly. Keep dropping Cf/Cs/Co/Cn and folding whitespace. `test_normalize_cell[ﬁnal-final]` is the only test that pins the current behaviour (mutation m19). Add a test that `2½`, `m²`, `10³` and `™` come back unchanged through the whole pipeline.

**H3 HIGH: an exception's text reaches the reply.** [Certain] with the fake planner; [Likely] with the real one, whose prompt plans `spreadsheet.analyze` for a report.
- Where: `buddy/hands.py` (`errors.append(str(exc))` on a `DispatchError`), rendered by `buddy/voice.py` `_stopped_message`, which falls back to `execution_result.errors` when the only failing step is the analysis (analysis verdicts are skipped).
- Scenario (p13): on a PDF, "extract the table and give me a report" (Brain: `extract_pdf_table` + `report_on_file`; plan: analyze + extract). The analysis can't be dispatched on a PDF, so the chain doesn't start.
- Reply (friend): "I ran into a problem before I could change anything. No skill supporting 'analyze' can handle task 'e4435db8dada24eb2908ab0f5a932819-task-0'. I didn't produce the report you asked for, since no file was handed back. I stopped at the analyze step, so extract table never ran ..." The quoted id is the session key.
- Reachable: on base, uploads were `.xlsx` only, so the analysis could always be dispatched. Here, any report request on a PDF upload that the planner plans as an analysis reaches it.
- Fix: in Hands, record fixed text, not `str(exc)` (for example `f"{request.capability} couldn't be run because no available skill can do it"`, the wording of the step's `failure_reason`), and never render `execution_result.errors` verbatim. Better still, don't plan `spreadsheet.analyze` on a non-spreadsheet target, and give the report the existing `report_unavailable_sentence`. Test: the p13 turn must contain neither "No skill supporting" nor "-task-".

**M1 MEDIUM: the two row counts on the design's own headline path don't add up.** [Certain]
- Where: `buddy/outcome.py` `_extraction_sentence` counts the header row; the cleaning replies count data rows only.
- Scenario (p01, every speaker): "Done — extracted the table: 5 rows and 3 columns." Then "now remove the duplicate rows": "removed 1 exact duplicate row. The copy has 3 rows". 5 − 1 = 4, so the person is led to think a row was lost.
- Reachable: the ordinary flow the design advertises.
- Fix: state the size the way the follow-up counts it, for example "Extracted the table: 3 columns, a first row and 4 rows below it." Test the extract-then-dedupe transcript for consistent counts.

**M2 MEDIUM: a table ruled only around its rows becomes one column of joined text.** [Certain]
- Where: `geometry.find_grids` and pdfplumber both accept a frame with a rule under each row and no rules between columns as a plain one-column grid.
- Scenario (p21, reportlab `BOX` + `LINEBELOW`, a common report style): the PDF shows Name/Qty/Price; the delivered sheet has one column: `Name Qty Price`, `Apple 10 2.50`, ... The follow-up dedupe then runs on the joined text.
- Reply: "Done — extracted the table: 5 rows and 1 column. ... Every cell holds the text read from the PDF's text layer". Nothing in it is false, but it presents the result as the PDF's table.
- Fix: refuse a grid with fewer than 2 columns (in `verify`; also in `extract` for a clear refusal). Better: also refuse when text inside one cell has a horizontal gap wider than about 2 em (unruled columns). Test with the p21 file.

**M3 MEDIUM: since decision 8, the analysis and the dedupe disagree about what an exact duplicate is.** [Certain]; uncommon file needed.
- Where: `skills/excel/analyze.py:415` and `buddy/inspector.py` `SpreadsheetAnalysisVerifier` (about line 866) still call `pd.read_excel(path)`, which converts types. The cleaning skills, their verifiers and `ResidualMeasurer` now read with `dtype=object`. `analyze.py`'s docstring at line 92 promises the same definition.
- Scenario (probe_d d1/d2): rows differing only by text `007` vs `7`, or text `7` vs number `7`; "remove the duplicate rows and then give me a report".
- Reply: "Done — there were no exact duplicate rows to remove. The copy has 3 rows, with 0 exact duplicate rows remaining. ... Report on the copy: 1 row repeats an earlier row exactly." One of those two statements is false. A PDF-extracted sheet (all text) with `2.50` vs `2.5` reaches it too.
- Fix: compute the exact and loose duplicate findings, and their independent checks, on `_read_cells(path)` (`dtype=object`). Keep the converting read for outliers and dates. Test d1/d2.

**M4 MEDIUM (security, not reachable by an ordinary request): the sandbox has no filesystem boundary, and two network paths are left open.** [Certain] for what code in the worker can do; [Possible] that a parser can be taken over (PDFium, a C++ parser, runs `probe` on every PDF upload).
- Where: `skills/pdf/_worker/lockdown.py`.
- From inside the real lockdown (`probe_c_sandbox.py`), code can: read another user's upload; write files outside its folder; `chmod` a read-only (0444) upload to 0644 (rule 2 relies on that mode); fork. `io_uring_setup` (425) is not filtered, and io_uring can open sockets on kernels 5.19 and later. On x86-64 the filter doesn't refuse x32-ABI numbers (`nr | 0x40000000`); this kernel has x32 off (ENOSYS), but others may not. The design's "Nothing to read" holds only for the code as written, not for a parser that has been taken over.
- Fix: add `io_uring_setup`/`io_uring_enter`/`io_uring_register` (425/426/427, both architectures) to the refused list. On x86-64, kill when `nr >= 0x40000000`. Import the parser modules first, then apply a Landlock ruleset (no privilege needed) that allows only reads under `sys.prefix` and the site-packages directories. Decide with the owner whether to fail closed where Landlock is missing. Extend `selftest` and the release check to cover each of these.

### Design deviations

1. `_worker/main.py` says `verify` shares "no code with `extract`". Both use `textnorm`, and `extract` uses `geometry.cells_are_plain_grid`. The shared normaliser is why H2 passes the check.
2. The design names socket, socketpair, execve, execveat and ptrace. On x86-64 the filter also lists fork and vfork, which blocks nothing in practice because `clone` stays open; arm64 doesn't list them.
3. "Nothing to read" (sandbox section) holds only for the code as written (M4).
4. `docs/backlog.md` §7 says every limitation "is a refusal today ..., never a wrong table". H1, H2 and M2 deliver wrong tables. It also says `/Rotate` is "not probed": probed here, it is refused (L2).

### Deferred (LOW; added to `docs/backlog.md`)

- **L1:** an upload refused because the workers are busy or slow gets 422 "That file is empty or isn't a valid .xlsx workbook or PDF." (`backend/uploads.py` `check_pdf`). Busy and timeout should get their own retry message.
- **L2:** a landscape (`/Rotate`) table is refused with internal wording: "the spreadsheet is 3 rows by 2 columns, larger than the PDF's table as a second reading found it (2 by 3)".
- **L3:** invisible text (render mode 3) and text under a white box are delivered. The reply says "text layer", but the person sees an empty cell in the PDF.
- **L4:** "The copy has the values only; formatting, column widths and other sheet settings aren't carried over" is said of a table taken from a PDF.
- **L5:** a text cell `=1+1` from a PDF makes the follow-up dedupe write a formula. The Inspector stops it, but the reply's reason is internal ("1 output row doesn't appear in its input").
- **L6:** dedupe asked on a PDF: "couldn't be run because no available skill can do it". The reply could say to extract the table first.
- **L7:** Hebrew and Arabic tables are always refused (PDFium gives visual order).
- **L8:** surviving mutations: m13 and m14 (no test shows the Inspector catching a cleaning skill that converts `007` to 7, design decision 8's claim), m04, m23 (the worker cap of 2 is untested).

### Not run

- Live runs with Groq: not this reviewer's step.
- Inside the production image (no container runtime here): `python -m skills.pdf` and the seccomp filter under Docker's profile.
- arm64: the filter's arm64 numbers were not exercised.
- PDFs from Word, Excel or browsers: only reportlab, hand-written bytes and LibreOffice were available.

## Review round 2 (commit 37e65eb)

**Verdict: DON'T MERGE.** H1 is only partly fixed: a sentence that runs into the empty cell beside it is still delivered split across two cells under "Every cell holds the text read from the PDF's text layer" (2 of 12 LibreOffice exports). Two MEDIUMs an ordinary request reaches.

Base `origin/main` `89625e7` (still the base after `git fetch`). Scope: `e841088..37e65eb`, round 1's findings, regression against base. Probes: `scripts/review_harness/feat-pdf-extract/` (README, "Round 2"); real `build_engine`, skills, sandboxed worker and verifiers, only the LLM faked.

### What was run

| What | Result |
|---|---|
| `python -m pytest -q` (head) | 3579 passed, 7 skipped, 845 s |
| `ruff check buddy tests`, by file and rule, head vs base | identical (13 groups); `check_ruff_baseline.py`: nothing beyond the baseline |
| New tests on `e841088` (fix tests copied onto it) | 19 fail (every finding's test, the new `normalize_cell` rows, the selftest test). The x32 test passes on `e841088` (the mode doesn't exist, so the worker exits non-zero = "crashed"); the two Landlock tests skip there |
| Round-1 `probe_a_worker.py` (24 PDFs) | H2 cases now come through unchanged; one-column frame refused; LibreOffice overflow cases of round 1 refused. Still agreed-but-not-drawn: invisible/covered text (L3, deferred) |
| Round-1 `probe_b_pipeline.py`, 21 scenarios x 3 speakers (75 turns) | originals' SHA-256 unchanged in all 75; 0 Voice-model calls; p01 counts add up (M1); p04 cells `2½`, `12 m²`, `¼`, `3³` (H2); p13 has no exception text (H3); p21 refused (M2) |
| `r2_overflow_sweep.py`: hand-drawn grid, sentence into the empty next cell, rule swept over 81 positions | **22 of 81 delivered split** (e.g. `["Paid i", "n full on delivery"]`, `["Paid in full", "on delivery"]`) |
| `r2_lo_overflow.py`: 12 LibreOffice Calc exports, note into the empty next cell | **2 of 12 delivered split** (`Ships next` / `week`, `Left at the` / `front desk`); 10 refused |
| `r2_pipeline.py` q01-q06 x 3 speakers (27 turns) | originals unchanged in all; q01/q02 split cells delivered (R2-H1); q03 joined columns delivered (R2-M1); q04 nothing done (R2-M2); q05/q06 counts right |
| `probe_d_dtype.py` | d1/d2: dedupe and "Report on the copy" now agree (M3 fixed) |
| `r2_sandbox.py` (full lockdown + Landlock, as `main.py`) | another upload, `/tmp`, the server's `/proc/<pid>/environ`: refused; io_uring, `clone` of a process: EPERM; x32: killed. Still allowed: `kill(getppid())`, `pidfd_open`, `chmod` of a known path. `--no-landlock`: other upload read, server environ read (8891 bytes), **server's socket stolen with `pidfd_getfd` and written to** (R2-M3) |
| Shared `harness.py`, 532 turns, head vs base | 518 identical, 14 differ only by the capability line; delivered files identical; originals unchanged |
| `r2_mutations.py`: 23 mutations of the round-1 fixes | 16 killed. Survived: r04 run-gap check off, r08 step summary = `str(exc)`, r10 analysis skill reads converted, r12 loose-duplicate verifier reads converted, r18 Landlock off (tests skip), r19 `process_vm_*` allowed, r22 (= m14) empty-rows skill without `dtype=object` |
| Prototype of the R2-H1/R2-M1 fix in a throwaway worktree | sweep 0 of 81 wrong, LibreOffice 0 of 12 wrong, q03 refused; every ordinary probe_a table still delivered; the 269 PDF tests pass |

### Round-1 findings

| | Status (own evidence) |
|---|---|
| H1 | **Partly fixed** [Certain]: last-column overflow and most mid-column cases refused; the rest is R2-H1 |
| H2 | Fixed [Certain] (p04, probe_a; mutations r05, r06 killed) |
| H3 | Fixed [Certain] (p13 in 3 speakers; r07 killed). The reply is still poor: R2-M2 |
| M1 | Fixed [Certain] (p01: "4 rows below it", then "removed 1 ... The copy has 3 rows"; r09 killed) |
| M2 | Fixed for one column [Certain] (p21; r13, r14 killed). Partly ruled tables: R2-M1 |
| M3 | Fixed [Certain] (probe_d d1/d2; r11, r21 killed); r10, r12 survive (deferred) |
| M4 | Fixed for what was listed [Certain] (r15-r17, r20 killed); new gaps: R2-M3 |
| L8 | m13, m23 now killed; m14 still survives (r22); m04 left as argued |

### Findings

**R2-H1 HIGH: text that runs into the empty cell beside it is still delivered split across the two cells, and the reply says every cell is the PDF's text.** [Certain]
- Where: `skills/pdf/_worker/main.py` `_text_overflows`. The crossing check misses a rule that falls in a word space or between two glyph boxes (`get_charbox` is the tight glyph box; `_BOX_SLACK` 0.5 pt). The run-gap check that should catch those groups characters by `round(centre_y / 2.0)`, and glyphs of one line have different centres (`n` vs `l` vs `g`), so neighbours land in different buckets and are never compared; a word space (~0.3 em) is also above `_RUN_GAP * min(h)` (~0.15 em). Mutation r04 (run-gap check removed) survives every test.
- Scenario (LibreOffice Calc export, a note longer than its column, Qty empty): Note `Ships next week` is delivered as Note `Ships next`, Qty `week`; `Left at the front desk` as `Left at the` / `front desk`. The follow-up dedupe then works on the split rows.
- Reply (focus, q01): "Done — extracted the table: 3 columns, a first row and 2 rows below it. The table from report.pdf is in a new spreadsheet, ready for you. ... Every cell holds the text read from the PDF's text layer; numbers and dates are not converted."
- Reachable: 2 of 12 ordinary LibreOffice exports with an overflowing note; 22 of 81 rule positions in the sweep.
- Fix (prototyped, numbers above): in `_text_overflows`, refuse when one PDF text object holds characters in two different cells (`raw.FPDFText_GetTextObject(textpage.raw, index)`, compared as `ctypes.cast(..., c_void_p).value`). Remove or rebuild the run-gap check on the baseline (`FPDFText_GetCharOrigin` y), not the box centre. Tests: the two LibreOffice notes above, and a hand-drawn grid with the rule in a word space and between two glyphs (`r2_overflow_sweep.py` first-column widths 34 and 29: `["Paid in", "full on delivery"]`, `["Paid i", "n full on delivery"]`): each refused; FRUIT still delivered.

**R2-M1 MEDIUM: a table whose columns are only partly ruled is delivered with two columns joined in one cell.** [Certain]
- Where: `geometry.find_grids` / pdfplumber accept the ruled grid; nothing looks for unruled columns inside a cell. The design's backlog line now names it ("read as ruled").
- Scenario (q03, reportlab `BOX` + `LINEBELOW` + `LINEAFTER` on the first column only, a common statement layout): PDF shows Name | Qty | Price; delivered `Name`, `Qty Price`; `Apple`, `10 2.50`. The follow-up dedupe runs on the joined text.
- Reply (all speakers): "Done — extracted the table: 2 columns, a first row and 3 rows below it. ... Every cell holds the text read from the PDF's text layer". Not false, but presents a table the PDF doesn't show.
- Reachable: ordinary request; a layout less common than a full grid.
- Fix (prototyped): in `verify`, group each cell's characters by baseline and refuse when two neighbouring characters are more than 1.5 x the font size apart (`FPDFText_GetFontSize`), with its own reason (for example `columns_not_ruled`: "some of the table's columns aren't ruled, and only tables with a border around every cell are supported for now"). `A  B` / `x   y` (double spaces) still delivered in the prototype. Test with q03's file.

**R2-M2 MEDIUM: "extract the table and give me a report" on a PDF does nothing, and the reply's reasons are wrong.** [Certain] with the fake planner; [Likely] with the real one (`report_on_file` maps to `spreadsheet.analyze`, planned on the PDF).
- Where: `buddy/hands.py` (the analysis step on a PDF can't be dispatched, so the chain doesn't start); `buddy/voice.py` renders `errors` as a sentence.
- Reply (q04/p13, every speaker): "I ran into a problem before I could change anything. analyze couldn't be run because no available skill can do it. I didn't produce the report you asked for, since no file was handed back. I stopped at the analyze step, so extract table never ran ..." A skill that analyses exists (it reads spreadsheets); the report is missing because the analysis can't read a PDF, not because no file was handed back; the extraction could have run. The sentence also starts with the lowercase internal name "analyze".
- Fix: don't plan `spreadsheet.analyze` on a PDF target. Run the extraction, and either analyse the extracted sheet (the decided "re-run on the final file") or say the report can't be made from a PDF (`report_unavailable_sentence`) while still handing the table back. *For the owner:* this is the extraction counterpart of the confirmed "remove the duplicate rows and then give me a report"; confirm which of the two you want. Test: q04 delivers the table and names the report's fate in words a person would use.

**R2-M3 MEDIUM (security, not reachable by an ordinary request): the worker can still reach the server process.** [Certain] for what code in the worker can do; [Possible] that a parser is taken over.
- Where: `skills/pdf/_worker/lockdown.py`.
- With Landlock (this kernel, ABI 7): `kill(getppid(), …)` succeeds (a taken-over parser can kill the server) and `pidfd_open` on it succeeds. Landlock's ptrace rule is what blocks `pidfd_getfd`.
- Without Landlock (the case the design and `python -m skills.pdf` let through with a warning): `pidfd_getfd` takes the server's connected socket and writes to it (the peer received `EXFIL-FROM-WORKER`), takes its listening socket, and reads the server's `/proc/<pid>/environ` (the API keys). The warning says "It still has no network", which is then untrue.
- Fix: refuse `pidfd_open`, `pidfd_getfd`, `pidfd_send_signal` (434, 438, 424 on both architectures) in the filter; allow `kill`/`tkill`/`tgkill` only on the worker's own pid (or add Landlock's signal scope where ABI ≥ 6). Fix the warning's wording, and settle with the owner whether a missing Landlock should refuse PDFs. Tests: from inside the worker, `pidfd_getfd` on a parent socket and `kill(getppid(), 0)` are refused, with and without `confine_filesystem`.

### Design deviations

1. Decision 4 says no character "runs past the border of the cell it starts in" once verified. R2-H1 shows it does.
2. Sandbox section: "Where Landlock is missing the worker still parses (everything above holds)". The seccomp list holds, but "no network" doesn't (R2-M3).
3. "What it does not do" now says a partly ruled table is "read as ruled": a known wrong table, at odds with backlog §7's "the reply says only what two parsers agreed on and the character check allowed" (R2-M1).

### Deferred (LOW; added to `docs/backlog.md`)

- **R2-L1 (tests):** mutations r18 (no Landlock: both filesystem tests skip instead of failing, so a broken `confine_filesystem` or a CI kernel without it passes silently), r19 (`process_vm_readv/writev` allowed), r10 and r12 (the analysis skill and the loose-duplicate verifier read converted), r22 (= m14), r08 (step summary = `str(exc)`; not shown to the person today) survive. The x32 test passes on `e841088` because the mode is missing there.
- **R2-L2:** under Landlock the worker can still `chmod` a file whose path it knows (0444 to 0666; bytes unchanged). It is given no path and can't list folders.
- **R2-L3:** `tests/skills/pdf/test_hostile_pdfs.py::test_active_content_in_a_pdf_is_never_followed` failed once while the machine was fully loaded (suite, harness and mutations at once); it passes alone and in the full suite. [Possible] timing flake.
- Round 1's L1-L7 are still open and still in the backlog (L4's values-only sentence and L5's internal reason seen again in p01 and p05).

### Not run

- Live runs with Groq: not this reviewer's step.
- The production image (no container runtime here): whether Docker's default seccomp profile lets `landlock_*` through, so whether production gets Landlock or the R2-M3 fallback. Run `python -m skills.pdf` in the container.
- arm64: the filter's arm64 numbers, and `clone`'s flags argument there.
- PDFs from Word, Excel or browsers (only reportlab, hand-written and LibreOffice). Whether a generator that draws a whole row in one text object exists among them: with the prototype fix it would be refused, not mis-delivered.

## Review round 3 (commit f82951a)

**Verdict: DON'T MERGE.** R2-H1 is only partly fixed: a sentence that the PDF draws as several text objects (a LibreOffice Calc note with one bold word, or any generator that draws word by word) is still delivered split across two cells (R3-H1). An ordinary cell such as "Mon - Fri" now gets a whole table refused with a false reason (R3-M1). The worker can still kill the server (R3-M2, not reachable by an ordinary request).

Base `origin/main` `89625e7` (unchanged after `git fetch`). Scope: `37e65eb..f82951a`, round 2's findings, regression against base. Probes: `scripts/review_harness/feat-pdf-extract/` (README, "Round 3"). Real `build_engine`, skills, sandboxed worker and verifiers; only the LLM faked.

### What was run

| What | Result |
|---|---|
| `python -m pytest -q` (head) | 3590 passed, 7 skipped, 782 s |
| `ruff check buddy tests`, by file and rule, head vs base | identical (13 groups); `check_ruff_baseline.py`: nothing beyond the baseline |
| New and changed tests (`test_pdf_extract_fixes2.py`, `fixes1`, `test_sandbox.py`) copied onto `37e65eb` | 11 fail (every R2 finding's test, the fail-closed and selftest tests). 3 pass there: `test_one_string_drawn_across_two_cells...` (it asserts "refused or whole", R3-L2), `test_words_with_ordinary_spaces...`, `test_the_real_cleaning_skills_keep_a_text_id_as_text` (a guard, killed s21) |
| `python -m skills.pdf` (release check) | every check true, "The PDF sandbox holds." (exit 0) |
| Round-2 `r2_lo_overflow.py`, `r2_overflow_sweep.py` | 0 of 12 LibreOffice notes split; 1 of 161 sweep widths delivered, with the sentence whole in its own cell (correct) |
| Round-2 `r2_pipeline.py` q01-q06 x 3 speakers (27 turns) | q01/q02 refused (`text_overflows`); q03 refused (`columns_not_ruled`); q04 table + "I couldn't produce the report you asked for: I didn't run the analysis."; q05/q06 counts right |
| Round-1 `probe_a_worker.py` (24 PDFs) | as round 2, except **`double_spaces` (`A  B`, `x   y`) is now refused as `columns_not_ruled`** (R3-M1) |
| `r3_layout.py`: overflow into the empty next cell, rule swept, text drawn one object per word | **delivered split: 28 of 181 widths** (10 pt, words with their trailing space), **15 of 100** (12 pt), **15 of 100** (12 pt, column-major order). Per glyph / per word at 10 pt without spaces: the verifier passes the split reading (38 widths) and only pdfplumber's different spacing stops delivery |
| `r3_layout.py punct`: one cell with punctuation in a ruled grid | **6 of 16 refused** as `columns_not_ruled` (`Mon - Fri`, `Mon – Fri`, `Mon — Fri`, `Smith - one`, `low - high`, `x ~ y`); LibreOffice Writer: `Mon - Fri`, `Smith - one` refused the same way |
| `r3_lo_rich.py`: 70 LibreOffice Calc exports, overflowing note with one bold or red word | **1 delivered split** (`Needs manager` / `approval`), 14 right, 55 refused |
| `r3_pipeline.py` s01-s10 x 3 speakers (42 turns) | originals' SHA-256 unchanged in all 69 turns of this round; 0 Voice-model calls; s07 false refusal (R3-M1); s08/s09 split cells delivered (R3-H1); s03/s04 reports on the extracted copy work; s06 (PDF then workbook) analysed and cleaned the workbook as before |
| `r3_sandbox.py` (full lockdown + Landlock) | `kill(0)`, `kill(-1)`, `kill(ppid)`, `pidfd_*`: refused. **`prlimit64` on the parent: allowed; with RLIMIT_CPU 1 s the parent was killed (exit -9)**; RLIMIT_NOFILE set to 5/5 on the parent; `setpriority`, `sched_setaffinity`, `ioprio_set` on the parent succeed (R3-M2). `chmod` of a known path still allowed (R2-L2) |
| Shared `harness.py`, 532 turns | head vs round-2 head: 532 identical; head vs base: only the capability line naming PDFs (14 turns); delivered files identical |
| `r3_mutations.py`: 21 one-place mutations of the round-2 fixes | 14 killed. Survived: s02 run-gap check removed, s07 dropped analysis keeps its dependants' links, s08 `_is_analyzable` ignores the active file, s10 `tgkill` unchecked, s11 `tkill`, s12 `rt_sigqueueinfo`, s13 `pidfd_send_signal` allowed, s16 release check ignores `filesystem_confined` |
| Prototype of the R3-H1/R3-M1 fix (`r3_prototype_fix.diff`, throwaway worktree) | every sweep above 0 delivered wrong; all 16 punctuation cells and `double_spaces` delivered right; q03 still refused; the 367 PDF tests pass. Cost: 2 of 2 LibreOffice Writer tables (tight auto-fit columns) refused instead of delivered |

### Round-2 findings

| | Status (own evidence) |
|---|---|
| R2-H1 | **Partly fixed** [Certain]: a sentence drawn as one text object is never delivered split (r2_lo_overflow 0/12, sweep, s01 mutation killed). Several objects: R3-H1 |
| R2-M1 | Fixed [Certain] (q03 refused in 3 speakers; s03, s04 killed). The new check over-refuses: R3-M1 |
| R2-M2 | Fixed as written [Certain] (q04, s02, s10: table handed back, report said not produced; follow-up reports on the copy work). Owner's confirmation still needed (below) |
| R2-M3 | **Partly fixed** [Certain]: signals, pidfds and socket theft refused; no PDF read without Landlock (s15 killed). `prlimit64` still kills the server: R3-M2 |
| R2-L1 | r18, r19, r10, r22 now killed (s18-s21); r04's check survives again as s02; r12 not guarded (as stated) |
| R2-L2, R2-L3 | open (R2-L2 seen again) |

### Findings

**R3-H1 HIGH: a sentence the PDF draws as several text objects is still delivered split across two cells, and the reply says every cell is the PDF's text.** [Certain]
- Where: `skills/pdf/_worker/main.py` `_text_layout_problem`. The object rule covers one object only. The cross-cell run check compares only characters next to each other in content-stream order (not reading order, as the docstring says), and against `0.5 x` the smaller tight glyph height (about 0.26 em for lower case), while a word space between tight glyph boxes measures 0.28-0.4 em. A word space that straddles the rule passes. Mutation s02 (the run check removed) survives every test.
- Scenario (s09, LibreOffice Calc export, the round-2 setting with one word bold, so LibreOffice draws three text objects): Note `Needs **manager** approval`, Qty empty. Delivered: Note `Needs manager`, Qty `approval`. PDFium's own text reading of the page has the note as one line, `Needs manager approval`; the source cell held one note. Rendered, the rule falls in the word space (`out/r3/lo_rich.png`).
- Reply (focus): "Done — extracted the table: 3 columns, a first row and 2 rows below it. The table from report.pdf is in a new spreadsheet, ready for you. ... Every cell holds the text read from the PDF's text layer; numbers and dates are not converted." s08 (one object per word): `Paid in full` / `on delivery`, then "now remove the duplicate rows" works on the split rows.
- Reachable: a rich-text note that overflows into an empty cell (1 of 70 LibreOffice exports); any generator that draws word by word (15-28 of 100-181 rule positions here). Word, Excel and browser PDFs not available to test.
- Fix (prototyped): in `_text_layout_problem`, record each character's advance box (`FPDFText_GetLooseCharBox`), font size (`FPDFText_GetFontSize`) and baseline (`FPDFText_GetCharOrigin`); per row, group characters into lines by baseline, sort each line by x, and refuse (`text_overflows`) when two neighbours in different cells are less than 0.5 em apart. Keep the object rule, the crossing rule and the beside-the-table rule. Measured gaps: word spaces across a rule 0.20-0.28 em; LibreOffice Writer's tight auto-fit cells 0.30-0.35 em, which this refuses. No threshold separates the two safely; if the owner wants those Writer tables, it needs another signal, not a smaller number. Tests: the s09 file (`r3_lo_rich.calc_pdf(..., 14, True)`), and the fixes2 sweep extended to text drawn one object per word (`r3_layout.drawn(..., unit="wordsp")`, 10 and 12 pt, row- and column-major): each refused or whole, never split.

**R3-M1 MEDIUM: an ordinary cell such as "Mon - Fri" gets the whole table refused, and the reply says the PDF's columns aren't ruled.** [Certain]
- Where: same function, `COLUMNS_NOT_RULED`: gap `> 1.5 x` the smaller tight glyph height. A hyphen, dash or tilde is about 0.08 em tall, so the ordinary word space beside it counts as a column.
- Scenario (s07, reportlab grid, every cell ruled): Shop | Open, North | `Mon - Fri`. Also `Smith - one`, `low - high`, `Mon – Fri`, `x ~ y`, double spaces.
- Reply (every speaker): "I ran into a problem before I could change anything. The extract table step: couldn't independently re-read the PDF: some of the table's columns aren't ruled (two columns share one cell), and only tables with a border around every cell are supported for now. I stopped at the extract table step and no file was handed back; your original file is unchanged." The table is fully ruled.
- Reachable: yes, ranges and "A - B" are common in cells.
- Fix (same prototype): measure the in-cell gap on advance boxes against the font size, on the baseline-grouped lines, and refuse above 1.0 em (q03's unruled columns measure 2.2 em; three spaces 0.83 em). Test: `Mon - Fri`, `Smith - one`, `x ~ y`, `A  B` in a ruled grid delivered whole; q03 still refused.

**R3-M2 MEDIUM (security, not reachable by an ordinary request): the worker can still kill or starve the server.** [Certain] for what code in the worker can do; [Possible] that a parser is taken over.
- Where: `skills/pdf/_worker/lockdown.py` (Landlock does not cover these calls).
- From inside the full lockdown with Landlock: `prlimit64(getppid(), RLIMIT_CPU, {1, 1})` killed the parent; `prlimit64(getppid(), RLIMIT_NOFILE, {5, 5})` leaves it unable to open a file or accept a connection, and it can't raise the limit again; `setpriority`, `sched_setaffinity`, `ioprio_set` on the parent succeed. The design's "a taken-over parser can neither signal the server ..." intends the server to be out of reach.
- Fix: allow `prlimit64` (x86-64 302, arm64 261) only when its first argument is 0 or the worker's own pid (the limits are set before the filter). Refuse `setpriority` (141/140), `sched_setparam` (142/118), `sched_setscheduler` (144/119), `sched_setaffinity` (203/122), `sched_setattr` (314/274), `ioprio_set` (251/30), `migrate_pages` (256/238), `move_pages` (279/239), or allow them only on pid 0 or the worker's own. Add `prlimit_on_parent_refused` and `setpriority_on_parent_refused` to `selftest` and the test.

### For the owner

- **R2-M2's fix:** on a PDF, a report asked with the extraction is not made; the table is handed back and the reply says no report was made. The decided rule says the analysis is re-run on the final file, which here would be the extracted sheet. Confirm which you want.
- **Fail-closed without Landlock:** if the production container's seccomp profile blocks `landlock_*`, PDFs are off in production. Run `python -m skills.pdf` there.
- **R3-H1's fix** refuses tight auto-fit tables such as LibreOffice Writer's (a refusal, never a wrong table).

### Design deviations

1. Decision 4: "no two characters of different cells sit side by side as close as the letters of a word" holds, but its purpose (no character runs past its cell's border) doesn't, for text in several objects (R3-H1). The docstring's "in reading order" is content-stream order.
2. Decision 4: "no two neighbouring characters inside one cell are further apart than a word space": the measure is the glyph's height, so a word space beside a dash exceeds it (R3-M1).
3. Sandbox, "No handle on the server": `prlimit64` and the scheduling calls reach it (R3-M2).

### Deferred (LOW; added to `docs/backlog.md`)

- **R3-L1:** "give me a report" on a PDF: "I couldn't produce the report you asked for: I didn't run the analysis. No changes were made to any file this turn." It doesn't say why (a PDF can't be analysed) or what to do (extract the table first). Same reason in the report-plus-extraction reply.
- **R3-L2 (tests):** mutations s02 (run check), s07, s08 (planner's dependency and active-file handling), s10-s13 (`tgkill`, `tkill`, `rt_sigqueueinfo`, `pidfd_send_signal`: not in `selftest`) and s16 (the release check passes without Landlock) survive. `test_one_string_drawn_across_two_cells...` passes on `37e65eb` (it asserts "refused or whole").
- R2-L2, R2-L3 and round 1's L1-L7: still open, still in the backlog.

### Not run

- Live runs with Groq: not this reviewer's step.
- The production image (no container runtime here): whether Landlock is available there, so whether PDFs work at all.
- arm64: the new filter numbers (`tkill`, signals, the own-pid check) were not exercised.
- PDFs from Word, Excel or browsers, to see how often they draw text word by word (R3-H1's reach beyond LibreOffice).

## Review round 4 (commit 86151a4)

**Verdict: DON'T MERGE.** The layout check measures its gaps in the `Tf` operand, not in the size the text has on the page: text written `1 Tf` with the size in the text matrix (the way macOS Quartz and cairo write text) is delivered split across two cells (R4-H1), and every ordinary table written that way is refused as "columns aren't ruled" (R4-M1). A double space that the rule cuts is delivered split at any font size (R4-H2). Every cairo table is refused with a false reason (R4-M3). The worker can still kill the server through an fd owner's signal (R4-M2, not reachable by an ordinary request).

Base `origin/main` `89625e7`. Scope: `f82951a..86151a4` (no change under `buddy/`, `core/`, `backend/`), round 3's findings, regression against base. Probes: `scripts/review_harness/feat-pdf-extract/` (README, "Round 4"). Real `build_engine`, skills, sandboxed worker and verifiers; only the LLM faked.

### What was run

| What | Result |
|---|---|
| `python -m pytest -q` (head) | 3705 passed, 6 skipped, 1180 s |
| `ruff check buddy tests`, by file and rule, head vs base | identical; `check_ruff_baseline.py`: nothing beyond the baseline |
| New and changed tests copied onto `f82951a` | `test_text_layout.py` can't be collected there (no `geometry.Glyph`); 37 other tests fail (fixes3 end-to-end x6, seccomp filter x20, sandbox x7, deploy/locks x3, worker cap x1) |
| `python -m skills.pdf` (release check) | every check true, "The PDF sandbox holds." (exit 0) |
| Round-3 `r3_layout.py` (758 cases), `r3_lo_rich.py` (70), `r2_lo_overflow.py` (12) | **0 delivered wrong** in each; 16/16 punctuation cells and 5/6 LibreOffice punctuation cells delivered right; LibreOffice Writer 3/3 refused (the accepted cost) |
| `r3_pipeline.py` s01-s10 x 3 speakers (42 turns) | originals' SHA-256 unchanged in all; 0 Voice-model calls; s07 (`Mon - Fri`) delivered; s08/s09 refused with the new sentence |
| `r4_tf.py`: one table written `10 Tf` / `1 Tf` + scaled `Tm` / `0.1 cm` + `100 Tf` | PDFium's `FPDFText_GetFontSize` reports 10 / **1** / **100** for the same 10 pt text. `10 Tf`: 0 wrong of 256 overflow cases. **`1 Tf`: 11 delivered split** (R4-H1), 4 of 6 ordinary tables refused `columns_not_ruled` (R4-M1). **`0.1 cm`: unruled columns delivered joined** (2/2), 4 of 6 ordinary tables refused `text_overflows` |
| `r4_tf.py wordspacing`, ad-hoc double-space sweep (`10 Tf`) | note `*Urgent*  reorder` / `*Paid.*  Thanks` (two spaces, first word bold): **14 and 17 of 60 widths delivered split** (R4-H2); `Tw` 3 pt: 8 of 118 split |
| `r4_lo_double.py`: 200 LibreOffice Calc exports, double space + bold/red word | 0 wrong (62 right, 138 refused) |
| `r4_cairo.py`: real cairo PDFs (libcairo through ctypes) | **15 of 15 ordinary tables refused** `text_overflows` (R4-M3); 0 of 427 overflow cases wrong |
| `r4_pipeline.py` t01-t05 x 3 speakers (18 turns) | originals unchanged in all; 0 Voice-model calls; t01 split delivered, then deduplicated (R4-H1); t02 false "columns aren't ruled" (R4-M1); t03 joined columns delivered; t04/t05 right |
| `r3_sandbox.py`, `--kill-server` | prlimit64, setpriority, sched_setaffinity, ioprio_set, kill(0)/kill(-1) on the parent: EPERM; the parent's limits, nice and affinity unchanged; the throwaway server survives |
| `r4_sandbox.py` (full lockdown + Landlock) | **`fcntl(F_SETOWN, getppid())` + `O_ASYNC` on a pipe, then a write: the server dies** (SIGIO exit -29; with `F_SETSIG` 9: -9; 15: -15; owner = the server's process group: -9). Same without Landlock (R4-M2) |
| `r4_mutations.py`: 14 one-place mutations of the round-3 fixes | 12 killed. Survived: n05 object rule off, n07 tight glyph boxes instead of advance boxes (stated in fixes3) |
| Active-content test as uid 65534 (`setpriv`) | passes (R2-L3's cause confirmed fixed) |
| Prototype `r4_prototype_fix.diff` (throwaway worktree) | em scaled by the character's matrix, object rule removed, Landlock signal scope: `r4_tf.py` gives the same result in all three writings (0 wrong of 768 overflow cases, 18/18 ordinary tables delivered, unruled columns refused in all 6); cairo 15/15 ordinary tables delivered, 0 of 427 overflow cases wrong; `r3_layout.py` 758 cases identical to head (0 wrong); `r3_lo_rich.py` 0/70 wrong; `r2_lo_overflow.py` 0/12; the 4 fd-owner tries leave the server running; `python -m skills.pdf` holds; the 359 PDF tests pass. LibreOffice and reportlab write an identity matrix, so the em change is a no-op for every PDF the suite has. R4-H2 not fixed by it |

### Round-3 findings

| | Status (own evidence) |
|---|---|
| R3-H1 | **Fixed for text whose `Tf` is its size** [Certain]: 0 wrong in 758 + 70 + 12 + 256 cases; s08/s09 refused in 3 speakers; mutations n01-n04, n06 killed. Not for text sized by its matrix: R4-H1. A gap of 0.5 em or more at the rule: R4-H2 |
| R3-M1 | **Fixed for text whose `Tf` is its size** [Certain] (16/16 punctuation cells, s07 in 3 speakers, n02/n08 killed). Text sized by its matrix: R4-M1 |
| R3-M2 | **Fixed for the calls listed** [Certain] (`r3_sandbox.py`, `--kill-server`; n09-n13 killed). Signals through an fd owner: R4-M2 |
| R3-L1 | open (s01: "I didn't run the analysis.") |
| R3-L2 | s10-s13, s16 guarded (n14 killed); s07, s08 open, as the backlog says |
| R2-L3 | fixed (port 0; passes as uid 65534) |

### Findings

**R4-H1 HIGH: text sized by its matrix (`1 Tf` and the size in `Tm`, as macOS Quartz writes it) is delivered split across two cells, and the reply says every cell is the PDF's text.** [Certain] mechanism and result; [Likely] that Quartz PDFs (Numbers, Pages, Excel for Mac, any "Save as PDF" on a Mac) are written this way; the case also needs every other cell of the table to be one word (see R4-M1).
- Where: `skills/pdf/_worker/main.py` `_text_layout_problem`: `size = raw.FPDFText_GetFontSize(...)`. PDFium returns the `Tf` operand: 1 for `/F1 1 Tf 10 0 0 10 x y Tm` (100 for a page drawn at `0.1 0 0 0.1 0 0 cm`), so `geometry.text_layout_problem`'s 0.5 em is 0.5 pt and 1.0 em is 1 pt. The advance boxes and baselines are in page space, so only the em is wrong.
- Scenario (t01; Name | Note | Qty, the Name cell `Urgent reorder` with its first word bold, overflowing into the empty Note cell): delivered as Name `Urgent`, Note `reorder`. The same table at `10 Tf` is refused. Then "now remove the duplicate rows" works on the split rows.
- Reply (focus, t01): "Done — extracted the table: 3 columns, a first row and 3 rows below it. The table from report.pdf is in a new spreadsheet, ready for you. ... Every cell holds the text read from the PDF's text layer; numbers and dates are not converted."
- Reachable: a Mac-made table whose cells are single words except one overflowing note drawn in several runs. 11 of 256 sweep widths here.
- Fix (prototyped): scale the em by the character's matrix: `FPDFText_GetMatrix(textpage.raw, index, &m)`, `size = FPDFText_GetFontSize(...) * sqrt(|m.a*m.d - m.b*m.c|)`. Tests: `r4_tf.table_pdf(rows, widths, writing="tm")` and `writing="cm"`, rich two-word note swept through the rule: refused or whole, never split; an ordinary table with two-word cells in both writings delivered.

**R4-H2 HIGH: a double space (or word spacing) that the rule cuts leaves 0.56 em between the halves, above the 0.5 em threshold, and the sentence is delivered split.** [Certain] on hand-written PDFs at `10 Tf`; [Possible] reach: 0 of 200 LibreOffice Calc exports, Word/Excel/browsers not available.
- Where: `geometry.RUN_EM` (0.5) in `text_layout_problem`. Two spaces measure 0.56 em; `Tw` adds to each space.
- Scenario: note `Paid.  Thanks` (two spaces after the full stop, `Paid.` bold, so two text objects) running into the empty next cell: delivered `Paid.` / `Thanks` at 17 of 60 widths; `Urgent  reorder` at 14 of 60. Under the same reply as R4-H1.
- Fix: not a threshold. With `RUN_EM = 0.7` in the prototype, 6 and 14 of 60 are still split, and a higher value refuses more ordinary tables. It needs another signal, for example the column's alignment: refuse when a cell's text starts at an x that is neither the column's left padding, nor right- or centre-aligned in its cell, while its left neighbour's text runs to within a word space of the rule. *For the owner* if no such signal is wanted: record that a sentence with a double space at a cell border may be delivered split, and stop saying "Every cell holds the text read from the PDF's text layer" as a guarantee of whole cells.

**R4-M1 MEDIUM: a table written `1 Tf` + scaled `Tm` is refused as "columns aren't ruled" whenever a cell holds two words; a page drawn in tenths of a point delivers unruled columns joined.** [Certain]; reach as R4-H1 [Likely] for Mac PDFs, [Possible] for tenth-point pages.
- Where: same line as R4-H1.
- Scenario (t02, every speaker): Fruit | Colour | Qty, `Green apple` | `light green` | `10`, every cell ruled. Reply: "I ran into a problem before I could change anything. The extract table step: couldn't independently re-read the PDF: some of the table's columns aren't ruled (two columns share one cell), and only tables with a border around every cell are supported for now. ..." The table is fully ruled. t03 (`0.1 cm`, `100 Tf`, review round 2's partly ruled table): delivered `Qty Price` / `10 2.50` under "Done — extracted the table: 2 columns ...", the R2-M1 result.
- Fix: R4-H1's. Test: the same table in the three writings gives the same result.

**R4-M3 MEDIUM: every table drawn by cairo is refused with a false reason.** [Certain] for cairo (Gnumeric, Firefox and GTK printing on Linux, Inkscape); reach: an ordinary request on such a PDF. Present since round 2 (the object rule); not new code in this round.
- Where: `_text_layout_problem`'s object rule (`one string drawn across two cells`). cairo writes a whole row as one `TJ` (`[(Ann Lee)-6943(New York)]TJ`), so one text object always has characters in several cells.
- Scenario: 15 of 15 ordinary ruled tables (9, 10, 12 pt; two-word cells, `Mon - Fri`, single words), refused "some text in the table sits too close to a cell's border, or runs past it, for me to be sure which cell it belongs to". Every cell has 3 pt of padding.
- Fix (prototyped): drop the object rule and leave it to the line rule, which since this round judges every pair of neighbours on a line whatever object they are in (mutation n05, the object rule off, survives every test). Prototype numbers above.

**R4-M2 MEDIUM (security, not reachable by an ordinary request): the worker can still kill the server, by making it the owner of a file descriptor.** [Certain] for what code in the worker can do, with and without Landlock; [Possible] that a parser is taken over.
- Where: `skills/pdf/_worker/lockdown.py`. `fcntl` is allowed with any command, and Landlock is created without a scope.
- From inside the full lockdown: `os.pipe()`, `fcntl(rd, F_SETOWN, getppid())`, `fcntl(rd, F_SETFL, O_ASYNC)`, `os.write(wr, b"x")`: the kernel sends SIGIO to the owner, whose default action ends it (exit -29). With `F_SETSIG` 9 the server is SIGKILLed; with 15 it gets SIGTERM. In the container the server is PID 1 (`exec python -m backend`), which drops signals it has no handler for, so SIGIO and SIGKILL may do nothing there, but SIGTERM reaches the server's own handler and shuts it down [Likely; not run in a container]. An owner of `-pgid` reaches the server's whole process group. The kernel checks only that the uids match.
- Fix (prototyped): where Landlock's ABI is 6 or more, create the ruleset with `scoped = LANDLOCK_SCOPE_SIGNAL` (`struct.pack("QQQ", handled, 0, 1 << 1)`): all four tries then leave the server running. Also refuse `fcntl` (x86-64 72, arm64 25) with `F_SETOWN` (8), `F_SETOWN_EX` (15) and `F_SETSIG` (10) in the filter, for kernels below ABI 6. Add both to `selftest` (an fd owned by the parent, a write, the parent still alive) and to the release check.

### Design deviations

1. Decision 4: gaps "in ems (the font size)" are in the `Tf` operand; "it doesn't depend on how the PDF drew the text" doesn't hold for how it sized it (R4-H1, R4-M1).
2. Sandbox, "No handle on the server": an fd owner's signal reaches it (R4-M2).
3. Decision 4 keeps the object rule as a separate refusal; it adds nothing the line rule doesn't catch in any probe or test, and refuses every cairo table (R4-M3).
4. "Libraries are pinned ... checked inside the production image on the owner's VM at `1575f04`": not checked here.

### Deferred (LOW; added to `docs/backlog.md`)

- **R4-L1 (tests):** no test writes text with a scaled matrix (`1 Tf` + `Tm`, or a scaled `cm`), which is how R4-H1 passed; mutations n05 (object rule off) and n07 (tight glyph boxes) survive.
- R3-L1, R2-L2 and round 1's L1-L7: still open, still in the backlog.

### For the owner

- R4-H2 has no clean fix by threshold (above).
- Fixes3 says this is the last round and that a DON'T MERGE leaves the decision to the owner's advisor. R4-H1 and R4-H2 need a specific kind of PDF: R4-H1 a Mac-made table whose cells are all single words but one, R4-H2 a double space exactly at a border inside a styled note. Neither was produced by LibreOffice here.

### Not run

- Live runs with Groq: not this reviewer's step.
- The production image and the CI image job (no container runtime): the sandbox under Docker, R4-M2 against PID 1 in the container.
- PDFs from macOS Quartz, Word, Excel or browsers: R4-H1's reach rests on Quartz writing `1 Tf` (not checked on a real Mac file here); cairo was checked.
- arm64 hardware (the filter's arm64 path through the tests' BPF interpreter only).
- The shared 532-turn `harness.py`: nothing under `buddy/`, `core/` or `backend/` changed since round 3, where it matched.
