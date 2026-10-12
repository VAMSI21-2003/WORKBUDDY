# feat/pdf-extract: implementation notes

Writer's notes for the independent review (`docs/process/review-loop.md`). Design: `docs/design/pdf-extract.md`. Base: `origin/main` at `89625e7`.

## What changed

| Area | Files | What |
|---|---|---|
| Brain (closed vocabulary) | `buddy/actions.py`, `buddy/brain.py`, `buddy/models.py` | One new action `extract_pdf_table` (capability `pdf.extract_table`, target `extracted_table`), in the intent vocabulary, Brain's prompt and the action table. No `mention` pattern. `Action.removes_rows` / `extracts_table` flags; `ACTIONS_BY_TARGET` keeps only the row-count targets. `StepMeasurement.columns`. |
| The skill | `skills/pdf/extract_table.py` | Write tier. Reads the PDF's bytes, asks the sandbox, validates the answer's shape, writes a text-only `.xlsx` (sheet `Table`), refuses with fixed sentences. |
| The sandbox | `skills/pdf/sandbox.py`, `skills/pdf/_worker/{main,lockdown,geometry,textnorm}.py`, `skills/pdf/__main__.py` | Separate isolated process, deadline, `RLIMIT_AS`/`CPU`/`FSIZE`/`NOFILE`, seccomp (no sockets, no exec), empty environment and folder, bytes on stdin, at most two at once, fail closed. `python -m skills.pdf` is the release check. |
| The verifier | `buddy/inspector.py` (`PdfTableVerifier`), `buddy/engine_factory.py` | Second reading with PDFium and `geometry`, every cell compared, text cells only. Wired into the `CompositeVerifier` that gates each chain step and re-checks the turn. |
| Reply | `buddy/outcome.py`, `buddy/voice_catalog.py`, `buddy/voice.py` | Fixed sentences from the verifier's measurement: "Extracted the table: N rows and M columns.", the PDF-specific file sentence, `TABLE_TEXT_SENTENCE` after the unchanged values-only sentence. The capability line, the "upload a ..." lines and the confirmation card name PDFs. |
| Upload | `backend/uploads.py`, `backend/settings.py`, `backend/errors.py`, `backend/names.py`, `backend/confirmation.py`, `backend/openapi.json`, page label | `.pdf` accepted, `%PDF-` at byte 0, 20-page cap by the sandboxed `probe`, same 2 MiB cap, the download name `<stem>_table.xlsx`, the plan line "Extract the table from <pdf>.". |
| File settling | `buddy/source.py`, `buddy/actions.py` (`Action.reads_upload`) | A `.pdf` name is a file name like `.xlsx`: close-match names, "which file?", resume, follow-up on the extracted sheet. A request Brain read as the extraction uses the PDF, not the copy made from it (design decision 9). |
| Existing skills | `skills/excel/dedupe.py`, `skills/excel/remove_empty_rows.py`, `buddy/inspector.py` (`_read_cells`) | **`dtype=object` on the reads** so text cells stay text (design decision 8). Not asked for; needed for the follow-up to be honest. |
| Dependencies | `requirements-runtime.txt`, `requirements.txt`, both locks | `pdfplumber>=0.11,<0.12`, `pypdfium2>=5,<6` (runtime); `reportlab>=4` (dev). Locks: additions only (`pdfminer-six`, `pdfplumber`, `pypdfium2` in both; `reportlab` in dev). |
| Tests | `tests/skills/pdf/`, `tests/buddy/test_pdf_extract.py`, `tests/backend/test_pdf_turns.py`, `tests/pdf_fixtures.py`, edits to six existing tests | see below |
| Tools | `scripts/measure_pdf_worker.py` | The memory/time measurement below. |

`deploy/` and the Dockerfile are unchanged: no system package was needed here. Not checked inside the production image (no container runtime in this session).

No on-disk format changed (session records, uploads and delivered files have the same shape; a PDF is one more upload with the name and hash every upload has; the delivered sheet's download name is derived at read time), so no migration and no new data fixture. `tests/deploy/test_saved_data_survives.py` passes.

## Rule 1 and rule 2 in this change

- **Never claim an outcome that didn't happen.** The only counts in a delivered reply are the verifier's (rows and columns of the sheet it read, after the second reading matched every cell). A skill that writes a wrong cell, a number, a formula or a missing row is stopped (`test_a_skill_that_writes_a_table_that_isnt_the_pdfs_is_stopped_by_the_inspector`, nine verifier mutations in `test_the_verifier_fails_unless_every_cell_matches`). Nothing from the file or an exception reaches a reply (a refusal reason is a code; the sentence is fixed text; a test searches the reply for a word of the file). A PDF that is not a table the parsers agree on is a refusal.
- **Never modify the original.** The skill reads bytes and the worker never gets a path; every pipeline test compares the PDF's SHA-256 before and after, and the API tests do too across the follow-up.

## Tests

- New: `tests/skills/pdf/` (geometry and text; sandbox: socket and exec refused from inside, a real listener never reached, empty environment, deadline kills the process and its child, memory and output limits, a closed set of answers, off-Linux and failure-to-start are refusals, the cap on workers at once, the release check; the skill on reportlab and hand-built PDFs; hostile PDFs in every worker mode), `tests/buddy/test_pdf_extract.py` (pipeline in three speakers, refusals in three speakers, the closed vocabulary, the verifier's failures, two different libraries are loaded by the skill and by its check, a lying skill), `tests/backend/test_pdf_turns.py` (upload and its refusals, extract then "now remove the duplicate rows" on the delivered sheet in three speakers, file settling).
- Existing tests edited only for the new facts: accepted extensions (`.xlsx`, `.pdf`), the upload and invalid-file texts, the action table now having a row that is not a row count, the intent-to-capability map, the prompt's action list spanning two lines, the capability line naming the extraction, the page's `accept` attribute and label (`--update-ui-copy` for the snapshot).
- `ruff check buddy tests`: no error beyond `ruff-baseline.txt` (`scripts/check_ruff_baseline.py`); the baseline file is unchanged. (With the `ruff` installed in the cloud session, which is not the locked version, four baseline entries show as fixed, one of them in `buddy/brain.py`; none was touched on purpose.)
- Full suite on the head before review round 1: 3552 passed, 7 skipped (the skips are existing browser/optional tests), 819 s on the cloud machine.

## Parser process: peak memory

`python scripts/measure_pdf_worker.py --markdown` (the real worker, one fresh process per read, on the cloud session's machine; 20-page PDFs built with reportlab). "Worker" figures are `VmHWM` and `VmPeak` read by the worker from `/proc/self/status` (a child of a big parent would inherit the parent's peak through `getrusage`, so that is not used for the worker's own figure).

| PDF | size | mode | answer | peak RSS (kernel, MiB) | peak RSS (worker, MiB) | peak virtual (MiB) | seconds |
|---|---|---|---|---|---|---|---|
| table + 19 text pages | 12 KiB | probe | ok | 79 | 21 | 31 | 0.8 |
| table + 19 text pages | 12 KiB | extract | ok | 79 | 60 | 74 | 1.2 |
| table + 19 text pages | 12 KiB | verify | ok | 80 | 23 | 31 | 0.8 |
| 20 pages, a 25x8 table on each | 34 KiB | probe | ok | 79 | 21 | 31 | 0.7 |
| 20 pages, a 25x8 table on each | 34 KiB | extract | many_tables | 87 | 87 | 101 | 2.1 |
| 20 pages, a 25x8 table on each | 34 KiB | verify | ok | 79 | 28 | 37 | 0.8 |
| table + 19 dense text pages | 13 KiB | probe | ok | 79 | 21 | 31 | 0.7 |
| table + 19 dense text pages | 13 KiB | extract | ok | 179 | 179 | 193 | 2.8 |
| table + 19 dense text pages | 13 KiB | verify | ok | 80 | 24 | 32 | 0.8 |
| table + 19 pages of 4-point text | 16 KiB | probe | ok | 79 | 21 | 31 | 0.7 |
| table + 19 pages of 4-point text | 16 KiB | extract | ok | 706 | 706 | 719 | 13.0 |
| table + 19 pages of 4-point text | 16 KiB | verify | ok | 80 | 32 | 42 | 0.9 |

The "kernel" column is `getrusage(RUSAGE_CHILDREN)` and includes about 79 MiB the child inherits from the small measuring process, so read it as an upper bound; the worker's own figures are the ones to use.

Limits: address space 1 GiB, deadline 20 s, two workers at once. The upload's `probe` and the Inspector's `verify` stay near 20 to 40 MiB resident whatever the text; the skill's `extract` (pdfplumber) is the heavy one and grows with the number of characters on the pages. The densest 20-page file this machine could build in 16 KiB (about 20,000 characters a page in 4-point type) used about 0.7 GiB resident and 12 s, which is near both limits: a heavier text layer ends in a `too_large` or `took too long` refusal. For sizing the VM: allow about 1 GiB per concurrent worker (two at most), plus the server.

## Deviations from the brief

1. Write tier, not Read (design decision 2).
2. The page cap is enforced twice: at upload (so a refused upload leaves nothing behind) and in the worker.
3. The memory figure is here and in the PR, not in `docs/reviews/feat-pdf-extract-review.md`: that file is the reviewer's, and the loop forbids the writer to edit it. The owner can ask a reviewer round to copy it in.
4. The existing cleaning skills were changed (design decision 8).

## For the owner

- Tier: Write (confirmation card shows "Extract the table from <file>.") rather than Read. Say if you want Read.
- The existing cleaning skills now keep text cells as text. Before, a text cell `007` in a workbook became `7` in the cleaned copy; that was true on `main` and is fixed by this change.
- Before trusting uploads on the server: `docker exec <container> python -m skills.pdf` must print "The PDF sandbox holds." If the container runtime forbids `prctl(PR_SET_SECCOMP)`, PDFs are refused (not read unprotected). Then a live turn on a real PDF with a ruled table.
- **Not run: live runs.** The cloud Groq key returns 401 in this session (as the brief said), so no live run was made. The owner's check on the server is the live check: upload a PDF with one ruled table, ask for it, approve the plan, download, then "now remove the duplicate rows".

## Report

Filled in at the end of the loop (rounds, final commit, what is deferred, what was not run).
