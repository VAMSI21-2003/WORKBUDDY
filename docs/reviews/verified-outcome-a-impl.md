# Implementation: `feat/verified-outcome-a` (PR-A of the verified-outcome design)

**Design:** `docs/design/verified-outcome-design.md`, with "Decisions (2026-09-30)" D1–D4 appended in this branch.
**Rule-touching:** yes, rule 1. This needs an independent review by a separate session (`docs/reviews/verified-outcome-a-review.md`) before merging. That review hasn't been done; this document is the implementer's account, not a review.
**Live runs:** `docs/reviews/verified-outcome-a-live-runs.md`. 7 of 7 runs completed, 0 false claims, every count matches pandas, and the original was unchanged in every run.

## What changed, in one paragraph

On a turn that delivered a verified file, the reply is now rendered entirely by code from `ExecutionResult` and `VerificationResult`. The counts come from new `StepMeasurement`s that the dedupe and empty-rows verifiers (and the final-file check) record only when their check passes. `PersonalityVoice` makes no model call on any turn. Its delivered replies are the same outcome block as `TemplateSpeaker`'s, with the file named as a cleaned copy of the original, plus an opener and a closer that code picks from a reviewed catalog. Undelivered, stopped and unverified turns are unchanged. N2 ("Removed 3 duplicate rows" for a file with none) can no longer happen: the reply says "Done — there were no duplicate rows to remove."

## Decisions applied

- **D1:** there's no model call on delivered turns. The phrase choice is deterministic: a SHA-256 of `"{session_id}:{turn_index}|{mode}|{slot}"` picks among the eligible entries, sorted by id. There's no JSON selection contract, and the free-text mode prompts are removed (see "Removed" below).
- **D2:** not applicable to PR-A (there's no mention detector yet). Nothing in PR-A can produce "Partly done —".
- **D3:** reply order is opener, outcome block, analysis findings, closer.
- **D4:** PR-A only. There's no ledger. `OutcomeReport.lead_word` is "Done" only when Brain's intent is a single-action intent, every file-changing step that ran and passed is for that intent's capability, and at least one did. A `multi_step_request` (or `unclear`, or no intent) never gets "Done —".

## Files

| File | Change |
|---|---|
| `buddy/models.py` | `StepMeasurement` (target, output_rows, remaining, input_rows, removed); `DUPLICATE_ROWS_TARGET`, `EMPTY_ROWS_TARGET`; `measurements: tuple[StepMeasurement, ...]` on `VerificationResult` and `StepVerification`. |
| `buddy/inspector.py` | `_check_against_input` returns an `_InputCheck` (problem, or input_rows and removed). Both verifiers attach a measurement **only on pass**. `_verify_final_file` carries each chained verifier's output-only measurement, only when the final check passed. `_aggregate_verdicts` copies measurements to each `StepVerification`. Finding strings are unchanged. |
| `buddy/outcome.py` (new) | `ActionOutcome`, `OutcomeReport` (with `lead_word`), `build_outcome_report`, `render_outcome`. The pure step helpers (`_step_passed`, `_verdict_is_for`, `_is_action_step`, `_describe_capability`) moved here from `voice.py` so this module needs nothing from Voice. `voice.py` imports them under the same names. |
| `buddy/voice_catalog.py` (new) | `Phrase`, `CATALOG` (11 entries, both modes), `eligible_phrases`, `select_phrases`. |
| `buddy/voice.py` | `_model_may_describe_outcome` is renamed `_verified_delivery` (same truth table). The new `_verified_delivery_reply` is used by both speakers' delivered paths. `SpeakingRequest` gains `session_id` and `turn_index`. `PersonalityVoice(provider=None)` no longer calls the provider. Removed: `FRIEND_MODE_PROMPT`, `FOCUS_MODE_PROMPT`, `_build_user_prompt`, `_mentions_file`, `TemplateSpeaker._success_message`, `_output_file_names`. |
| `buddy/context.py` | `Context.buddy_reply_count()`, used for `turn_index`. |
| `scripts/run_dedupe_manually.py` | `--fixture {default,no-duplicates}`; prints the verifier measurements, the `OutcomeReport`, each LLM call's stage, and the original's counts. |
| `CLAUDE.md` | The "Decided behaviour" line is replaced with the user's approved wording. |
| `docs/design/verified-outcome-design.md` | Appended "Decisions (2026-09-30)". |
| `docs/backlog.md` | Item 1 status (PR-A done, PR-B open); the design's Deferred items added. |
| `docs/WORKBUDDY_ARCHITECTURE.md`, `docs/BUDDY_PERSONALITY.md` §14 | One paragraph each on how the delivered reply is built now. |

## The catalog (reviewed entries)

| ID | Text | Eligible when |
|---|---|---|
| `friend.opener.neutral` | "Here's where things landed." | always |
| `friend.opener.rundown` | "Okay, here's the rundown." | always |
| `friend.opener.seeing_double` | "That sheet was seeing double." | a done dedupe step measured `removed >= 1` |
| `friend.opener.blank_rows` | "Those blank rows were just taking up space." | a done empty-rows step measured `removed >= 1` |
| `friend.opener.nothing_to_remove` | "Short one: there was nothing to remove." | at least one action, and every action measured `removed == 0` |
| `friend.closer.none` | (none) | always |
| `friend.closer.anything_else` | "Anything else you want me to look at?" | the reply asks nothing about analysis findings |
| `friend.closer.whats_next` | "What's next on the list?" | same |
| `focus.opener.none` | (none) | always |
| `focus.closer.none` | (none) | always |
| `focus.closer.anything_else` | "Anything else?" | the reply asks nothing about analysis findings |

No entry matches `_COMPLETION_CLAIM`, carries a digit or has a placeholder, and the tests enforce all three. The outcome block's lead word is the only "Done".

## Deviations from the design, and why

1. **`measurements` is a tuple, not `measurement: StepMeasurement | None`.** The final-file check measures one target per chained step, so a single optional field couldn't hold it. Per-step verifiers produce exactly one entry.
2. **`input_rows` is the input's row count as read, including empty rows.** The design said "measured on the non-empty rows". `input_rows` isn't rendered anywhere. "Rows in the step's input" is the plainer meaning, and it matches a plain pandas read (the tests check that). `removed` for dedupe is measured on the non-empty rows, as the design says.
3. **Wording for a step with no measurement.** It's "The duplicate-row removal ran and passed its check." rather than "Removed the duplicate rows; the result was checked.". Without a count, "Removed the duplicate rows" would imply there were some, which is N2's error in a milder form.
4. **"left" for later chain steps.** A step that read an earlier step's output says "There were no fully-empty rows left to remove.". This came up in `test_full_slice_runs_two_real_skills_…`: dedupe's output lost the trailing empty row in the xlsx round trip, so the empty-rows step found none. "There were no fully-empty rows to remove." would have read as a claim about the user's file.
5. **Step-verifier finding strings aren't in delivered replies any more.** "output has 4 row(s), 0 duplicate row(s) remaining" is replaced by the measured final-state sentence, which uses the same wording. On a delivered turn, only analysis findings are still rendered from strings:
   - open findings, led by "I also noticed: " (D3 moved them after the outcome, so "Before anything else" no longer fits);
   - uncategorized analysis notes such as "None of the issues I check for were found.".

   A failed analysis's own text ("the analysis reported failure") is dropped, because `INITIAL_ANALYSIS_FAILED_SENTENCE` already says it.
6. **The free-text mode prompts and the prompt builder are deleted, not left unused.** Under D1 no turn calls the model. Prompts that tell a model to "report the outcome" contradict the new CLAUDE.md line, and they'd be one wiring change from coming back. Backlog item 2 will need new, conversation-only prompts.
7. **No `buddy/actions.py` yet.** The per-action wording lives in `buddy/outcome.py` (`_WORDING_BY_CAPABILITY`). `_INTENT_CAPABILITIES` stays in `planner.py`. PR-B needs the shared table for the mention patterns and can move both there.
8. **`turn_index` is always 0 today.** It's `Context.buddy_reply_count()`, and nothing in the pipeline calls `record_buddy_reply` yet. In practice, then, the phrase choice varies only by session id. Within one session the same report gets the same phrases every turn. It's stable and harmless, but less varied than D1 intends until the engine records replies.

## Tests

`python -m pytest -q`: **688 passed** (baseline on `main`: 593). `ruff check buddy tests`: 22 errors, down from 28 on `main`, and every remaining one is pre-existing. The 6 removed errors were long lines in code or tests that this branch deleted or rewrote.

### New tests

- **Verifier measurements** (`tests/buddy/test_inspector.py`):
  - `test_dedupe_verifier_measures_what_it_confirmed`: 6 rows, 2 duplicates, 1 empty row. Checks `removed=2`, `remaining=0`, and an `input_rows` equal to a fresh pandas read. Finding strings unchanged.
  - `test_dedupe_verifier_measures_zero_removed_on_a_file_with_no_duplicates`: N2's data.
  - `test_empty_rows_verifier_measures_what_it_confirmed`.
  - `test_a_failing_verifier_records_no_measurement`: a wrong row count, duplicates left, empty rows left.
  - `test_a_verifier_with_no_input_measures_only_the_output`.
  - `test_composite_verifier_carries_each_steps_measurements_and_the_final_files`.
- **The report and renderer** (`tests/buddy/test_outcome.py`, new):
  - `test_render_outcome`: a table of removed, N2, no measurement, unknown capability, chain with "left" and the order sentence, and inconsistent final-state counts.
  - `test_lead_word`: 7 cases.
  - `test_requested_comes_only_from_a_single_action_intent`.
  - Report-building tests: measurement attribution, failed steps excluded, single-step final state, a wrong-target measurement never attributed.
  - `test_the_reply_never_uses_a_skills_self_reported_count`: a Skill that says 99; the reply has the verifier's 2.
- **Property test:** `test_every_number_in_a_rendered_reply_is_a_measured_field`. 200 random reports × 2 file styles × 2 modes × every eligible opener/closer pair (more than 1,000 replies checked). Every integer in the reply, outside the file names, equals a measured field of the report.
- **Catalog** (`tests/buddy/test_voice_catalog.py`, new):
  - ids are unique and match mode and slot;
  - no completion claims, digits or placeholders;
  - every mode and slot always has an eligible entry;
  - Focus mode has no opener;
  - each factual entry's predicate is checked against true and false reports;
  - no closer question when findings already ask one;
  - the selector is deterministic, always picks an eligible entry, varies across sessions and turns, and treats an unknown mode as friend.
- **Voice** (`tests/buddy/test_voice.py`):
  - `test_the_reviews_model_replies_can_never_appear_in_a_delivered_reply`: N1 a/b/c, N2, C2, N5, the order-run reply, and a JSON "selection" carrying text, × N2-shaped and chain requests × both modes. The provider is never called, and the reply equals the rendered reply.
  - `test_n2_reply_says_there_was_nothing_to_remove`.
  - `test_delivered_reply_order_is_opener_outcome_findings_closer`.
  - `test_voice_seeds_the_phrase_choice_with_the_session_and_turn`.
  - `test_template_speaker_states_no_count_for_a_result_without_measurements`.
- **End to end through `build_engine`** (`tests/buddy/test_engine_factory.py`):
  - `test_n2_end_to_end_says_there_was_nothing_to_remove`: the review's 6 literal Voice replies × Template/friend/focus, on a real xlsx with no duplicates. Checks that:
    - the queued Voice reply is never consumed;
    - the reply has "Done — there were no duplicate rows to remove.";
    - every number in the reply equals a pandas read of the delivered file;
    - the original is byte-identical.
  - `test_a_single_step_verified_turn_still_says_done_and_names_the_file`: the regression test, with the exact "Done — removed 2 duplicate row(s). The copy has 3 row(s), 0 duplicate row(s) remaining." checked against pandas, the file named, and the original unchanged, for all three speakers.

### Existing tests changed, and why

`tests/buddy/test_voice.py`:

| Test | Change | Why |
|---|---|---|
| `test_template_speaker_success_message_mentions_output_file_and_finding` | Now uses a dedupe step with a measurement and intent `remove_duplicate_rows`, and asserts the exact reply | The old fixture had no steps and no intent. Under the renderer that states no count and no "Done" (see the new no-measurement test). The test now covers the real shape. |
| `test_template_speaker_success_message_covers_every_steps_output_and_finding` | Uses two steps with measurements; asserts both file names and both steps' sentences; no "Done" | Same reason; this is not a single-intent turn. |
| `test_template_speaker_analysis_preface_still_reports_other_steps_outcome` | Asserts the outcome comes before "I also noticed: …"; "0 duplicate row(s) remaining" replaced by the no-measurement sentence | D3 order; the fixture has no measurement. |
| `test_template_speaker_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step` | "Before anything else, here's what I noticed" → "I also noticed: " | The delivered-turn lead-in changed (D3). |
| `test_personality_voice_does_not_present_an_unrun_intent_as_done` | Prompt-builder assertions replaced with: no model call, reply == template reply, no completion claim | The prompt builder is gone; the undelivered reply is unchanged. |
| `test_personality_voice_flags_a_dedupe_step_that_ran_but_failed_verification` | Same kind of change; asserts no "Done" | Same. |
| `_delivered_request` (helper) | Verdicts carry measurements (empty rows 6→5, removed 1; dedupe 5→3, removed 2; final 3/0/0) | So delivered-turn tests assert counts. |
| `test_personality_voice_reply_always_mentions_the_file_handed_back` | Expects the rendered reply (opener, block, file sentence, note, closer) and asserts the model's text is absent | The model's text is no longer used. |
| `test_personality_voice_reports_analysis_of_the_final_file_not_the_original` | Asserts on the reply instead of the prompt | The prompt is gone; the reply-level assertion is stronger. |
| `test_template_speaker_reports_analysis_of_the_final_file_not_the_original` | Exact expected reply; "none of …" is now capitalized as its own sentence | Rendering change. |
| `test_voice_reports_the_originals_analysis_when_nothing_was_re_analyzed` | Dropped the prompt-builder line | The prompt builder is gone. |
| `test_template_speaker_reports_a_failed_re_check_and_still_names_the_file` | `startswith("Done")` → no "Done", starts with the step sentence | A `multi_step_request` has no lead word until PR-B (D4). |
| `test_voice_names_the_delivered_file_when_the_initial_analysis_failed` | The PersonalityVoice half: no model call, reply has the file and the failed-analysis sentence, model text absent; the template half additionally asserts "reported failure" isn't repeated | No model; the failed-analysis wording is said once. |
| `test_template_speaker_states_the_order_used_when_it_couldnt_be_confirmed` | `endswith` → contained, and placed before "Saved as" | The order sentence is now inside the outcome block. |
| `test_the_order_used_is_not_stated_otherwise` | The PersonalityVoice half checks the reply, not the prompt | The prompt builder is gone. |

Renamed and rewritten in `tests/buddy/test_voice.py` (same intent, new assertion target):

| Old | New | Why |
|---|---|---|
| `test_personality_voice_uses_friend_mode_prompt`, `…uses_focus_mode_prompt` | `test_personality_voice_makes_no_model_call_on_a_verified_delivery` (both modes) | There are no mode prompts; D1. |
| `test_personality_voice_returns_the_models_reply_stripped_for_a_verified_delivery` | `…never_returns_the_models_reply_for_a_verified_delivery` | **Inverted** (design 4.2). |
| `test_personality_voice_uses_the_models_reply_for_a_verified_delivered_turn` | `…never_uses_the_models_reply_for_a_verified_delivered_turn` | **Inverted** (design 4.2). |
| `test_personality_voice_prompt_contains_only_understanding_execution_and_verification_facts` | `…reply_contains_only_…` | The facts are asserted on the reply. |
| `…prompt_reports_a_failed_re_check_and_the_file`, `…prompt_says_nothing_was_delivered_when_the_last_step_stopped`, `…prompt_says_where_the_chain_stopped_and_what_never_ran`, `…prompt_states_no_changes_when_only_analysis_ran`, `…prompt_states_the_file_handed_back`, `…prompt_states_the_order_used_when_it_couldnt_be_confirmed` | the same names with `reply` for `prompt` | Reply-level now. The last one closes the backlog's F4-with-`PersonalityVoice` item. |
| `test_personality_voice_falls_back_to_template_speaker_on_provider_failure` | `test_personality_voice_is_unaffected_by_a_failing_provider` | There's no model call to fail. A delivered turn gets the rendered reply, not the template's. |
| `test_model_may_describe_outcome` | `test_verified_delivery` | Function renamed; the truth table is identical. |

Removed from `tests/buddy/test_voice.py`:

| Test | Why |
|---|---|
| `test_personality_voice_leaves_a_reply_that_already_names_the_file` | There's no model text to inspect; the file sentence is always rendered (design 4.2 says to delete it). |
| `test_personality_voice_prompt_carries_no_capability_tags_or_ids` | The prompt builder is gone. The reply-level `test_rendered_reply_contains_no_capability_tags` still covers it. |
| `test_mode_prompts_forbid_repeating_internal_labels` | The mode prompts are gone. |

`tests/buddy/test_engine_factory.py`:

| Test | Change | Why |
|---|---|---|
| `test_full_slice_runs_two_real_skills_and_aggregates_verification_and_reply` | "none of the issues…" → "None of the issues… ." | Now its own sentence after the outcome. |
| `test_full_slice_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step` | "Before anything else…" → "I also noticed: ", placed after "Saved as" | D3. |
| `test_full_slice_reports_a_failed_re_check_without_failing_the_delivered_file` | `startswith("Done")` → no "Done", and starts with "Removed 1 fully-empty row(s). Removed 1 duplicate row(s)." | `multi_step_request`: no lead word until PR-B (D4). The counts are the fixture's (4 rows: 1 empty, 1 duplicate). |
| `test_full_slice_names_the_delivered_file_when_the_initial_analysis_fails` | Same | Same. |
| `test_full_slice_personality_voice_mentions_the_file_when_the_re_check_fails` | The exact model-text reply → no provider call, model text absent, file sentence and `RECHECK_FAILED_SENTENCE` present | No model (design 4.2 predicted this). |
| `test_full_slice_states_the_order_used_when_it_couldnt_be_confirmed` (6 cases) | Both speakers: the order sentence is in the reply, before the file sentence, and no provider call. The template's `endswith` is gone. | The order sentence is inside the outcome block, for both speakers. |

`tests/buddy/test_inspector.py`: no existing test changed.

## What this PR does not do (PR-B and Deferred)

- There's no ledger. N1, C1, C2, C3 and N5 are **not** closed by PR-A:
  - A misclassified dedupe request (N1) still gets "Done — removed 1 fully-empty row(s). …". That's true about the file but misleading about the request, and there's no mention of duplicates.
  - C1 and C2 now give an honest block with no "Done" (the intent is `multi_step_request`) but say nothing about the dropped step.
  - C3 (Brain says `remove_duplicate_rows` for a dedupe-and-empty request) still gets "Done —", because the dedupe capability passed and nothing else ran.

  No model text can reach the user in any of them, but the "not done" lines are PR-B.
- There are no residual counts ("It still has 1 fully-empty row(s).").
- Design Deferred items 1–6 are listed in `docs/backlog.md`.

## Review checklist for the independent reviewer

- Can any delivered reply contain a number that isn't a verifier measurement? The property test covers the renderer and catalog. Analysis findings are verifier-computed strings, as before.
- Is every catalog predicate strict enough for its text?
- Is there any path where `_verified_delivery` is false but the new renderer runs, or the reverse?
- Are the undelivered, stopped and unverified paths byte-identical to `main`'s? That's intended. The tests on those paths are unchanged apart from the removed prompt-builder assertions.
- Re-run the final review's harness (scenarios A, C, D and N1–N7) against this branch, as design 5.2 asks.
