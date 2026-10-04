# Backlog

Open work, in priority order. Each item follows the working method in `CLAUDE.md`: its own branch, an independent review for rule-touching changes, and live runs before merging.

## 1. The outcome of a successful turn comes from verifier data, never from the model (F2 / N1 / N2)

**Status (2026-09-30):** design approved (`docs/design/verified-outcome-design.md`, "Decisions (2026-09-30)"). PR-A (`feat/verified-outcome-a`) is merged: the delivered outcome is rendered by code from verifier measurements, the Voice model call is gone, N2 is closed, and the order sentence is reply-level for `PersonalityVoice`. **PR-B (`feat/verified-outcome-b`, not merged; independent review found H1/H2, fixed in `docs/reviews/verified-outcome-b-fixes.md`; the re-review's N1/N2 are fixed in `docs/reviews/verified-outcome-b-fixes2.md`; the separate-session "Re-review 2" (2026-10-01) says DON'T MERGE: three remaining false "Done —" paths in `_no_skill_outcome` (P1–P3) and a new false "Partly done" on fully done requests (Q1), with the exact fix in that section; those were fixed in `docs/reviews/verified-outcome-b-fixes3.md`, and the separate-session "Re-review 3" (2026-10-01) confirms P1–P3 and Q1 fixed but says DON'T MERGE: the new location strip turns a correct "across/in the workbook" `unsupported` flag into "Done —" on a multi-sheet workbook (R1), with a two-word fix in that section)** adds the ledger of requested actions (Brain's `requested_actions`, the mention detector), `NOT_DONE` / `DONE_UNREQUESTED` lines, "Partly done —", residual counts, and brings back the "nothing to remove" opener and the completion closers (PR-A review M1), gated on every requested action being done. In the `build_engine` tests it closes N1, C1–C3 and N5; see `docs/reviews/verified-outcome-b-impl.md` and `-live-runs.md`. Once PR-B is reviewed and merged, this item is closed apart from the deferred list below.

**Problem:** on a verified delivery, `PersonalityVoice` uses the model's reply without any check against what was requested or what the verifier measured. It's the one known open breach of rule 1, and it exists on `main`.

**Evidence** (`docs/reviews/chaining-wip-final-review.md`, sections F2, "F2 on main" and V5):
- **N2**, which needs only one model error: correct classification and plan, dedupe on a file with no duplicates. The model's "Removed 3 duplicate rows — your sheet is clean." reaches the user.
- **N1:** Brain misclassifies a dedupe request as `remove_empty_rows`. The empty-rows file is delivered, and the model's "duplicates removed" claim reaches the user.
- **C1–C3 and N5:** a compound request where one requested action has no step. The reply says "Done" and claims both actions.

**Acceptance criteria:**
- The sentence stating what was done, including counts (rows removed, rows remaining) and which actions ran, is built from `ExecutionResult`/`VerificationResult` data by code, never by the model. The model may add wording around it, but can't replace or contradict it.
- A requested action with no passing step is stated as not done. That applies to a compound request with an item that wasn't planned, and to a misclassified request. There's no bare "Done" for a compound request with any unaddressed item.
- Tests reproduce N1, N2, C1, C2, C3 and N5 end to end through `build_engine`, with the review's model replies, and assert that no false claim reaches the user.
- Also covered, from the fixes-3 verification: with `PersonalityVoice`, the order sentence is appended when the model's reply omits it, as is already done for the file name.
- An independent review, and live runs with Groq, show 0 false claims.

**Deferred from the verified-outcome design** (its "Deferred" section):
- Ask the user when the ledger sources disagree (design 6.7). Depends on item 2.
- Stop unrequested write steps before execution (6.8). Depends on a measured ledger recall.
- Compare the ledger's phrase order with the executed order, as an order check independent of the planner.
- Ledger lines on undelivered turns ("I didn't remove duplicates: I needed to ask you something first").
- Verify rule 2 at runtime: hash the upload before and after in Hands.
- Report "give me a report" as a delivered report (item 3); the design only makes it an honest `NO_SKILL` line.
- **From the PR-B review, L1 (closed as N2 in `docs/reviews/verified-outcome-b-fixes2.md`):** `has_reading` now needs a supported Brain action, so `unsupported` entries alone no longer make a passing step "unrequested".
- **From the PR-B review, L3:** "Partly done —" when Brain lists an action the user said to keep (`b4`: `remove_duplicate_rows` with the phrase "keep the duplicates"), and mention-detector noise on unrelated words ("deduplicated", "blank lines … are intentional"). Each sentence is true, but the lead word misleads. It follows from D2 and Brain's prompt rule. Measure it with the ledger-recall set (design 6.8) before changing the detector.
- **From the PR-B re-review 2 (Low, deferred):** L-a, a negated `unsupported` clause ("don't delete the file") renders "Partly done" although nothing done is called not done; L-b, an `unsupported` phrase of only function words ("remove the", "please") is quoted as "can't do"; L-c, when Brain's only entries are `unsupported` and the planner runs an unrequested step, the reply states the removal without "I didn't read that as part of your request" (no false claim; fixing it needs ledger recall, 6.8). Also the residual risks named in section 6 of that re-review (a done action's phrase with no joiner that holds an unsupported verb; a double negation in a paraphrase).
- **From the PR-B re-review 3 (deferred):** the residual HIGH-class false "Done —" paths accepted in re-review 2 §6 are still present (`z31`: Brain gives "delete the file" as the dedupe phrase; `z18`: a paraphrased double negation, "don't keep rows with nothing in them", with Brain's own words "don't keep the empty rows" as `unsupported`); MEDIUM-u, a false "Partly done … can't do yet" on fully done work after a near-miss action name plus a word outside the lists ("de-dupe", "removing", "for me", "this sheet", "my excel file") or a whole-message `unsupported` flag on a supported request (not reachable from a correct Brain reading); L-d, no guard test on `_REMOVAL_VERBS`, `_DETERMINERS` or `_LOCATION_PHRASE` (requested with the R1 fix). Scenarios: `scripts/review_harness/rereview3.py`.
- **From the PR-B review round 5 (deferred, all pre-existing on `main` unless said):**
  - **(Addressed on `fix/values-only-copy`, see item 1b; not merged until reviewed.)** **Silent loss in a delivered one-sheet file (MEDIUM-HIGH, on `main`; `scripts/review_harness/formulas_probe.py`):** the skills rewrite the sheet with pandas, so formulas, formatting, column widths, merged cells and anything above the header row are lost or turned into columns, and the reply says only "Done — removed N duplicate row(s)". Needs either a "formulas and formatting aren't kept" sentence or an openpyxl-based writer.
  - **(Addressed on `fix/values-only-copy`: `YDATA_PROFILING_NO_ANALYTICS=1` is set before the import.)** **Telemetry (LOW-MEDIUM, on `main`):** `ydata-profiling` 4.18.4 sends the row and column count, OS and Python version of every analysed sheet to `packages.ydata.ai` unless `YDATA_PROFILING_NO_ANALYTICS` is set. Set it in `skills/excel/analyze.py` (or the deployment environment) before a public deployment.
  - **Failure reply and delivered files (LOW, for PR-2):** when a stage after Hands raises (`c-fail-inspector`), `state.execution_result.outputs` still holds a real file while the reply says no file is handed back. The API must hand back nothing when `state.failure` is set.
  - **".xlsx will work" (LOW):** the legacy-`.xls` refusal says "saving it as .xlsx will work", which isn't checked (a multi-sheet file saved as .xlsx is still refused).
  - **Chart sheet (LOW):** a workbook with a chart sheet that openpyxl can't read in read-only mode is refused as "damaged" (`a12`).
  - **"your original file is unchanged" (LOW):** `_chain_stop_sentence` says it with no runtime check (the failure replies deliberately don't); same wording as `main`.
- **From PR-B:** the leftover case the design names (6.9 and "Costs"). If Brain misclassifies *and* the message paraphrases the request so the mention detector misses it, the reply can still lead with "Done —" for work that wasn't asked for. That's no false outcome claim, but it's misleading about the request. Measuring the ledger's recall (a labelled set of about 50 messages) comes before 6.8.

## 1a. M3 (high): multi-sheet workbooks lose every sheet after the first, while the reply says "cleaned copy"

**Source:** `docs/reviews/verified-outcome-a-review.md`, M3. It exists on `main` too; it isn't introduced by PR-A. It's a rule 1 breach, so it comes right after PR-B.

**Status (2026-10-01):** `fix/multi-sheet` (not merged; re-review `fix-multi-sheet-review2.md`: MERGE) takes the "refuse" option. Both skills refuse a workbook with more than one sheet, the reply says why, and the verifier compares each step's sheet names with its input's. See `docs/reviews/fix-multi-sheet-impl.md`, `fix-multi-sheet-review.md`, `fix-multi-sheet-fixes.md` and `fix-multi-sheet-live-runs.md`.

**Fixed after the review (`fix-multi-sheet-fixes.md`):** H1, H2, M1, M2, M3 (the sheet list is the one *declared* in `workbook.xml`, shared by the skills and the Inspector; OLE2 files are refused as password-protected or legacy `.xls`, never counted; no exception text in user-facing errors), M4 (no proactive analysis on a workbook the skills refuse), L2 (partial output deleted on a failed write) and L6 (`think.py` wording).

**Still open:**
- L3: crafted sheet names Excel forbids fail at the write, and names over 31 characters are written as is.
- L4: Inspector wording for a reorder ("lost or renamed") and for a non-workbook output (raw pandas text).
- L5: say "(including hidden sheets)" when hidden sheets push the count above one.
- An *explicit* request to analyse a multi-sheet workbook still analyses the first sheet only, without naming it.
- Per-sheet cleaning (keep every sheet and clean each one, or the one the user named). It needs per-sheet measurements in the Inspector and Outcome code.
- A real `.xls` can't be cleaned. The skills refuse it ("older .xls workbooks … aren't supported yet"). Don't add `xlrd` until the sheet count includes chart sheets, macro sheets and VB modules, which `xlrd` skips.
- A test with a hostile `workbook.xml` (entity expansion). The re-review measured a billion-laughs, a quadratic blowup and two XXE files: all refused in under 0.05 s with fixed text (expat 2.6.1); there's still no test in the suite.
- Real encrypted-file coverage in the suite: the tests and the live run use an `EncryptedPackage` stand-in. The re-review confirmed real `msoffcrypto`-encrypted files are caught.

**Deferred from the re-review (`docs/reviews/fix-multi-sheet-review2.md`), in this order:**
- N1 (Medium): a valid one-sheet workbook whose workbook part is typed by a `<Default>` entry (not an `<Override>`) is refused as "may be damaged"; `main` cleans it. Template content types are probably refused too. Mirror `openpyxl.reader.excel._find_workbook_part` in `_declared_sheet_names`, plus a test.
- N4 (Low): the write error "could not write the cleaned copy" matches `_COMPLETION_CLAIM`; use "could not write the new file", plus an engine-level write-failure test.
- N2 (Medium): a vague (`unclear`) turn on a multi-sheet or unreadable workbook now gets "I wasn't able to confirm that worked. No verifier has been configured." (pre-existing text for an empty plan, newly reachable). Give the empty plan an honest reply, or skip the analysis only when a file-changing step is planned.
- N3 (Low): `EncryptedPackage` in `.xls` cell text (with a character above U+00FF) makes the file "password-protected"; match only at an OLE2 directory-entry position (`(offset - 512) % 128 == 0`, name length 34 at `offset + 64`).
- N5 (Low): any non-encrypted OLE2 file (e.g. a renamed Word `.doc`) is called an "older .xls workbook".
- N6 (Low): the live script's `password-protected` fixture is a stand-in, and its independent check prints only "unreadable".

**Problem:** `skills/excel/dedupe.py` and `skills/excel/remove_empty_rows.py` read only the first sheet (`pd.read_excel(input_path)`) and write one sheet named `Sheet1`. For a workbook with sheets `Q1` and `Q2`, the delivered file has one sheet, and the reply says "Your cleaned copy of report.xlsx is ready." and "The copy has 2 row(s)…". Nothing tells the user that `Q2` was dropped, and no verifier notices.

**Acceptance criteria:**
- Either the skills keep every sheet (and clean each one, or only the one the user named), or they refuse a multi-sheet workbook and the reply says so. A delivered file never silently loses a sheet.
- The verifier compares the input's and output's sheet lists, so a dropped sheet fails verification whatever the skill did.
- Tests through `build_engine`: a two-sheet workbook, all three speakers, single step and chain. The delivered file's sheet names match the original's (or nothing is delivered and the reply says why), and no reply calls a one-sheet file "the copy" of a two-sheet workbook.
- Live runs before merging, per `CLAUDE.md`.

## 1b. A delivered copy keeps values only (P-1)

**Source:** `docs/reviews/verified-outcome-b-review.md`, round 5, P-1. Design: `docs/design/values-only-copy.md`.

**Status:** `fix/values-only-copy` (not merged; review loop in progress, see `docs/reviews/fix-values-only-copy-*.md`). A sheet that isn't a plain table (merged cells, a header that isn't on row 1, a column with no header, two equal headers) is refused by both skills and, independently, by the Inspector. A delivered copy always says "The copy has the values only; formatting, column widths and other sheet settings aren't carried over.", and says how many formula cells the original has (and how many had no saved value) when it has any.

**Still open (decided not to do in that change):**
- **Keep formulas and formatting.** An openpyxl-based writer that edits a copy of the workbook in place (drop the rows, keep everything else) would make the copy keep formulas, formatting, widths, filters and validation, and would let sheets with a title row or merged banner be cleaned. When it exists, the values-only sentence must become per-capability (it is unconditional today because the writer never keeps formatting) and the plain-table refusal can be narrowed.
- **Formulas with no saved value.** A file written by a script has formulas with no saved result, so the skills see those cells as empty (dedupe and empty-row removal judge rows without them). The reply now says so; refusing such a file, or recalculating it, is the stronger option.
- A one-column table with a title in A1 and the header in A2 can't be told from a table whose first value is the header's name; it isn't refused.
- Each plain-table check streams the sheet once more (skill, Inspector, planner, measurer); measure it on a large file before a public launch.
- **From the `fix/values-only-copy` review, round 1 (deferred, LOW; the HIGH and MEDIUM findings R1-1 to R1-3 are in `docs/reviews/fix-values-only-copy-review.md`):**
  - R1-4: "had no saved value" is the wrong reason for a formula that returns `""` (Excel saves an empty string); the count also includes formula cells in rows the turn removed. Reword to "K of them are empty in the copy".
  - R1-6: cost of the extra passes on a large sheet (100,000 x 5: pandas read 8.9 s, plain-table check 10.1 s, formula count 17.6 s); an up-front refusal reads "partway through"; `sheet._get_source()` is private openpyxl API (pin or guard); no test for an empty-string header cell (`_is_empty` mutation survives).

## 2. An explicit conversation intent, and relaying Think's clarifying question

**Problem:**
- Every turn that doesn't deliver a file gets the factual template, including a greeting. "hi, what can you do?" gets a missing-values report (live-run L-5).
- When Think asks for clarification, the user never sees the question: `Reasoning` has no question field.

**Acceptance criteria:**
- Brain's vocabulary gains an explicit conversational intent, for example `conversation`. Only that intent lets the model phrase a turn that delivered nothing, and it adds no route for outcome claims:
  - If a step above READ tier was planned or ran, the turn is not conversational, whatever the intent.
  - The completion-claim check still applies.
- `Reasoning` carries the clarifying question, and both `TemplateSpeaker` and `PersonalityVoice` put it in the reply when `clarification_required` is true.
- Tests: a greeting gets a conversational reply. An action request misclassified as conversational still can't get a completion claim through. A clarification turn shows Think's question.
- Live runs: a greeting, a capability question, and one clarification case.

## 3. "Give me a report" (L-3), and the analyzer doesn't report exact duplicates (L-4)

**Problem:**
- **L-3:** "give me a report" is mapped to `spreadsheet.analyze` and then absorbed by the duplicate-analyze filter, so no step records the user's request. The reply may say "Done" without a report (`docs/reviews/chaining-wip-live-runs.md`).
- **L-4:** `spreadsheet.analyze` never reports exact duplicate rows. Its duplicate check covers only near-duplicates, and only when there's a name column plus a phone or email column. The no-findings wording was softened on `main` ("none of the issues I check for were found"), but the model paraphrases it as "no issues found".
- **L-4, more visible since PR-B:** a delivered reply can now say "It still has 2 duplicate row(s)." followed by the analyzer's "None of the issues I check for were found." Both are true (the analyzer doesn't check exact duplicates), but together they read as a contradiction (live run 7 in `docs/reviews/verified-outcome-b-live-runs.md`).

**Acceptance criteria:**
- A requested report is either delivered, as the analysis findings on the final file clearly labelled as the report, or stated as not available. It's never silently absorbed.
- The analyzer reports exact duplicate rows, with a count, as a finding.
- Tests for both. A live run of "remove the duplicate rows and then give me a report" shows a report or an honest statement that there isn't one.

## 4. Backend API design, including F5 (per-turn output folders) and R6 (a crash still gets a reply)

**Status (2026-10-01):** design on `design/backend-api` (`docs/design/backend-api-design.md`). PR-1 (`feat/pipeline-failure-and-output-dir`, not merged, not yet independently reviewed) implements the pipeline half: R6 in the engine and Voice, F5's `SkillTask.output_dir` / `BuddyState.turn_output_dir`, `Context.to_dict` / `from_dict`, an explicit LLM request timeout, and `build_engine(confirmation_provider=...)`. See `docs/reviews/pipeline-failure-and-output-dir-impl.md`. PR-2 to PR-4 (the API itself) are open. The acceptance criteria below are met at the pipeline level only; the API-level tests are PR-2's.

**Scope:** a design doc, and then an implementation, for the API a frontend will use:
- upload a file;
- run a turn;
- get state (the turn's reply, steps and files);
- confirm or reject a Write/External-Effect step (replacing `NullConfirmationProvider`'s auto-approve);
- list files.

**Must include:**
- **F5:** outputs go in a per-turn folder, or otherwise never overwrite a file handed back in an earlier turn. Today names are unique within a turn only (`{stem}_{op}_{session}-task-{index}.xlsx`).
- **R6:** a verifier or skill that raises must still produce a reply that states nothing was confirmed. Today `core/engine.py` re-raises and the turn ends with no reply, and an intermediate file can be left on disk.

**Acceptance criteria:**
- The design doc is written to `docs/design/` and independently reviewed before implementation.
- Tests: two turns in one session never overwrite each other's outputs. A raising verifier gives an honest reply and leaves the original unchanged.
- Confirm/reject is tested for both answers.

## 5. Remaining deferred items from the reviews

Each item has its own acceptance criterion: fixed with a test, or explicitly closed as won't-fix with a reason.

- **R5 (Low)** (`chaining-wip-rereview.md`): `_plain_words` rewrites other file names that contain "spreadsheet." (`budget-spreadsheet.ods` becomes `budget-ods`).
- **R7 (Low):** a step discarded by its own well-formedness check is reported as "did not produce an output file". Hands sets no `failure_reason` on that path.
- **R8 (Low, not reachable today):** the strict "passed" rule compares `input_reference` strings exactly (`/` vs `\`, relative vs absolute).
- **L1** (`chaining-wip-review.md`): `Hands(step_verifier=None)` is still the default, so a `Hands` built without a verifier chains unverified outputs. Only `build_engine` passes one.
- **L3 / N3:** when nothing was delegated, the reply can say "I wasn't able to confirm that worked. No verifier has been configured." That's internal wording, and "that worked" is misleading when nothing was attempted.
- **L4:** an unregistered read-only capability stops the chain, because the permission tier is only known after a successful dispatch.
- **L6:** on the `PersonalityVoice` path the user hears "Your cleaned copy of X is ready." with no location.
- **M2 fallback / F4 real fix:** when the stated order can't be confirmed, the model's order still runs (now stated in the reply). Consider asking the user instead, before a non-commutative skill is added.
- **verified-outcome-a L1 (Low):** a Skill's self-reported near-duplicate string, including its "match probability 0.97", reaches a delivered reply after "I also noticed:". It's labelled "(self-reported, not independently re-checked)", but it's a number the verifier didn't measure. Either keep numbers out of self-reported findings, or state them as the Skill's claim. The property test in `tests/buddy/test_outcome.py` excludes analysis findings, so it doesn't catch this.
- **verified-outcome-a L2 (Low):** `turn_index` is always 0. `PersonalityVoice` seeds its phrase choice with `Context.buddy_reply_count()`, and nothing in `buddy/`, `core/` or `scripts/` calls `Context.record_buddy_reply`, so a session's seed never changes. Harmless to truthfulness; wire the call in when the engine records replies (item 4).
- **pipeline-failure-and-output-dir D1 (Low, not reachable through Voice):** `build_outcome_report` (`buddy/outcome.py`) doesn't look at `BuddyState.failure`. The design (Section 4.2) asks for it, but `buddy/outcome.py` was off limits for PR-1 (another branch is editing it). Voice returns the failure reply before any speaker or `build_outcome_report` runs, and `_verified_delivery` is false on a failure, so no reply path reaches it today. Add the guard once that branch has merged.
- **pipeline-failure-and-output-dir D2 (Low):** `Engine.run` skips every stage but Voice whenever `state.failure` is already set, including a stale one from a previous turn. `reset_transient()` clears it, and PR-2's turn procedure calls that first; a caller that reuses a state without it gets a failure reply for a turn that never ran.
- **pipeline-failure-and-output-dir D3 (product question for PR-2):** in a live two-turn run, "now remove the empty rows" ran on the original upload, not on the first turn's deduplicated file. The reply was accurate about its own file, but a user saying "now … too" may expect the changes to add up. This belongs with PR-2's multi-turn live run 7.
- **pipeline-failure-and-output-dir review M1 (Medium, PR-2 obligation):** when Voice raises, `Engine.run` re-raises and leaves `state.failure` as `None`, with `execution_result.outputs` naming a real file and `verification_result.passed` true. PR-2 must treat any raise from `engine.run` as a failed turn and discard the state (design 3.3 step 7), never call `delivered_files` on it, and test that. Recommended hardening: set `StageFailure("voice", …)` before the re-raise. See `docs/reviews/pipeline-failure-and-output-dir-review.md`.
- **pipeline-failure-and-output-dir review M2 (Medium, PR-2):** `Context.from_dict` raises `TypeError` and `AttributeError` as well as the documented `ValueError` and `KeyError`. It also silently accepts wrongly typed data: a string `uploaded_files` becomes one file per character, `active_file` doesn't have to be one of the uploads, and timestamps without a time zone are accepted. Validate when restoring `session.json`, or catch every exception there (design 7.2: "a clean error, not a crash").
- **pipeline-failure-and-output-dir review L3 (Low, pre-existing):** the writing skills put `task_id` (and so `session_id`) into the output name unchecked. A `../` session id made the dedupe skill overwrite an upload. Not reachable with server-generated `token_urlsafe` ids. Add a `resolve()` / `is_relative_to(output_dir)` check in `_build_output_path`.
- **pipeline-failure-and-output-dir review D-R1 (pre-existing, with item 2):** when Brain and Think fail (for example on a provider timeout), the reply is "None of the issues I check for were found. No changes were made to any file this turn." That's true, but it doesn't say the request wasn't understood or carried out, so a user who asked to remove duplicates may read it as "no duplicates found". Reproduced on `main`.
- **verified-outcome-a L3 (Low):** `_delivered_file_name` (`buddy/voice.py`) falls back to the *output's* name when no step has an `input_reference`, giving "Your cleaned copy of out.xlsx is ready." with the output presented as the original. Not reachable through `build_engine` (Hands always records steps), but `test_personality_voice_is_unaffected_by_a_failing_provider` enshrines it. Fix the fallback and the test together.

## 6. Decide the supported Python version

**Status (2026-10-01):** decided: 3.11 and newer (backend design, "Decisions (2026-09-30)" item 3). PR-1 (`feat/pipeline-failure-and-output-dir`) sets `requires-python = ">=3.11"` and the black, ruff and mypy targets, updates the README, and pins `setuptools<81` (a fresh 3.12+ install otherwise can't import `ydata_profiling`). The full suite passed on 3.11.15 and 3.13.14. Closes when PR-1 merges. Still open: CI that runs both versions (there is no CI yet).

`pyproject.toml` required Python 3.13 (`requires-python = ">=3.13"`), but cloud sessions default to Python 3.11.
