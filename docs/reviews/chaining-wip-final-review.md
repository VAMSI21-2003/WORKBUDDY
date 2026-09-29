# Final review: `chaining-wip` (independent)

Review only. No code was changed and nothing was committed. This file is the only file written.

> **Reviewer independence.** I have no memory of writing or reviewing this code, and no conversational memory of this branch. One disclosure: my auto-memory index had a one-line pointer to an earlier session's notes on this branch (the chaining rules and the two fix commits). I did not open those notes. I also did not read anything in `docs/reviews/` until my own findings below were written. Section 5 compares them against those docs afterwards.

**Method.** Every finding comes from a scenario I ran. Each one went through the real `build_engine` pipeline, with real skills and real `.xlsx` files on disk. Only the LLM was faked, by a provider that routes on each stage's system prompt (Brain, Think, Planner, Voice). The scripts are in the session scratchpad, not in the repo.

## 1. Repository state

```
$ git status
On branch chaining-wip
nothing to commit, working tree clean

$ git log --oneline -8
570e271 fix: address chaining-wip re-review (R1/R3 structural Voice guard, R2 allowlist, R4)
71ec26d fix: address chaining-wip review (B1, M1, L2, M2/M3 guards, L5, one "passed" rule)
ed08226 fix: no false completion claims, produced file always named, findings describe the final file
53db0b3 wip: step chaining + duplicate-analyze filter (not ready: false completion claim in Voice)
9bb03bc fix: Think now knows Buddy executes skills itself and their real limits
a5552a4 fix: force UTF-8 console output in run_dedupe_manually.py so Windows cp1252 consoles don't crash on LLM Unicode
12d4489 fix: PersonalityVoice no longer fabricates outcomes or leaks internal capability labels
048451c fix: replace deprecated Groq model default with openai/gpt-oss-120b
```

**Everything is committed.** Line numbers below refer to `570e271`.

## 2. Summary

| ID | Severity | Rule | Finding |
|---|---|---|---|
| F1 | **High** | 1 | An action request that Brain labels `unclear`, `None` or any unexpected string is treated as "plain conversation". The model then phrases the reply, and only the word list stands between it and a false completion claim. The same R1 phrasings that fixes-2 says are blocked still reach the user. |
| F2 | **High** (lower likelihood) | 1 | On a delivered, verified turn, nothing checks that every requested action ran. If the planner drops a requested step, the reply is "Done", and a model reply claiming the dropped step goes through unchanged. |
| F3 | Medium | (regression) | A single-step dedupe on a `.xls` upload now always fails, because a `.xlsx` output "can't feed the chain". It works on `main`. |
| F4 | Medium | order | The executed order can still silently contradict the stated order ("and after that", "once that's done", "afterwards", "before you X, Y", paraphrased phrases). The reply never mentions it. |
| F5 | Low (exists on `main`) | 2-adjacent | Output paths are `{stem}_{op}_{session}-task-{index}.xlsx`. A later turn silently overwrites a file handed back earlier in the same session. |

**Rule 2 (the original is never modified):** held in every scenario. I compared the original's SHA-256 before and after about 40 runs, including stopped chains, failed steps and multi-turn sessions, and it never changed. F5 overwrites a *previously delivered* file, not the upload.

**Unverified or failed output feeding the next step:** not reproduced. `build_engine` wires `step_verifier` (`buddy/engine_factory.py:150-154`). An undispatchable step, a failed step, a failed verification or a non-feeding output each stops the chain, and nothing is handed back (scenario E below). `Hands(step_verifier=None)` is still the class default (L1, deferred by the earlier reviews), but no production caller uses it.

**Test suite:** `python -m pytest -q` gives **512 passed**, 16 warnings. That matches the expected 512.

---

## 3. Findings

### F1 (High): "plain conversation" is decided from an intent that misclassified or failed action requests also produce

**Where:**
- `buddy/voice.py:285`: `_ACTION_INTENTS = {"remove_duplicate_rows", "multi_step_request"}`.
- `buddy/voice.py:299` and `:325-326`: any other intent, with only READ steps, means "no action requested or attempted", so the model may phrase the turn.
- `buddy/voice.py:984`: on that path, the only guard is `_COMPLETION_CLAIM`, a word list.

**Every intent the Brain can actually emit:**

| Source | Intent | In `_ACTION_INTENTS`? |
|---|---|---|
| `ClaudeInterpreter`, the prompt vocabulary (`buddy/brain.py:196-198`) | `remove_duplicate_rows` | yes |
| same | `multi_step_request` | yes |
| same | `unclear` | **no** |
| `ClaudeInterpreter`, provider exception (`brain.py:256-262`) | `None` | **no** |
| `ClaudeInterpreter`, non-JSON or wrongly shaped reply (`brain.py:307-331`) | `None` | **no** |
| `ClaudeInterpreter`, any other string the model returns (`brain.py:317`; `data.get("intent")` is **not validated** against the vocabulary) | e.g. `remove_empty_rows`, `deduplicate`, `Remove_Duplicate_Rows` | **no** |
| `NullInterpreter` (`brain.py:82-86`) | `None` | **no** |

There is no rule-based interpreter. `NullInterpreter` and `ClaudeInterpreter` are the only two, and `build_engine` uses `ClaudeInterpreter`.

**Why this reaches real users:**
- The Brain vocabulary has **no conversational intent**. The "plain conversation" branch is therefore only ever reached through `unclear` or `None`, which is exactly what an action request becomes when Brain fails or can't classify it.
- A plain "remove the empty rows" request has **no intent at all**, even though `spreadsheet.remove_empty_rows` is a registered skill. It can only come out as `unclear`, or as an unvalidated invented string.
- The earlier live runs hit 429 rate limits mid-turn (`chaining-wip-fixes.md:175`). A 429 on the Brain call alone gives intent `None`.

**Scenario A (run 18 times):**
- Message: "remove the duplicate rows please", on a real `.xlsx` with duplicates and one empty row.
- Brain intent: each of `unclear`, `None`, `remove_empty_rows`, `Remove_Duplicate_Rows`, `deduplicate`, `remove_duplicates`.
- Speaker: `PersonalityVoice`, tried with each of the three model replies below.
- In every case, only `spreadsheet.analyze` (READ) ran, `outputs == ()`, and the original was unchanged.

| Model reply | What reached the user (all 6 intents) |
|---|---|
| "All done — duplicates removed." | Replaced by the factual template ("…No changes were made to any file this turn."), because the word list catches "done" and "removed". |
| "Duplicates are gone and your sheet is spotless now." | **Passed through verbatim.** |
| "Got rid of those pesky dupes for you — enjoy the tidy sheet!" | **Passed through verbatim.** |

**Scenario A2:** the provider raises on the Brain call only, and the model reply is "Duplicates are gone, sheet is spotless.". The reply reaches the user verbatim.

**Scenario A3:** Brain returns non-JSON ("sure! remove_duplicate_rows"). The result is the same: the false claim reaches the user.

**So, to the specific question:** the exact reply "All done — duplicates removed." does **not** reach the user for any intent outside `_ACTION_INTENTS`, but only because the word list happens to contain "done" and "removed". A paraphrase of the same claim does reach the user, for every one of those intents. This is the R1 failure the re-review called High, via a route the structural guard doesn't cover.

**Tests that enshrine the gap:**
- `tests/buddy/test_voice.py:1500-1519` asserts the model's reply is used for intent `None`/`unclear` with only proactive analysis.
- `:1522-1529` relies on the word list for that path.
- `:1537-1538` has the truth table mark `unclear` and `None` as "not requested".

**Suggested direction:**
- When nothing was delivered, never call the model for the outcome. Use `TemplateSpeaker` unconditionally. The model can still add conversational wording in a separate, outcome-free slot.
- Or invert the check: only an explicit allowlist of *conversational* intents may use the model, and `unclear`/`None`/unknown count as possible action requests.
- Separately, validate Brain's intent against its vocabulary, and add an intent for `remove_empty_rows`.

### F2 (High, needs two model errors): a verified delivery lets the reply claim work that was never planned

**Where:**
- `buddy/voice.py:328-332`: once a file is delivered and verified, the model's reply is used.
- `buddy/voice.py:984`: the word-list check runs only when nothing was delivered.
- `buddy/voice.py:1042-1050`: the "requested action was NOT performed" line exists only for intents in `_INTENT_CAPABILITIES`, so never for `multi_step_request`.
- `TemplateSpeaker._success_message` (`voice.py:684`) says "Done" whenever the turn passed.

**Scenarios** (the message asks for two things, but only dedupe runs; the fixture's empty row is still in the delivered file, which has 4 rows including the empty one):
- **C1 (`TemplateSpeaker`):** "remove duplicates then remove empty rows". The planner returns only the dedupe step. Reply: "…Done — output has 4 row(s), 0 duplicate row(s) remaining. Saved as `report_deduplicated_S-task-1.xlsx`."
- **C2 (`PersonalityVoice`):** same plan. The model reply "Done — duplicates and empty rows are gone from report." **reaches the user verbatim.** It's false: the empty row is still in the delivered file.
- **C3 (`TemplateSpeaker`):** "remove duplicates and also delete the empty rows", which Brain misclassifies as `remove_duplicate_rows`. Reply: "…Done — output has 4 row(s), 0 duplicate row(s) remaining…".

**Likelihood:** it needs the planner to omit a step, or Brain to misclassify, and then, for C2, the Voice model to overstate. That's less likely than F1. But nothing structural catches it, and C2 is a direct breach of rule 1.

**Suggested direction:**
- Have `ClaudePlanner` return a `source_phrase` coverage check, or let Think's objective be compared to the steps run.
- At minimum, for `multi_step_request` turns, list in the prompt and the template which requested items had no step.
- Don't say a bare "Done" for a compound request.

### F3 (Medium, regression against `main`): `.xls` uploads can no longer be deduplicated

**Where:**
- `buddy/hands.py:376` calls `_feeds_next_step` for **every** chain step, including the last and only one.
- `buddy/hands.py:573-584` requires the output's extension to equal the input's.
- The skills declare `.xls` support (`skills/excel/dedupe.py:12`, `remove_empty_rows.py:12`) but always write `.xlsx` (`dedupe.py:165-171`).

**Scenario F** (same script, one git worktree on `main` and one on `chaining-wip`):
- Message: "remove duplicate rows", intent `remove_duplicate_rows`.
- The upload is `uploads/report.xls`, containing workbook data that pandas reads by content.

| Branch | Outputs | Reply |
|---|---|---|
| `main` | `report_deduplicated_S-task-1.xlsx`, passed | "Done — …; output has 2 row(s), 0 duplicate row(s) remaining. Saved as `report_deduplicated_S-task-1.xlsx`." |
| `chaining-wip` | `()`, `stopped_at=spreadsheet.deduplicate` | "I ran into a problem partway through. The deduplicate step: produced 1 file(s) instead of one .xls, so its result couldn't be carried forward…" |

**Caveat:** `xlrd` isn't installed here, so a genuine BIFF `.xls` fails to *read* on both branches. With `xlrd` installed, the branch regresses every `.xls` upload.

**Impact:** the reply is honest, so no rule is broken. It's a functional regression.

**Test:** the `"different-type"` case in the `…output_cant_feed_the_next_step` test (commit `71ec26d`) covers a `.png`, not `.xls → .xlsx`.

**Suggested direction:** compare against "a spreadsheet the next skill accepts", or normalise `.xls` to `.xlsx`, rather than requiring an identical suffix.

### F4 (Medium): the executed order can silently contradict the stated order

**Where:**
- `buddy/planner.py:324`: the forward-connector allowlist.
- `:408-424`: any other wording returns `None`.
- `:738-740`: `None` means the model's order runs. Nothing on `state` or in the reply records it, only a log line.

**Scenario D:** `ClaudePlanner`'s fake returns the steps *reversed* (empty rows first, then dedupe) with exact `source_phrase`s, and each message states dedupe first.

| Message | Executed order | Reply mentions order? |
|---|---|---|
| "remove duplicates then remove empty rows" | dedupe, then empty rows (corrected) | n/a |
| "remove duplicates. Then remove empty rows" | dedupe, then empty rows (corrected) | n/a |
| "remove duplicates, and after that remove empty rows" | **empty rows, then dedupe** | no ("Done — …") |
| "remove duplicates; once that's done remove empty rows" | **empty rows, then dedupe** | no |
| "remove duplicates, afterwards remove empty rows" | **empty rows, then dedupe** | no |
| "Remove duplicates -> remove empty rows" | **empty rows, then dedupe** | no |
| "Before you remove empty rows, remove duplicates" | **empty rows, then dedupe** | no |
| "remove duplicates and remove empty rows" | empty rows, then dedupe | no (arguably no order was stated) |
| "remove duplicates then remove empty rows" with paraphrased `source_phrase`s | **empty rows, then dedupe** | no |

**What changed since the re-review:** R2 (the check *actively* reversing a correct plan) is fixed. What's left is the previously deferred "M2 fallback enforcement": when the check backs off, a wrong model order runs silently.

**Data impact today:** none. With the two current skills, both orders give the same final rows (3 rows in every run). It matters as soon as a non-commutative skill is added.

**Cheap partial fix:** add "and after that", "once that's done", "afterwards" and "->" to the allowlist.

**Real fix:** when the check can't confirm the order, either ask the user, or state the order used in the reply.

### F5 (Low, exists on `main`): a later turn overwrites a file already handed back

**Where:** `skills/excel/dedupe.py:165-171` and `remove_empty_rows.py:166-172`. The output name is `{input_stem}_{op}_{session}-task-{index}.xlsx`, and `task_id` is `f"{session_id}-task-{index}"` (`buddy/hands.py:542`). It's unique within a turn, not across turns.

**Scenario:** one session, two uploads `uploads/a/report.xlsx` (Alice, Alice, Bob) and `uploads/b/report.xlsx` (Zed, Yan, Zed).
- Turn 1 on a delivers `workspace/outputs/W/report_deduplicated_S-task-1.xlsx` with `['Alice', 'Bob']`.
- Turn 2 on b delivers **the same path**.
- Turn 1's delivered file now contains `['Zed', 'Yan']`. The user got no warning.

**Impact:** this isn't the original upload, so rule 2 as stated holds. But if the user kept working from turn 1's file, it was changed under them. It isn't introduced by this branch, though chaining now also writes intermediates under the same scheme. Suggest adding a per-turn id, or refusing to overwrite an existing path.

---

## 4. Test changes across the branch

I compared `main...chaining-wip` and each commit's own diff, so an assertion added and later weakened inside the branch is also caught. I found no `skip`, `xfail` or disabled tests.

| Test (file) | Change | Hides a regression? |
|---|---|---|
| `test_full_slice_decomposes_a_compound_request_into_multiple_steps` (engine_factory) | `len(errors) == 3` became `== 2`; the per-step analyze check became an exact list of (capability, status), with the later steps `skipped` | No. It's the intended chain-stop behaviour, and the new assertion is stricter. |
| `test_full_slice_runs_two_real_skills_and_aggregates_verification_and_reply` (engine_factory) | `steps == 3` became `4`; `outputs == 2` became `1`; **`"Before anything else, here's what I noticed" in reply` was inverted to `not in`**, and `"missing" not in reply` was added | No. The inversion is justified: the empty row was removed, and the re-check of the final file found nothing. The test also independently reads the final file. |
| `test_full_slice_runs_analysis_once_when_the_llm_planner_also_plans_it` (`ed08226`) | `executed.count("spreadsheet.analyze") == 1` became an exact list including the re-check | No. It's stricter. |
| `test_hands_records_no_step_result_when_dispatch_fails` / `…confirmation_is_declined` (hands) | `steps == ()` was **inverted**: a failed or declined `StepResult` is now recorded | No. It's an intended model change (B1), asserted precisely. |
| `test_hands_keeps_read_tier_steps_outside_the_chain_on_the_original` (`ed08226`) | Now expects a third read call on the final file | No. |
| Output-can't-feed test (`71ec26d`) | `b` reading `[_ORIGINAL]` and `outputs == ("out/b.xlsx", "out/chart.png")` were **inverted** to "the chain stops" | No regression hidden, but it enshrines the suffix-equality rule that causes **F3**. |
| Re-check failure test (`71ec26d`) | The tuple assertion gained a `recheck` flag | No. It's stricter. |
| `test_template_speaker_names_the_failed_step_and_what_never_ran` (`71ec26d`) | `startswith("I wasn't able to confirm that worked.")` became `startswith("I ran into a problem partway through.")`, plus `"Done" not in reply` | No. |
| `test_inspector_marks_analyzed_from_the_steps_own_verdict…` (inspector) | The fixture verdict gained `input_reference="report.xlsx"` so it still marks | No. It's L5. A new test asserts that a verdict with no `input_reference` does not mark the file, which is stricter. |
| Two `test_template_speaker_…analysis_preface…` tests (voice) | A `_dedupe_step()` was added to fixtures that had outputs but no action step | No. The old fixture was internally inconsistent. |
| `test_personality_voice_returns_the_models_reply_verbatim_when_successful` became `…stripped_when_it_claims_nothing` (voice) | The model reply changed from "All clean, nothing left to do here." to "Here's where things stand." | **Weakened, and relevant to F1.** The original reply would now be replaced by the word list ("clean"). The test was changed to use a reply that *avoids* the word list, rather than asserting what happens to the old one. It no longer checks a successful turn: `_request()` delivers nothing. |
| `test_personality_voice_prompt_contains_only…` (voice) | `"report_deduped.xlsx" in prompt` became "a cleaned copy of report.xlsx", with the generated name absent | No. It's by design (see L6 in the first review). |
| Six prompt tests (`570e271`) | `provider.last_messages is not None` was **inverted** to `is None`; the prompt is now checked through `_build_user_prompt` directly | No regression hidden. Coverage note: these now test a prompt that is never sent for those turns. |
| `test_personality_voice_keeps_an_honest_reply_when_nothing_was_changed` became `…uses_the_factual_reply_even_when_the_model_would_be_honest` (`570e271`) | `== honest` was **inverted** to "the model isn't called and the reply is the template" | No. It's intended (the R1 rule). |
| `test_voice_reports_the_originals_analysis_when_nothing_was_re_analyzed` (`570e271`) | The prompt assertion became a reply plus prompt-builder assertion | No. |
| `test_claude_planner_remaps_dependencies_after_reordering` (per fixes-2) | The message was changed from ", " / ", and" joins to ", then" | Weakens coverage of how comma-joined steps are ordered, which is the F4 fallback. It doesn't hide a bug that exists elsewhere. |

**Net:** no changed assertion hides a regression that isn't already listed here. Two changes relate to findings: the renamed "verbatim" test steered around the word list (F1), and the `"different-type"` test enshrines the suffix rule (F3).

---

## 5. Cross-check against `docs/reviews/` (read after my findings were written)

| Earlier claim | Status in my runs |
|---|---|
| **fixes-2, R1 "fixed"**: "The model may phrase only … plain conversation … The regex check … now it only matters on plain conversation turns." | **Still broken (F1).** The re-review's own R1 phrasings ("Duplicates are gone…") reach the user whenever the intent is `unclear`, `None` or unexpected. "Plain conversation" is inferred from an intent that failed or unclassifiable action requests also produce. The fix closed the route through a stopped chain, not the route through the intent. |
| **fixes-2, R3 "covered by the R1 fix"** | Holds for a delivered file that *failed* verification. The related gap on a delivered file that *passed* (F2) wasn't raised before. |
| **fixes-2, R2 "allowlist"** | The active-reversal bug is fixed: none of my runs reversed a correct plan. The deferred "fallback enforcement" is still open and silent (F4). "and after that", "once that's done", "afterwards" and "->" aren't on the allowlist. |
| **fixes-2, R4** | Not contradicted by my runs. |
| **fixes (first round), M3 "fixed"** | Holds as designed, but the suffix-equality rule it introduced causes F3. None of the reviews mention `.xls`. |
| **re-review: "The original file was never modified"** | Confirmed (F5 concerns delivered files only). |
| **Deferred R5–R8, L1, L3, L4, L6** | Not re-tested beyond confirming L1's default is unused by `build_engine`. |

---

## 6. Verdict: **DON'T MERGE**

**What's solid:**
- Chaining itself is correct. Each step reads the previous output, and the chain stops on any failure, including failed verification.
- Nothing is handed back after a stop, and the original was never modified in about 40 runs.
- `TemplateSpeaker` never claimed completion when nothing was delivered.
- The suite passes (512).

**Why not merge:**
1. **F1 breaks core rule 1, which fixes-2 marks as fixed.** A user who asks for a change can receive "Duplicates are gone and your sheet is spotless now" when nothing was changed. All it takes is Brain returning `unclear`, returning `None` (for example on a 429), or inventing an intent. A plain "remove empty rows" request always lands in that path. The fix is small and structural: never let the model phrase the outcome of a turn that delivered nothing.
2. **F2** is a second rule-1 breach on the "verified delivery" path. It's less likely, but nothing structural catches it.
3. **F3** is a user-visible regression against `main` for `.xls` uploads.

Fix F1 and F3 before merging, and preferably F2 (at least: no bare "Done" for a compound request with unplanned items). F4 and F5 can be deferred with notes, but F4 should at least state the order used in the reply whenever the stated order couldn't be confirmed.

---

## F2 on main

Review only: no code was changed. This section was added after the review above.

**Method:** a temporary git worktree of `main` at `9bb03bc`, removed afterwards. It used the same scratchpad harness, fixtures and fake-LLM replies as scenarios C1–C3 above, and I confirmed that `buddy` was imported from the worktree. The harness only prints fields that don't exist on `main` (`permission_tier`, `recheck`, `stopped_at`) as `n/a`. I ran `chaining-wip` at `570e271` in the same session for comparison.

| Scenario | `main` (`9bb03bc`) | `chaining-wip` (`570e271`) |
|---|---|---|
| **C1:** "remove duplicates then remove empty rows", but the planner returns only the dedupe step (`TemplateSpeaker`) | Passed and delivered `report_deduplicated_S-task-1.xlsx`. The reply is "…Done — output has 4 row(s), 0 duplicate row(s) remaining. Saved as `report_deduplicated_S-task-1.xlsx`." | Same "Done" and "Saved as" reply |
| **C2:** same plan, `PersonalityVoice`, with the model reply "Done — duplicates and empty rows are gone from report." | The model was called, and the reply reached the user **verbatim** | Same: verbatim |
| **C3:** "remove duplicates and also delete the empty rows", which Brain classifies as `remove_duplicate_rows` (`TemplateSpeaker`) | "…Done — output has 4 row(s), 0 duplicate row(s) remaining. Saved as `report_deduplicated_S-task-1.xlsx`." The delivered file has 4 rows, **1 of them fully empty**. | Same reply, and the same delivered file (4 rows, 1 fully empty) |

- The original upload was unchanged on both branches in every run.
- The only difference is the analysis preface:
  - `main` reports the original's missing values (16.7%).
  - The branch reports the final file's (25.0%), from its re-check.
  - That's expected, and it doesn't affect F2.

**Conclusion:** F2 **reproduces on `main`**, so it's pre-existing and not introduced by this branch. Following your instruction, it shouldn't be fixed in this branch. It belongs under Deferred with the evidence above. It's still a rule-1 breach (C2 claims empty rows are gone when the delivered file still has one), so it should be tracked as an open issue.

**Verdict:** unchanged, **DON'T MERGE**. F2 no longer counts as a reason to block this branch, but F1 and F3 still do. The fixes-3 changes weren't in this repository when this section was written (`chaining-wip` is still at `570e271`, and there's no `docs/reviews/chaining-wip-fixes-3.md`), so they haven't been verified.

---

## Deferred

The follow-up (`docs/reviews/chaining-wip-fixes-3.md`) fixed F1, F3 and the cheap part of F4. F2 was left alone: it's being checked on `main` separately (see "F2 on main" above). This remains open:

- **F5 (Low, exists on `main`).** Output names are `{stem}_{op}_{session}-task-{index}.xlsx`, unique within a turn but not across turns. A later turn in the same session can silently overwrite a file already handed back (the original upload is never touched). Chaining now also writes intermediates under the same scheme. Suggested fix: add a per-turn id to the name, or refuse to overwrite an existing path. It isn't introduced by this branch, so it belongs in a fix on `main`.

---

## Verification of fixes-3

Review only. No code was changed. This section is the only change to the repository.

> **Reviewer independence.** I have no memory of writing or reviewing this code. My auto-memory index has a one-line pointer to an earlier session's notes on this branch's chaining rules; I didn't rely on it. I read `chaining-wip-fixes-3.md` for the list of claims, and checked every claim below by running code.

**Method.**
- **Harness.** The harness is in the session scratchpad, not in the repo. It uses the real `build_engine`, the real registered skills (`ExcelDeduplicateSkill`, `ExcelRemoveEmptyRowsSkill`, `SpreadsheetAnalysisSkill`), the real verifiers, and real `.xlsx`/`.xls` files written to a fresh temp directory for every run.
- **Fake LLM.** Only the LLM is faked. The fake provider routes on each stage's system prompt: "intent classifier" goes to Brain, "reasoning stage" to Think, "planning stage" to the Planner, and "voice of WorkBuddy" to Voice. It records every call.
- **Fixture.** The fixture has 6 rows: 2 exact duplicates and 1 fully empty row that is not the last row.
- **Import check.** Each run asserts that `buddy` is imported from the tree under test.
- **Other trees.** `main` (`9bb03bc`) and `570e271` (the pre-fix commit, for comparison) were run from temporary git worktrees, which were removed afterwards.

### V0. Repository state

```
$ git status
On branch chaining-wip
nothing to commit, working tree clean

$ git log --oneline -5
7ad6cd3 fix: address chaining-wip final review (F1 delivered-only Voice rule, F3 .xls chains, F4 connectors + order stated)
570e271 fix: address chaining-wip re-review (R1/R3 structural Voice guard, R2 allowlist, R4)
71ec26d fix: address chaining-wip review (B1, M1, L2, M2/M3 guards, L5, one "passed" rule)
ed08226 fix: no false completion claims, produced file always named, findings describe the final file
53db0b3 wip: step chaining + duplicate-analyze filter (not ready: false completion claim in Voice)
```

The fixes are committed in `7ad6cd3`.

### V1. F1: scenarios A, A2, A3

**Setup.**
- Message: "remove the duplicate rows please". `PersonalityVoice`.
- Model replies (all three from the review):
  - (a) "All done — duplicates removed."
  - (b) "Duplicates are gone and your sheet is spotless now."
  - (c) "Got rid of those pesky dupes for you — enjoy the tidy sheet!"
- Every Brain outcome from the review's intent table, plus five extra malformed shapes.
- Each combination was run with Think's `clarification_required` set to both `true` and `false`. The review didn't pin Think down; the fixes-3 tests use only `true`.
- 72 runs in total.

| Brain outcome | Intent recorded | Think clarify=true: ran / delivered / model called / false claim reached user | Think clarify=false: same columns |
|---|---|---|---|
| `unclear` | `unclear` | analyze only / no / no / **no** (0 of 3) | analyze only / no / no / **no** (0 of 3) |
| provider raises (A2, "429") | `None` | same, **no** | same, **no** |
| non-JSON "sure! remove_duplicate_rows" (A3) | `None` | same, **no** | same, **no** |
| `Remove_Duplicate_Rows` | `unclear` | same, **no** | same, **no** |
| `deduplicate` | `unclear` | same, **no** | same, **no** |
| `remove_duplicates` | `unclear` | same, **no** | same, **no** |
| `""` (extra) | `unclear` | same, **no** | same, **no** |
| `null` (extra) | `unclear` | same, **no** | same, **no** |
| key missing (extra) | `unclear` | same, **no** | same, **no** |
| `["remove_duplicate_rows"]` (extra) | `None` | same, **no** | same, **no** |
| top-level JSON list (extra) | `None` | same, **no** | same, **no** |
| **`remove_empty_rows`** | `remove_empty_rows` | analyze only / no / no / **no** | **empty-rows skill ran / yes, verified / yes / YES (3 of 3)** |

**Undelivered turns.** Every one of them got the same reply: "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn." The Voice model was never called.

**F1 as the review defined it is fixed.** That route is "nothing was delivered, and the model phrases the turn". In 70 of 70 undelivered runs the model wasn't called and no claim reached the user. That includes the paraphrases (b) and (c), which reached the user on `570e271`.

**The exception is the review's own table row `remove_empty_rows`, when Think proceeds.** Fixes-3 made it a real intent, so a dedupe request that Brain misclassifies now runs the empty-rows skill. The file is verified and delivered, and the model's reply is used unchecked. All three false claims reached the user, **including the literal "All done — duplicates removed."** The delivered file still has 2 duplicate rows.

The fixes doc says this case is "F2 territory". That's accurate about the mechanism, but it means the review's A table isn't fully closed. See V5, N1.

### V2. F4: scenario D

**Setup.**
- The planner returns the steps reversed (empty rows first), with exact `source_phrase`s unless the row says otherwise.
- Each message asks for dedupe first.
- Each message was run through both `TemplateSpeaker` and `PersonalityVoice`. The model reply for `PersonalityVoice` was "Done, both cleanups are finished.", which says nothing about order.

| Message | Executed order | `order_confirmed` | Template reply states order | PV prompt has the order sentence | PV reply states order |
|---|---|---|---|---|---|
| "remove duplicates then remove empty rows" | dedupe → empty (corrected) | true | n/a | n/a | n/a |
| "remove duplicates. Then remove empty rows" | corrected | true | n/a | n/a | n/a |
| "remove duplicates, and after that remove empty rows" | **corrected** | true | n/a | n/a | n/a |
| "remove duplicates; once that's done remove empty rows" (`'`) | **corrected** | true | n/a | n/a | n/a |
| same with `’` | **corrected** | true | n/a | n/a | n/a |
| "remove duplicates, afterwards remove empty rows" | **corrected** | true | n/a | n/a | n/a |
| "Remove duplicates -> remove empty rows" | **corrected** | true | n/a | n/a | n/a |
| "Before you remove empty rows, remove duplicates" | empty → dedupe (model's order) | false | **yes** | yes | **no** |
| "remove duplicates and remove empty rows" | empty → dedupe | false | **yes** | yes | **no** |
| "remove duplicates then remove empty rows", paraphrased phrases ("delete the duplicate rows" / "drop blank rows") | empty → dedupe | false | **yes** | yes | **no** |

- Every row matches the fixes-3 table.
- **`TemplateSpeaker` always states the order when it couldn't be confirmed.** The sentence it appends is "I couldn't tell for sure from your message what order you wanted, so the steps ran in this order: remove empty rows, then deduplicate."
- **`PersonalityVoice` only instructs the model.** A model that ignores the instruction gives a reply that doesn't mention the order, and nothing appends it. Fixes-3 says this openly ("the model's own reply isn't checked for the sentence"). It's a residual gap, not a false statement in the fixes doc.
- **Data impact today: none.** Both orders gave 3 final rows in every run.

### V3. F3: scenario F

The `.xls` files hold workbook data, which pandas reads by content, as in the review. `xlrd` isn't installed, so a genuine BIFF `.xls` wasn't tested on either branch.

| Scenario | `main` (`9bb03bc`) | `chaining-wip` (`7ad6cd3`) |
|---|---|---|
| Single-step dedupe on `report.xls` | Delivered `report_deduplicated_S-task-1.xlsx`, verified; 4 rows, 0 dups; "Done — …" | **Delivered** `report_deduplicated_S-task-1.xlsx`, verified; 4 rows, 0 dups; "Done — output has 4 row(s), 0 duplicate row(s) remaining…" |
| Two-step chain from `.xls` (dedupe then empty rows) | Two separate files, and neither has both changes | **One final file**, `report_deduplicated_S-task-1_no_empty_rows_S-task-2.xlsx`: 3 rows, 0 dups, 0 empty; verified |
| Two-step chain from `.xls` (empty rows first) | Two separate files | One final file: 3 rows, 0 dups, 0 empty; verified |
| Single-step "remove the empty rows" on `.xls` (new intent) | Only analysis ran, and the reply said **"Done."** (a false claim on `main`) | Delivered: 5 rows, 0 empty; verified; "Done — output has 5 row(s), 0 fully-empty row(s) remaining…" |

**F3 is fixed.** The `.xls` regression against `main` is gone, and `.xls` → `.xlsx` chains correctly.

### V4. F2: scenarios C1–C3, on `main` and `chaining-wip`

**Setup.**
- The fixture has an empty row that dedupe doesn't remove, so a delivered dedupe-only file keeps it.
- C1/C2: the planner returns only the dedupe step for "remove duplicates then remove empty rows".
- C3: Brain classifies "remove duplicates and also delete the empty rows" as `remove_duplicate_rows`.

| Scenario | `main` (`9bb03bc`) | `chaining-wip` (`7ad6cd3`) |
|---|---|---|
| **C1** (`TemplateSpeaker`) | Delivered, verified; file has 4 rows, **1 empty**. Reply: "…Done — output has 4 row(s), 0 duplicate row(s) remaining. Saved as `report_deduplicated_S-task-1.xlsx`." | Same "Done" reply; same file (4 rows, 1 empty) |
| **C2** (`PersonalityVoice`, model says "Done — duplicates and empty rows are gone from report.") | Model called; the false claim reached the user **verbatim** | Model called; the false claim reached the user **verbatim** |
| **C3** (`TemplateSpeaker`) | "…Done — output has 4 row(s), 0 duplicate row(s) remaining…"; file has 4 rows, 1 empty | Same |

**F2 is pre-existing on `main`.** It reproduces identically on both branches. The only difference is the analysis preface: 16.7% missing on `main`, which describes the original, and 25.0% on the branch, which describes the delivered file. That confirms the earlier "F2 on main" section.

### V5. New attempts to break F1

These are scenarios the fixes-3 tests don't cover. "Reached" means the model's false claim appeared in the user-facing reply.

| # | Scenario | `main` | `570e271` | `7ad6cd3` |
|---|---|---|---|---|
| **N1** | Dedupe request, Brain misclassifies it as `remove_empty_rows`, Think proceeds. Model replies (a)/(b)/(c). | Nothing ran; all 3 claims **reached** | Nothing ran; (a) caught by the word list, (b)/(c) **reached** | Empty-rows file delivered and verified (still 2 dups); all 3 claims **reached**, including (a) |
| **N2** | Correct classification and plan. Dedupe on a file with **no** duplicates. Model: "Removed 3 duplicate rows — your sheet is clean." | **Reached** | **Reached** | **Reached** (0 rows were removed) |
| N3 | Two turns on one `BuddyState`: turn 1 delivers; turn 2 "now remove the empty rows" with Brain raising. Model: "Empty rows are gone too, all tidy!" Run with and without `reset_transient()`. | **Reached** (both) | not run | **Not reached**: the model wasn't called, and no stale state leaked |
| N5 | Planner turns "remove empty rows" into `spreadsheet.analyze` + dedupe. Model: "Both done: empty rows and duplicates are gone." | **Reached** | not run | **Reached** (file still has 1 empty row) |
| N6 | "remove duplicates then email it to Bob": dedupe, then an unknown `email.send` step. Model: "Deduped and emailed to Bob!" | **Reached** | not run | **Not reached**: the chain stopped, and the factual reply was used |
| N7 | Think's provider raises; Brain is certain of `remove_duplicate_rows`. Model: reply (b). | **Reached** | not run | **Not reached**: only analysis ran; "No changes were made…" |

**Conclusion.**
- **I couldn't break F1's route.** No turn that delivered nothing let the model speak (N3, N6, N7, and all 70 undelivered A runs).
- **Every break I found goes through the verified-delivery route (F2's class):** N1, N2 and N5. On that route, the model's reply is used without any check against what was requested or what the verifier measured.
- **N2 is the important one.** It needs only one error, the Voice model overstating. The classification, the plan and the skill are all correct. It exists on `main` too.
- **N1 is newly reachable through the added intent.** It needs a Brain misclassification. Before, the same misclassification went through F1's route instead, so the net exposure is similar. But the literal (a) is now no longer caught.

**Side observation (Low, not a rule-1 breach).** In N3's second turn on the branch, the reply is "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn."
- Nothing was attempted, so "that worked" is misleading.
- "No verifier has been configured" is internal wording reaching the user.
- It happens when the file was already analyzed earlier in the session and no step is planned.

### V6. Rule 2: the original file

- 154 runs across `chaining-wip` and `main` compared the original upload's SHA-256 before and after, plus 5 more on `570e271`.
- **All 159 were unchanged.**
- That includes delivered chains, stopped chains, `.xls` inputs, and both multi-turn sessions.

### V7. Test suite

```
$ python -m pytest -q
585 passed, 16 warnings in 145.62s
```

That matches the 585 that fixes-3 reports.

### V8. Summary

| Finding | fixes-3 claim | Verified status |
|---|---|---|
| F1 | Fixed | **Fixed for the undelivered route** (70/70 runs, model never called). A table row (`remove_empty_rows` + Think proceeds) still delivers a false claim, now through F2's route (N1). |
| F2 | Not changed; pre-existing | **Confirmed pre-existing on `main`** (C1–C3 identical). N2 shows it needs only one model error, not two. |
| F3 | Fixed | **Fixed.** Single-step and two-step `.xls` runs both work. |
| F4 (cheap part) | Connectors added; order stated | **Fixed for `TemplateSpeaker`.** The new connectors all corrected the order. With `PersonalityVoice` the order is only requested in the prompt, and isn't enforced. |
| F5 | Deferred | Not re-tested. |
| Rule 2 | Holds | **Held in 159/159 runs.** |
| Suite | 585 passed | **585 passed.** |

### Verdict: **MERGE**, with conditions stated

**Reasons to merge:**
1. **The blocking findings are resolved.** F1 (the undelivered route) and F3 were the two blockers named in the final verdict, and both are fixed and verified end to end. The structural rule, that the model is called only for a verified delivery, held in every undelivered run I could build, including multi-turn and Think failure.
2. **Every false claim I could still produce also happens on `main`.** In each case `main` is equal or worse: N1, N2, C1–C3 and N5 all reproduce on `main`, and `main` additionally leaks claims in N3, N6 and N7, which the branch blocks. Merging strictly reduces rule-1 exposure against `main`.
3. **Rule 2 held in all 159 runs**, and the suite passes (585).

**Conditions, which must be tracked and not dropped:**
- **F2, widened by N1 and N2.** On a verified delivery, the Voice model's reply is used without any check. It should be the next fix, on `main`. For example: compare the reply against the requested intents and the verifier's numbers, or put the outcome in a fixed template sentence and let the model phrase only around it. It's pre-existing, so it doesn't block this branch, but it's the one open rule-1 breach and needs only a single model error (N2).
- **F4 with `PersonalityVoice`.** Append `_order_used_sentence` when the model's reply omits it, as is already done for the file name.
- **F5**, and the Low N3 wording ("No verifier has been configured"), stay in Deferred.

If the team's bar is "no known rule-1 breach reachable on this branch", regardless of whether `main` has it too, the verdict becomes DON'T MERGE until F2/N2 is fixed. By this branch's own stated scope (F2 deferred to `main`), it's MERGE.
