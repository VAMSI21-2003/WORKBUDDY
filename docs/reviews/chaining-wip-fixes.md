# Fixes for the `chaining-wip` review

The review is `docs/reviews/chaining-wip-review.md`. This file covers what changed for each item, on branch `chaining-wip`. Nothing was merged to `main`.

## Must fix

### 1. B1: "Done" when nothing was delivered

**Approach:** Hands now records the step that stopped the chain, so Inspector sees a failing step. Separately, TemplateSpeaker checks whether anything was delivered before it chooses a reply.

**a) Hands (`buddy/hands.py`)**
- **Dispatch failure:** recorded as a `StepResult` with the request's capability, `status="failure"`, `skill=""`, and a plain-words `failure_reason` ("couldn't be run because no available skill can do it").
- **Declined confirmation:** recorded as a `StepResult` with `status="declined"` (new `DECLINED_STATUS` in `buddy/models.py`), the skill's name and tier, and `failure_reason` ("wasn't confirmed, so it didn't run").
- **Inspector:** `CompositeVerifier` fails a step that has a `failure_reason` and uses the reason as its finding. It doesn't run a verifier on it, because there's no Skill result to check.

**b) TemplateSpeaker (`buddy/voice.py`)**
- Routes on `_nothing_delivered` first. If the chain stopped, or no verified file came back, none of the "Done" branches can run.
- **Stopped chain:** a new `_stopped_message`. It:
  - opens with "I ran into a problem partway through.";
  - gives the failing step's reason in plain words;
  - ends with the existing stop sentence ("I stopped at the … step … no file was handed back; your original file is unchanged.").
- **Earlier steps' findings are left out**, because they describe an intermediate file that was never handed back.
- **Wording:** the reply is checked against `PersonalityVoice`'s completion-claim pattern. "I couldn't finish this" was rejected because "finish" matches that pattern.
- **`PersonalityVoice`:** its fallback (for a completion claim, an empty reply or a model failure) is `TemplateSpeaker`, so it inherits the fix.

**Tests**
- **Hands:** an earlier passing step, then a final step that fails dispatch. An earlier passing step, then a declined final step.
- **Voice:** both scenarios through `TemplateSpeaker`, and through `PersonalityVoice` with a completion-claim reply, an empty reply and a model failure. Each asserts:
  - no "Done";
  - no match for the completion-claim pattern;
  - the stop sentence is present;
  - no `spreadsheet.` id.
- **End to end:** "remove the duplicate rows and then give me a report" through `build_engine` with the real skills and a real `.xlsx`. It checks:
  - the `report` step is recorded as `failure`;
  - Inspector fails it;
  - the reply claims nothing;
  - the original is unchanged.

  It also runs through `PersonalityVoice` with two false model replies.

Reply in the review scenario, before and after:
> **Before:** "Done — nothing notable found; output has 2 row(s), 0 duplicate row(s) remaining. I stopped at the report step…"
>
> **After:** "Before anything else, here's what I noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these? I ran into a problem partway through. The report step: couldn't be run because no available skill can do it. I stopped at the report step and no file was handed back; your original file is unchanged."

### 2. M1: a failed final-file re-check failed the turn

**Approach:** re-check steps are now marked, Inspector records their failure without failing the turn, and every capability id is removed from reply text, wherever it appears.

**Changes**
- **Marking:** `StepResult.recheck` (new) is set by `Hands._recheck_final_file`, and `StepVerification.recheck` (new) mirrors it.
- **`CompositeVerifier`:**
  - a failed re-check gets the plain finding "the final file couldn't be re-checked after the changes";
  - `_aggregate_verdicts` leaves re-check verdicts out of `passed` and `confidence`, and still records them per step;
  - the `final_output` check ignores re-check steps.
- **Voice:**
  - `_findings_for_reply` now uses the `recheck` flag instead of "analysis ran twice". When a re-check ran, the original's analysis findings are dropped. The re-check's findings replace them only if it passed.
  - When it failed, `TemplateSpeaker` adds "I couldn't re-check the final file for anything else worth flagging after the changes." The reply still says "Done …" and "Saved as `…`".
  - `PersonalityVoice`'s prompt says the file couldn't be re-checked but was still verified and handed back. The existing "Your cleaned copy of X is ready." safeguard still applies.
- **Capability ids anywhere:** `_plain_words` replaces every capability id in a known domain (e.g. `spreadsheet.*`, including unregistered ones like `spreadsheet.report`) with plain words, wherever it appears in the text. It skips file names such as `spreadsheet.xlsx`. It's applied to:
  - every finding, through `_strip_finding_tags`;
  - the whole `TemplateSpeaker` reply;
  - the warnings, errors and recommendations in the `PersonalityVoice` prompt;
  - the model's reply.

  The source string in `SpreadsheetAnalysisVerifier` is now "the analysis reported failure".

**Tests**
- **Inspector:** a failed re-check doesn't fail the turn.
- **Voice:** the reply and the prompt report a failed re-check and name the file; ids are replaced inside findings.
- **End to end:** the review's scenario, with analyze failing only on its second call. The turn passes, the reply starts with "Done", includes the re-check sentence and "Saved as", and contains no `spreadsheet.` or `[`. A `PersonalityVoice` variant checks that the file sentence is appended.

Reply now:
> "Done — output has 3 row(s), 0 fully-empty row(s) remaining; output has 2 row(s), 0 duplicate row(s) remaining; final file: … Saved as `report_no_empty_rows_…_deduplicated_….xlsx`. I couldn't re-check the final file for anything else worth flagging after the changes."

### 3. L2: "I did the work" with no verification

**Approach:** the no-verification case now goes through `_nothing_delivered` as well.

- **Nothing delivered** (e.g. only read-only steps ran): the reply is "I wasn't able to confirm any result. No changes were made to any file this turn." With errors, it's "I ran into a problem: …" plus the same sentence.
- **A change was delivered:** "I made the changes, but wasn't able to confirm the result."
- **Test:** only a read-only step ran, with no verification.

### 4. One definition of "passed" in `voice.py`

**Approach:** the strict rule. `_action_passed_verification` is now built on `_step_passed`.

**Rule:** a step passed only if its Skill reported `success` *and* Inspector has a passing verdict for that same step. A step with no verdict, or with no Inspector at all, did not pass. If Inspector gave no per-step breakdown (`NullVerifier`, or a single non-composite verifier), its overall `passed` is used for every step.

**Why strict:** the lenient rule ("passed unless a verdict failed it") is what let an unchecked step count as done. Voice exists to avoid claiming what wasn't confirmed. Now the prompt's "(passed)" labels, the "requested action was NOT performed" line and the no-changes rule all agree.

**Matching steps to verdicts:** a verdict is matched on capability, on the `recheck` flag, and on `input_reference` when both sides recorded one. So the analysis of the original and its re-check on the final file are never mixed up. `StepVerification.input_reference` is new and filled in by `CompositeVerifier`.

**Tests:** an unchecked step doesn't pass; the original analysis and its re-check are judged separately.

### 5. M2 guard: repeated phrases

**Approach:** in `_order_stated_by_user` (`buddy/planner.py`), each `source_phrase` must appear exactly once in the message (`str.count == 1`). Otherwise it logs a warning and keeps the model's order.

**Test:** "for the sales rows, remove duplicates, then drop the empty rows", with the model quoting "rows" for the empty-rows step. The model's (correct) order is kept. Before the fix, it was reversed.

### 6. M3 guard: output that can't feed the chain

**Approach:** if a passing chain step doesn't produce exactly one file of its input's type, the chain stops. It no longer carries on from the old input.

**Details**
- The step's `StepResult` becomes `status="failure"` with a `failure_reason` ("produced N file(s) instead of one .xlsx, so its result couldn't be carried forward").
- An error is recorded, later steps are skipped, and nothing is handed back.
- This applies to the last step too: handing back the previous file would claim a change that isn't in it.

**Tests**
- The old test that asserted the chain *continued* on a `.png` output now asserts that it stops, parametrized over a different type, no output, and two outputs.
- A separate test covers the last step.

### 7. L5: marking a file analyzed

**Approach:** `Inspector._mark_analyzed_files` now also requires `StepVerification.input_reference == active_file`, so a passing re-check of the final file never marks the original.

**Tests**
- The existing "marks analyzed" test now sets `input_reference`, as real `CompositeVerifier` output does.
- Two new tests: only the re-check passed, and a verdict with no `input_reference`.

## Cleanup

### 8. Spike script and misnamed test

- **`scripts/spike_tools_test.py`:** deleted.
  - Some comments still cite it as the source of the Splink thresholds: `skills/excel/analyze.py`, `tests/skills/excel/test_analyze.py`, `tests/buddy/test_engine_factory.py` and two design docs.
  - They're provenance notes, not imports. The script is still in git history (e.g. `ed08226`).
- **`test_personality_voice_returns_the_models_reply_verbatim_when_successful`:** renamed to `test_personality_voice_returns_the_models_reply_stripped_when_it_claims_nothing`, which is what it tests: nothing was delivered, and a reply with no completion claim passes through, with surrounding whitespace removed.

### 9. Docs

**Superseded notes added:**
- `docs/design/multi-step-planner-design.md`, after the `outputs[0]` paragraph and after the "per-item, non-fatal" paragraph.
- `docs/design/proactive-analysis-design.md`: the "reads the original through `active_file`" line, and "one target shared by every step".
- `docs/design/hands-verifier-review.md`: section 5, whose fixture says "not chained". The note also covers section 4's code snapshot.

**New section:** "In-loop verification, the final-file re-check, and the Voice safeguards", at the end of `docs/design/multi-step-planner-design.md`.

## Deferred

M2 fallback enforcement, L1, L3, L4 and L6 are appended to the review under "Deferred". L4 is partly helped: an unregistered read-only step still stops the chain, but it's now recorded, so Inspector and the reply report it correctly.

## Existing tests changed, and why

| Test | Change | Why |
|---|---|---|
| `test_hands_records_no_step_result_when_dispatch_fails` / `…_when_confirmation_is_declined` | Renamed to `…records_a_failed…` / `…records_a_declined…`; they now assert the recorded step | B1a requires a `StepResult` for these |
| `test_hands_does_not_chain_an_output_of_a_different_file_type` | Replaced by the parametrized stop test | M3: the chain must stop, not continue |
| `_assert_stopped_at_first_step` | Handles the M3 reason wording; checks the stopping step is recorded | B1a / M3 |
| `test_full_slice_decomposes_a_compound_request_into_multiple_steps` | Expects `("spreadsheet.column_check", "failure")` in the steps | B1a |
| `test_template_speaker_names_the_failed_step_and_what_never_ran` | Opening changed from "I wasn't able to confirm that worked." to "I ran into a problem partway through."; asserts no "Done" | B1b's stopped reply |
| `_delivered_request` fixture (test_voice) | The final-file analysis step and its verdict carry `recheck=True` and `input_reference` | Matches what Hands and Inspector now record (item 2/4) |
| `test_inspector_marks_analyzed_from_the_steps_own_verdict…` | The verdict carries `input_reference="report.xlsx"` | L5 |

## Results

**Test suite:** 449 passed (previously 417): 32 new tests, and the 7 changed tests listed above. Ruff reports the same 27 findings as before these changes; none are new.

**Live runs:** only 6 of the 10 are valid. All used Groq, focus mode and `PersonalityVoice`. After run B1, Groq's daily token limit for `openai/gpt-oss-120b` ran out (200,000 tokens/day; HTTP 429). Runs B2–B5 then failed at every LLM stage (Brain, Think, Planner and Voice). There's no Anthropic key in this environment to fall back on. **B2–B5 need rerunning once the Groq quota resets.**

- **A** = "remove the empty rows and then remove the duplicate rows"
- **B** = "remove the duplicate rows and then remove the empty rows"
- **One file with both changes:** the final file was checked independently (3 rows, 0 duplicates, 0 empty rows), and the original file's hash was unchanged.

| Run | clarification_required | Plan → executed | One file with both changes | Reply | False completion claim |
|---|---|---|---|---|---|
| A1 | False | analyze, empty rows, dedupe → same, then analyze re-check on the final file | yes (3 rows, 0 dup, 0 empty; original unchanged) | "Cleaned manual_report.xlsx returned; 3 rows, no empty or duplicate rows, nothing notable." | no |
| A2 | False | same | yes (same) | "Done — manual_report.xlsx now 3 rows, no empty or duplicate rows." | no |
| A3 | False | same | yes (same) | "Returned cleaned manual_report.xlsx: 3 rows, no empty or duplicate rows." | no |
| A4 | False | same | yes (same) | "Manual_report.xlsx cleaned, 3 rows, no empty or duplicate rows." | no |
| A5 | False | same | yes (same) | "Manual_report.xlsx cleaned; 3 rows left, no empties or duplicates—nothing notable found." | no |
| B1 | False | analyze, dedupe, empty rows → same, then analyze re-check on the final file | yes (same) | "Done — manual_report.xlsx cleaned, 3 rows, no duplicates or empty rows." | no |
| B2–B5 | *invalid (429)* | analyze only (the planner's call failed) | no file | "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn." (the fallback reply, because Voice's model call also failed) | no |

- **Zero false completion claims in all 10**, including the four failed runs.
  - In every valid run, a file was delivered and verified, so completion wording was accurate.
  - In B2–B5, nothing was delivered, and the reply says so.
- **Order:** every valid run followed the user's stated order.
- **Original file:** unchanged in all 10 runs.
- **Safeguards:** neither the completion-claim check nor the file-mention addition had to act in the valid runs. Every reply is the model's own wording and names the file.
- **B2–B5 aren't evidence about the model.** They do show the degraded path live: with every LLM stage failing, the reply still claimed nothing.
