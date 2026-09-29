# Fixes for the `chaining-wip` re-review

The re-review is `docs/reviews/chaining-wip-rereview.md`. This file covers what changed for R1, R2, R3 and R4 on branch `chaining-wip`. Nothing was merged to `main`. R5–R8 are appended to the re-review under "Deferred".

The code was written by the same session that wrote the re-review. The before/after results below come from rerunning the re-review's own probe scenarios against the new code.

## R1 and R3: the model no longer describes an action turn that didn't deliver a verified file

**Approach:** a structural check runs before any model call. The word list is no longer what keeps false claims out.

**Change (`buddy/voice.py`):** `PersonalityVoice.__call__` calls `_model_may_describe_outcome(request)` first. If that returns `False`, it returns `TemplateSpeaker`'s factual reply and **never calls the LLM**.

**The model may phrase only two kinds of turn:**
1. **Plain conversation:** no action was requested or attempted.
2. **Verified delivery:** a file was delivered, *and* Inspector ran, *and* Inspector passed the turn.

**Every other action turn gets the factual reply:**
- the chain stopped;
- nothing was delivered;
- only read-only steps ran for an action request;
- a delivered file failed verification (**R3**);
- verification is missing.

**How "action requested or attempted" is decided (`_action_requested_or_attempted`):**
- **Requested:** Brain's intent is in `_ACTION_INTENTS`, which is every intent `RuleBasedPlanner` maps to a capability (today only `remove_duplicate_rows`), plus `multi_step_request`. This holds even when Think then asked for clarification and only read-only analysis ran. `unclear` and a missing intent are not requests.
- **Attempted:** any step in `ExecutionResult.steps` whose `permission_tier` isn't `"read"`, or the chain stopped (`stopped_at` is set). This counts a step that ran above READ tier, and also one with no recorded tier because it was never dispatched or never started. Proactive analysis and its re-check are READ tier, so on their own they're neither.

**"Delivered"** uses the existing `_nothing_delivered`: the chain didn't stop, there's an output, and an action step passed by the strict rule.

**The regex check is kept as an extra layer.** Now it only matters on plain conversation turns.

**Trade-off:** that layer is broad. A harmless chat reply that happens to use a listed word, such as "ready", is still replaced by the factual template. The template has no conversational reply of its own. When nothing was executed it says "I wasn't able to find anything to do with that request.", which is honest but flat.

**Behaviour change on clarification turns:** a turn where the user asked for an action and Think asked a clarifying question now always gets the factual reply. The model can no longer add wording around it. This is required by rule (a). The factual reply doesn't relay Think's question, which is a known open issue: `Reasoning` has no question field.

**Tests**
- **The three R1 replies** ("Duplicates are gone…", "All set! I took care…", "Your spreadsheet now has 3 rows…"), each tried on four turn shapes: a stop because a step couldn't run, a stop because confirmation was declined, a stop because verification failed, and read-only steps for an action request. In every case the model isn't called, and the reply is the factual one.
- **End to end:** the re-review's three-step chain that stops at its middle step, through `build_engine`, once with each of the three replies. The model is never called.
- **R3:** a file was delivered but its final-file check failed, and separately, a file was delivered but verification is missing. The model isn't called, the reply has no "All done" or "is ready", and the `TemplateSpeaker` reply is honest.
- **A verified, delivered turn** still uses the model's reply, in both modes.
- **A plain conversation turn** still uses the model's reply: intent `None` or `unclear`, with nothing executed, nothing delegated, or only proactive analysis.
- **The regex layer** still replaces a claim made in a plain conversation turn.
- **A truth table** for `_action_requested_or_attempted`.

**Result on the re-review's probe:** a three-step chain stopped in the middle, with each of the four model replies. Every reply is now the factual "I ran into a problem partway through… I stopped at the report step, so remove empty rows never ran and no file was handed back; your original file is unchanged."

## R2: the order check uses an allowlist

**Approach:** the steps are put in the order their phrases appear only when that order is stated in wording on an allowlist. Anything else keeps the model's order and logs why.

**Change (`buddy/planner.py`, `_order_stated_by_user`):** reordering happens only when all of these hold:
- **Each quoted phrase is placed exactly:** it appears exactly once (case-insensitive), and no two phrases overlap. Both rules are unchanged.
- **Each pair of neighbouring phrases is joined only by a forward connector:** "then", "and then", "after that", "followed by" or "next". Punctuation is ignored, so ", then" and "; then" count.
- **No other order wording appears anywhere else.** That means before the first phrase, after the last, or inside a phrase. The check covers words like first, second, last, finally, before, after, ahead, once, until and prior. The one exception is a lone "first" before the first phrase ("first X, then Y").

**Removed:** the old inverting-word list and the late-"first" rule. The allowlist covers both.

**Tests**
- **Model's order kept** for the 4 R2 messages from the re-review:
  - "…last, and…"
  - "…second and…"
  - "…ahead of that"
  - "…; before that, …"
- **Model's order kept** for three more cases:
  - a plain "and";
  - a trailing "first";
  - order wording inside a quoted phrase.
- **Stated order followed**, with the model listing the steps both in the stated order and reversed, for 9 wordings:
  - "remove the empty rows and then remove the duplicate rows" and its reversed wording;
  - ", then", a bare "then", "; after that,", "followed by", ", next", "first X, then Y";
  - a leading "please" with a trailing period.
- **Changed existing test:** `test_claude_planner_remaps_dependencies_after_reordering`. Its message "remove the empty rows, remove the duplicate rows, and give me a report" joins the steps with ", " and ", and", which aren't forward connectors. It now reads "…, then remove the duplicate rows, then give me a report".

**Result on the re-review's probe:** all four R2 messages now keep the model's correct order.

## R4: failed analysis of the original no longer fails the turn

**Approach:** a READ-tier step outside the chain is now non-blocking, like the M1 re-check. The only exception is a turn where nothing else was verified.

**Changes**
- **`buddy/models.py`:** new field `StepVerification.read_only`.
- **`buddy/inspector.py`:**
  - `CompositeVerifier` sets `read_only` from the step's `permission_tier == "read"`.
  - `_aggregate_verdicts` leaves re-check and read-only verdicts out of the overall result.
  - If *every* verdict is non-blocking, all of them count. So a turn that is only an analysis the user asked for still fails when the analysis fails.
- **`buddy/voice.py`:**
  - When the original's read-only step failed and a file was delivered, `TemplateSpeaker` adds "The initial analysis of your original file couldn't run." The reply still says "Done …" and "Saved as `…`".
  - `PersonalityVoice`'s prompt states the same, and adds that the delivered file was verified.
  - The model may phrase this turn, because the delivered file was verified. It still gets "Your cleaned copy of X is ready." appended if it doesn't name the file.
- **L5 is unchanged:** a failed analysis never marks the file as analyzed.

**Hands is unchanged:** a failed READ step still adds an error, so `ExecutionResult.success` is `False`, and the prompt says so as well. The new prompt line explains that this doesn't affect the file.

**Tests**
- **Inspector:** a failed read-only step doesn't fail the turn, but a turn whose only step is a failed read still fails.
- **Voice:** the reply names the file and includes the new sentence, for both speakers.
- **End to end:** the R4 scenario (analysis fails on its first call and passes on the re-check). The turn passes. The reply starts "Done", names the saved file, and includes "The initial analysis of your original file couldn't run.". The original is unchanged and isn't marked analyzed.

**Result on the re-review's probe:** "Done — …; nothing notable found. Saved as `report_no_empty_rows_s-task-1_deduplicated_s-task-2.xlsx`. The initial analysis of your original file couldn't run."

## Other changes

- **Existing `test_voice.py` tests that inspected the model prompt on turns that no longer call the model:** 6 test functions, 9 test cases. They now assert the model wasn't called and check `PersonalityVoice._build_user_prompt` directly, since the prompt builder still serves verified turns. The tests are:
  - `…does_not_present_an_unrun_intent_as_done`
  - `…flags_a_dedupe_step_that_ran_but_failed_verification`
  - `…prompt_carries_no_capability_tags_or_ids`
  - `…prompt_says_where_the_chain_stopped_and_what_never_ran`
  - `…prompt_states_no_changes_when_only_analysis_ran`
  - `…prompt_says_nothing_was_delivered_when_the_last_step_stopped`
- **`test_personality_voice_keeps_an_honest_reply_when_nothing_was_changed`:** renamed to `…uses_the_factual_reply_even_when_the_model_would_be_honest`. It now asserts the opposite, as rule (a) requires.
- **`test_voice_reports_the_originals_analysis_when_nothing_was_re_analyzed`:** it now checks the reply and the prompt builder, because the model isn't called for that turn.
- **`docs/design/multi-step-planner-design.md`:** the Voice-safeguards section is updated for the new rule and for R4.

## Results

- **Test suite:** 512 passed (was 449). The 63 new test cases include parametrized ones.
- **Ruff:** 26 findings, one fewer than before (27). None are new, and the new order check fixes one existing `zip()` warning.
- **Live runs:** none were run, because Groq's daily quota is used up.

## Live runs needed

Use `PersonalityVoice`, since that's the path these fixes change. For example: `scripts/run_dedupe_manually.py --mode focus --message "…"` with `GROQ_API_KEY`.

1. **The chaining regression runs:** 5 × "remove the empty rows and then remove the duplicate rows" and 5 × "remove the duplicate rows and then remove the empty rows". The last batch never ran the second wording (B2–B5), because the quota ran out. Expected: one file with both changes, in the stated order; the model's own reply, which names the file; the original unchanged.
2. **A stopped chain, which has never been seen live:** 3 × "remove the duplicate rows and then give me a report". Expected: the factual stop reply every time. The model's reply must never appear; confirm the log line "using the factual reply without a model call".
3. **The new allowlist on real planner output:** 3 × "first remove the empty rows, then remove the duplicate rows" (reordered if the model lists them the other way). Also 3 × "remove the duplicate rows last, and remove the empty rows": the model's order is kept and logged, so check which order the model actually chose.
4. **Plain conversation:** 2 × a non-action message such as "hi, what can you do?", with a file uploaded. Expected: the model's reply is used. Watch whether the regex layer replaces an innocent reply ("ready", "done"); that's the trade-off noted under R1.
5. **A clarification turn:** 2 × a vague action request that makes Think ask for clarification. Expected: the factual reply, with no completion claim. Note whether losing the model's wording (and Think's question) hurts the reply.
