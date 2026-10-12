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
