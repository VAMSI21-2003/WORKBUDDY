# Fixes for the `chaining-wip` final review

The review is `docs/reviews/chaining-wip-final-review.md`. This file covers what changed for F1, F3 and the cheap part of F4 on branch `chaining-wip`. Nothing was merged to `main`.

- **F2** was not changed. It's being checked on `main` in another session (see "F2 on main" in the review).
- **F5** was not changed. It exists on `main`, and it's now listed under "Deferred" at the end of the review.

**Test suite:** `python -m pytest -q` gives **585 passed** (it was 512).

## F1: the model phrases a turn only when a verified file was delivered

**Rule (`buddy/voice.py`, `_model_may_describe_outcome`):** `PersonalityVoice` calls the model only when a file was delivered **and** Inspector ran **and** Inspector passed the turn. Every other turn gets `TemplateSpeaker`'s factual reply, and the model is never called.

**What was removed:**
- `_ACTION_INTENTS` and `_action_requested_or_attempted`. The rule no longer looks at the intent at all.
- The "plain conversation" path. Brain has no conversational intent. The only way to reach that path was through `unclear` or `None`, and those are exactly what a failed or unclassifiable *action* request produces. When a conversational intent is added later, it will be allowed explicitly.
- The post-model `_COMPLETION_CLAIM` check. It could only run on a turn where nothing was delivered, and the model is no longer called for those turns, so the check was unreachable. The regex itself stays: tests use it to check that factual replies make no claim.

**Brain (`buddy/brain.py`):**
- `INTENT_VOCABULARY` lists the four intents: `remove_duplicate_rows`, `remove_empty_rows`, `multi_step_request` and `unclear`.
- Any other intent in a parsed reply is recorded as `"unclear"` and logged. That covers an invented label, the wrong case, an empty string, `null` and a missing key.
- A provider failure or non-JSON reply still gives `None` with `missing_information`, as before. Those replies have no intent to validate.
- A JSON reply that isn't an object (for example a list) used to raise `AttributeError` out of the interpreter, which broke its "never raises" contract. It now falls back like any other wrongly shaped reply.

**New intent: `remove_empty_rows`.** It was small, so it's added to the Brain prompt and to `_INTENT_CAPABILITIES`, mapped to `spreadsheet.remove_empty_rows`. `RuleBasedPlanner` now plans a plain "remove the empty rows" request as one step. The Voice prompt's "the requested action was NOT performed" line covers it automatically.

**Tests**
- **Scenarios A, A2 and A3, end to end** (`test_full_slice_personality_voice_never_phrases_an_unclassified_action_request`, 18 cases):
  - Message: "remove the duplicate rows please", on a real `.xlsx` that has duplicates and an empty row.
  - Brain outcomes: `unclear`, three invented intents (`deduplicate`, `Remove_Duplicate_Rows`, `remove_duplicates`), a provider failure (A2), and the non-JSON reply "sure! remove_duplicate_rows" (A3).
  - Each outcome is run with all three model replies from the review.
  - Checked each time: the intent is `unclear` or `None`, only analysis ran, nothing was delivered, and the original is byte-identical. The Voice model is **never called**. The reply equals `TemplateSpeaker`'s and ends with "No changes were made to any file this turn." Neither the model's reply nor any completion wording appears.
  - All 18 cases fail on `570e271`.
  - The review's A list also had the invented label `remove_empty_rows`. That's now a real intent, and it runs the empty-rows skill, so it isn't an F1 case anymore. A Brain misclassification like that is F2 territory.
- **Brain:** each vocabulary intent round-trips. Six invalid or missing intents become `unclear`. The prompt offers exactly `INTENT_VOCABULARY`. A JSON list reply falls back instead of raising.
- **Planner:** `remove_empty_rows` maps to the empty-rows capability.
- **Voice:** a truth table for `_model_may_describe_outcome`. A verified delivery gives `True` for every intent, including `unclear` and `None`. A failed or missing verification, nothing delivered, and analysis only all give `False`, for every intent.

**Changed tests that enshrined the old gap (`tests/buddy/test_voice.py`)**

| Old test | Change | Why |
|---|---|---|
| `test_personality_voice_uses_the_models_reply_for_a_plain_conversation_turn` (6 cases: intent `None`/`unclear` × nothing executed / nothing delegated / only proactive analysis) | Renamed `…uses_the_factual_reply_when_the_intent_is_unclear_or_missing`, with the same 6 cases. It now asserts the model isn't called and the reply is `TemplateSpeaker`'s. | These are the exact turn shapes F1 exploits. The old test required the model's wording to reach the user on them. |
| `test_the_regex_layer_still_catches_a_claim_in_a_plain_conversation_turn` | Removed. | It tested the post-model regex layer, which is gone because it can no longer run. The same turn is covered by the test above, where the model isn't called at all. |
| `test_action_requested_or_attempted` (truth table marking `unclear`/`None` as "not requested") | Replaced by `test_model_may_describe_outcome`. | The function it tested no longer exists. The new table asserts the opposite of what enshrined the gap: an unclear or missing intent never lets the model phrase an undelivered turn. |
| `test_personality_voice_returns_the_models_reply_stripped_when_it_claims_nothing` | Renamed `…stripped_for_a_verified_delivery`. It now uses a verified delivered turn, with the reply "  All clean, report is down to 3 rows.  ". | The review noted that this test had been weakened into an undelivered turn that dodges the word list. It now checks what its name says, on the only kind of turn the model phrases. The reply includes "clean", which the old version had to avoid. |
| `test_personality_voice_uses_friend_mode_prompt` / `…focus_mode_prompt` | Use `_delivered_request(mode)` instead of an empty request. | On an empty request the model is no longer called, so there's no system prompt to check. |

## F3: a chain step needs exactly one spreadsheet output that the next skill accepts

**Change (`buddy/hands.py`):** `_feeds_next_step`, which required the output's suffix to equal the input's, is replaced by `Hands._why_output_cant_feed_the_chain`. A chain step's output carries the chain forward (or is handed back, for the last step) when all of these hold:

1. there is **exactly one** output;
2. it's a **spreadsheet**: `.xlsx` or `.xls`, the types the skills read;
3. **the next file-changing step accepts it.** Hands finds that step's skills with `Dispatcher.locate_by_capability`. It skips READ-tier steps, which sit outside the chain and read the original. It builds the task that step would get with this output as input, and asks the skills `can_handle`.
   - If no skill declares the next capability at all, this check doesn't stop the chain. The next step then stops the chain itself with its usual "couldn't be run because no available skill can do it" reason, so the stop is reported against the right step.

So `.xls` in and `.xlsx` out is fine. A chart, no output, two outputs, or an output the next skill rejects still stops the chain, and nothing is handed back.

**Failure reasons (what the Voice reply relays):**
- "produced N file(s) instead of one spreadsheet, so its result couldn't be carried forward" (replaces "…instead of one .xlsx…").
- "produced a .xlsx file the next step can't read, so its result couldn't be carried forward".

**Tests**
- **End to end, single-step dedupe on `.xls`** (`test_full_slice_deduplicates_an_xls_upload`): one `.xlsx` output with no duplicates, verification passed, the reply says "Done", and the original is unchanged.
- **End to end, two-step chain from `.xls`** (`test_full_slice_chains_two_steps_from_an_xls_upload`): the dedupe step reads `uploads/report.xls` and writes `.xlsx`. The empty-rows step reads that `.xlsx`. One final file comes back with `["Alice", "Bob"]`, verification passed, and the original is unchanged.
- **Hands unit tests:**
  - `.xls` in, `.xlsx` handed back;
  - `.xls` → `.xlsx` → next step;
  - the next skill only accepts `.xls`, so the chain stops at the first step with the "can't read" reason;
  - the next step has no skill, so the chain stops at *that* step, not the one before.
- The existing "different-type" (`.png`), "none" and "two" cases still stop the chain, unchanged.
- The two end-to-end tests and the first two Hands tests fail on `570e271`.
- **Caveat, as in the review:** `xlrd` isn't installed, so the `.xls` fixtures hold workbook data, which pandas reads by content. A genuine BIFF `.xls` still fails to *read* on both branches without `xlrd`.

## F4 (cheap part): more forward connectors, and the order used is stated when it couldn't be confirmed

**Allowlist (`buddy/planner.py`):**
- `_FORWARD_CONNECTORS` gains "and after that", "afterwards" and "once that's done". The last is stored as "once that s done", because normalizing turns both `'` and `’` into a space.
- "->" can't live in that set, because normalizing strips punctuation and would turn it into an empty gap, which is what a bare comma also becomes. So it's in a separate `_FORWARD_SYMBOLS` set, compared with surrounding whitespace removed. ",", ">" and "-" are still not connectors.

**Order used, stated when it couldn't be confirmed:**
- **`PlanningStep.order_confirmed`** (`buddy/models.py`, default `True`). `ClaudePlanner` sets it `False` on every step when it had two or more steps and `_order_stated_by_user` returned `None`, meaning the model's order ran. `replace()` keeps the flag when analysis is moved to the front.
- **`SpeakingRequest.order_confirmed`** (default `True`). `Voice._build_request` sets it from `state.planning`: it's `False` if any planned step is unconfirmed.
- **`_order_used_sentence`** (`buddy/voice.py`) returns a sentence only when the order is unconfirmed **and** two or more file-changing steps ran. Skipped and declined steps don't count. The sentence is, for example: "I couldn't tell for sure from your message what order you wanted, so the steps ran in this order: remove empty rows, then deduplicate."
- **`TemplateSpeaker`** appends it to every reply shape, including a stopped chain.
- **The `PersonalityVoice` prompt** adds: "The order the user wanted couldn't be confirmed from their message. Tell them, in plain words, the order the steps ran in. In Buddy's words: …". Only the prompt changed, as asked: the model's own reply isn't checked for the sentence.

**Tests: the scenario D messages, end to end.** In each run the planner returns the steps reversed, with empty rows first.

| Message | Executed order | Order sentence |
|---|---|---|
| "remove duplicates then remove empty rows" | dedupe, then empty rows (corrected) | no |
| "remove duplicates. Then remove empty rows" | corrected | no |
| "remove duplicates, and after that remove empty rows" | **corrected** (was: model's order) | no |
| "remove duplicates; once that's done remove empty rows" (`'` and `’`) | **corrected** (was: model's order) | no |
| "remove duplicates, afterwards remove empty rows" | **corrected** (was: model's order) | no |
| "Remove duplicates -> remove empty rows" | **corrected** (was: model's order) | no |
| "Before you remove empty rows, remove duplicates" | empty rows, then dedupe (model's order) | **yes**, in the `TemplateSpeaker` reply and the `PersonalityVoice` prompt |
| "remove duplicates and remove empty rows" | model's order | **yes**, both |
| "remove duplicates then remove empty rows", paraphrased `source_phrase`s | model's order | **yes**, both |

**Other tests:**
- **Planner:** the new connectors work with either model order, and so does "->" without spaces. Every confirmed plan has `order_confirmed=True`. Every existing "keeps the model's order" case now also asserts `order_confirmed=False`. ", " / " > " / " - " don't count as an arrow. A single-step plan is confirmed.
- **Voice:** the sentence appears in both the template and the prompt when unconfirmed. It's absent when confirmed, and absent when only one step ran because the other was declined. `Voice._build_request` passes the flag on from `state.planning`.

**Not done (the review's "real fix"):** asking the user when the order can't be confirmed. The reply now says which order ran, but the model's order still runs. With today's two skills both orders give the same rows, as the review notes.

## Behaviour changes to be aware of

- **Every undelivered turn now gets the factual template, with no model wording.** That includes a greeting or a question with no file. The template has no conversational reply of its own. With nothing executed it says "I wasn't able to find anything to do with that request." That's honest but flat, and it was a known trade-off of the old rule too. It goes away once an explicit conversational intent exists.
- **A plain "remove the empty rows" request now runs as a single step**, where before Brain could only call it `unclear`. It goes through the same verifier (`SpreadsheetEmptyRowsVerifier`) as the chained step.

## Live runs needed

These results are from the test suite and the review's scenarios with a faked LLM. No live-model runs were done for this round. Worth checking live:
1. A single "remove the empty rows" request: does the real Brain pick `remove_empty_rows`?
2. A casual greeting: confirm the flat template reply is acceptable for now.
3. One of the new connectors against the real planner.
