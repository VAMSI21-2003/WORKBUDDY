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
