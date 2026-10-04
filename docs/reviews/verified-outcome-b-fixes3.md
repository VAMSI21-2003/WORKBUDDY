# Third fixes for `feat/verified-outcome-b` (re-review 2: P1, P2, P3, Q1)

**Review:** "Re-review 2 (fixes commit `3ec8a8c`)" in `docs/reviews/verified-outcome-b-review.md` (verdict: DON'T MERGE). Section 6 ("Exact fix needed") was the specification, implemented as written in `_no_skill_outcome` and `_FILLER_WORDS` in `buddy/outcome.py`. Nothing else under `buddy/` changed. No word was added to any set beyond what section 6 lists.
**Not merged.** The session that wrote this code must not review it; a separate session still has to re-review it (harness plus the review's 32 adversarial scenarios).
**Suite:** `python -m pytest -q` gives **1281 passed** (1137 before, +144 new engine cases; the two updated unit tests were edited, not added). `ruff check buddy tests`: 22 errors, the same count as head before this change (and on `main`); none are new, and `ruff check buddy/outcome.py` is clean.
**Live runs:** `docs/reviews/verified-outcome-b-live-runs.md`, "After the third review fixes": 4 of 4 completed, no 429.

## Causes

- **P1 (`s22`).** The second loop removed Brain's quoted words for a done action from an `unsupported` phrase even when those words were the whole message ("get rid of the repeated entries and delete the file"), so a "delete the file" part vanished and the reply said "Done —".
- **P2 (`t23`).** The first loop removed the mention pattern of every action, including one that never ran and has no sentence of its own ("remove empty rows" in Brain's own words, not found in the message). The request for it was dropped silently.
- **P3 (`f10`).** `keep`, `leave`, `only` and the negations were filler without a condition. "keep only the duplicate rows" left only filler after the action's object was removed, so a request Brain flagged was dropped behind "Done —".
- **Q1 (`g26`–`g30`).** `remove` was in the filler set but no other removal verb, determiner or preposition was. Ordinary phrasing around a done action ("all", "delete", "can you", "from my sheet") left a word over and gave "Partly done … can't do yet" on a request that was fully done. This one was introduced by `3ec8a8c`.

## Changes (all in `_no_skill_outcome` / `_FILLER_WORDS`, per section 6)

1. The phrase is normalised first: `casefold()` and curly `’` → `'` (also applied to Brain's quoted words for done actions).
2. Each action is stripped as removal verb + determiners + object (`remove|delete|drop|clear|erase|eliminate|get rid of|take out`, then `all|any|every|each|the|these|those|this|that|my|our|your|of`), then one location phrase (`from|in|on|across` + optional determiner + `sheet|file|spreadsheet|workbook|table|data`) is removed. `remove` leaves `_FILLER_WORDS`, so a bare "remove it" or "remove all" stays as a leftover.
3. `_FILLER_WORDS` is `a an the please pls and then also just only so but can could would will you i we to do dont don't not no never keep leave`: function words only. The guard test's `allowed` set matches it.
4. The pattern loop records `named_passed` (a matched action's capability passed) and `named_silent` (a matched action didn't pass and has no ledger entry of its own).
5. Brain's quoted words for a done action are handled after the pattern loop. If they occur in the phrase, the entry counts as naming an action (done work is never quoted as "can't do"). They are removed only if they contain no joiner (`and|then|also|plus` or `, ; & +`), and then `named_passed` is set.
6. The generic `NO_SKILL` outcome is returned if a word is left over; or a negation/keep word (`dont don't not no never keep leave`) is present and `named_passed`; or `named_silent` and no negation word is present. Otherwise the entry is dropped.

Choices where section 6 is silent (flagging them rather than hiding them): the negation check runs on the text left after all stripping (not the raw phrase), and the location phrase is removed right after the action loop and before Brain's quoted words are looked for. Neither changes any case in the review's table or my tests.

## Tests

All engine cases run through `build_engine` for template, friend and focus × six session ids (`s1`–`s6`), like the N1 tests.

**New (144 cases)**
- `test_p1_p2_p3_a_flagged_part_is_never_dropped_behind_a_done_lead`, ids `s22`, `t23`, `f10` (54 cases): no "Done —", and the reply contains "I read part of your request as something I can't do yet."
- `test_q1_a_fully_done_request_leads_done_with_no_cant_do`, ids `g26`–`g30` (90 cases): the reply contains "Done — removed N …" and neither "can't do" nor "Partly done". The friend voice puts an opener before the lead, so the check is "in reply", the same as the existing tests.

**Existing tests changed (3), and why**
1. `tests/buddy/test_outcome.py::test_n1_a_leftover_content_word_keeps_the_generic_not_done_sentence`: it pinned the old behaviour. "delete the duplicates" and "remove all duplicates" are now `[]` (a removal verb + object is the action's own words). In the unit's setup the dedupe passed, so "keep the duplicates" and "don't remove the duplicates please" are now `[""]` (a keep/negation word next to a done action); "but keep the duplicates" stays `[""]`, now for that reason, since "but" became filler.
2. `tests/buddy/test_outcome.py::test_n1_the_filler_set_holds_only_function_words`: the `allowed` set is the new filler set, and `remove` is added to the list of words that must not be in it.
3. Both are in section 6's list; nothing else was changed. The existing N1 and N2 engine cases, `x2`–`x6`, `e2`, `k-*` and `b5` pass unchanged.

**Pre-fix check** (with `buddy/` stashed back to `842aaa2`): the 144 new engine cases all fail (18 per ID), and so do the two updated unit tests. The unchanged N1/N2/H2 engine and unit tests pass on both.

## Limits

- The word lists are still lists. Brain phrasing that uses a verb or noun outside them leaves a word over, which gives the generic sentence ("Partly done"), the safe side.
- L-a (negated unsupported clauses say "Partly done"), L-b (function-word-only phrases quoted) and L-c (an unrequested step with only `unsupported` Brain entries) are unchanged; see "Deferred" in the review.
- The harness and the review's 32 adversarial scenarios were **not** re-run here: the harness isn't in this branch, and re-running my own fix isn't an independent check. The reviewer's prototype of section 6 gave 532 byte-identical harness turns; this implementation was written from the same text but has not been compared against that prototype.

## Residual risks (section 6; also in the code comment on `_no_skill_outcome`)

- A done action's phrase with no joiner that contains an unsupported verb (Brain gives "delete the file" as the dedupe phrase) is still dropped.
- A double negation in a paraphrase ("don't keep the empty rows") counts as a negation.
- Both need a gross misread by Brain. Closing them needs the ledger-recall measurement (backlog item 1, design 6.8), not more words.
