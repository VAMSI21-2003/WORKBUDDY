# Implementation: `feat/verified-outcome-b` (PR-B of the verified-outcome design)

**Design:** `docs/design/verified-outcome-design.md`, Sections 2.2–2.5 and 3, with "Decisions (2026-09-30)". D2 and D4 apply directly.
**Code:** commit `31bbb5f` (code and tests). The docs commit after it changes one docstring in `buddy/engine_factory.py` and no behaviour.
**Fixes:** review H1, H2 and M1 are fixed in `docs/reviews/verified-outcome-b-fixes.md`; the tables below are updated to match. Where the text below says a mention entry counts as requested, the fixes doc supersedes it.
**Rule-touching:** yes, rule 1. This needs an independent review by a separate session, written to `docs/reviews/verified-outcome-b-review.md`, before merging. That review hasn't been done. This document is the implementer's account, not a review.
**Live runs:** `docs/reviews/verified-outcome-b-live-runs.md`. 6 of 6 runs completed. There were 0 false claims, every count matched pandas, and the original was unchanged in every run.
**Suite:** `python -m pytest -q` gives **839 passed** (`main`: 710). `ruff check buddy tests` finds 22 errors, the same set as on `main` (compared with line numbers stripped); none are in new or changed lines.

## What changed, in one paragraph

A delivered reply now checks what ran against what was asked for. Brain returns `requested_actions`: each is an action from a closed vocabulary with the user's own words. A deterministic mention detector (`MentionCrossCheck`) adds any action the message names that Brain didn't list. `build_outcome_report` compares this ledger with the steps that passed. It marks each action `DONE`, `NOT_DONE` (with a reason) or `DONE_UNREQUESTED`, and the renderer states each one in fixed code wording. "Done —" leads only when every ledger action is done and nothing else ran. "Partly done —" leads only when part of *Brain's* reading wasn't done (D2). A `ResidualMeasurer` reads the delivered file, so the reply can say what's still in it for a requested action that wasn't done ("It still has 1 fully-empty row(s)."). The "nothing to remove" opener and the three completion closers are back, but only when the request is complete. Routing is unchanged: the planner still follows Brain's `intent`.

## Files

| File | Change |
|---|---|
| `buddy/actions.py` (new) | The single action table: `Action(name, capability, target, request, object, rows, counted, step, mention)`. `INTENT_CAPABILITIES` moved here from `buddy/planner.py` (planner, voice and outcome import it). Also `ACTION_VOCABULARY`, `UNSUPPORTED`, `find_phrase`, `mentioned_actions`, `brain_ledger` and `requested_ledger`. |
| `buddy/models.py` | `RequestedAction(action, source_phrase, source, phrase_found)` and the `LEDGER_FROM_BRAIN` / `_INTENT` / `_MENTION` constants. `Understanding.requested_actions` and `VerificationResult.residuals`. |
| `buddy/brain.py` | The prompt asks for `requested_actions`. `_parse_requested_actions` does the parsing and validation. `MentionCrossCheck` is the new interpreter wrapper. |
| `buddy/inspector.py` | `ResidualMeasurer`, a verifier wrapper used at turn level only. Shared `_duplicate_rows` / `_empty_rows` counters, now used by both step verifiers too (same formulas as before). |
| `buddy/outcome.py` | Statuses and reason codes, `ActionOutcome.reason/source/user_phrase`, `OutcomeReport.brain_reading/residuals/request_complete`, the new lead-word rule, the derivation, and the new sentences. The `requested` field is removed (see Deviations 8). |
| `buddy/voice_catalog.py` | `friend.opener.nothing_to_remove`, `friend.opener.partial`, `friend.closer.anything_else`, `friend.closer.whats_next`, `focus.closer.anything_else`. |
| `buddy/voice.py` | Passes the `Understanding` to `build_outcome_report`, and imports the table from `buddy.actions`. |
| `buddy/planner.py` | Imports `INTENT_CAPABILITIES` from `buddy.actions`. No behaviour change. |
| `buddy/engine_factory.py` | `Brain(MentionCrossCheck(ClaudeInterpreter(...)))` and `Inspector(ResidualMeasurer(verifier))`. Hands keeps the plain `CompositeVerifier`. |
| `scripts/run_dedupe_manually.py` | Prints Brain's raw `requested_actions`, the full ledger, every report action with its status, reason and source, Brain's reading, and the residuals. The original's duplicate count now includes blank repeats (`duplicated()`), matching the verifier's definition. |
| Docs | This file, the live runs, `docs/backlog.md` item 1 status (plus two follow-ups), `docs/WORKBUDDY_ARCHITECTURE.md` (Speak), `docs/BUDDY_PERSONALITY.md` §14, and a wording note in design 2.3. `CLAUDE.md` is unchanged: no "Decided behaviour" line changes. |

**Intents:** `remove_empty_rows` was already a Brain intent (`INTENT_VOCABULARY`), so nothing was added. `test_the_prompt_offers_exactly_the_action_vocabulary` asserts it's still one.

## The ledger, as implemented

**Sources, in ledger order:**
1. **Brain's `requested_actions`** (`source="brain"`). One entry is kept per supported action; every `unsupported` entry is kept. An action outside the vocabulary becomes `unsupported` and keeps its phrase. `phrase_found` is a case-insensitive search of the message. When the phrase is found, `source_phrase` is the user's own text (their casing), not the model's.
2. **Brain's single-action intent** (`source="intent"`), when `requested_actions` doesn't list it. It's what the planner runs, so it's Brain's reading. This covers a reply with no `requested_actions` (the design's fallback, and every older test fake). It also covers a reply where the two disagree, so a step Brain classified can never be called "unrequested".
3. **The mention detector** (`source="mention"`): actions whose object the message names and that 1–2 don't list. Patterns: `duplicated? (rows|lines|entries|records) | duplicates? | dupes? | dedup\w*` and `(empty|blank)[\s-]+(rows?|lines?)`, both case-insensitive.

A missing or malformed field (not a list) gives no Brain entries; an entry with no action is skipped. `MentionCrossCheck` writes sources 1–3 into `Understanding.requested_actions`, and `requested_ledger()` rebuilds the same ledger from any `Understanding`, so Voice and `ResidualMeasurer` read it the same way.

## The report (design 2.2, as implemented)

| Ledger has it? | A step for its capability… | Status / reason | Rendered |
|---|---|---|---|
| yes, Brain or intent | passed | `DONE` | "Removed 2 duplicate row(s)." / "There were no … to remove." / "The … ran and passed its check." (PR-A wording) |
| yes, mention only, and Brain gave a reading | passed | `DONE_UNREQUESTED` (fix for review H1) | "I also removed 2 duplicate row(s), though I didn't read that as part of your request." |
| no, and Brain gave a reading | passed | `DONE_UNREQUESTED` | "I also removed 2 duplicate row(s), though I didn't read that as part of your request." ("I" instead of "I also" when no done sentence precedes it) |
| no, and Brain gave **no** reading | passed | `DONE`, `source=""` | as `DONE`, but it blocks "Done —" (Deviations 3) |
| yes, Brain or intent | didn't run | `NOT_DONE` / `NOT_PLANNED` | "I didn't run the empty-row removal, though I read it as part of your request." |
| yes, mention only, and Brain gave **no** reading | passed | `DONE`, `source="mention"` | as `DONE`, but it blocks "Done —" (fix for review H1) |
| yes, mention only | didn't run | `NOT_DONE` / `NOT_READ_AS_REQUEST` | "Your message mentions duplicate rows, but I didn't run the duplicate-row removal." then, once, "I read your request as: remove empty rows." (only if Brain's reading has a supported action) |
| yes, `unsupported` | (no capability) | `NOT_DONE` / `NO_SKILL` (fix for review H2: dropped when the phrase only names an action that has a capability, unquoted when it also asks for more; identical sentences collapse) | "I can't do this part yet: “sort by amount”." if the phrase is the user's own words; otherwise "I read part of your request as something I can't do yet." |
| yes | ran but didn't pass | `NOT_DONE` / `NOT_CONFIRMED` | "I couldn't confirm the empty-row removal: its check didn't pass." (Deviations 2) |

**Residuals** (design 2.5): for each `NOT_DONE` action whose target was measured on the delivered file, "It still has 1 fully-empty row(s)." If the count is 0, "It has no fully-empty rows." Without a final-state sentence before it, the subject is "The copy". If the read failed, no sentence is added.

**Order in the block:** done actions in the order they ran (the lead word attaches to the first), then unrequested ones, then not-done ones in ledger order, then the final state, residuals, the order-used sentence and the file.

**Lead word** (design 2.3 + D2):
- **"Done"** when `request_complete`: there's at least one action, every action is `DONE`, and every one has a **Brain** source (`brain` or `intent`). A mention entry never counts (review H1).
- **"Partly done"** when some action from **Brain's reading** is `DONE` and some action from Brain's reading is `NOT_DONE`. This includes `NO_SKILL`.
- **None** otherwise. So a mention-only `NOT_DONE` (`NOT_READ_AS_REQUEST`) never produces "Partly done", and it keeps "Done" away (D2).

## The catalog (every entry, with why its predicate makes its text true)

| Entry | Text | Eligible when | Why that's enough, given what ran and what was requested |
|---|---|---|---|
| `friend.opener.neutral` | "Here's where things landed." | always | Claims nothing. |
| `friend.opener.rundown` | "Okay, here's the rundown." | always | Claims nothing. |
| `friend.opener.seeing_double` | "That sheet was seeing double." | a `DONE` dedupe with confirmed `removed_data >= 1` | Unchanged from PR-A. It's about the sheet. `DONE_UNREQUESTED` no longer qualifies, so it never praises unrequested work (N5). |
| `friend.opener.blank_rows` | "Those blank rows were just taking up space." | a `DONE` empty-row removal with confirmed `removed >= 1` | Unchanged from PR-A. Same `DONE`-only restriction. |
| `friend.opener.nothing_to_remove` (back) | "Short one: there was nothing to remove." | `request_complete`, and every action's confirmed `removed == 0` | Every requested action ran, each removed nothing (blank repeats included), and nothing else ran. PR-A's counterexample (the planner dropped the empty-row step) is now `NOT_PLANNED`, so the entry isn't eligible. |
| `friend.opener.partial` (new, from the design's table) | "Heads-up: only part of this went through." | `lead_word == "Partly done"` | True exactly when part of Brain's reading was done and part wasn't. It's never eligible for a mention-only gap (D2). |
| `friend.closer.none` / `focus.opener.none` / `focus.closer.none` | "" | always | Empty. |
| `friend.closer.anything_else` (back) | "Anything else you want me to look at?" | `request_complete` and `open_findings == 0` | It presupposes the request is handled. That's what `request_complete` says, and nothing else is being asked. |
| `friend.closer.whats_next` (back) | "What's next on the list?" | same | Same (review M1). |
| `focus.closer.anything_else` (back) | "Anything else?" | same | Same. |

`test_every_entry_is_eligible_only_when_its_text_is_true` states each entry's claim as its own function in the test file. It checks that every entry is eligible only where its claim holds, across 22 reports (C1, C1 with nothing to dedupe, C3, N1, N5, unrequested extras, dedupe-and-sort, no reading, and complete single and chain reports). A new entry fails the test until it has a stated claim.

## Deviations from the design, and why

1. **Not-done wording names the step, not the rows.** The design's rows are "I didn't remove the empty rows: that step was left out of what I ran." and "Your message also mentions duplicate rows, but I didn't remove any." Both can be false:
   - Dedupe removes repeated blank rows (H1), so with C1 on a file with two blank rows, "I didn't remove the empty rows" would sit next to "Removed 2 duplicate row(s), including 1 repeated blank row".
   - Empty-row removal removes blank rows that `duplicated()` counts as duplicates, so "I didn't remove any [duplicate rows]" can be false in N1.
   - "I didn't run the … removal" is true by derivation. "Also" is dropped because in N1 the message mentions nothing else.
   - A note is added under the design's 2.3 table.
2. **A fourth reason code, `NOT_CONFIRMED`**, for a ledger action whose step ran but didn't pass. Without it, that case would get `NOT_PLANNED` ("didn't run"), which would be false. It's not reachable through `build_engine`: a failing file-changing step fails the turn, and then no report is built. It's covered by a unit test.
3. **`DONE_UNREQUESTED` needs a reading from Brain.** With no Brain entries (for example a `multi_step_request` reply without `requested_actions`), "I didn't read that as part of your request" would be unsupported. The planner did read it, from the user's words. Such a step is `DONE` with `source=""`: it's rendered like `DONE` and blocks "Done —". Test: `test_n5_without_a_brain_ledger_claims_nothing_about_the_request`.
4. **Brain's single-action intent is always part of Brain's reading**, not only when `requested_actions` is missing. Otherwise a reply with `intent=remove_duplicate_rows` and `requested_actions=[remove_empty_rows]` would run dedupe and call it unrequested.
5. **The order in the block** is done, then unrequested, then not-done. The design has "ledger order, then unrequested". Reason: the lead word must attach to a done sentence, and the design's own N5 example puts the unrequested sentence first.
6. **`ResidualMeasurer` measures every supported ledger target** on the delivered file, not only those without a passing step. The report keeps only the `NOT_DONE` ones, so the output is the same. Deciding "no passing step" inside `buddy/inspector.py` would need a second copy of the `_step_passed` rule, because `buddy.outcome` imports `buddy.inspector`. Measuring an extra target costs one count on a file that's read anyway.
7. **`NO_SKILL` quotes only the user's own words.** When Brain's phrase isn't in the message, it's the model's text, and D1 keeps model text out of replies, so the sentence becomes "I read part of your request as something I can't do yet." The quote is sanitised: one line, no backticks or quote marks, at most 80 characters with "…".
8. **`OutcomeReport.requested` is gone**, replaced by `ActionOutcome.source`. `build_outcome_report` takes the `Understanding` instead of the intent string. PR-A's field was Brain's single intent, which the ledger supersedes.
9. **The duplicate mention pattern lists "duplicate(d) rows/lines/entries/records" first**, so the recorded phrase is "duplicate rows", not "duplicate". It matches everything the design's pattern matches, plus "duplicated lines/entries/records" (the design's `duplicates?` alternative fails the word boundary on "duplicated").

## Tests

### New tests (129 more collected than on `main`)

- **`tests/buddy/test_engine_factory.py`**, through `build_engine` with a fake LLM, real skills and a real `.xlsx` (the review's 6-row fixture). Every delivered scenario asserts, via `_check_delivered_reply`:
  - the queued Voice-model reply was never consumed and isn't in the reply;
  - none of the review's false phrases appear ("duplicates removed", "Removed 3", "empty rows are gone", "Both done", …);
  - the exact code-rendered block is present, built from a fresh pandas read of the original and the delivered file;
  - every number in the outcome block is in the pandas-derived set;
  - there's no bare "Done —" and no completion closer;
  - the file sentence is right for the speaker;
  - the original is byte-identical afterwards.

  | Test | Scenario | Variants |
  |---|---|---|
  | `test_n1_misclassified_dedupe_request_says_what_ran_and_what_didnt` | **N1** | template/friend/focus × Brain's ledger wrong too / no ledger field × the review's 3 model replies (18) |
  | `test_c1_c2_a_step_the_planner_dropped_is_stated_as_not_run` | **C1** (template), **C2** (friend, focus; the model reply "Done — duplicates and empty rows are gone from report.") | 3 |
  | `test_c3_a_compound_request_brain_read_as_one_action` | **C3** | 3 speakers × with / without ledger field (6) |
  | `test_n5_unrequested_work_is_stated_as_such_and_the_request_as_not_run` | **N5** | 3 |
  | `test_n5_without_a_brain_ledger_claims_nothing_about_the_request` | N5 with an older Brain reply | 3 |
  | `test_dedupe_and_sort_says_the_sort_part_cant_be_done` | **"remove duplicates and sort by amount"** | 3 speakers × the user's words / paraphrased phrase (6) |
  | `test_dedupe_and_sort_as_a_chain_hands_nothing_back_and_claims_nothing` | the same message read as `multi_step_request` (chain stops at the sort step) | 1 |
  | `test_remove_the_empty_rows_but_keep_the_duplicates` | **"remove the empty rows but keep the duplicates"** | 3 |
  | `test_a_fully_done_chain_says_done_and_may_close` | regression: both requested actions done gives "Done —" | 3 |

  **These tests fail on `main`:** copied into a `main` worktree, 45 of the 46 fail. The one that passes is the chain-stops case, whose undelivered reply PR-B deliberately doesn't change.
- **`tests/buddy/test_outcome.py`:**
  - `test_render_ledger_rows`: every new sentence type, and the C1, N1/C3 and N5 blocks.
  - The derivation table: `NOT_PLANNED`, `NOT_READ_AS_REQUEST`, `NO_SKILL` (quoting and sanitising), `DONE_UNREQUESTED`, the no-reading case, `NOT_CONFIRMED`.
  - Residuals are kept only for not-done actions, and there's no residual sentence without a read.
  - `test_lead_word` covers the PR-B and D2 table.
  - The property test (every number in any reply is a measured field) now generates `NOT_DONE`, `DONE_UNREQUESTED`, residuals and `NO_SKILL` phrases.
- **`tests/buddy/test_brain.py`:** parsing in order with the user's casing; unknown action becomes `unsupported`; a phrase not found is kept but unquotable; missing or malformed field; entries with no action are skipped; the prompt's action vocabulary. `MentionCrossCheck`: adds only what Brain left out (N1), adds nothing Brain listed, adds the negated mention, runs when Brain failed, misses a paraphrase (the documented limit).
- **`tests/buddy/test_actions.py` (new):** every action is an intent with a registered skill and a counted target; the planner uses the same table; 15 mention-detector cases, positive and negative; `find_phrase`; ledger order and de-duplication.
- **`tests/buddy/test_inspector.py`:** `ResidualMeasurer` counts match pandas; the intent fallback; no ledger means no residuals; unverified, undelivered or stopped turns aren't measured; an unreadable file means no residuals and an unchanged verdict; the original is never touched.
- **`tests/buddy/test_voice_catalog.py`:** request-dependent entries are ineligible for 8 incomplete reports (C1, C1 with nothing to dedupe, C3, N1, N5, unrequested extra, dedupe-and-sort, no reading); closers return for 5 complete reports but never alongside open findings; `nothing_to_remove` needs confirmed zeros; `partial` follows the lead word; every entry is eligible only when its stated claim holds.

### Existing tests changed, and why (all of them)

| File | Test | Change | Why |
|---|---|---|---|
| `test_voice.py` | `test_personality_voice_reply_always_mentions_the_file_handed_back` | 3rd argument to `build_outcome_report` is `Understanding(intent="multi_step_request")`, not the string | Signature change (Deviations 8). No assertion changed. |
| `test_voice.py` | `test_delivered_reply_order_is_opener_outcome_findings_closer` | Same, with `"remove_duplicate_rows"` | Same. No assertion changed. |
| `test_engine_factory.py` | `test_full_slice_reports_a_failed_re_check_without_failing_the_delivered_file` | `"Done" not in reply` + `startswith("Removed 1 …")` became `startswith("Done — removed 1 fully-empty row(s). Removed 1 duplicate row(s).")` | D4: PR-A withheld "Done" from every `multi_step_request` until the ledger existed. The message mentions both actions and both passed, so the request is complete. The new assertion is stricter (exact prefix). |
| `test_engine_factory.py` | `test_full_slice_names_the_delivered_file_when_the_initial_analysis_fails` | Same change | Same. |
| `test_engine_factory.py` | `test_a_dropped_second_action_never_yields_a_nothing_to_remove_opener` | The allowed-number set gains the delivered file's empty-row count. Added: the `NOT_READ_AS_REQUEST` sentence, "It still has 1 fully-empty row(s).", and no "Done —" | The reply now states the residual (1, checked against pandas). Stricter: it also asserts the unrun request is stated. |
| `test_engine_factory.py` | `test_h1_removed_counts_repeated_blank_rows_and_every_number_matches_pandas` | Chain case: `"Done —" not in reply` became `reply.count("Done — removed") == 1`. The M1 closer check changed from "never" to "only when 'Done — ' leads" | Both requested actions ran and passed, so "Done" is right (D4). Closers are allowed back only on a complete request (task item 4). **This is a relaxation**: a friend-mode chain reply here now ends "Anything else you want me to look at?", which is true for that turn. |
| `test_outcome.py` | `test_render_outcome` (5 cases) | `requested=(…)` replaced by `source=` on the action | The field was removed (Deviations 8). The expected strings are unchanged; the chain case's comment was updated. |
| `test_outcome.py` | `test_lead_word` | Rewritten | The old table encoded PR-A's intent-based rule, which PR-B replaces. The new table covers Done, Partly done, D2 and the no-reading case, and also asserts `request_complete`. |
| `test_outcome.py` | `test_requested_comes_only_from_a_single_action_intent` | Replaced by `test_with_no_requested_actions_the_ledger_is_brains_single_action_intent` | Same rule (only a single-action intent gives an entry), expressed on the ledger, since `requested` is gone. |
| `test_outcome.py` | `test_report_takes_each_steps_measurement_from_its_own_passing_verdict`, `test_single_step_final_state_is_its_own_measurement_when_it_made_the_delivered_file`, `test_a_measurement_for_another_target_is_never_attributed_to_a_step` | The intent string became `Understanding(intent=…)`. The last test's expected `ActionOutcome` gains `source="intent"` | Signature change. The action now records its ledger source. |
| `test_outcome.py` | property test helpers (`_random_report`, `_measured_numbers`, `_without_file_sentence`) | Generate the new statuses, residuals and `NO_SKILL` phrases. Residuals count as measured. Quoted user phrases are stripped before collecting numbers | The new sentence types must be under the property. User phrases are the user's words, not counts. |
| `test_voice_catalog.py` | `test_no_entry_claims_there_was_nothing_to_remove` | Deleted. Replaced by `test_nothing_to_remove_needs_every_requested_action_done_with_a_confirmed_zero` and `test_no_request_dependent_entry_is_eligible_unless_the_request_is_complete` | The entry is back (task item 4); the replacements assert it's gated on the ledger, including PR-A's counterexample. |
| `test_voice_catalog.py` | `test_no_closer_implies_the_request_is_finished` | Deleted. Replaced by `test_no_request_dependent_entry_is_eligible_unless_the_request_is_complete` and `test_closers_come_back_when_the_request_is_complete` | Same reason: the closers are back, gated on `request_complete` and `open_findings == 0`. |

No test was skipped, marked `xfail` or disabled.

## Known issues (not fixed here)

- **The analyzer's "None of the issues I check for were found" can follow "It still has 2 duplicate row(s)."** (live run 7, and N1 in the tests). Both are true, since the analyzer doesn't check exact duplicates, but together they read as a contradiction. This is backlog L-4 (item 3), made more visible by the residual sentence. Added to `docs/backlog.md` item 3.
- **The leftover case in the design ("Costs") remains:** a Brain misclassification plus a paraphrase the detector misses can still give "Done —" for unrequested work. There's no false outcome claim. Added to `docs/backlog.md` item 1's deferred list.
- **Brain's `source_phrase` can be broader than the request** (live run 7 quoted the whole message). It's only shown to the user for `unsupported` actions, where a broad quote would still be the user's own words.
- **The mention detector is English only** and covers the two actions with skills (design 3.2).

## Deferred (also in `docs/backlog.md`)

- Everything in the design's "Deferred" list: asking when the sources disagree (6.7); stopping unrequested steps (6.8, which needs a measured ledger recall; 6 live runs aren't enough); a ledger-based order check; ledger lines on undelivered turns; runtime hashing for rule 2; a real "report" skill (item 3).
- The Low items from the PR-A review (L1–L3) and M3 (item 1a) are untouched.

## Review checklist for the independent reviewer

1. Run the PR-A review's harness (scenarios A, C, D and N1–N7) against this branch. Every undelivered, stopped or unverified reply should be byte-identical to `main`. The only code path changed for them is Brain's ledger, which doesn't affect routing.
2. For each renderer sentence, try to construct a report or turn where it's false. The ones I considered riskiest: `NOT_READ_AS_REQUEST` when another step removed rows of that kind (H1 blanks); `DONE_UNREQUESTED` when Brain's reading is empty; "I read your request as" when Brain's reading only comes from the intent fallback.
3. Check that every catalog predicate makes its text true given both what ran and what was requested (the table above; `test_every_entry_is_eligible_only_when_its_text_is_true`).
4. Check that no model text can reach a reply: the only Brain text that can is a `NO_SKILL` quote, and only when it's a verbatim substring of the user's message, taken from the message itself.
5. Check that `ResidualMeasurer` never changes a verdict and never reads the original.
6. Re-run live rows 4, 6, 7, 8 and 10 if the reviewer's session has Groq access.
