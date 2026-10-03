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
- `buddy/voice_catalog.py:95-112`. The closers and `friend.opener.partial` inherit this, because they're gated on `request_complete` / `lead_word`.

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

---

# Re-review (fixes commit `ef3166f`)

**Verdict: DON'T MERGE.** H1 and M1 are fixed. H2's named scenarios are fixed, but the H2 fix introduced a new false "Done —" (N1 below).

> **Independence caveat.** This re-review was run by the same session that wrote the fixes, at the user's explicit request, after the session objected that `CLAUDE.md` requires a separate session with no memory of the code. It is not the independent review the working method calls for. I tried to attack the fix rather than confirm it, and that found N1, but a separate session should still re-run this before anything merges. The harness is not in the repo (it lives in the session scratchpad), so it should also be rebuilt from the tables below.

## Method

- **Tree.** `origin/feat/verified-outcome-b` at `ef3166f` (local and remote heads equal). Comparison trees: `origin/main` (`310f660`) and the pre-fix head `3617c9f`, each as a temporary git worktree. The harness asserts that `buddy` is imported from the tree under test.
- **Harness.** The real `build_engine` with the real skills and verifiers, and a fake LLM routed by system prompt (Brain / Think / Planner / anything else = Voice, counted). It has 38 scenarios × (template + friend × 12 session ids + focus = 14 turns) = **532 turns per tree**. Each turn records the reply, the steps, `delivered`, a fresh pandas read of the original and of the delivered file, the Voice-model call count and the original's SHA-256 before and after.
- **Scenarios.** Every scenario from the review (c2, c3, c6, c7, k-remove_duplicates and its near-miss spellings, k-multi, a1–a5, b2, b4, b5, b6, e1, M1's N1 and d2), plus new ones aimed at the fixes (x1–x10, c8, e2, r-*).

## Results

| Check | Result |
|---|---|
| `python -m pytest -q` | **1008 passed** (181 s) |
| `ruff check buddy tests` | 22 errors, identical to `main` with line numbers stripped |
| Voice-model calls, all 532 turns | **0**; the model's marker text never reached a reply |
| Original file changed (rule 2), all 532 turns | **0** |
| Undelivered turns (chain stop, unclear): head vs `main` | **28 of 28 byte-identical** |

### H1: fixed

| Scenario | Head (template; friend × 12 and focus checked for the same markers) |
|---|---|
| `c2` "don't remove duplicates, just remove the empty rows" | "Removed 1 fully-empty row(s). I also removed 2 duplicate row(s), though I didn't read that as part of your request. …" No "Done", "Partly done", closer, or "seeing double" opener in any of 14 replies. |
| `c3`, `c7` (no `requested_actions`) | Both removals stated plainly. No "Done", "Partly done", or closer in any of 14 replies each. |
| `c6` | "I removed 2 duplicate row(s), though I didn't read that as part of your request. I didn't run the empty-row removal, though I read it as part of your request. … It still has 1 fully-empty row(s)." No "Partly done" and no "only part of this went through". |
| `c8` (negation, "do not dedupe, only remove blank rows") | Dedupe stated as not run; the residual says 2 duplicates remain. |
| `x7` (Brain missed one of two real requests) | Honest but flat: "I also removed 2 duplicate row(s), though I didn't read that as part of your request." No "Done". This is the accepted cost of D2 and H1. |
| `x8` (both actions listed by Brain) | "Done —" still leads, and friend gets closers (3 of 12 "Anything else", 4 of 12 "What's next"). |

Minor: for `c3` and `c7`, 2 of 12 friend replies open "That sheet was seeing double." The dedupe did remove rows, so the sentence is true, but it sits on a request that said to keep them. There's no reading from Brain in those scenarios, so nothing in the ledger can say so. Low.

### H2 near-miss and broad phrases: fixed

All 5 spellings of the action name (`remove_duplicates`, `Remove_Duplicate_Rows`, `"remove_duplicate_rows "`, `deduplicate`, `REMOVE_DUPLICATE_ROWS`) give "Done — removed 2 duplicate row(s).", with no "can't do" and no "Partly done" (14 of 14 turns each). Also:
- `a1`, `a2`, `a5`: "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet." The whole-message quote is gone.
- `a4`, `e2`: the entry is dropped, and the reply says "Done —".
- `b5`: "keep the duplicates" is dropped; the reply is the N1-style block and no "Partly done".
- `b2`: one sentence. `k-multi-nearmiss`: two real actions, no false "can't do".

### M1: fixed

N1, d2 and c8 show "It still has 2 duplicate row(s)." followed by "None of the **other** issues I check for were found." With no residual (`m1-clean`) the sentence is unchanged.

## New findings

### N1 (High, introduced by the H2 fix): a real unsupported request vanishes, and the reply says "Done —"

**Where:** `buddy/outcome.py`, `_FILLER_WORDS` / `_no_skill_outcome`. The filler list contains the verbs "delete", "drop", "clean", "clear", "erase", "strip", "get", "rid" and the nouns "file", "sheet", "spreadsheet", "report", "data", "rows", "lines". If an `unsupported` phrase names an action and everything left is in that list, the entry is dropped.

**Scenarios** (default fixture, dedupe ran; Brain's `unsupported` entry is a broad phrase that begins with the dedupe words, the same shape as live run 7's whole-message phrase):

| ID | Message | Brain's `unsupported` phrase | Pre-fix head (`3617c9f`) | Fixed head |
|---|---|---|---|---|
| `x2` | "remove duplicates and delete the file" | "remove duplicates and delete the file" | "Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove duplicates and delete the file”." | **"Done — removed 2 duplicate row(s)."** (14 of 14 turns, all speakers) |
| `x3` | "remove duplicates and clean the data" | "remove duplicates and clean the data" | "Partly done — … can't do …" | **"Done —"** (14 of 14) |
| `x4` | "remove duplicates and drop the sheet" | "remove duplicates and drop the sheet" | "Partly done — … can't do …" | **"Done —"** (14 of 14) |
| `x5` "…and email me the report" | | same shape | "Partly done" | "Partly done … something I can't do yet" (correct) |
| `x6` "delete the file" alone | | "delete the file" | "Partly done … can't do: “delete the file”" | same (correct: no action named) |

**Why it matters:**
- Brain flagged a part of the request as unsupported, and the reply now drops it and leads "Done —". That's a code-rendered completion claim for a request Buddy itself knows it only partly did. `main` also says "Done —" here, because it ignores the ledger, so it isn't a regression against `main`. But it is a regression against the pre-fix branch, and it defeats the purpose of the ledger.
- It needs a broad phrase from Brain, which live run 7 shows happens, and one of the filler words. "email", "sort", "chart" and any other non-listed word are safe.
- The filler list was written to drop "please" and "keep" around an action name. It doesn't need the verbs as a class or any of the nouns.

**Suggested fix (small):**
- Reduce `_FILLER_WORDS` to articles, politeness, negation and keep/leave words, and the removal verbs ("remove", "delete", "drop", "get", "rid") only.
- Remove all nouns ("file", "sheet", "spreadsheet", "report", "data") from the list. Also remove "clean", "clear", "erase", "strip".
- Keep "rows", "lines", "entries" and "records" only if the mention pattern has already consumed the word before them.
- Add `build_engine` tests for `x2`, `x3` and `x4`: each must show "Partly done —" and the generic sentence, never "Done —".

### N2 (Low–Medium, widened by the H1 fix; was L1): "I didn't read that as part of your request" when Brain's phrase names the action

**Scenarios:** `a3` (Brain's only entry is an `unsupported` whole-message phrase "remove duplicates and sort by amount", and the planner ran dedupe), `x1` and `e1` (Brain's only entry is `unsupported` "sort by amount").

**Head:** "I removed 2 duplicate row(s), though I didn't read that as part of your request. I read part of your request as something I can't do yet." Pre-fix, the mention entry made the dedupe `DONE`, giving "Partly done — removed 2 duplicate row(s)…". Now the reply tells the user Buddy didn't read "remove duplicates" as part of a request whose text says it (a3 quotes it in full). The sentence is about Brain's reading, but with an `unsupported` entry that contains the words, it misleads. L1 already covers it. The H1 change widens it: before, a mention entry made these steps `DONE`, so `a3` and `x1` got "Partly done" instead of the "didn't read" sentence.

**Suggested fix (small):** `has_reading` should require a supported action in Brain's reading, as L1 says. Then these turns take the "no reading" path (`source="mention"`, no claim, no lead word). Not blocking on its own, but it's the same function as N1.

### Low, unchanged

- `b6`: a bare "please" `unsupported` entry still renders "I can't do this part yet: “please”." and "Partly done". The fix's notes say this was left on purpose; it needs a model slip to happen.
- `b4` (L3): "Partly done" when Brain lists an action the user said to keep. Deferred (backlog).

## Regression summary

| Area | Status |
|---|---|
| Undelivered, stopped, unverified replies vs `main` | 28 of 28 byte-identical |
| Voice model | 0 calls, no model text in any reply |
| Rule 2 | original unchanged in 532 of 532 turns |
| Counts | every "removed N" / "still has N" / "The copy has N" matched the pandas read in the turns I spot-checked (k, c2, c6, b5, x8, x10) |
| Suite and lint | 1008 passed; 22 lint errors = `main` |

## Verdict: DON'T MERGE

1. **N1 blocks.** The H2 fix turns a wrong-but-flagged "Partly done … can't do: <whole message>" into a clean "Done —" when the unsupported part is phrased with a listed verb or noun. The fix is a few lines in `_FILLER_WORDS` plus three `build_engine` tests.
2. N2 is not blocking, but it should be fixed together with N1 (same function: `has_reading`).
3. After both, a separate session should re-run these scenarios (x2–x4, a3, x1, e1 especially) and then the live runs.

## Deferred

- N2 if not fixed now; L3; `b6`; carried over from the PR-A review: M3 and its L1–L3.

# Re-review 2 (fixes commit `3ec8a8c`)

**Verdict: DON'T MERGE.** N1 as reported (x2, x3, x4) is fixed, and so is N2. But the same function still has three paths to a false "Done —" (HIGH; they were there at `ef3166f` too, and `main` says "Done —" in all of them). The N1 fix also adds a new false "Partly done … I can't do yet" on fully completed requests, H2's own near-miss-name case among them (MEDIUM; `ef3166f` and `main` both say "Done —"). The exact fix is below. A prototype of it in a scratch copy closes all eight cases and leaves the 532 harness turns unchanged.

> **Independence.** This was run by a separate session with no memory of writing PR-B or its fixes, as `CLAUDE.md` requires. I didn't modify anything under `buddy/` or `tests/` in the repository. The prototype fix and the guard-test mutation were done in throwaway copies (`git archive HEAD`) in the session scratchpad, and are not committed. Because I wrote the prototype, I must not review the real fix.

## Method

- **Trees.** Head is `origin/feat/verified-outcome-b` at `3ec8a8c`. Comparison trees, each a temporary `git worktree`: `origin/main` (`310f660`) and the previous head `ef3166f`.
- **Harness.** `scripts/review_harness` from `origin/review/pr-b-harness`, used as is: the real `build_engine` and real skills and verifiers, with a fake LLM routed by system prompt. It runs 38 scenarios × 14 turns (template, friend × 12 session ids, focus) = 532 turns per tree. It isn't committed to this branch.
- **Adversarial set.** 32 new scenarios (448 turns per tree), with the same mechanics as the harness. The script and its output are in the session scratchpad, and every scenario is in the table below, so the set can be rebuilt from this document. Fixture: the harness's 6 rows (2 duplicates, 1 empty row).
- **Scope.** The diff `ef3166f..3ec8a8c` (`_FILLER_WORDS`, the curly apostrophe and `has_reading` in `buddy/outcome.py`), plus `_no_skill_outcome` as a whole, because N1's claim ("a real unsupported part is never dropped behind a Done lead") is a claim about that function. I didn't re-open earlier decisions (D2, H1's `DONE_UNREQUESTED`, the L3 deferral).
- **Not run:** live runs with Groq. They weren't part of this review's scope, and the verdict doesn't depend on them.

## 1. Suite and lint

| Check | Result |
|---|---|
| `python -m pytest -q` | **1137 passed**, 0 failed (262 s) |
| `ruff check buddy tests` | 22 errors on head, 22 on `main`. Compared by file and rule with line numbers removed, the two lists are identical: 15 E501, 3 UP017, 2 I001, 1 B905, 1 UP037. **No new lint errors.** |

## 2. Harness: head vs `main` (and `ef3166f`)

| Check | Head | `main` | `ef3166f` |
|---|---|---|---|
| Turns, errors | 532, 0 | 532, 0 | 532, 0 |
| Voice-model calls | **0** | 0 | 0 |
| Original changed (rule 2) | **0 of 532** | 0 | 0 |
| Delivered / undelivered | 504 / 28 | 504 / 28 | 504 / 28 |
| Undelivered turns (`r-chain-stop`, `r-unclear`) equal to `main` (reply, steps, delivered) | **28 of 28** | | |

Between `ef3166f` and head, replies changed only in `x2`, `x3`, `x4` (N1) and `a3`, `x1`, `e1` (N2), 14 of 14 turns each. Every other scenario is byte-identical.

The adversarial set gives the same picture: 448 turns per tree, 0 Voice calls and the original unchanged in every turn on all three trees.

## 3. N1: fixed as reported

| ID | Head, 14 turns | "Done —" |
|---|---|---|
| `x2` "…and delete the file" | "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet." 14/14 | 0 |
| `x3` "…and clean the data" | same, 14/14 | 0 |
| `x4` "…and drop the sheet" (Brain's only entry is `unsupported`) | same, 14/14 | 0 |
| `x5`, `x6` | unchanged and correct ("Partly done"; x6 quotes “delete the file”) | 0 |

**The guard test isn't vacuous.** I added `"delete"` to `_FILLER_WORDS` in a copy of head, and `test_n1_the_filler_set_holds_only_function_words` failed (`assert _FILLER_WORDS <= allowed`), along with one more N1 unit test. Without the mutation it passes. The limit is that the guard only catches words outside its `allowed` list. `remove`, `keep` and `leave` are verbs inside that list (see P3).

**N2 is fixed as reported:** `a3`, `x1` and `e1` no longer say "didn't read that". `x1` and `e1` say "Removed 2 duplicate row(s). I can't do this part yet: “sort by amount”." There's no lead word, and no "Done".

## 4. Adversarial scenarios (Brain returns an `unsupported` phrase; a step passed)

"Head" etc. count the 14 turns that lead with "Done —" or "Partly done". **Fix sketch** is the prototype in section 6. Bold IDs are findings.

| ID | User message | Fake Brain (and plan, if called) | Head reply, template (up to "The copy has") | Head | `ef3166f` | `main` | Fix sketch |
|---|---|---|---|---|---|---|---|
| `v01-delete-sheet` | remove duplicates and delete the sheet | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and delete the sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `v02-rename-file` | remove duplicates and rename the file | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and rename the file” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `v03-archive-it` | remove duplicates and archive it | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and archive it” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `v04-mixed-case` | Remove Duplicates And DELETE The File | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “Remove Duplicates And DELETE The File” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `v05-punct` | remove duplicates, then delete the file! | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates, then delete the file!” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `v06-unsup-only-drop-sheet` | Remove duplicates; drop the sheet. | intent `remove_duplicate_rows`; `unsupported` “Remove duplicates; drop the sheet.” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `f07-and-the-sheet` | remove duplicates and the sheet | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and the sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `f08-remove-it` | remove duplicates and remove it | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and remove it” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `f09-remove-all` | remove duplicates and then remove all | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and then remove all” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| **`f10-keep-only-dups`** | keep only the duplicate rows | intent `remove_duplicate_rows`; `unsupported` “keep only the duplicate rows” | Done — removed 2 duplicate row(s). | Done 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `f11-keep-only-dups-multi` | keep only the duplicate rows | intent `multi_step_request`; `unsupported` “keep only the duplicate rows”; plan: dedupe “duplicate rows” | Removed 2 duplicate row(s). | no lead | no lead | no lead | no lead |
| `f12-plain-filler` | Please, just remove the DUPLICATES. | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove the DUPLICATES”; `unsupported` “Please, just remove the DUPLICATES.” | Done — removed 2 duplicate row(s). | Done 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| `r13-remove-formatting` | remove duplicates and remove the formatting | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove the formatting” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove the formatting”. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `r14-remove-column-b` | remove duplicates and remove column B | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove column B” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove column B”. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `r15-remove-dups-and-col` | remove duplicates and column B | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and column B” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `n16-curly-dont-delete` | remove duplicates but don’t delete the file | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “don’t delete the file” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “don’t delete the file”. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `n17-dont-delete-whole` | remove duplicates but don't delete the file | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates but don't delete the file” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `n18-curly-dont-remove-dups` | remove the empty rows but don’t remove the duplicates | intent `remove_empty_rows`; `remove_empty_rows` “remove the empty rows”; `unsupported` “don’t remove the duplicates”; plan: dedupe “the duplicates”, empty-rows “remove the empty rows” | Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: remove empty rows. | no lead | Partly 14/14 | Done 14/14 | no lead |
| `n19-never-delete-anything` | remove duplicates and never delete anything | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and never delete anything” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| `s20-substring-dups` | remove the duplicate rows | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove the duplicate rows”; `unsupported` “duplicate rows” | Done — removed 2 duplicate row(s). | Done 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| `s21-substring-remove-the` | remove the duplicate rows | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove the duplicate rows”; `unsupported` “remove the” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove the”. | Partly 14/14 | Partly 14/14 | Done 14/14 | Partly 14/14 |
| **`s22-done-phrase-is-whole-msg`** | get rid of the repeated entries and delete the file | intent `remove_duplicate_rows`; `remove_duplicate_rows` “get rid of the repeated entries and delete the file”; `unsupported` “get rid of the repeated entries and delete the file” | Done — removed 2 duplicate row(s). | Done 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| **`t23-paraphrase-empty-not-run`** | dedupe and get rid of rows with nothing in them | intent `remove_duplicate_rows`; `remove_duplicate_rows` “dedupe”; `unsupported` “remove empty rows” | Done — removed 2 duplicate row(s). | Done 14/14 | Done 14/14 | Done 14/14 | Partly 14/14 |
| `t24-found-empty-not-run` | remove duplicates and remove empty rows | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14/14 | no lead |
| `t25-whole-msg-empty-not-run` | remove duplicates and remove empty rows | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and remove empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14/14 | no lead |
| **`g26-remove-all-dups`** | please remove all duplicates | intent `remove_duplicate_rows`; `remove_duplicate_rows` “remove all duplicates”; `unsupported` “please remove all duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| **`g27-nearmiss-rich-phrase`** | remove all the duplicate rows from my sheet | intent `remove_duplicate_rows`; `remove_duplicates` “remove all the duplicate rows from my sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| **`g28-delete-the-dups`** | delete the duplicates | intent `remove_duplicate_rows`; `remove_duplicate_rows` “delete the duplicates”; `unsupported` “delete the duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| **`g29-can-you`** | can you remove the duplicates | intent `remove_duplicate_rows`; `deduplicate` “can you remove the duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| **`g30-empty-nearmiss-rich`** | remove the empty rows from this sheet | intent `remove_empty_rows`; `remove_blank_rows` “remove the empty rows from this sheet” | Partly done — removed 1 fully-empty row(s). I read part of your request as something I can't do yet. | Partly 14/14 | Done 14/14 | Done 14/14 | Done 14/14 |
| `p31-n2-planner-extra` | remove the empty rows and sort by amount | intent `multi_step_request`; `unsupported` “sort by amount”; plan: dedupe “remove the empty rows” | Removed 2 duplicate row(s). I can't do this part yet: “sort by amount”. Your message mentions empty rows, but I didn't run the empty-row removal. | no lead | no lead | no lead | no lead |
| `p32-n2-no-mention` | sort by amount please | intent `multi_step_request`; `unsupported` “sort by amount please”; plan: dedupe “sort by amount please” | Removed 2 duplicate row(s). I can't do this part yet: “sort by amount please”. | no lead | no lead | no lead | no lead |

Where Brain's `remove_duplicate_rows` phrase isn't shown, it's “remove duplicates”. In `g27`, `g29` and `g30`, Brain's action name is off the vocabulary, so the parser turns it into `unsupported` (H2's case).

## 5. Findings

### P1 (HIGH, N1 class, present since `ef3166f`): a done action's quoted words swallow the unsupported part (`s22`)

**Where:** `_no_skill_outcome`, second loop (`if said and said in lowered: … lowered.replace(said, " ")`).
**Scenario:** "get rid of the repeated entries and delete the file". Brain gives the whole message as the phrase for both `remove_duplicate_rows` and `unsupported`. The dedupe passed, its phrase covers the whole unsupported phrase, nothing is left over, and the entry is dropped.
**Reply:** "Done — removed 2 duplicate row(s)." in 14 of 14 turns. The file wasn't deleted (Buddy can't), and Brain flagged it.
**Likelihood:** it needs Brain to give a whole-message phrase to a supported action. Live run 7 shows whole-message phrases happen (for `unsupported`).

### P2 (HIGH, N1 class, present since `ef3166f`): an action that didn't run is "named", so the request for it vanishes (`t23`)

**Where:** `_no_skill_outcome`, first loop. It removes the mention pattern of **every** action, not only those that passed or that have their own sentence.
**Scenario:** "dedupe and get rid of rows with nothing in them". Brain lists the dedupe and `unsupported` “remove empty rows” (its own words, not found in the message, so the mention detector adds nothing). "empty rows" is removed, "remove" is a filler word, and the entry is dropped.
**Reply:** "Done — removed 2 duplicate row(s)." in 14 of 14 turns. The delivered file still has 1 fully-empty row (pandas read: `(4, 0, 1)`), and the reply says nothing about it.
**Compare:** when the phrase *is* in the message (`t24`, `t25`), the mention entry gives "Your message mentions empty rows, but I didn't run the empty-row removal. … It still has 1 fully-empty row(s)." That's correct, so only the paraphrase case is broken.

### P3 (HIGH, N1 class, present since `ef3166f`): "keep"/"only" are filler even when the named action ran (`f10`)

**Where:** `_FILLER_WORDS` contains `keep`, `leave`, `only` and the negations without a condition.
**Scenario:** "keep only the duplicate rows". Brain's intent is `remove_duplicate_rows` (a misread), and it also lists `unsupported` “keep only the duplicate rows”. After "duplicate rows" is removed, only "keep only the" is left, so the entry is dropped.
**Reply:** "Done — removed 2 duplicate row(s)." in 14 of 14 turns. Buddy did the opposite of the request and calls it done, although Brain flagged the request. It needs a Brain misread, but the one signal against it is the entry this rule throws away.
**Also:** the same drop silences `f11` (Brain's only entry is that `unsupported` phrase): "Removed 2 duplicate row(s)." and nothing else. That's not a false claim (see section 7).

### Q1 (MEDIUM, introduced by `3ec8a8c`): "Partly done … I can't do yet" on a request that was fully done (`g26`–`g30`)

**Where:** the reduced `_FILLER_WORDS`. "remove" is in it, but no other removal verb, determiner or preposition is.
**Scenarios and replies** (14 of 14 turns each; `ef3166f` and `main` both say "Done —"):
- `g26` "please remove all duplicates" ("all" is left over)
- `g28` "delete the duplicates" ("delete")
- `g27` near-miss action `remove_duplicates`, phrase “remove all the duplicate rows from my sheet” ("all", "from", "my", "sheet")
- `g29` near-miss `deduplicate`, “can you remove the duplicates” ("can", "you")
- `g30` near-miss `remove_blank_rows`, “remove the empty rows from this sheet” (the empty-row action)

Each gives "Partly done — removed N … row(s). I read part of your request as something I can't do yet." Everything the user asked for was done.

**Why it matters:**
- `g27`, `g29` and `g30` are H2's near-miss-name case, the original High. The H2 tests (`k-*`) only use the all-filler phrase “remove the duplicate rows”, so they still pass, but any ordinary word in Brain's phrase brings back a "can't do" next to "removed".
- `fixes2.md` "Limits" accepted the trade for words like "but" and "all". It didn't consider this path, and the rubric for this review counts a "can't do" about done work as MEDIUM.

### Low

- **L-a** (`n16`, `n17`, `n19`): a negated unsupported clause ("don't delete the file", "never delete anything") gives "Partly done" and either "I can't do this part yet: “don’t delete the file”" or the generic sentence. Nothing done is called not done, but the lead word is misleading. `n17` was "Done —" at `ef3166f`. Parsing the scope of a negation safely is out of reach for a word list, so this stays flat on purpose.
- **L-b** (`s21`): an `unsupported` phrase of only function words that names no action ("remove the") is quoted as "I can't do this part yet: “remove the”". It's the same as `b6` and unchanged since `ef3166f`.
- **L-c** (N2's opposite side; see section 7).
- **Observation, out of scope** (unchanged since `ef3166f`): `k-multi-nearmiss` says "I also removed 2 duplicate row(s), though I didn't read that as part of your request." Brain did read it, under a near-miss name. This follows from the H1 decision, so I'm only listing it.

## 6. Exact fix needed (P1, P2, P3, Q1)

All of it is in `buddy/outcome.py` / `_no_skill_outcome`; nothing else changes. I prototyped it in a scratch copy:
- **Suite:** 1135 passed. The only 2 failures are the tests that pin the old behaviour (listed below).
- **Harness:** all 532 turns byte-identical to head.
- **Adversarial set:** P1–P3 → "Partly done", Q1 → "Done —", `f11` gains the generic sentence, and the other 23 scenarios are unchanged.
- **Ruff:** clean on the file.

1. **Normalise first:** `lowered = phrase.casefold().replace("’", "'")`, before any matching.
2. **Strip an action as verb + object, not just object (Q1).** Strip each action's object with an optional removal verb and determiners in front: `(?:\b(?:remove|delete|drop|clear|erase|eliminate|get\s+rid\s+of|take\s+out)\s+(?:(?:all|any|every|each|the|these|those|this|that|my|our|your|of)\s+)*)?(?:<action.mention.pattern>)`.
   - Then remove one location phrase, `\b(?:from|in|on|across)\s+(?:(?:the|this|that|my|our|your)\s+)?(?:sheet|file|spreadsheet|workbook|table|data)\b`.
   - "remove" leaves `_FILLER_WORDS`. A bare "remove it" / "remove all" is then left over, so the generic sentence stays.
3. **Function words only.** `_FILLER_WORDS` = `a an the please pls and then also just only so but can could would will you i we to do dont don't not no never keep leave`. Still no verb or noun. Update the guard test's `allowed` set to match.
4. **Only actions that have a sentence can absorb words (P2).** In the pattern loop, record `named_passed` (the action's capability passed) and `named_silent` (it didn't pass and has no ledger entry of its own).
5. **A done action's quoted words (P1):**
   - Run this loop **after** the pattern loop.
   - If `said` occurs in the phrase, set `names_action = True`, so done work is never quoted as "can't do".
   - Remove `said` only when it can't span two requests: no `\b(?:and|then|also|plus)\b` and no `[,;&+]`. When it is removed, set `named_passed = True`.
6. **Drop only when it's safe (P2, P3).** Return the generic `NO_SKILL` outcome if any of these hold: a word is left over; a negation or keep word (`dont don't not no never keep leave`) is present and `named_passed`; or `named_silent` and no negation word is present. Otherwise return `None`.

**Tests to change** (both pin the old behaviour):
- In `test_n1_a_leftover_content_word_keeps_the_generic_not_done_sentence`, "delete the duplicates" and "remove all duplicates" become `[]`. In that unit's setup the dedupe passed, so "keep the duplicates" and "don't remove the duplicates please" become `[""]`.
- `test_n1_the_filler_set_holds_only_function_words`: the new `allowed` set.

**Tests to add** (`build_engine`, 3 speakers × 6 session ids, like the N1 tests):
- `s22`, `t23` and `f10` must not contain "Done —", and must contain "I read part of your request as something I can't do yet."
- `g26`–`g30` must lead "Done —", with no "can't do".
- `x2`–`x6`, `e2`, `k-*`, `b5` and the existing N1 engine cases stay as they are.

**Residual risk, to note in the code comment:**
- A done action's phrase with no joiner that contains an unsupported verb (Brain gives "delete the file" as the dedupe phrase) is still dropped.
- A double negation in a paraphrase ("don't keep the empty rows") counts as a negation.
- Both need a gross misread by Brain. Closing them needs the ledger-recall measurement (backlog item 1, design 6.8), not more words.

## 7. N2: does the change hide a true "I didn't read that as part of your request"?

Yes, in one shape, but with no false claim.

**The shape:** Brain's only entries are `unsupported`, and the planner runs an action the user never asked for.
- `p31` "remove the empty rows and sort by amount", where the planner ran the dedupe.
- `p32` "sort by amount please", where the planner ran the dedupe.
- `f11` "keep only the duplicate rows".

**What changed:**
- `ef3166f` said "I removed 2 duplicate row(s), though I didn't read that as part of your request." That was true and useful.
- Head says "Removed 2 duplicate row(s)." with no lead word, no closer and no "Done". In `p31` it adds "Your message mentions empty rows, but I didn't run the empty-row removal." That's the same as `main`'s "Removed 2 duplicate row(s).", and the reply makes no false claim.

**Rating: LOW (L-c).** It's a lost caveat, not a false statement. Any fix would bring back N2 for `a3`/`x1`/`e1`, unless it uses the mention detector, which H1 showed can't confirm a request. I'm deferring it to the ledger-recall work.

## 8. Verdict: DON'T MERGE

1. **P1, P2, P3 (HIGH).** A false "Done —" is still reachable through `_no_skill_outcome` while Brain flags part of the request as unsupported. None of them is a regression against `main` (which says "Done —" everywhere). But they're the same class as N1, which blocked the last round, and the N1 fix's own comment claims this can't happen.
2. **Q1 (MEDIUM, introduced here).** "Partly done … I can't do yet" on requests that were fully done, including H2's near-miss case. This one *is* a regression against both `ef3166f` and `main`.
3. **Fix:** section 6, a single-function change. After it, a separate session should re-review using the harness plus this section's 32 scenarios. The live runs should be repeated per `CLAUDE.md`.

## Deferred

- L-a: negated unsupported clauses render "Partly done" (flat, intentional).
- L-b / `b6`: function-word-only `unsupported` phrases are quoted.
- L-c: an unrequested step with only `unsupported` Brain entries is stated plainly, without "I didn't read that as part of your request".
- The residual risks named at the end of section 6.
- Carried over: L3, and the PR-A review's M3 and L1–L3.

---

# Re-review 3 (fixes commit `688f62b`)

**Verdict: DON'T MERGE.** The fix closes P1, P2, P3 and Q1 as specified. All 32 of Re-review 2's scenarios now give the expected reply, and nothing else in the 532-turn harness changed. But the new location-phrase strip adds one false "Done —" (**R1**, HIGH, a regression against `3ec8a8c`). Brain flags "…across the workbook" (or "…in the workbook") as unsupported, and the strip removes the flag. On a two-sheet workbook the reply then says "Done — removed 2 duplicate row(s)." The delivered file holds only the first sheet, and the second sheet's duplicates were never touched. The fix is two words in `_LOCATION_PHRASE` (section 6).

The mistake is in the specification, not the implementation. `across` and `workbook` were in Re-review 2's section 6, and `688f62b` implemented that section as written.

> **Independence.** I'm a separate session, with no memory of writing PR-B, its fixes, or the earlier reviews. I didn't modify anything under `buddy/` or `tests/`. Mutations and the prototype fix were made in throwaway copies (`git archive`) in the session scratchpad.

## Method

- **Trees.** Head is `feat/verified-outcome-b` at `688f62b`. Two comparison trees, each a temporary `git worktree`: `origin/main` (`310f660`) and the previous head `3ec8a8c`.
- **Harness.** `scripts/review_harness/harness.py` from `origin/review/pr-b-harness`, unchanged. 38 scenarios × 14 turns = 532 turns per tree.
- **Adversarial set.** `scripts/review_harness/rereview3.py`, committed with this review. It uses the harness's own `P` fake, `brain()`, `plan()`, fixture and speaker loop, through `import harness`. Its run function differs in one way: it can write a second sheet (`Q2`: 4 rows, 2 duplicates) for the multi-sheet scenarios, and it records the delivered file's sheet names.
  - It holds 77 scenarios: `rr2-*`, the 32 from Re-review 2 §4 rebuilt from the table, and `z01`–`z45`, new ones aimed at the `688f62b` code.
  - That's 1078 turns per tree.
  - Run it with `python scripts/review_harness/rereview3.py [--tree DIR] --summary [ID-prefix …]`.
- **Scope.** The diff `3ec8a8c..688f62b` (`_no_skill_outcome`, `_FILLER_WORDS` and the new `_NEGATION_WORDS`, `_REMOVAL_VERBS`, `_DETERMINERS`, `_ACTION_WITH_VERB`, `_LOCATION_PHRASE` and `_TWO_REQUESTS`, plus their tests), and regression against `main` on undelivered paths. I didn't re-open earlier decisions (D2, H1, the L3 deferral, and the residuals Re-review 2 accepted).
- **Not run:** live runs with Groq. They weren't in this review's scope. `fixes3.md` reports 4 of 4.

## 1. Suite and lint

| Check | Result |
|---|---|
| `python -m pytest -q` (head) | **1281 passed**, 0 failed (341 s) |
| `ruff check buddy tests` | 22 on head and 22 on `main`. Compared by file and rule, ignoring line numbers, the lists are identical (15 E501, 3 UP017, 2 I001, 1 B905, 1 UP037). **No new lint errors.** `rereview3.py` is ruff-clean at line length 100. |
| Implementer's pre-fix claim | Verified. Running head's tests against `3ec8a8c`'s `buddy/` fails all 54 P1–P3 cases, all 90 Q1 cases and the two edited unit tests. |

## 2. Harness: head vs `main` and vs `3ec8a8c`

| Check | Head | `main` | `3ec8a8c` |
|---|---|---|---|
| Turns, errors | 532, 0 | 532, 0 | 532, 0 |
| Voice-model calls | **0** | 0 | 0 |
| Original changed (rule 2) | **0 of 532** | 0 | 0 |
| Delivered / undelivered | 504 / 28 | 504 / 28 | 504 / 28 |
| Undelivered turns (`r-*`) equal to `main` (reply, steps, delivered) | **28 of 28** | | |
| Replies changed vs `3ec8a8c` | **0 of 532** | | |

**Undelivered paths.** The changed code is reachable only from `build_outcome_report`. Its one caller is `_verified_delivery_reply` (`buddy/voice.py:390`), which runs only on a delivered, verified turn. So the diff can't reach an undelivered reply, and `r-chain-stop` and `r-unclear` confirm it: they're byte-identical to `main`.

**Adversarial set, all three trees:** 1064 turns per tree for z01–z44 and the 32 `rr2-*` scenarios, plus 14 for z45, run separately. 0 errors, 0 Voice calls, original unchanged in every turn.

**Replies changed vs `3ec8a8c` (adversarial set, 14 of 14 turns each):**
- Re-review 2 scenarios: `f10`, `f11`, `s22`, `t23` and `g26`–`g30`.
- New scenarios: `z09`, `z10`, `z13`, `z15`, `z19`, `z20`, `z32`, `z40`, `z41`, `z43`, `z45`.
- Every other scenario is identical.

## 3. Re-review 2's 32 scenarios on head

**Expected** is Re-review 2 §4's "Fix sketch" column. **Lead** counts the turns (of 14) that contain "Done —" or "Partly done", or that have no lead, plus the turns that contain "can't do". All 32 match. P1–P3 (`s22`, `t23`, `f10`) have no "Done —". Q1 (`g26`–`g30`) says "Done —" in 14 of 14 turns, with no "can't do".

**Rebuild check:** on `3ec8a8c`, each rebuilt scenario reproduces Re-review 2's "Head" column, so the rebuild is faithful.

| ID | Head reply, template (up to "The copy has") | Head | `3ec8a8c` | `main` | Expected | Match |
|---|---|---|---|---|---|---|
| `v01-delete-sheet` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `v02-rename-file` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `v03-archive-it` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `v04-mixed-case` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `v05-punct` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `v06-unsup-only-drop-sheet` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `f07-and-the-sheet` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `f08-remove-it` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `f09-remove-all` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `f10-keep-only-dups` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Done 14 | Done 14 | Partly | yes |
| `f11-keep-only-dups-multi` | Removed 2 duplicate row(s). I read part of your request as something I can't do yet. | no lead, can't-do 14 | no lead | no lead | no lead | yes |
| `f12-plain-filler` | Done — removed 2 duplicate row(s). | Done 14 | Done 14 | Done 14 | Done | yes |
| `r13-remove-formatting` | Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove the formatting”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `r14-remove-column-b` | Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove column B”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `r15-remove-dups-and-col` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `n16-curly-dont-delete` | Partly done — removed 2 duplicate row(s). I can't do this part yet: “don’t delete the file”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `n17-dont-delete-whole` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `n18-curly-dont-remove-dups` | Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: remove empty rows. | no lead | no lead | Done 14 | no lead | yes |
| `n19-never-delete-anything` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `s20-substring-dups` | Done — removed 2 duplicate row(s). | Done 14 | Done 14 | Done 14 | Done | yes |
| `s21-substring-remove-the` | Partly done — removed 2 duplicate row(s). I can't do this part yet: “remove the”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | Partly | yes |
| `s22-done-phrase-is-whole-msg` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Done 14 | Done 14 | Partly | yes |
| `t23-paraphrase-empty-not-run` | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Done 14 | Done 14 | Partly | yes |
| `t24-found-empty-not-run` | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14 | no lead | yes |
| `t25-whole-msg-empty-not-run` | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14 | no lead | yes |
| `g26-remove-all-dups` | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | Done | yes |
| `g27-nearmiss-rich-phrase` | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | Done | yes |
| `g28-delete-the-dups` | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | Done | yes |
| `g29-can-you` | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | Done | yes |
| `g30-empty-nearmiss-rich` | Done — removed 1 fully-empty row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | Done | yes |
| `p31-n2-planner-extra` | Removed 2 duplicate row(s). I can't do this part yet: “sort by amount”. Your message mentions empty rows, but I didn't run the empty-row removal. | no lead, can't-do 14 | no lead, can't-do 14 | no lead | no lead | yes |
| `p32-n2-no-mention` | Removed 2 duplicate row(s). I can't do this part yet: “sort by amount please”. | no lead, can't-do 14 | no lead, can't-do 14 | no lead | no lead | yes |

## 4. Adversarial scenarios against the new code (`z01`–`z45`)

These target the removal-verb strip, the location strip, the joiner rule and the negation/keep rule.

**Fixture:** the harness's 6 rows (2 duplicates, 1 empty row). Where a scenario says 2 sheets, there's also `Q2`.
**Brain's entries:** the intent is `remove_duplicate_rows` unless the table says otherwise. An off-vocabulary action name (`remove_duplicates`, `dedupe`) is H2's near-miss case, which the parser turns into `unsupported`.
**Final:** the delivered file's (rows, duplicates, empty rows), read by pandas. It's (4, 0, 1) everywhere except `z43` and `z44`, which give (3, 0, 0).
**Verdict key:**
- OK: no false statement.
- MEDIUM-u: a false "can't do" about done work that a sensible, correct Brain reading can't reach (see section 5).

| ID | User message | Brain's entries | Head reply, template (up to "The copy has") | Head | `3ec8a8c` | `main` | Verdict |
|---|---|---|---|---|---|---|---|
| `z01-del-file-from-sheet` | remove duplicates and delete the file from the sheet | `remove_duplicate_rows` “remove duplicates”; `unsupported` “delete the file from the sheet” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “delete the file from the sheet”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. The unsupported part is quoted. |
| `z02-del-file-from-sheet-whole` | remove duplicates and delete the file from the sheet | `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and delete the file from the sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. “delete … file” is left over, so the generic sentence is used. |
| `z03-delete-dups-email-it` | delete the duplicates and email it | `remove_duplicate_rows` “delete the duplicates”; `unsupported` “email it” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “email it”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z04-delete-dups-email-it-whole` | delete the duplicates and email it | `remove_duplicate_rows` “delete the duplicates”; `unsupported` “delete the duplicates and email it” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. The verb is attached to the done object; “email it” is left over. |
| `z05-remove-and-delete-the-dups` | remove and delete the duplicates | `remove_duplicate_rows` “remove and delete the duplicates”; `unsupported` “remove and delete the duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u: false “can't do”. Everything asked was done. “remove and” is left over because the verb strip only takes the verb next to the object. Needs a whole-message flag on a fully supported request. Same on `3ec8a8c`. |
| `z06-dups-and-delete` | delete duplicates and delete | `remove_duplicate_rows` “delete duplicates”; `unsupported` “delete duplicates and delete” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. The bare trailing “delete” is left over. |
| `z07-loc-and-sort` | remove duplicates from the sheet and sort it | `remove_duplicate_rows` “remove duplicates from the sheet”; `unsupported` “remove duplicates from the sheet and sort it” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z08-loc-and-sort-unsup-only` | remove duplicates from the sheet and sort it | `unsupported` “remove duplicates from the sheet and sort it” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z09-loc-across-workbook-2sheets` | remove duplicates across the workbook | `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates across the workbook” | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | **R1 HIGH.** Two-sheet workbook. The delivered file has only `Sheet1`, and Q2's 2 duplicates are gone with it. Brain's flag on the scope is dropped by the location strip. `3ec8a8c`: Partly. |
| `z10-loc-across-workbook-1sheet` | remove duplicates across the workbook | `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates across the workbook” | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | True on a one-sheet file. Same cause as z09. |
| `z11-loc-every-sheet` | remove duplicates in every sheet | `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates in every sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. “every” isn't in the location determiners. |
| `z12-loc-on-the-file-move` | remove duplicates and move them on the file | `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates and move them on the file” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z13-drop-empty-not-run` | remove duplicates and drop the empty rows | `remove_duplicate_rows` “remove duplicates”; `unsupported` “drop the empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | Partly 14, can't-do 14 | Done 14 | OK. The mention sentence says the empty-row removal didn't run. (`3ec8a8c` added a vaguer generic sentence.) |
| `z14-drop-empty-not-run-paraphrase` | remove duplicates and drop the rows with nothing in them | `remove_duplicate_rows` “remove duplicates”; `unsupported` “drop the empty rows” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK (the P2 path). Brain's words for a step that didn't run are kept. |
| `z15-drop-empty-only-misread` | drop the empty rows | `unsupported` “drop the empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | Partly 14, can't-do 14 | Done 14 | OK. Same as z13. Nothing is called done that wasn't. |
| `z16-dups-and-empty-lumped` | remove duplicates and empty rows | `remove_duplicate_rows` “remove duplicates and empty rows”; `unsupported` “remove duplicates and empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14 | OK. The mention sentence reports the empty rows. |
| `z17-dont-keep-empty-found` | remove duplicates and don't keep the empty rows | `remove_duplicate_rows` “remove duplicates”; `unsupported` “don't keep the empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14 | OK. The double negation is found in the message, and the mention sentence is true. |
| `z18-dont-keep-empty-paraphrase` | remove duplicates and don't keep rows with nothing in them | `remove_duplicate_rows` “remove duplicates”; `unsupported` “don't keep the empty rows” | Done — removed 2 duplicate row(s). | Done 14 | Done 14 | Done 14 | **Residual (HIGH class, accepted in Re-review 2 §6).** False “Done —”: 1 empty row remains. Needs Brain to call an empty-row request unsupported, in words not in the message. Same on `3ec8a8c` and `main`. |
| `z19-keep-dups-misread` | keep the duplicates | `unsupported` “keep the duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Done 14 | Done 14 | LOW (L-a). The reply honestly says “removed 2 duplicate row(s)”, but the lead is “Partly done”. `3ec8a8c`: Done (the P3 class). |
| `z20-do-not-remove-dups-misread` | do not remove the duplicates | `unsupported` “do not remove the duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Done 14 | Done 14 | LOW (L-a), as z19. |
| `z21-keep-dups-delete-file` | keep the duplicates, delete the file | `unsupported` “keep the duplicates, delete the file” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z22-not-the-empty-rows` | remove the duplicates, not the empty rows | `remove_duplicate_rows` “remove the duplicates”; `unsupported` “not the empty rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14 | OK (H1 design) |
| `z23-but-keep-blank-rows` | remove duplicates but keep the blank rows | `remove_duplicate_rows` “remove duplicates”; `unsupported` “keep the blank rows” | Removed 2 duplicate row(s). Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows. | no lead | no lead | Done 14 | OK (H1 design) |
| `z24-dont-leave-dups-curly` | Don’t leave any duplicates | `remove_duplicate_rows` “Don’t leave any duplicates”; `unsupported` “Don’t leave any duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u: false “can't do”. “Don't leave any duplicates” was done, and the double negation counts as a negation (the documented residual, inverted). Needs a whole-message flag. Same on `3ec8a8c`. |
| `z25-caps-punct-delete-file` | REMOVE DUPLICATES, AND DELETE THE FILE!! | `remove_duplicate_rows` “REMOVE DUPLICATES”; `unsupported` “REMOVE DUPLICATES, AND DELETE THE FILE!!” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z26-caps-semicolon-erase` | Remove the Duplicates; Erase The Sheet. | `remove_duplicate_rows` “Remove the Duplicates”; `unsupported` “Remove the Duplicates; Erase The Sheet.” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z27-hyphen-de-dupe-nearmiss` | de-dupe the sheet | `dedupe` “de-dupe the sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u (Q1 class): near-miss name; “de” is left over from “de-dupe”. Same on `3ec8a8c`. |
| `z28-said-in-longer-archive` | please remove the duplicate rows and then archive the workbook | `remove_duplicate_rows` “remove the duplicate rows”; `unsupported` “please remove the duplicate rows and then archive the workbook” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK |
| `z29-said-paraphrase-rename` | get rid of the repeated entries and then rename it | `remove_duplicate_rows` “get rid of the repeated entries”; `unsupported` “get rid of the repeated entries and then rename it” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. The said words are removed and “rename it” is left over. |
| `z30-said-not-substring-whole` | get rid of the repeated entries and sort by amount | `remove_duplicate_rows` “remove duplicate rows”; `unsupported` “get rid of the repeated entries and sort by amount” | Partly done — removed 2 duplicate row(s). I can't do this part yet: “get rid of the repeated entries and sort by amount”. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u: the quote includes the done part. Brain's dedupe phrase isn't the user's words, so it can't absorb them. Needs a whole-message flag. Same on `3ec8a8c`. |
| `z31-said-delete-the-file-residual` | remove duplicates and delete the file | `remove_duplicate_rows` “delete the file”; `unsupported` “delete the file” | Done — removed 2 duplicate row(s). | Done 14 | Done 14 | Done 14 | **Residual (HIGH class, accepted in Re-review 2 §6).** False “Done —”. Needs Brain to give “delete the file” as the dedupe phrase. Same on `3ec8a8c` and `main`. |
| `z32-said-with-joiner-lumped` | clean the repeated entries and the rest | `remove_duplicate_rows` “clean the repeated entries and the rest”; `unsupported` “clean the repeated entries and the rest” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Done 14 | Done 14 | OK. The joiner rule keeps “and the rest”. `3ec8a8c`: Done. |
| `z33-dup-columns-misread` | remove duplicate columns | `unsupported` “remove duplicate columns” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. “columns” is left over. |
| `z34-dup-sheet-misread` | delete the duplicate sheet | `unsupported` “delete the duplicate sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | OK. “sheet” is left over. |
| `z35-dups-of-my-file` | delete the duplicates of my file | `remove_duplicate_rows` “delete the duplicates”; `unsupported` “delete the duplicates of my file” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u: false “can't do”. “of my file” is left over (“of” isn't a location preposition). Needs a whole-message flag. Same on `3ec8a8c`. |
| `z36-nearmiss-from-my-excel-file` | remove the duplicates from my excel file | `remove_duplicates` “remove the duplicates from my excel file” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u (Q1 class): near-miss name; “excel” is left over. Same on `3ec8a8c`. |
| `z37-nearmiss-removing` | removing duplicates please | `remove_duplicates` “removing duplicates” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u (Q1 class): near-miss name; “removing” isn't a listed verb form. Same on `3ec8a8c`. |
| `z38-nearmiss-for-me` | could you please remove the duplicate rows for me | `remove_duplicates` “could you please remove the duplicate rows for me” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u (Q1 class): near-miss name; “for me” is left over. Same on `3ec8a8c`. |
| `z39-nearmiss-dedupe-this-sheet` | dedupe this sheet | `dedupe` “dedupe this sheet” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u (Q1 class): near-miss name; “this sheet” has no preposition. Same on `3ec8a8c`. |
| `z40-nearmiss-in-the-data` | remove duplicates in the data | `remove_duplicates` “remove duplicates in the data” | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | OK (fixed vs `3ec8a8c`) |
| `z41-nearmiss-get-rid-all` | get rid of all the duplicates | `remove_duplicates` “get rid of all the duplicates” | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | OK (fixed vs `3ec8a8c`) |
| `z42-whole-flag-remove-them` | find the duplicates and remove them | `remove_duplicate_rows` “find the duplicates and remove them”; `unsupported` “find the duplicates and remove them” | Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | Done 14 | MEDIUM-u: false “can't do”. “find … them” is left over. Needs a whole-message flag on a fully supported request. Same on `3ec8a8c`. |
| `z43-multi-both-done-whole-flag` | remove duplicates and empty rows from the sheet | intent `multi_step_request`; `remove_duplicate_rows` “remove duplicates”; `remove_empty_rows` “empty rows”; `unsupported` “remove duplicates and empty rows from the sheet” | Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). | Done 14 | Partly 14, can't-do 14 | no lead | OK. Both steps passed and the location phrase is stripped. `3ec8a8c`: Partly. |
| `z44-multi-both-done-delete-tail` | remove duplicates and empty rows, then delete the sheet | intent `multi_step_request`; `remove_duplicate_rows` “remove duplicates”; `remove_empty_rows` “empty rows”; `unsupported` “remove duplicates and empty rows, then delete the sheet” | Partly done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). I read part of your request as something I can't do yet. | Partly 14, can't-do 14 | Partly 14, can't-do 14 | no lead | OK |
| `z45-loc-in-the-workbook-2sheets` | remove duplicates in the workbook | `remove_duplicate_rows` “remove duplicates”; `unsupported` “remove duplicates in the workbook” | Done — removed 2 duplicate row(s). | Done 14 | Partly 14, can't-do 14 | Done 14 | **R1 HIGH**, as z09 (“workbook” is a location noun). |

**Where the new rules hold:**
- **Verbs across a joiner** (`z01`–`z06`, `z25`, `z26`): a removal verb is absorbed only when it sits right in front of the action's object. A verb aimed at another object ("delete the file", "Erase The Sheet", a bare trailing "delete") is always left over.
- **Location phrases** (`z07`, `z08`, `z11`, `z12`): they never hide a verb or another noun.
- **An object plus a verb for a step that didn't run:**
  - When the object is in the message (`z13`, `z15`, `z16`), the mention entry gives a true "didn't run" sentence, and there's no lead word.
  - When it's only in Brain's words (`z14`), `named_silent` keeps the generic sentence.
- **Negation and keep:**
  - Next to a done action (`z19`, `z20`, `z21`), the flag is kept.
  - Next to an action that didn't run but is in the message (`z17`, `z22`, `z23`), the mention sentence covers it.
- **The joiner rule:** Brain's quoted words are not removed when they span "and" (`z32`).

## 5. Findings

### R1 (HIGH, introduced by `688f62b`): "across the workbook" / "in the workbook" is stripped as a location, so a correct scope flag gives "Done —" (`z09`, `z45`)

**Where:** `_LOCATION_PHRASE` in `buddy/outcome.py`. The prepositions include `across` and the nouns include `workbook`.

**Scenario:** "remove duplicates across the workbook" on a workbook with sheets `Q1` and `Q2`, each with 2 duplicate rows. Brain gives `remove_duplicate_rows` “remove duplicates” and `unsupported` “remove duplicates across the workbook”, which is a correct flag: the skills read only the first sheet. The code then:
1. strips "remove duplicates" (the pattern);
2. strips "across the workbook" (the location);
3. finds nothing left, and drops the entry.

**Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining." in 14 of 14 turns. The delivered file's sheets are `['Sheet1']`, so `Q2` and its 2 duplicates are gone, and nothing says so. `z45` ("…in the workbook") gives the same reply.

**Compare:**
- `3ec8a8c`: "Partly done — … I read part of your request as something I can't do yet." in 14 of 14 turns, which is correct.
- `main`: "Done —", but `main` has no ledger at all.

**Why it's HIGH, and how reachable:**
- It's a false completion claim, and the signal that would have stopped it (Brain's flag) is discarded by the new code. That's the N1/P1–P3 class that blocked the last two rounds.
- It needs Brain to put the scope in an `unsupported` entry. Brain's prompt defines `unsupported` as an action, not a scope, so this is plausible rather than likely.
- Without that flag the reply is "Done —" anyway on every tree. That's backlog item 1a (M3: multi-sheet workbooks lose every sheet after the first), which is already HIGH. R1 removes the one path on which PR-B would have caught it.

### Residuals already accepted in Re-review 2 §6 (HIGH class; not regressions)

- **`z18`** "remove duplicates and don't keep rows with nothing in them". Brain gives `unsupported` “don't keep the empty rows” in its own words. The reply is "Done —", and 1 empty row remains.
- **`z31`** Brain gives "delete the file" as the dedupe phrase. The reply is "Done —".

Both need a gross misread by Brain, both are documented in the code comment on `_no_skill_outcome`, and both are identical on `3ec8a8c` and `main`. I'm leaving them deferred, as Re-review 2 did. **The user should know these are still false "Done —" paths.**

### MEDIUM, not reachable from a sensible, correct Brain reading: a false "can't do" on fully done work

**Q1-class leftovers** (`z27`, `z36`–`z39`) need an off-vocabulary action name (H2's near-miss), and then a word outside the lists:
- "de-dupe" leaves "de";
- "removing" isn't a listed verb form;
- "for me";
- "this sheet", which has no preposition;
- "my excel file".

**Whole-message flags on a fully supported request** (`z05`, `z24`, `z30`, `z35`, `z42`) need Brain to list an `unsupported` entry for something it can do:
- "remove and delete the duplicates";
- "Don't leave any duplicates" (the double-negation residual, inverted);
- "find the duplicates and remove them";
- "of my file";
- in `z30`, the quote includes the done part, because Brain's dedupe phrase isn't the user's words.

**Reachability:** a correct Brain reading of an ordinary single-sentence request uses a vocabulary name, and has no `unsupported` entry when everything is supported. Then `_no_skill_outcome` never runs. So none of these is reachable from a sensible, correct reading.

All of them reply "Partly done … I can't do yet" in 14 of 14 turns, the same as on `3ec8a8c`, so none was introduced here; `688f62b` narrows the class (`g26`–`g30`, `z40`, `z41`, `z43` now say "Done"). They're the safe side of the word lists, as `fixes3.md` "Limits" says.

### LOW

- **L-d: the new word lists have no guard test.** The guard covers `_FILLER_WORDS` only. In a scratch copy I added `sort|email` to `_REMOVAL_VERBS` and `amount` to `_LOCATION_PHRASE`'s nouns, and all 618 tests in `test_outcome.py` and `test_engine_factory.py` still passed. A future edit could put a non-removal verb or a content noun in either list unnoticed. Section 6 includes a guard.
- **L-a widened** (`z19`, `z20`). With a misread intent, "keep the duplicates" or "do not remove the duplicates" now gives "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet." `3ec8a8c` said "Done —". It's honest about the removal, but the lead word is wrong. That's the P3 fix working as specified, and it's better than "Done —".
- **L-b** (`s21`) is unchanged.

## 6. Exact fix needed (R1)

In `buddy/outcome.py`, change `_LOCATION_PHRASE` to drop `across` from the prepositions and `workbook` from the nouns:

```python
_LOCATION_PHRASE = re.compile(
    r"\b(?:from|in|on)\s+(?:(?:the|this|that|my|our|your)\s+)?"
    r"(?:sheet|file|spreadsheet|table|data)\b"
)
```

- Add a comment saying the location strip must never take a phrase that names more than the one sheet the skills read ("across", "workbook", "every", "all").
- Add the guard from L-d: a unit test that pins `_REMOVAL_VERBS`, `_DETERMINERS` and `_LOCATION_PHRASE` to their current closed lists, in the same style as `test_n1_the_filler_set_holds_only_function_words`.
- Add an engine test like `z09`: a two-sheet workbook, all three speakers. "remove duplicates across the workbook", with `unsupported` “remove duplicates across the workbook”, must not contain "Done —".

**Prototype, in a scratch copy:** I made only the two-word change.
- `python -m pytest -q`: 1281 passed.
- Harness: 532 of 532 turns byte-identical to head.
- Adversarial set: only `z09`, `z10` and `z45` change, to "Partly done … can't do yet".
- `ruff check buddy/outcome.py`: clean.

**The cost:** `z10` (a one-sheet file) also becomes "Partly done", although that file was fully deduplicated. That's a false "can't do", but only after Brain flags the scope (MEDIUM-u). It's the safe side, and the right trade until M3 makes the skills multi-sheet aware.

**Who should review it:** I wrote this prototype, so I must not review the real fix. A separate session should re-run the harness and `rereview3.py`.

## 7. Is the guard test vacuous?

No. In a scratch copy I added `delete` to `_FILLER_WORDS`:
- `test_n1_the_filler_set_holds_only_function_words` failed, with "Extra items in the left set: 'delete'" (1 failed, 54 passed in `test_outcome.py`).
- Without the mutation it passes.

The guard is the only test that notices. All 270 N1/P1–P3/Q1 engine cases still passed under that mutation, because the scenarios that would be affected keep another content word ("file"). The guard is also blind to the other lists (L-d above).

## 8. Verdict: DON'T MERGE

1. **R1 (HIGH, introduced by `688f62b`, via Re-review 2's specification).** A correct `unsupported` flag on a multi-sheet scope ("across the workbook", "in the workbook") is stripped as a location, so the reply says "Done —" for a workbook whose second sheet wasn't touched and isn't in the delivered file. `3ec8a8c` said "Partly done".
2. **Fix:** section 6, a two-word change plus two tests. After it, a separate session should re-run the harness and `rereview3.py`, and the live runs should be repeated per `CLAUDE.md`.
3. **Everything else in scope passes:**
   - P1, P2, P3 and Q1 are fixed as specified.
   - No new lint errors; Voice calls 0; the original is unchanged in every turn.
   - Undelivered paths equal `main`.
   - No reachable MEDIUM from a sensible, correct Brain reading.

## Deferred

- **Residual HIGH-class paths** accepted in Re-review 2 §6, still present and unchanged since `3ec8a8c`:
  - `z31`: a done action's phrase that is itself an unsupported verb phrase;
  - `z18`: a double negation in a paraphrase.
  - Both need a gross Brain misread. Closing them needs the ledger-recall measurement (backlog item 1, design 6.8).
- **MEDIUM-u:** a false "can't do" on fully done work after a near-miss action name (`z27`, `z36`–`z39`) or a whole-message `unsupported` flag on a supported request (`z05`, `z24`, `z30`, `z35`, `z42`). It's not reachable from a correct reading and is unchanged from `3ec8a8c`.
- **L-d:** no guard test on `_REMOVAL_VERBS`, `_DETERMINERS` or `_LOCATION_PHRASE`. Section 6 asks for it with the R1 fix; if it doesn't land with that fix, it stays here.
- **L-a** (now also `z19`, `z20`), **L-b** and **L-c**, unchanged.
- Carried over: L3, and the PR-A review's M3 (backlog 1a). M3 is what makes R1 a false claim, and it stays HIGH on its own.

---

# Review round 5 (commit d0b32c4)

**Verdict: MERGE.** R1 is fixed, with my own evidence. I found no HIGH in `_no_skill_outcome` or anywhere in the diff since `688f62b`, and no MEDIUM that an ordinary request reaches. The merge of `origin/main` brought in no regression: every undelivered path equals `main`, every reply I produced makes only claims the verifier supports, and the original was byte-identical in every run. The items below are Deferred; two are pre-existing on `main` and worth the owner's attention (formula/format loss, analytics beacon). Live runs with Groq are still required on this head before merging (`CLAUDE.md`); I didn't do them.

> **Independence.** I'm a fresh reviewer agent with no memory of writing or reviewing this branch. I changed nothing under `buddy/`, `core/`, `skills/`, `backend/` or `tests/`. Mutations were made in a throwaway `git archive` copy; comparison trees were temporary worktrees (removed). Last reviewed commit: `688f62b`. Head: `d0b32c4` (R1 fix `d0b32c4`, merge `59bcce5` of `origin/main` = `6d000c5`).

## 1. Suite and lint

- `python -m pytest -q`: **1568 passed**, 11 warnings, 329 s. Matches `fixes4.md`.
- `ruff check buddy tests`: **22 errors** on head and on `origin/main`. I diffed the counts by file and rule: identical. Nothing new.

## 2. R1: fixed [Certain]

- **The diff.** `git diff 688f62b HEAD -- buddy/outcome.py` is exactly the change I specified: `across` and `workbook` removed from `_LOCATION_PHRASE`, plus a comment. Nothing else in `outcome.py` changed.
- **Differential over 41,412 generated phrases** (`scope_diff.py`: 7 removal phrases × 12 prepositions × 17 determiners × 29 nouns, each passed as Brain's `unsupported` entry next to a passed dedupe). Count of phrases whose entry is dropped although the part after the verb holds a scope word (all, every, each, both, other, second, remaining, workbook, across, sheets, tabs, worksheet, whole, entire, rest, "sheet 2", q2, a number): **0 on head, 378 on `688f62b`.** So no phrase of this family can still turn a scope flag into "Done".
- **Harness, 532 turns.** Head against `688f62b`: 0 differing replies. Head against `main`: the 351 differences are all in scenarios where the ledger changes the reply (expected); `r-*` (undelivered) are identical to `main`. Voice-model calls 0, original changed 0, errors 0.
- **`rereview3.py`, 1078 turns.** Head against `688f62b`: only `z09`, `z10`, `z11`, `z45` differ, all expected.
  - `z10` (one-sheet, "remove duplicates across the workbook" flagged): "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. The copy has 4 row(s), 0 duplicate row(s) remaining." The file has 4 rows, 0 duplicates, 1 empty row: the numbers match. This is the accepted MEDIUM-u cost (a false "can't do" on a file that was fully deduplicated, reachable only after Brain's flag).
  - `z09`, `z11`, `z45` (two sheets): now "I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped … no file was handed back; your original file is unchanged." No file is delivered, so nothing false is claimed. (On `688f62b` the skills still read one sheet, which is why those scenarios were "Partly done" there.)
  - All 32 `rr2-*` and the other 41 `z*` scenarios: identical replies on head and `688f62b`.
- **Tests, mutation.** I put `buddy/outcome.py` back to the `688f62b` version in a scratch copy: `-k r1` gives **37 failed** (the guard plus the 36 one-sheet cases) and 18 passed. The 18 are the two-sheet cases, which pass on old code because the skills refuse the workbook first, as `fixes4.md` says. They pin that behaviour, not the strip.
- **Guard test, not vacuous.** In the scratch copy, each of these made `test_outcome.py` fail on its first failing case: add `sort` to `_REMOVAL_VERBS`; add `amount` to the location nouns; add `across` back; add `workbook` back; add `sheets` to the nouns; add `sort` to `_DETERMINERS`; add `delete` to `_FILLER_WORDS`. This closes L-d. (The guard pins the exact pattern strings, so any edit trips it; that is brittle by design.)
- **`_no_skill_outcome` two-rounds rule.** I found no HIGH in it this round, so the writer's "remove the dropping logic" fallback isn't triggered.

## 3. The merge of `origin/main` (diff `688f62b..HEAD` outside `outcome.py`)

Files: `core/engine.py`, `buddy/{state,voice,voice_catalog,hands,engine_factory,inspector,planner,think,context,llm,models}.py`, `skills/excel/{workbook,dedupe,remove_empty_rows,analyze}.py`, `skills/base.py`. That code was reviewed on `main` (`fix-multi-sheet-review*.md`, `pipeline-failure-and-output-dir-review.md`). I checked it only where it meets PR-B's ledger, outcome and reply code.

`scripts/review_harness/round5.py`: 38 scenarios, 228 turns, template + friend ×4 + focus, real `build_engine`, only the LLM faked. Same run on `origin/main` for comparison. Voice calls 0, original changed 0 (SHA-256 before and after, every turn).

| Case | What happened | Reply (all three speakers) | Rule 1 |
|---|---|---|---|
| 2-sheet workbook, plain dedupe / empty rows / chain / "across the workbook" flag / "every sheet" (`a01 a04 a08 a09 a10 a13`) | Skill refuses, nothing delivered, no analysis lead | "I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped … no file was handed back; your original file is unchanged." Chain adds "so remove empty rows never ran". | True; same as `main` |
| Hidden second sheet (`a11`) | Counted as a sheet, refused | same | True |
| One-sheet file, scope flagged "across/in the workbook", "on all sheets" (`a02 a03 a05 a07`) | Delivered, 4 rows, 0 duplicates | "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. The copy has 4 row(s), 0 duplicate row(s) remaining." | Numbers match the file; "can't do" is the accepted MEDIUM-u |
| Quoted flag "the workbook" (`a06`) | same file | "…I can't do this part yet: “the workbook”." | Honest |
| Hostile / 40-char / XML-escaped sheet name (`b01 b02 b03`, chain `b10`) | Delivered, name preserved | "Done — removed 2 duplicate row(s)…"; the sheet name never appears in any reply | True |
| Corrupt zip, text file named .xlsx (`b04 b05`) | Refused | "could not read the workbook; it may be damaged or not an Excel file" | True, hedged |
| OLE2 `.xls`, password-protected (`b06 b07 b11`) | Refused | fixed texts | True (see Deferred: ".xlsx will work") |
| Empty sheet, secret cell text (`b08 b09`) | Delivered | "Done — there were no duplicate rows to remove. The copy has 0 row(s)…"; "removed 1 duplicate row(s)… 1 row(s)" | Numbers match; no cell text in any reply |
| Stage raises: eyes, brain, think, planner, delegate, hands, inspector, inspector in a chain (`c-fail-*`), exception text "SECRET_EXC_TEXT cell=Alice" | `state.failure` = stage name and class only | "Something went wrong {where}, so I can't confirm that any change was made. I haven't handed back any file from this turn." (5 distinct texts over 40 turns; the secret appears in no reply) | True. Voice model not called |
| Voice's speaker raises (`c-fail-voice-speaker`) | `Engine.run` re-raises (by design); original unchanged | no reply | The caller must handle it (PR-2) |
| Per-turn output folder, single and chain (`d01 d02`) | File written under `turn_outputs/t1/`, none under `workspace/outputs/` | "Done — …Saved as `report_deduplicated_session-1-task-1.xlsx`" | True |
| Confirmation declined, first or second step (`d03 d05`) | Nothing delivered | "…The deduplicate step: wasn't confirmed, so it didn't run. I stopped … no file was handed back" | True |
| Confirmation provider raises (`d04`) | Stage failure at hands | failure reply | True |

Other checks on the merged code:

- **File text in replies.** `Hands` now relays a skill's `errors` as the step's finding. All three skills return fixed strings (`could not read the workbook`, `could not write the cleaned copy`, the `WorkbookRefused` texts); the one dynamic string, "cannot handle task '<task_id>'", is built from the session id and never reached a reply in my runs (a `.csv` upload gives "couldn't be run because no available skill can do it"). The Inspector's `_sheet_list_problem` finding does put sheet names in text, but it needs the output's sheet list to differ from the input's. `sheetname_probe.py` ran 23 crafted names (quotes, RTL mark, emoji, tab, newline, padding, 32 and 80 characters, XML-special, NUL) through the real skill: no mismatch, so that text is unreachable [Likely].
- **Context persistence.** `to_dict`/`from_dict`: JSON round trip equal, reply count equal, version 2 and an invalid mode raise `ValueError`. Nothing crosses sessions.
- **Failure reply vs. ledger.** `Voice.__call__` returns `failure_reply` before any speaker or outcome code runs, and `_verified_delivery` also requires `failure is None`, so the ledger can't render "Done —" after a stage failed. In `c-fail-inspector` a real file exists on disk and in `execution_result`, and the reply says none is handed back (see Deferred).

## 4. Design document

No new deviation. `verified-outcome-design.md` has nothing on location phrases, multi-sheet or stage failure; the earlier-round deviations are unchanged.

## 5. Findings

**No HIGH. No MEDIUM reachable from an ordinary request in this diff.**

Pre-existing on `main` and outside this diff (I'm listing them because a reader of the review would otherwise assume "Done" is complete):

- **P-1 (MEDIUM-HIGH by the severity table, [Certain], identical on `main`): a delivered one-sheet file silently loses formulas, formatting, widths, merged cells and rows above the header.** `scripts/review_harness/formulas_probe.py`: a sheet with formulas, a bold red header, a merged note cell and a wide column. Reply: "Done — removed 1 duplicate row(s). The copy has 3 row(s), 0 duplicate row(s) remaining." The delivered file has formulas as empty/static values, the merged note cell turned into an "Unnamed: 3" column, and formatting gone. Reachable by any spreadsheet that has formulas or formatting. Not caused by this branch, so it doesn't change my verdict, but a "your formulas and formatting are not kept" sentence (or an openpyxl-based writer) is needed before a public launch.
- **P-2 (LOW-MEDIUM, [Certain], `main`): analytics beacon.** `ydata-profiling` 4.18.4 requests `https://packages.ydata.ai/ydata-profiling?…&nrows=…&ncols=…` on each analysis (1,637 blocked connection attempts during my runs). It carries counts, not cell values. Set `YDATA_PROFILING_NO_ANALYTICS=1`.

## 6. Deferred (all added to `docs/backlog.md`)

- P-1 and P-2 above.
- **Failure reply and the delivered file (LOW, [Certain]):** after a failure past Hands (`c-fail-inspector`) `state.execution_result.outputs` holds a real file while the reply says none is handed back. The API must key on `state.failure`.
- **".xlsx will work" (LOW):** the legacy-`.xls` refusal promises something unchecked; a multi-sheet file saved as .xlsx is still refused.
- **Chart sheet (LOW):** a workbook with a chart sheet openpyxl can't read in read-only mode is refused as "may be damaged" (`a12`; my fixture was an empty chart sheet made by openpyxl, so [Possible] for real files).
- **"your original file is unchanged" (LOW):** `_chain_stop_sentence` says it with no runtime check; the failure replies deliberately don't. Same wording as `main`.
- **R1 test coverage (LOW):** the two-sheet engine test pins the skills' refusal, not the strip, so only the 36 one-sheet cases and the guard protect R1.
- **Carried over, unchanged:** residual HIGH-class paths `z18` and `z31` (gross Brain misreads, need ledger-recall measurement); MEDIUM-u (false "can't do" after a near-miss name or whole-message flag); L-a, L-b, L-c, L3.

## 7. Not run

- **Live runs with Groq** (`CLAUDE.md`, required before merge on this head; no key available to me).
- **`.xls` reading with xlrd** (not installed); OLE2 files were fake headers only.
- **Real Excel files** (cached formula values, charts, macros, tables): I only had files made by pandas and openpyxl.
- **The backend** (`backend/` is empty on this branch), so the API-side handling of `state.failure` and of a raising Voice wasn't exercised.
- A full re-run of the suite on `origin/main`.

## Probe scripts (this round)

`scripts/review_harness/round5.py`, `scope_diff.py`, `sheetname_probe.py`, `formulas_probe.py`; usage in `scripts/review_harness/README.md`.
