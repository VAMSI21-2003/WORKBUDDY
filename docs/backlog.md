# Backlog

Open work, in priority order. Each item follows the working method in `CLAUDE.md`: its own branch, an independent review for rule-touching changes, and live runs before merging.

## 1. The outcome of a successful turn comes from verifier data, never from the model (F2 / N1 / N2)

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
