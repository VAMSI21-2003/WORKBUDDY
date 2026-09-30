# Second fixes for `feat/verified-outcome-b` (re-review N1 and N2)

**Review:** "Re-review (fixes commit `ef3166f`)" in `docs/reviews/verified-outcome-b-review.md` (verdict: DON'T MERGE, N1 blocking).
**Fixed here:** N1 and N2. **Not merged.** A separate session with no memory of writing this code still has to re-review (`CLAUDE.md`); the re-review was done by the session that wrote the first fixes, and so were these.
**Suite:** `python -m pytest -q` gives **1137 passed** (1008 before, +129 new). `ruff check buddy tests`: 22 errors, the same set as before this change (line numbers stripped, diffed); none are new.
**Live runs:** `docs/reviews/verified-outcome-b-live-runs.md`, "After the second review fixes": 4 of 4 completed, no 429.

## N1: a real unsupported part vanished behind "Done —"

**Cause.** `_FILLER_WORDS` in `buddy/outcome.py` held content words: the verbs "delete", "drop", "clean", "clear", "erase", "strip", "get", "rid" and the nouns "file", "sheet", "spreadsheet", "report", "data", "rows", "lines", and more. `_no_skill_outcome` removes an action's mention pattern and Brain's quoted words for a done action from an `unsupported` phrase, then dropped the entry if only filler was left. So "remove duplicates and delete the file" left "delete the file", all filler, and the reply said "Done —" although Brain had flagged part of the request as something Buddy can't do.

**Change.**
- `_FILLER_WORDS` is now a closed set of function words: `a an the please and then also just only do dont don't not no never keep leave remove`. No other word of any kind was added. A comment above it states the rule: content words and verbs never go in it; a verb belongs in an action's mention pattern attached to its object; when unsure, keep the not-done sentence.
- Everything else left over now renders the generic "I read part of your request as something I can't do yet." and so blocks "Done —" (the lead word is "Partly done").
- The curly apostrophe is normalised to `'` before the check, so "don’t" matches "don't" instead of leaving a stray "t" (which would only ever have kept the sentence, never dropped it wrongly).
- Mention patterns were **not** changed.

**Tests** (through `build_engine`; template, friend and focus speakers × six session ids each; Brain's unsupported phrase is the whole message, as in live run 7):
- "delete the file", "clean the data", "drop the sheet" (and "email me the report" as a guard) appended to "remove duplicates": the reply contains "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet." and never "Done —". 72 cases.
- "delete the file" alone (x6): still quoted ("I can't do this part yet: “delete the file”."), as before. 18 cases.
- "don't remove the duplicates please" left with the empty-row removal: still dropped, no "can't do". 18 cases.
- Unit: a leftover content word keeps the generic sentence for each of the three cases, "delete the duplicates", "remove all duplicates" and "but keep the duplicates"; "keep the duplicates" and "don't remove the duplicates please" are dropped.
- **Guard:** `test_n1_the_filler_set_holds_only_function_words` asserts `_FILLER_WORDS` is a subset of the allowed function-word list written in the test, and that "delete", "drop", "clean", "file", "sheet", "data", "report" and "rows" are not in it. Adding a content word to the set fails it; widening the test's list is a visible, reviewable change.
- **x2, x3, x4 (the re-review's table) are the "delete the file" / "clean the data" / "drop the sheet" cases above, and x5/x6 are covered too.** The old-rule behaviour ("Partly done", as before the H2 fix) is what they assert.

## N2: "I didn't read that as part of your request" from `unsupported` entries alone

**Cause.** `has_reading` was true for any Brain-sourced ledger entry, including `unsupported` ones. With only an `unsupported` entry (for example "sort by amount"), the dedupe that the planner ran was labelled `DONE_UNREQUESTED`, giving "I removed 2 duplicate row(s), though I didn't read that as part of your request." next to a phrase that may have said it.

**Change.** `has_reading` needs a Brain entry whose action is a supported one (`entry.action in ACTIONS_BY_NAME`). Otherwise the step is `DONE` with an empty source: the removal is stated, no lead word, no closer, and nothing about how Brain read it, as in PR-A.

**Tests.**
- `build_engine`, three speakers × six session ids: intent `multi_step_request`, Brain's only entry `unsupported` "sort by amount", the planner ran dedupe. No "didn't read that"; the removal and "I can't do this part yet: “sort by amount”." are stated; no "Done". 18 cases.
- Unit: the same ledger gives `DONE` with source `""`, no lead word, no "didn't read that".
- Note on scenario: with `intent: remove_duplicate_rows` the ledger gets an *intent* entry for dedupe (a supported Brain reading), so "Partly done" there is correct; the N2 case needs an intent that names no action.

## Tests that fail on the old code

With `buddy/` stashed back to `ef3166f`: **75 of the 129 new tests fail** (54 N1 engine cases, 18 N2 engine cases, 3 unit tests). The other 54 pass on both old and new code, by design, as regression guards: "email me the report" (18), "delete the file" alone (18) and the "don't remove the duplicates please" case (18).

## Existing tests changed

None. No existing test encoded the old filler list. The earlier H2 tests (`a4`, `b5`, `test_h2_an_unsupported_entry_naming_a_done_action_is_dropped_or_unquoted`, `test_k_*`) still pass unchanged: their dropped phrases ("remove duplicates", "Remove the duplicate rows please", "keep the duplicates", "get rid of the repeated entries") leave only function words.

## Limits

- **The set is deliberately tiny, so some harmless phrases now render the generic "can't do" sentence** where H2 used to drop them: anything containing "but", "or", "all", "these", "those", "this", "it", "to", "of", "with", or a politeness word like "pls". Example: Brain lists "but keep the duplicates" as unsupported → "Partly done — … I read part of your request as something I can't do yet." That's the intended trade: a wrong "can't do" is honest but flat; a wrong "Done —" is a false claim.
- **A verb attached to an action's object, such as "delete the duplicates", is not dropped.** Dropping it means putting the verb into that action's mention pattern (`buddy/actions.py`), which also drives the detector and `source_phrase` ordering. That's a wider change and I did not make it; today it renders the generic sentence.
- **Live evidence for N1 is still missing.** Groq read "remove duplicates and delete the file" as a plan that included a `file.delete` step, so the chain stopped and nothing was delivered (see the live runs). The N1 case needs Brain to return a broad `unsupported` phrase *and* the planner to drop the step; that didn't happen. N1 and N2 are proven by the `build_engine` tests only.
- The independent-session re-run of the review scenarios (x2–x4, a3, x1, e1) and the harness rebuild that the re-review asks for are still open.

## Deferred

Unchanged from the first fixes: L3 ("Partly done" when Brain lists something the user said to keep; mention noise), `b6` (a bare "please" `unsupported` phrase is still quoted), and the PR-A review's M3 and L1–L3. N2 was L1's widened form and is closed here.
