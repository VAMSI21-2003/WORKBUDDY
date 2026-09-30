# Review: `feat/verified-outcome-b` (PR-B of the verified-outcome design)

Review only. No code was changed. This file is the only file written or committed.

> **Reviewer independence.** I have no memory of writing this code, and no conversational memory of this branch or of the design. I read `docs/reviews/verified-outcome-b-impl.md` only to get the list of claims and risks to check. Every finding below comes from a scenario I ran.

## 0. Method

- **Trees.** The branch is at `cbde3ab`: code commit `31bbb5f` plus a docs commit. `main` is at `310f660` (`origin/main`), checked out as a temporary git worktree in the session scratchpad. Each harness run asserts that `buddy` is imported from the tree under test.
- **Harness.** The scripts live in the session scratchpad, not in the repo. Every scenario goes through the real `build_engine`, with the real registered skills and verifiers. Each run writes a fresh `.xlsx` to its own temp directory.
- **Fake LLM.** Only the LLM is faked. The provider routes on the system prompt: "intent classifier" goes to Brain, "reasoning stage" to Think, "planning stage" to the Planner, and anything else to Voice. Every call is recorded, so "the Voice model was called" is measured, not assumed.
- **Speakers.** `TemplateSpeaker`, and `PersonalityVoice` in friend and focus modes.
- **Recorded for every turn:**
  - the intent, the ledger (`Understanding.requested_actions` after `MentionCrossCheck`) and `clarification_required`;
  - the steps executed, `stopped_at` and the outputs;
  - `VerificationResult.passed` and `residuals`;
  - a fresh pandas read of the delivered file (rows, exact duplicates, fully-empty rows, sheet names);
  - the reply, and the Voice-model call count;
  - the original's SHA-256 before and after.
- **Default fixture.** The PR-A review's: 6 rows (Alice, Bob, *blank*, Alice, Carol, Bob), with 2 exact duplicates and 1 fully-empty row mid-file. Other fixtures are named where they're used:
  - `nodup`: no duplicates, 1 blank row;
  - `clean`: no duplicates or blank rows;
  - `two_empty`: X-two-empty;
  - `bt`: Alice, blank, Bob, blank, Carol;
  - `blank_only_dups`: the only duplicates are blank rows;
  - `dups_only`: duplicates, no blank rows;
  - `multisheet`.

**Run counts:**

| Harness | Branch turns | `main` turns |
|---|---|---|
| PR-A review scenarios re-run (A, N1, C1/C2/C1b, C3/C3b, N2, N3, N5, N6, N7, D ×9 wordings, the X shapes, forced final-file failure) | 348 | 348 |
| PR-B adversarial (§3) | 174 | 174 |
| Near-miss action names (H2) | 13 | 13 |
| Session-seed sweeps (catalog) | 24 | — |
| Model-text marker probe (§4) | 27 | — |
| `ResidualMeasurer` probe: verdicts and reads (§5) | 522 Inspector calls | — |
| `ResidualMeasurer` probe: forced unreadable file | 21 | — |

**The original's hash was unchanged in every one of the 1,121 recorded turns on both trees.**

## 1. Summary

| ID | Severity | Rule | New vs `main`? | Finding |
|---|---|---|---|---|
| **H1** | **High** | 1 | **Yes** | A **mention-only** ledger entry counts as "requested" on the *done* side. So a step the user told Buddy **not** to run becomes plain `DONE`. The reply then leads **"Done —"** and gets a completion closer ("What's next on the list?"), or it leads "Partly done —" when nothing requested was done. `main` says neither. This contradicts the design's own safety claim for the detector ("it can only add items to the not-done side"). |
| **H2** | **High** | 1 | **Yes** | `NO_SKILL` trusts Brain's `unsupported` label and its phrase boundaries. A single model slip gives **"Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove the duplicate rows”."** Any of these triggers it: a near-miss action name (`remove_duplicates`, `Remove_Duplicate_Rows`, a trailing space), a whole-message phrase (seen live in run 7), or a mislabel. `main` says "Done — removed 2 duplicate row(s).", which is true. |
| M1 | Medium | 1 (implied) | Partly (the residual sentence is new; the analyzer wording isn't) | "It still has 2 duplicate row(s)." followed by "None of the issues I check for were found." **misleads** (question 2d). |
| L1 | Low | 1 (reading) | Yes | `DONE_UNREQUESTED` fires when Brain's only reading is an `unsupported` entry. Then "I removed 2 duplicate row(s), though I didn't read that as part of your request." for a request the user made in so many words ("get rid of the repeated entries and sort by amount"). |
| L2 | Low | — | Yes | Repeated `NO_SKILL` sentences: the same quote twice, or the generic sentence twice. |
| L3 | Low | 1 (implied) | Yes | "Partly done —" when Brain lists something the user said to keep (`b4`, `b5`). Also mention-detector noise on unrelated words (`c4`, `c5`). Each sentence is true, but the lead word misleads. |

**The six questions, in short:**

1. **The PR-A scenarios.** Every undelivered (180), stopped (6) and unverified (18) reply is **byte-identical to `main`**. The Voice model was called 0 times on either tree. Every count in the 144 verified deliveries matches pandas.
2. **Renderer sentences and catalog entries.** Two produce false or misleading replies: H1 (the lead word and closers) and H2 (`NO_SKILL`). The implementer's three named risks all came out true in my runs (§3.3).
3. **Model text.** None reaches a reply. The one exception is a `NO_SKILL` quote, which comes only from the user's message, sanitised. H2 is about what that quote is *attached to*, not where it comes from.
4. **`ResidualMeasurer`.** It never changed a verdict (522 of 522 Inspector calls). It read only `outputs[0]`, the delivered file, and never the original. On an unreadable file it adds no residual sentence and the verdict is unchanged.
5. **Tests.** No test was skipped or marked `xfail`. Five changed assertions encode H1's rule: "Done" from a mention-only ledger. They don't hide a regression in their own scenarios, but they will have to change with the H1 fix (§6).
6. **Suite.** `python -m pytest -q`: **839 passed**, 11 warnings, in 145 s. `ruff check buddy tests`: 22 errors, the **same set as `main`** (compared with line numbers stripped).

---

## 2. Findings

### H1 (High, introduced): a mention-only entry turns forbidden or unrequested work into "Done —" and a completion closer

**Where:**
- `buddy/outcome.py:307-308`. A passing step is `DONE` whenever `ledger_by_capability` has its capability, from any source, mention included. It's `DONE_UNREQUESTED` only if no source has it.
- `buddy/outcome.py:227-229`. `request_complete` accepts any truthy `source`, so `"mention"` counts.
- `buddy/outcome.py:244`. `requested_done` for "Partly done" also accepts a mention source.
- These contradict the docstrings at `buddy/actions.py:120`, `buddy/brain.py:436` and `docs/design/verified-outcome-design.md:222`: "it can only add items to the **not done** side".
- The design's own 2.2 table ("yes, any source → `DONE`") is the cause. The implementation follows the table, not the safety claim.
- `buddy/voice_catalog.py:95-102`. The closers and `friend.opener.partial` inherit this, because they're gated on `request_complete` / `lead_word`.

**Scenarios run.** All use the default or `dups_only` fixture, a Brain reply of `multi_step_request`, and a planner that runs a step Brain's reading doesn't have. That's the same class of model error as N5, which PR-B exists to handle.

| ID | Message | Brain's reading | Planner ran | Branch reply (friend, seed "S") | `main` |
|---|---|---|---|---|---|
| `c2` | "don't remove duplicates, just remove the empty rows" | `[remove_empty_rows]` | dedupe (phrase "remove duplicates") + empty rows | "Okay, here's the rundown. **Done —** removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), … None of the issues I check for were found. **What's next on the list?**" | "…Removed 2 duplicate row(s). Removed 1 fully-empty row(s)…": no "Done", no closer |
| `g3` (12 seeds × friend/focus, `dups_only`) | same | same | same | **24 of 24** lead "Done —". Friend: 9 of 12 end with a completion closer and 4 of 12 open "That sheet was seeing double.". Focus: 6 of 12 end with "Anything else?". | 0 "Done", 0 closers |
| `c3` | "remove the empty rows but keep the duplicates" | none (no `requested_actions` field) | empty rows + dedupe (phrase "the duplicates") | "**Done —** removed 1 fully-empty row(s). Removed 2 duplicate row(s). … **What's next on the list?**" | no "Done", no closer |
| `c7` | "keep the duplicates, remove the empty rows" | none | dedupe + empty rows | "**Done —** removed 2 duplicate row(s). …" (template/focus); friend adds "What's next on the list?" | no "Done", no closer |
| `c6` | "don't remove duplicates, just remove the empty rows" | `[remove_empty_rows]` | dedupe only | "Heads-up: only part of this went through. **Partly done —** removed 2 duplicate row(s). I didn't run the empty-row removal, though I read it as part of your request. …" (3 of 12 seeds get the opener) | "Removed 2 duplicate row(s). …" |

**Why it matters:**
- The user said not to remove duplicates. The file has none left, and the reply says "Done —" and asks "What's next on the list?". Each count is true, but the lead word and the closer are the reply's only claims about the *request*, and here both are false.
- In `c6`, nothing the user asked for was done, and the reply says "Partly done" and "only part of this went through".
- **Without** the mention entry, the same `c2` turn would render the N5 disclosure that PR-B added ("I also removed 2 duplicate row(s), though I didn't read that as part of your request."). So the detector *removes* a true "not requested" line and replaces it with a completion claim. That is the opposite of its stated role.
- Deviation 3 in the impl doc argues that with no Brain reading, "Done" must be withheld (`source=""`). `c3` and `c7` show that mention entries bypass that rule: the whole "Done" there comes from a regex over a sentence that says "keep the duplicates".
- On `main` (PR-A), a `multi_step_request` turn never gets "Done —" and no closer exists, so this is a **regression**.

**What would fix it** (small):
- Required: `request_complete` and "Partly done"'s `requested_done` count only `BRAIN_SOURCES`, so a mention entry can never confirm a request.
- Recommended: when Brain gave a reading, a passing step whose only ledger match is a mention entry becomes `DONE_UNREQUESTED`. The detector then keeps its role of only adding "not done / not read" information.
- Add `c2`, `c3`, `c6` and `c7` as `build_engine` tests.
- The design's 2.2 row "yes (any source)" and the impl doc's table need the same correction.

### H2 (High, introduced): "I can't do this part yet: “remove the duplicate rows”" right after removing them

**Where:**
- `buddy/brain.py:405-409`. Any action string not *exactly* in `ACTION_VOCABULARY` becomes `unsupported`, with no `strip()` or case folding. Scenario A shows these near-misses are realistic Brain outputs.
- `buddy/outcome.py:326-332`. Every `unsupported` entry becomes `NOT_DONE`/`NO_SKILL`. Nothing checks it against what ran, or against the mention detector.
- `buddy/outcome.py:547-549`. It renders "I can't do this part yet: “<phrase>”".
- `buddy/outcome.py:246`. `NO_SKILL` is a Brain-source `NOT_DONE`, so it also produces "Partly done —" and makes `friend.opener.partial` eligible.

**Scenarios run** (default fixture; the intent is `remove_duplicate_rows` unless stated; dedupe ran and removed 2 in every row):

| ID | Message | Brain's `requested_actions` | Branch reply (template) | `main` |
|---|---|---|---|---|
| `k-remove_duplicates` (also `Remove_Duplicate_Rows`, `deduplicate`, `"remove_duplicate_rows "`: 12 turns, all 3 speakers) | "remove the duplicate rows please" | `[{"action": "remove_duplicates", "source_phrase": "remove the duplicate rows"}]` | "**Partly done —** removed 2 duplicate row(s). **I can't do this part yet: “remove the duplicate rows”.** The copy has 4 row(s), 0 duplicate row(s) remaining. …". In friend mode, 3 of 12 seeds open "Heads-up: only part of this went through." | "Done — removed 2 duplicate row(s). …" |
| `k-multi-nearmiss` | "remove duplicates then remove empty rows" | `remove_duplicates` + `remove_empty_rows`; both steps ran | "Partly done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). **I can't do this part yet: “remove duplicates”.**" | no lead; both removals stated |
| `a1` (question 2a) | "remove duplicates and sort by amount" | dedupe "remove duplicates" + `unsupported` "remove duplicates and sort by amount" (the whole message) | "Partly done — removed 2 duplicate row(s). **I can't do this part yet: “remove duplicates and sort by amount”.**" | "Done — removed 2 duplicate row(s)." |
| `a2` / `a3` | same | only the whole-message `unsupported` / the same as a `multi_step_request` | same sentence | same as above |
| `a4` | same | `unsupported` "remove duplicates" | "Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove duplicates”." | "Done — …" |
| `a5` | "sort by amount and remove duplicates" | `unsupported` "sort by amount and remove duplicates" + dedupe | "I can't do this part yet: “sort by amount and remove duplicates”." | — |
| `b5` | "remove the empty rows but keep the duplicates" | empty + `unsupported` "keep the duplicates" | "Partly done — removed 1 fully-empty row(s). **I can't do this part yet: “keep the duplicates”.** … It still has 2 duplicate row(s)." The duplicates *were* kept. | — |
| `b6` | "remove the duplicate rows please" | dedupe + `unsupported` "please" | "Partly done — … I can't do this part yet: “please”." | — |

**Question 2a, answered: yes.** With a whole-message phrase, the reply says Buddy can't do "remove duplicates and sort by amount" right after saying it removed the duplicates. The implementer's "Known issues" dismisses broad phrases because "a broad quote would still be the user's own words". That's true of where the words come from, but the sentence around them is Buddy's claim that it **can't** do that part, and that claim is false for the part it did. Live run 7 shows Groq returns a whole-message `source_phrase` (for a supported action there, where it's harmless), so this isn't hypothetical.

**Why it's High:**
- It's a code-rendered false statement: Buddy says it can't do the exact thing it just did.
- It downgrades a fully done request to "Partly done", with a friend opener saying "only part of this went through".
- It's triggered by one model slip, which the design's whole premise says to expect. The near-miss case doesn't even need a wrong reading: the model spelled the action name slightly off.
- `main` is correct in the `k-*` rows.

**What would fix it** (small; the implementer should pick):
- Normalise action names before the vocabulary check (strip, casefold).
- Never render `NO_SKILL` for an `unsupported` entry whose phrase contains a mention-pattern match (or the ledger phrase) of an action that is `DONE`. Either drop the entry, or render the unquoted generic sentence, and only when the phrase has words outside the done action's match.
- Collapse duplicate `NO_SKILL` entries (L2).
- Add `build_engine` tests for `k-remove_duplicates`, `a1`, `a4` and `b5`.

### M1 (Medium, partly pre-existing): "It still has 2 duplicate row(s)." next to "None of the issues I check for were found." (question 2d)

**Where:** the residual sentence comes from `buddy/outcome.py:559-573`. The analyzer's `NO_ANALYSIS_FINDINGS` is at `buddy/inspector.py:60` and `:625`.

**Scenarios run:** `d1` (N1), `d2` ("remove the empty rows but keep the duplicates"), C1b, C3b and `i2`, all three speakers. For example, template N1: "Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: remove empty rows. The copy has 5 row(s), 0 fully-empty row(s) remaining. It still has 2 duplicate row(s). Saved as `…`. None of the issues I check for were found."

**Answer: it misleads.** It isn't false: the analyzer doesn't check exact duplicates. But the last sentence the user reads is an unqualified all-clear, two sentences after a stated defect. The reader has to guess that "the issues I check for" excludes the one just named. It's still **better than `main`**: on `main`, N1 said "Done — removed 1 fully-empty row(s). … None of the issues I check for were found." and never said the 2 duplicates remained.

**Not blocking**, because each sentence is true and the branch improves on `main`. But the fix is cheap: when any residual `remaining > 0`, qualify the analyzer's sentence (for example "none of the other issues I check for were found") or leave it out. This is backlog L-4. The implementer already added it there.

### L1 (Low): `DONE_UNREQUESTED` when Brain's reading is only an `unsupported` entry

- **Where:** `buddy/outcome.py:292` (`has_reading` counts `unsupported` entries) and `:308`.
- **Scenario `e1`:** the message is "get rid of the repeated entries and sort by amount". Brain's reading is `[unsupported "sort by amount"]`. The planner ran dedupe with the phrase "get rid of the repeated entries".
- **Reply:** "I removed 2 duplicate row(s), though I didn't read that as part of your request. I can't do this part yet: “sort by amount”. …"
- **Why it's wrong:** the user did ask, and Buddy's planner quoted the user's own words for that step. Under `build_engine`, `DONE_UNREQUESTED` is reachable only through `ClaudePlanner`, which drops any step with an empty `source_phrase`. So a planner-side reading always exists. The sentence is true of *Brain's* reading only.
- **Suggestion:** require a supported action in Brain's reading before claiming "unrequested". Otherwise use the `source=""` path (Deviation 3).

### L2 (Low): repeated `NO_SKILL` sentences

- `b2`: "I can't do this part yet: “sort by amount”. I can't do this part yet: “sort by amount”."
- Marker probe `mt-single`: "I read part of your request as something I can't do yet." appears twice, around a quoted one.
- `brain_ledger` de-duplicates only supported actions (`buddy/actions.py`, `brain_ledger`).

### L3 (Low): "Partly done" and noise from misread or incidental mentions

- **`b4`.** Brain lists `remove_duplicate_rows` with the phrase "keep the duplicates". The reply is "Partly done — … I didn't run the duplicate-row removal, though I read it as part of your request." The sentence is true of the reading, but "Partly done" misstates the request. It needs Brain to break its prompt rule.
- **`c4`.** For "I already deduplicated this; remove the blank rows", the reply says "Your message mentions duplicate rows, but I didn't run the duplicate-row removal." The message said "deduplicated". "Done" and the closers are withheld from a request that was fully met. That's honest noise, and D2 accepts it.
- **`c5`.** "blank lines … are intentional" gives the same kind of noise.

---

## 3. Every renderer sentence and catalog entry

### 3.1 Renderer sentences (`buddy/outcome.py`)

| Sentence | False or misleading case found? | Scenarios |
|---|---|---|
| "Done — …" lead | **Yes (H1):** a mention-only entry confirms forbidden or unrequested work | `c2`, `c3`, `c7`, `g3` |
| "Partly done — …" lead | **Yes (H1, H2, L3):** nothing requested was done (`c6`), everything was done (`k-*`, `a1`), or Brain listed a kept action (`b4`, `b5`) | as listed |
| "Removed N …" / "There were no … to remove." / "…ran and passed its check." | No. Every count in 144 + 174 + 13 + 24 + 27 verified turns matched pandas (the removed total equals the rows-in minus rows-out delta) | all |
| "I (also) removed N …, though I didn't read that as part of your request." | Only L1 | N5, `e1`, `mt-n5` |
| `NOT_PLANNED` "I didn't run the …, though I read it as part of your request." | No. True by derivation in C1, C1b, C3, `b3` (the planner dropped a step with an empty `source_phrase`) and `b7` (intent and ledger disagree) | as listed |
| `NOT_READ_AS_REQUEST` "Your message mentions …, but I didn't run the …" | No false case. It holds even when the other step removed rows of that kind: in `i2` (`two_empty`, N1), empty-row removal took 2 blank rows (1 of them a `duplicated()` row), and the sentence names the *step*, so it stays true. Noisy for negations and incidental words (L3). | N1, C3b, `c1`, `c4`, `c5`, `d2`, `i1`, `i2` |
| "I read your request as: …" | No. With the intent fallback only (N1 with no ledger field), it states Brain's intent, which is what ran | N1-none, C3-noledger |
| `NO_SKILL` quoted / generic | **Yes (H2)**, plus L2 | `a*`, `b5`, `b6`, `k-*`, `b2` |
| `NOT_CONFIRMED` | Not reachable through `build_engine`. A failing file-changing step stops the chain, and the turn is undelivered (N6, forced failures) | — |
| Residual "It still has N …" / "It has no …" | Counts match pandas in every run. Misleading next to the analyzer's all-clear (M1) | N1, C1, C1b, `d*`, `mt-n5-clean` |

### 3.2 Catalog entries (`buddy/voice_catalog.py`)

| Entry | Predicate | Can it be eligible while false or misleading? |
|---|---|---|
| `friend.opener.neutral` / `rundown` | always | No claim. |
| `friend.opener.seeing_double` | a `DONE` dedupe with `removed_data >= 1` | The fact is always true. But after H1 it opens replies to "don't remove duplicates" (`g3`: 4 of 12). It resolves with the H1 fix. |
| `friend.opener.blank_rows` | a `DONE` empty-rows step with `removed_data >= 1` | The fact is true. In N1 it praises the step the user didn't ask for (as PR-A's review noted). It's no longer followed by "Done". Low. |
| `friend.opener.nothing_to_remove` | `request_complete` and every `removed == 0` | Not false in any run: `g1`, 5 of 12 on the `clean` fixture. With H1, `request_complete` can be mention-sourced, but "nothing to remove" stays true whenever every step removed 0. |
| `friend.opener.partial` | `lead_word == "Partly done"` | **Yes**, inheriting H1 (`c6`: nothing requested went through) and H2 (`k-*`: everything went through). 3 of 12 seeds in each. |
| `friend.closer.anything_else` / `whats_next`, `focus.closer.anything_else` | `request_complete` and `open_findings == 0` | **Yes (H1):** `c2`, `c3`, `c7`, `g3`. Otherwise correct: `g1` and `g2` are real complete requests. |
| `friend.closer.none`, `focus.opener.none`, `focus.closer.none` | always | Empty. |

The catalog's predicates are what the impl doc says. The false cases all come from `request_complete` and `lead_word` (H1, H2), not from the catalog itself.

### 3.3 The implementer's named risks

- **`NOT_READ_AS_REQUEST` when another step removed rows of that kind:** holds (`i1`, `i2`, above).
- **`DONE_UNREQUESTED` with an empty Brain reading:** holds. N5-noledger gives plain `DONE` with no "unrequested" claim, and no "Done". But the opposite gap exists: a reading made only of `unsupported` entries (L1).
- **"I read your request as" from the intent fallback only:** holds.

---

## 4. No model text in replies

- **Voice model:** 0 calls on the branch in every recorded turn. The same is true on `main` since PR-A.
- **Marker probe** (27 delivered turns, 3 speakers, fixtures `default` / `two_empty` / `clean`, single-intent, multi-step and N5 shapes):
  - I put distinct markers (`ZQX1`–`ZQX17`) into every model-written field. On the Brain side: `source_phrase` values not in the message, a bogus action name, `notes`, `missing_information`, `ambiguity_flags` and `referenced_files`. On the Think side: `objective`, `summary`, `recommended_strategy`, `assumptions` and `risks`. On the planner side: `description` and a `source_phrase` not in the message. Plus the Voice reply itself.
  - **None reached any reply.**
  - The only quoted text was `“email ZQXU to bob”`: the user typed it, and Brain's phrase was a substring of the message.
- **The quote is the user's words, but not always a verbatim substring.** `_quoted_phrase` removes backticks and quote marks, collapses whitespace and truncates at 80 characters. In `h1`, the user's text "then \`rm -rf\` \"everything\" xxx…" is shown as "then rm -rf everything xxx…". Characters are only removed; none are added, so the quote comes only from the message.
- **Brain phrases not in the message** always render the generic sentence (`b8`, `h2`, `mt-*`).
- **Routing is unaffected by the ledger.** Think and the Planner read only `intent`, `referenced_files`, `missing_information` and `ambiguity_flags` from `Understanding` (`buddy/think.py:368-380`, `buddy/planner.py:125`, `:679`, `:716`). Malformed `requested_actions` values (a string, a list of ints or `None`) parse to no entries, and the turn delivers as normal (`h3`, `h4`). The steps and outputs were identical to `main` in every PR-A scenario.

## 5. `ResidualMeasurer`

**How I checked it:** I wrapped `ResidualMeasurer.__call__` and recorded:
- the inner verifier's result against the wrapper's;
- every `pd.read_excel` call made *after* the inner verifier returned.

**Result:** across 522 Inspector calls (the PR-A and PR-B harnesses):
- **0 verdict changes.** The wrapper's result equals the inner result in every field except `residuals`, and the inner result's `residuals` was always empty.
- **315 residual reads, every one of `execution_result.outputs[0]`**, which is the delivered file (every delivery had exactly one output).
- **0 reads of the original.**
- No reads on undelivered, stopped or unverified turns (the early return at `buddy/inspector.py:941-947`).

**Forced unreadable delivered file** (21 turns: C1, N1, N5, C3b and D4 × speakers): `residuals` is empty, **no** "still has / has no" sentence appears, and the verdict still passes.

**Rule 2** held in every run (§0).

## 6. Test changes (`git diff origin/main...HEAD -- tests/`)

I read every removed `def test_` and `assert` line. No test was skipped, marked `xfail` or disabled. The removed and changed lines match the impl doc's table exactly.

| Change | Weakened, inverted or deleted? | Does it hide a regression? |
|---|---|---|
| `test_full_slice_reports_a_failed_re_check…` (`tests/buddy/test_engine_factory.py:964`) and `…_when_the_initial_analysis_fails` (`:1060`): `"Done" not in reply` became `startswith("Done — …")` | **Inverted** | Not in their own scenario, because the message really asks for both. But the comments say the "Done" comes from the mention detector alone ("this Brain reply has no requested_actions"). That's H1's rule, and these assertions will need to change with its fix. |
| `test_h1_removed_counts…` chain case (`:1683`): `"Done —" not in reply` became `count("Done — removed") == 1`. The closer check changed from "never" to "only with 'Done — '". | **Inverted / relaxed** | Same as above: the Brain fixture has no `requested_actions`, so this is mention-only "Done". |
| `test_outcome.py:224` (`test_lead_word`): `(dedupe brain, empty mention)` → "Done", and `:248` "a mention-only action that was done counts as requested and done" | **Rewritten** | These rows *assert* H1's rule as intended behaviour. |
| `test_a_dropped_second_action_never_yields…`: the number set gains the residual. New asserts: the `NOT_READ_AS_REQUEST` sentence, the residual, no "Done —". | Stricter | No. |
| `test_no_entry_claims_there_was_nothing_to_remove`, `test_no_closer_implies_the_request_is_finished` | **Deleted** | They're replaced by gated tests (`…unless_the_request_is_complete`, `…closers_come_back…`, `…nothing_to_remove_needs…`). The replacements are only as strong as `request_complete`, which is where H1 lives. None of the 22 reports in `test_every_entry_is_eligible_only_when_its_text_is_true` has a mention-only `DONE` next to a Brain reading that excludes it. |
| `test_requested_comes_only_from_a_single_action_intent` → `test_with_no_requested_actions_the_ledger_is_brains_single_action_intent` | Replaced | No. It's the same rule, on the ledger. |
| `test_voice.py` (2), and the `test_outcome.py` signature changes | Mechanical | No. |

**Net:** no changed test hides a regression in the scenario it covers. But five assertions encode H1's rule, and no test covers a negated or forbidden action, or a near-miss or broad `unsupported` phrase. That's why H1 and H2 pass CI.

## 7. Suite, lint and live runs

- `python -m pytest -q`: **839 passed**, 11 warnings, in 145.5 s. This matches the impl doc's count.
- `ruff check buddy tests`: **22 errors**, identical to `main`'s set with line numbers stripped. None are new.
- **Live runs:** I didn't repeat them. `docs/reviews/verified-outcome-b-live-runs.md` reports 6 of 6 on `31bbb5f`. Its run 7 is the evidence that Brain returns whole-message phrases (H2). The fixes for H1 and H2 change delivered replies, so `CLAUDE.md` requires new live runs on the fixed head.

---

## 8. Verdict: **DON'T MERGE**

**What's solid, verified by running it:**
- The undelivered, stopped and unverified paths are byte-identical to `main` (204 of 204 turns).
- The Voice model is never called, and no model-written text reaches a reply.
- Every count matches pandas.
- `ResidualMeasurer` never changes a verdict and never reads the original.
- Rule 2 held in all 1,121 recorded turns.
- The ledger does fix what PR-A's review left for it: N1, C1, C3, N5 and "dedupe and sort" now state what didn't run, with true residual counts, and M2's "Done —" on a misclassified request is gone.

**Why not merge yet:**
1. **H1 is a new rule-1 regression.** On a turn where the planner ran something the user forbade ("don't remove duplicates"), the reply now says "Done —" and "What's next on the list?". `main` says neither. The mention detector, which was meant only to *add* "not done" lines, is what makes it say "Done". The fix is a two-line change to `request_complete` and `lead_word` (plus, ideally, mapping mention-only matches to `DONE_UNREQUESTED`). Five test assertions will need to change back.
2. **H2 is a new code-rendered false sentence.** "I can't do this part yet: “remove the duplicate rows”" appears right after "removed 2 duplicate row(s)". It's triggered by one near-miss action name or a broad phrase, and live runs show Brain produces broad phrases. `main` is correct there. The fix is small: normalise action names, and never render `NO_SKILL` over a phrase that names an action which was done.

**The re-review needs H1 and H2 fixed**, with `build_engine` tests for `c2`, `c3`, `c6`, `c7`, `k-remove_duplicates`, `a1`, `a4` and `b5`, followed by live runs. M1 and L1–L3 can go to the backlog.

## Deferred

These don't block merging, but they should go into `docs/backlog.md`:

- **M1:** qualify or leave out the analyzer's "none of the issues I check for were found" when a residual is non-zero. Extends the existing L-4 note.
- **L1:** don't claim "unrequested" when Brain's reading has no supported action.
- **L2:** collapse duplicate `NO_SKILL` sentences.
- **L3:** "Partly done" when Brain lists an action the user said to keep. Mention-detector noise on "deduplicated" and "blank lines". Both follow from D2 and the prompt rule; measure them with the ledger-recall set (design 6.8).
- **Carried from the PR-A review, unchanged here:** M3 (multi-sheet: `X-multisheet` still says "Done — removed 1 duplicate row(s). The copy has 2 row(s)…" with sheet `Q2` dropped), and L1–L3 from that review.
