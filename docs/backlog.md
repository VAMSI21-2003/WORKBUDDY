# Backlog

Open work, in priority order. Each item follows the working method in `CLAUDE.md`: its own branch, an independent review for rule-touching changes, and live runs before merging.

## 1. The outcome of a successful turn comes from verifier data, never from the model (F2 / N1 / N2)

**Status (2026-09-30):** design approved (`docs/design/verified-outcome-design.md`, "Decisions (2026-09-30)"). PR-A (`feat/verified-outcome-a`, not merged) renders the delivered outcome by code from verifier measurements, removes the Voice model call, closes N2, and makes the order sentence reply-level for `PersonalityVoice` (the F4 item below). **Still open for PR-B:** the ledger of requested actions (Brain's `requested_actions`, the mention detector), `NOT_DONE` / `DONE_UNREQUESTED` lines and residual counts, which close N1, C1–C3 and N5. Until PR-B, a `multi_step_request` turn gets no "Done —" lead word.

**PR-B follow-up:** reintroduce target-specific "nothing to remove" lines (the removed `friend.opener.nothing_to_remove`) only when the ledger confirms every requested action ran. PR-A dropped that opener because its predicate saw only the steps that ran: with "remove duplicates then remove empty rows" and a planner that returns only dedupe, a file with 0 duplicates and 1 empty row got "nothing to remove" while the empty row was still there.

**PR-B follow-up (closers):** reintroduce completion-implying closers ("Anything else?", "What's next on the list?") only when the ledger confirms every requested action is DONE. PR-A removed `friend.closer.anything_else`, `friend.closer.whats_next` and `focus.closer.anything_else` (review M1): their predicate only checked that no analysis findings were open, so they appeared on turns where a requested action never ran, and "What's next?" presupposes the current item is finished.

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

## 1a. M3 (high): multi-sheet workbooks lose every sheet after the first, while the reply says "cleaned copy"

**Source:** `docs/reviews/verified-outcome-a-review.md`, M3. It exists on `main` too; it isn't introduced by PR-A. It's a rule 1 breach, so it comes right after PR-B.

**Problem:** `skills/excel/dedupe.py` and `skills/excel/remove_empty_rows.py` read only the first sheet (`pd.read_excel(input_path)`) and write one sheet named `Sheet1`. For a workbook with sheets `Q1` and `Q2`, the delivered file has one sheet, and the reply says "Your cleaned copy of report.xlsx is ready." and "The copy has 2 row(s)…". Nothing tells the user that `Q2` was dropped, and no verifier notices.

**Acceptance criteria:**
- Either the skills keep every sheet (and clean each one, or only the one the user named), or they refuse a multi-sheet workbook and the reply says so. A delivered file never silently loses a sheet.
- The verifier compares the input's and output's sheet lists, so a dropped sheet fails verification whatever the skill did.
- Tests through `build_engine`: a two-sheet workbook, all three speakers, single step and chain. The delivered file's sheet names match the original's (or nothing is delivered and the reply says why), and no reply calls a one-sheet file "the copy" of a two-sheet workbook.
- Live runs before merging, per `CLAUDE.md`.

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
