# Review: `chaining-wip`

Review only. No code was changed and nothing was committed during the review.

## What was reviewed

State of the repository when the review was written:

```
$ git status
On branch chaining-wip
nothing to commit, working tree clean

$ git log --oneline -5
ed08226 fix: no false completion claims, produced file always named, findings describe the final file
53db0b3 wip: step chaining + duplicate-analyze filter (not ready: false completion claim in Voice)
9bb03bc fix: Think now knows Buddy executes skills itself and their real limits
a5552a4 fix: force UTF-8 console output in run_dedupe_manually.py so Windows cp1252 consoles don't crash on LLM Unicode
12d4489 fix: PersonalityVoice no longer fabricates outcomes or leaks internal capability labels
```

- **Scope:** the two branch commits, `53db0b3` and `ed08226`, on top of `main` at `9bb03bc`.
- **No uncommitted work:** the working tree is clean, and `git diff main` is identical to `git diff main...chaining-wip` (17 files, +3181/−151).
- **Disclosure:** this code was written earlier in the same conversation that produced this review. None of the earlier claims about it were taken as given. Every finding below was confirmed by running the code, as described in each scenario.
- **Line numbers** refer to the files at `ed08226`.

## Summary

| ID | Severity | Finding |
|---|---|---|
| B1 | **Blocker** | The reply says "Done" when the last chain step can't be dispatched or isn't confirmed, and nothing was delivered |
| M1 | Medium | A failed re-check of the final file flips Inspector to failed, leaks a raw capability id, and drops the file name |
| M2 | Medium | The order check can silently reorder steps wrongly, and keeps a wrong order when the message uses an inverting word |
| M3 | Medium (not reachable today) | A step whose output doesn't feed the chain is dropped from the final file, while the prompt says every change was applied |
| L1 | Low | `Hands(step_verifier=None)` by default lets unverified outputs feed the next step |
| L2 | Low | With no verification, `TemplateSpeaker` says "I did the work" when only read-only steps ran |
| L3 | Low | The reply when nothing was delegated wrongly says "No verifier has been configured" |
| L4 | Low | An unregistered read-only capability stops the chain |
| L5 | Low | The original is marked "analyzed" when only the final-file re-check passed |
| L6 | Low | On the `PersonalityVoice` path, the user is never told where the output file is |

**Test suite:** 417 passed, run by the reviewer on `chaining-wip` at `ed08226`.

**Recommendation:** don't merge until B1 and M1, including M1's leaked capability id, are fixed. M2 and M3 are real, but M3 isn't reachable with the current skills.

---

## Findings

### B1 (Blocker): "Done" when nothing was delivered

**Where:**
- `buddy/hands.py:210-220`: a dispatch failure stops the chain but records **no** `StepResult`.
- `buddy/hands.py:238-254`: a declined confirmation also stops the chain and records no `StepResult`.
- `buddy/voice.py:336-341`: `TemplateSpeaker.__call__` chooses between its replies using `_no_changes_made` and `verification_result.passed`. It never consults `_nothing_delivered` (`buddy/voice.py:174`), even though `stopped_at` is set.
- `buddy/voice.py:788-793`: `PersonalityVoice`'s completion check falls back to `TemplateSpeaker`. So do the model-failure and empty-reply paths (`buddy/voice.py:777`, `782`).

**Scenario** (run end to end through `build_engine` with real skills and a real `.xlsx`; only the LLM was faked). The message is "remove the duplicate rows and then give me a report". The plan is `spreadsheet.deduplicate`, then `spreadsheet.report`, which has no registered skill.

- **Hands:**
  - `success=False`, `outputs=()`, `stopped_at='spreadsheet.report'`
  - `errors=("No registered skill declares support for 'spreadsheet.report'.",)`
  - `steps=[(analyze, success, read), (deduplicate, success, write)]`. There's no entry for the report step.
- **Inspector:** `passed=True`, with `steps=[(analyze, True), (deduplicate, True)]`. It never sees the step that failed.
- **Reply from the default `TemplateSpeaker`:**
  > "Done — nothing notable found; output has 2 row(s), 0 duplicate row(s) remaining. I stopped at the report step and no file was handed back; your original file is unchanged."
- **`PersonalityVoice` with a model reply of "All done — duplicates removed.":** the completion check correctly rejects that reply, then substitutes the fallback, which is the same "Done —…" text. The safeguard swaps one false claim for another.
- **`PersonalityVoice` with an honest model reply:** the honest reply goes through. That's the only case that avoids the false claim.
- The original file was unchanged in all three runs.

**Why:** "changes were made" (`_action_passed_verification`) is true because step 1 passed. Nothing on the `TemplateSpeaker` path checks that `outputs == ()` and `stopped_at` is set. When the step that stopped the chain left no `StepResult`, Inspector has no failing verdict to route on.

**Why the tests missed it:** all the new stop tests stop at the *first* step (`_assert_stopped_at_first_step` in `tests/buddy/test_hands.py`). No test combines an earlier passing step with a final step that fails without a `StepResult`.

---

### M1 (Medium): a failed final-file re-check fails a correct, delivered result

**Where:**
- `buddy/hands.py:438-449`: a failed re-check is added to `ExecutionResult.steps` with `status="failure"`.
- `buddy/inspector.py` `CompositeVerifier.__call__`: checks that step like any other, and `SpreadsheetAnalysisVerifier` returns `passed=False` with the finding "spreadsheet.analyze reported failure".
- `_aggregate_verdicts` then fails the whole turn.

**Scenario** (real pipeline; `SpreadsheetAnalysisSkill.execute` patched to fail only on its second call, the re-check). The message is "remove the empty rows and then remove the duplicate rows".

- **Hands:** `success=True`, 1 output, `errors=()`, and `warnings=('could not re-check the final file with spreadsheet.analyze',)`. Hands handled it correctly.
- **Inspector:** `passed=False`. Every chain step passes and `final_output` passes; only the re-check fails.
- **Reply:**
  > "I wasn't able to confirm that worked. Output has 4 row(s), 0 fully-empty row(s) remaining; output has 3 row(s), 0 duplicate row(s) remaining; final file: output has 3 row(s), 0 fully-empty row(s) remaining; final file: output has 3 row(s), 0 duplicate row(s) remaining; spreadsheet.analyze reported failure."

**Three problems in that reply:**
1. It contradicts a file that was delivered and verified.
2. It leaks the raw capability id "spreadsheet.analyze". The id is inside the finding text, not a leading tag, so `_strip_finding_tags` doesn't remove it.
3. It never mentions the delivered file, because the failure message has no "Saved as".

---

### M2 (Medium): the order check can silently reorder wrongly, and keeps a wrong order on fallback

**Where:** `buddy/planner.py:329` (`_order_stated_by_user`), `:353` (the inverting-word check), `:364` (`message.find`, which returns the first occurrence).

**How it works:**
- If every step's `source_phrase` appears word for word (ignoring case) in the message, steps are sorted by where their phrase first appears.
- It backs off and keeps the model's order when:
  - a phrase is missing or not found;
  - two phrases overlap;
  - the message contains after/before/once/until/prior to/following/preceding, or a "first" that isn't the opening word.
- When it backs off, it logs a WARNING or INFO line and nothing else enforces the order.

**Scenario 1, a wrong reorder** (tested by calling `_order_stated_by_user` directly).
- Message: "for the sales rows, remove duplicates, then drop the empty rows".
- The model lists `[dedupe, empty rows]`, which is correct, but quotes only "rows" for the empty-rows step.
- "rows" first appears in "sales rows", so the result is `[empty rows, dedupe]`: the correct plan is reversed, with no warning.

**Scenario 2, a wrong order kept** (tested).
- Message: "remove the duplicate rows before you remove the empty rows". The model lists empty rows first, which is wrong.
- "before" triggers the fallback, so `_order_stated_by_user` returns `None` and the model's wrong order runs.
- The only trace is a log line. The user isn't told.

**Reliability:** it can only be as reliable as the model's quoting. Generic or short phrases can reorder wrongly, and inverting wording has no enforcement at all.

---

### M3 (Medium, not reachable today): a step whose output doesn't feed the chain is left out of the final file

**Where:**
- `buddy/hands.py:350-353`: if a chain step passes but its output isn't exactly one file of the same type, it doesn't become the next step's input.
- `buddy/inspector.py:706`: the final-file check doesn't run when steps don't read each other's output.
- `buddy/voice.py:884`: the prompt says "a cleaned copy of X, **with every change above applied**".

**Scenario:** a future file-changing skill produces two `.xlsx` outputs, or none.
- Its outputs don't feed the next step, so the next step reads the previous input, possibly the original upload.
- The final file therefore lacks that skill's change.
- The final-file check doesn't run, so nothing catches it.
- Voice tells the model every change was applied, which is false.

**Exposure:** no current skill produces zero or two `.xlsx` outputs on success, so this can't happen today. It becomes possible as soon as one is added.

---

### L1 (Low): no verification between steps by default

**Where:** `buddy/hands.py:121`, `step_verifier: Verifier | None = None`.

**Scenario:** any code that builds `Hands` without a `step_verifier`, as the unit tests do, chains each step's output into the next with no verification. Only `buddy/engine_factory.py` passes the shared `CompositeVerifier`.

---

### L2 (Low): "I did the work" when verification is missing

**Where:** `buddy/voice.py:532`, in `TemplateSpeaker._describe_without_verification`.

**Scenario** (tested). With an `ExecutionResult` whose only step is a successful read-only analyze step, and `verification_result=None`, the reply is "I did the work, but wasn't able to confirm the result." None of the requested work was done. Inspector always runs in `build_engine`, so this only affects other ways of building the pipeline. `PersonalityVoice` falls back to the same text.

---

### L3 (Low): wrong reply wording when nothing was delegated

**Where:** `CompositeVerifier` falls back to `NullVerifier` when there are no steps. The reply is built in `TemplateSpeaker._no_changes_message` (`buddy/voice.py`).

**Scenario** (tested). With an `ExecutionResult` that has no steps (Hands reported "nothing to execute"), the reply is "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn." A verifier *is* configured, so the middle sentence is wrong, and "confirm that worked" suggests something was attempted. It isn't a completion claim.

---

### L4 (Low): an unregistered read-only step stops the chain

**Where:** `buddy/hands.py:210-220`. The permission tier is only known after a successful dispatch.

**Scenario:** if the analyze skill isn't registered, the proactive analyze step fails to dispatch and is treated as a chain stop. All of the user's file-changing steps are skipped, even though a read-only step is meant to sit outside the chain.

---

### L5 (Low): the original is marked "analyzed" from the final-file re-check

**Where:** `buddy/inspector.py:839`, in `Inspector._mark_analyzed_files`: `any(step.capability == "spreadsheet.analyze" and step.passed ...)`.

**Scenario:** analysis of the original fails (reported failure, not malformed), the chain completes, and the re-check on the final file passes. The original upload (`active_file`) is marked as analyzed based on a check of a different file, so it won't be analyzed again next turn.

---

### L6 (Low): on the `PersonalityVoice` path, the user isn't told where the file is

**Where:**
- `buddy/voice.py:884`: the prompt now names only "a cleaned copy of X". The generated output name was deliberately removed.
- The appended sentence "Your cleaned copy of X is ready." gives no location either.

**Scenario:** a chain succeeds, and the user hears the file is ready but not where it is. `TemplateSpeaker` still gives the location ("Saved as `…`").

---

## Verdict on each review item

### 1. Can Voice claim completion when no action step passed verification?
**Yes. Fails.**
- **Stopped chain:** B1.
- **Verification missing:** L2.
- **Model failure or empty reply:** falls back to `TemplateSpeaker`, so it inherits B1.
- **Empty steps:** no completion claim found; the wording problem is L3.
- **Read-only-only turn (the live B4 case):** correct. The prompt states "No changes were made to any file this turn."; `TemplateSpeaker` ends with that sentence; and a model completion claim is rejected.

### 2. Can the original upload be modified, or a failed or unverified step's output feed the next step?
- **Original modified:** none found. Every skill opens the upload read-only (`# READ-ONLY open of the upload`) and writes only to a new path under `workspace/outputs/`. The original was unchanged in every run.
- **Failed output feeding forward:** none found. Skill failure, a malformed result and failed in-loop verification all stop the chain before `chain_input` is updated (`buddy/hands.py:302-305`, `338-348`).
- **Unverified output feeding forward:** yes, when `step_verifier=None` (L1). Related: M3.

### 3. Order enforcement
**Partially works.** It follows the order in the user's words when the model quotes them exactly and the message has no inverting word. It can silently reorder wrongly with a generic phrase, and when it backs off it keeps the model's order with no enforcement. See M2.

### 4. Existing tests that were modified
Every removed or changed line in an existing test was checked:

| Test (file:line) | Change | Verdict |
|---|---|---|
| `test_engine_factory.py:259-276`, `test_full_slice_decomposes_a_compound_request_into_multiple_steps` | 3 per-step errors became a stop + 2 skipped steps + `outputs == ()` | **Justified** by the stop-on-failure requirement; the new assertions are stricter |
| `test_engine_factory.py:278-282`, `_write_fixture_with_duplicates_and_an_empty_row` | docstring | Justified, comment only |
| `test_engine_factory.py:360` | steps 3 → 4 | **Justified** by the final-file re-check |
| `test_engine_factory.py:367-370` | outputs 2 → 1 | **Justified** by chaining; replaced with chained-input, final-file (0 duplicates / 0 empty) and `final_output` assertions |
| `test_engine_factory.py:409-412` | "Before anything else" asserted present → asserted absent | **Justified** by the stale-findings fix, plus new "missing" absent / "nothing notable found" present checks |
| `test_hands.py:6` | import added | Justified |
| `test_voice.py:258`, `312` | a dedupe `StepResult` added to `execution_result.steps` | **Justified**: the old fixtures had dedupe in the verification results but not in the execution steps, which real Hands never produces. It does leave no test for a completed turn with a missing execution entry, the gap behind B1 |
| `test_voice.py:394`, `399`, `test_personality_voice_returns_the_models_reply_verbatim_when_successful` | reply text changed from "All clean…" to a neutral sentence | **Justified** (that text is now correctly rejected), but the test name is now wrong: its request delivers nothing, so it no longer covers a successful turn |
| `test_voice.py:415-434`, `test_personality_voice_prompt_contains_only_understanding_execution_and_verification_facts` | the generated-file-name assertion replaced by "a cleaned copy of report.xlsx", plus an assertion that the generated name is absent | **Justified** by design; the source of L6 |

**Verdict:** none of the modified tests hides a regression. Together, though, they leave the B1 case untested.

### 5. The final-file re-check in Hands
- **Can it run a non-READ skill?** None found. The tier is checked before `execute` (`buddy/hands.py:432-433`).
- **Can it change a file?** None found. The analyze skill is READ tier and never writes a file (`skills/excel/analyze.py:308`, `outputs=[]`).
- **Can it turn a successful chain into a failure?** It never changes Hands' `success`: re-check failures are warnings. **But yes at the Inspector and reply level** (M1).

### 6. Dead code, duplicated logic, and out-of-date docs
- **Dead code:** none found.
- **Duplicated or inconsistent logic:**
  - Two conflicting definitions of "passed" in `buddy/voice.py`. `_step_passed` (line 78) passes a step unless a verdict fails it. `_action_passed_verification` (line 134) requires a verdict that passes it. The prompt's "(passed)" labels and the no-changes rule can disagree.
  - `TemplateSpeaker` works out whether anything was delivered on its own instead of using `_nothing_delivered`. This is the root cause of B1.
  - `"spreadsheet.analyze"` is hardcoded in 4 places: `buddy/planner.py:247`, `buddy/voice.py:23`, `buddy/inspector.py:840` and `buddy/engine_factory.py:130`.
- **Out-of-date docs:**
  - `docs/design/multi-step-planner-design.md:87-88` (keying off `outputs[0]`) and `:226` (per-step errors that don't stop anything) have no superseded notes.
  - `docs/design/proactive-analysis-design.md:425` (analysis verification reads the original through `active_file`) and `:728` (one target shared by every step).
  - `docs/design/hands-verifier-review.md:342-344`, committed on this branch, already says "not chained".
  - Nothing documents the in-loop verification, the final-file re-check, or the Voice safeguards (the completion check and the file-mention sentence).
- **Committed without review:** `scripts/spike_tools_test.py` (its own docstring says "Safe to delete"), plus `docs/design/enterprise-readiness-notes.md` and `docs/design/hands-verifier-review.md`, all included in the WIP commit `53db0b3`.

### 7. Full suite
**417 passed**, run by the reviewer on `chaining-wip` at `ed08226`. That matches the expected count.

---

## Deferred

These were reviewed and deliberately not fixed in the follow-up (`docs/reviews/chaining-wip-fixes.md`). Each is still open.

- **M2, fallback enforcement.** The ambiguity guard was fixed: steps are reordered only when every `source_phrase` appears exactly once in the message. What's deferred is what happens when the check backs off. With inverting wording ("before", "after", a late "first"), or a phrase that's missing, paraphrased, repeated or overlapping, the model's order still runs unchecked, and only a log line records it. The user isn't told.
- **L1.** `Hands(step_verifier=None)` is still the default, so code that builds `Hands` without a verifier chains unverified outputs. Only `build_engine` passes one.
- **L3.** When nothing was delegated, the reply still says "No verifier has been configured" (from `NullVerifier`, the `CompositeVerifier` fallback for an empty step list), which is wrong wording. It isn't a completion claim.
- **L4.** An unregistered read-only capability still stops the chain, because the permission tier is only known after a successful dispatch. Note that the failing step is now recorded as a `StepResult` (from the B1 fix), so Inspector and the reply report it correctly.
- **L6.** On the `PersonalityVoice` path, the user still hears "Your cleaned copy of X is ready." with no location. `TemplateSpeaker` still gives "Saved as `…`".
