# Live runs: `chaining-wip` at `7ad6cd3`

Live check only. No code was changed.

**Setup**
- **Script:** `scripts/run_dedupe_manually.py`, unchanged: `--mode focus`, `PersonalityVoice`, `GroqProvider` with the default model `openai/gpt-oss-120b`.
- **Wrapper:** the script ran inside a small wrapper kept in the session scratchpad, not in the repo. The wrapper subclasses `GroqProvider` to record which stage made each LLM call (Brain, Think, Planner, Voice, identified by system prompt) and any provider exception. That record is where "Voice model called" comes from.
- **Fixture:** each run the script rewrites `scratch/uploads/manual_report.xlsx`. It has 6 data rows:
  - `Alice 10`, `Bob 20`, one fully empty row, `Alice 10`, `Carol 30`, `Bob 20`;
  - so 2 exact duplicates and 1 empty row.
- **Hashes:** because the fixture is rewritten each run, its hash differs *between* runs (the xlsx metadata includes a timestamp). The check that matters is before vs after within each run.

**Rate limits:** none. All 11 runs completed, and no provider call raised.

## Summary

| # | Message | Brain intent | clarification_required | Steps executed (file-changing, in order) | File delivered | Independent check: rows / dup / empty | Voice model called | False claim? | Original hash |
|---|---|---|---|---|---|---|---|---|---|
| 1 | remove the empty rows and then remove the duplicate rows | `multi_step_request` | False | remove_empty_rows → deduplicate | yes | 3 / 0 / 0 | yes | no | unchanged |
| 2 | same | `multi_step_request` | **True** | none (analysis only) | no | n/a | no | no | unchanged |
| 3 | same | `multi_step_request` | False | remove_empty_rows → deduplicate | yes | 3 / 0 / 0 | yes | no | unchanged |
| 4 | remove the duplicate rows and then remove the empty rows | `multi_step_request` | **True** | none | no | n/a | no | no | unchanged |
| 5 | same | `multi_step_request` | **True** | none | no | n/a | no | no | unchanged |
| 6 | same | `multi_step_request` | **True** | none | no | n/a | no | no | unchanged |
| 7 | remove the duplicate rows and then give me a report | `multi_step_request` | False | deduplicate → remove_empty_rows (**not requested**) | yes | 3 / 0 / 0 | yes | no false claim about the file; see notes | unchanged |
| 8 | same | `multi_step_request` | False | deduplicate | yes | 4 / 0 / 1 | yes | no | unchanged |
| 9 | remove the empty rows | `remove_empty_rows` | False | remove_empty_rows | yes | 5 / 2 / 0 | yes | no completion claim is false; "Nothing notable found" is misleading (see notes) | unchanged |
| 10 | remove the empty rows | `remove_empty_rows` | False | remove_empty_rows | yes | 5 / 2 / 0 | yes | no | unchanged |
| 11 | hi, what can you do? | `unclear` | True | none | no | n/a | no | no | unchanged |

- Every run also ran `spreadsheet.analyze` first, on the original. Every delivered run also ran it again on the final file (the re-check).
- **The original file was unchanged in all 11 runs.** Per-run hashes, before → after:
  - `2052a298a6be1320` → same
  - `0e3ee11c7b97ba89` → same
  - `a1f5c32142cbb329` → same
  - `e93bd0cbb8c3c4cc` → same
  - `d59957de4a634ada` → same
  - `95b52db44e3f8d2d` → same
  - `6fd821168f0daaea` → same
  - `47909f0c56398659` → same
  - `c51851d461102f79` → same
  - `6db4252083c52267` → same
  - `ee57e6829ae4c4fe` → same

## Replies (verbatim)

| # | Reply |
|---|---|
| 1 | manual_report.xlsx cleaned: 3 rows left, no empty or duplicate rows; nothing notable found. |
| 2 | Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn. |
| 3 | manual_report.xlsx cleaned to 3 rows, no empty or duplicate rows. |
| 4 | *(same as 2)* |
| 5 | *(same as 2)* |
| 6 | *(same as 2)* |
| 7 | Done — manual_report.xlsx cleaned, 3 rows, no duplicates or empty rows. Steps ran: deduplicate then remove empty rows. |
| 8 | manual_report.xlsx returned, 4 rows, no duplicates. name: 1 missing, amount: 1 missing. Which to prioritize? |
| 9 | Nothing notable found; manual_report.xlsx cleaned to 5 rows, no empty rows—what's next? |
| 10 | Empty rows removed; manual_report.xlsx back with 5 rows, none empty. |
| 11 | *(same as 2)* |

- **Voice model calls:** the model was called only on the six runs that delivered a verified file (1, 3, 7, 8, 9, 10). It was never called on the five that delivered nothing (2, 4, 5, 6, 11). That matches the F1 rule.
- **Replies on those six runs:** each is the model's text verbatim. No "your cleaned copy is ready" suffix was added, because every reply already names the file.

## What held

- **The chaining rules held on every chained run (1, 3, 7).** Each step read the previous step's output, and one final file came back.
- **Stated order:** runs 1 and 3 ran in the stated order (empty rows first). In both, the planner's own order already matched, so the order check had nothing to correct.
- **Stale Think text in run 1:** Think's summary said "first deduplicating, then removing empty rows", the opposite of the message. The planner and the order check ignore Think's order, so it didn't affect what ran. The text isn't shown to the user.
- **No false completion claims:** every factual statement in the replies matches the independent check of the delivered file.
  - Run 8's "name: 1 missing, amount: 1 missing" describes the delivered file. That file still has its empty row, because the user didn't ask for it to be removed. The re-check reported 25.0% on it.
- **The new `remove_empty_rows` intent works live:** runs 9 and 10 ran one step, and the 2 duplicates correctly stayed.
- **F4 live:** in run 7 the order couldn't be confirmed, because one step had an empty `source_phrase`. The model relayed the order sentence from the prompt: "Steps ran: deduplicate then remove empty rows."

## Findings

### L-1 (High, pre-existing): Think never sees the user's message, so it asks for clarification on clear compound requests

- **What happened:** in 4 of 6 runs of the two clear cleanup messages (runs 2, 4, 5, 6), Think set `clarification_required=True`. Nothing was done.
- **Think's own summaries say why:**
  - "did not specify which operations (e.g., deduplicate, remove empty rows, analysis) they want applied";
  - "The user uploaded a spreadsheet but did not specify what they want done with it."
- **Cause:** `ClaudeThinker._build_user_prompt` (`buddy/think.py`) sends only:
  - the classified intent;
  - the referenced files, missing information and ambiguity flags;
  - the uploaded file names;
  - the recent conversation.
  
  It never sends `user_message`. For `multi_step_request` the intent carries no actions, so Think is guessing. In runs 1 and 3 it guessed right. Runs 1 and 7 also show it inventing detail: it reversed the order in run 1, and added empty-row removal in run 7.
- **Impact:** honest (the reply says "No changes were made to any file this turn."), but the user's request is refused half the time. The reply shows missing-value findings and asks "Want me to tackle any of these?", which doesn't relate to what the user asked.
- **Suggested fix:** pass the user's message to Think, as the planner already gets it.

### L-2 (Medium): an unrequested step was planned and ran (run 7)

- **What happened:** for "remove the duplicate rows and then give me a report", the planner returned three steps:
  1. deduplicate, `source_phrase` "remove the duplicate rows";
  2. **remove_empty_rows, `source_phrase` `""`**;
  3. analyze, "then give me a report".
- **Where it came from:** Think's strategy (built without the message, see L-1) had recommended empty-row removal, and the planner followed it.
- **What ran:** step 2 ran and removed a row the user never asked to remove. The file handed back is 3 rows, where 4 were expected (as in run 8).
- **What the reply said:** it disclosed the step ("Steps ran: deduplicate then remove empty rows"), so the user can see it. But "Done" presents it as what was asked for.
- **Not a rule-2 breach:** the original is untouched.
- **Suggested fix:** the planner could drop, or at least flag, any step whose `source_phrase` is empty or not found in the message. Today, an unlocatable phrase only makes the order check back off.

### L-3 (Medium, related to F2): "give me a report" is quietly absorbed (runs 7 and 8)

- **What happened:** the planner mapped "give me a report" to `spreadsheet.analyze`. The duplicate-analyze filter then removed it, because proactive analysis and the re-check already cover it. So no step is recorded for the report the user asked for.
- **What the replies said:**
  - Run 7 said "Done" and gave no report. The re-check found "nothing notable", and the reply didn't mention it.
  - Run 8 gave two findings and asked "Which to prioritize?", which arguably serves as the report.
- **No false statement about the file,** but a bare "Done" for a compound request with an unaddressed item is the F2 pattern.
- **The stopped-chain case wasn't reproduced live:** in neither run did the planner produce an unrunnable `spreadsheet.report` step, so that path is still covered only by the tests.

### L-4 (Low): "Nothing notable found" on a file that still has exact duplicates (run 9)

- **What happened:** the re-check of run 9's delivered file reported "nothing notable found", and the model led with it. The file still has 2 exact duplicate rows. The user didn't ask for those to be removed, so leaving them is correct.
- **Why:** `spreadsheet.analyze` doesn't look for exact duplicates. Its duplicate check (`NearDuplicateAnalyzer`) only runs on near-duplicates, and only when there's a name column plus a phone or email column.
- **Impact:** true to what the analyzer checks, but a user would read it as "the file is clean".
- **Suggested fix:** have the analyzer report exact duplicate rows, or word the finding as "none of the checked issues were found".

### L-5 (Low, expected): the greeting gets a file-focused template reply (run 11)

- **What happened:** "hi, what can you do?" became `unclear`. Think asked for clarification, the Voice model wasn't called, and the reply was the missing-values template ending in "No changes were made to any file this turn."
- **Honest, as fixes-3 predicted, but it doesn't answer the question.** It goes away once an explicit conversational intent exists.

## Counts

- **Runs completed:** 11 of 11. No 429.
- **Delivered:** 6. All passed independent checks consistent with what was actually run.
- **Nothing done:** 5. Four were wrongly refused clear requests (L-1), and one was the greeting.
- **False completion claims:** 0.
- **Original modified:** 0 of 11.
