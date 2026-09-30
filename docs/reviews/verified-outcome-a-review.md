# Review: `feat/verified-outcome-a` (PR-A of the verified-outcome design)

Review only. No code was changed. This file is the only file written or committed.

> **Reviewer independence.** I have no memory of writing this code, and no conversational memory of this branch or of the design. I read `docs/reviews/verified-outcome-a-impl.md` only to get the list of claims to check. Every finding below comes from a scenario I ran.

## 0. Method

- **Trees.** The branch is at `03744e6`. `main` is at `0b499fe` (`origin/main`), checked out as a temporary git worktree in the session scratchpad. Each harness run asserts that `buddy` is imported from the tree under test.
- **Harness.** The scripts live in the session scratchpad, not in the repo. Every scenario goes through the real `build_engine`, with the real registered skills and verifiers. Each run writes a fresh `.xlsx` to its own temp directory.
- **Fake LLM.** Only the LLM is faked. The provider routes on the system prompt: "intent classifier" goes to Brain, "reasoning stage" to Think, "planning stage" to the Planner, and anything else to Voice. It records every call, so "the Voice model was called" is measured, not assumed.
- **Speakers.** `TemplateSpeaker`, `PersonalityVoice` in friend mode and `PersonalityVoice` in focus mode.
- **Independent checks.** For every turn I recorded:
  - the intent and Think's `clarification_required`;
  - the steps executed, `stopped_at` and the outputs;
  - a fresh pandas read of each delivered file: rows, exact duplicates, fully-empty rows and sheet names;
  - the reply, and whether the Voice model was called;
  - the original's SHA-256 before and after.
- **Default fixture.** It's the one the final review used: 6 rows, 2 exact duplicates, and 1 fully-empty row that isn't the last row. Other fixtures are named where they're used.

**Run counts:**

| Tree | Main harness (scenarios / turns) | Forced-failure harness (§4) | Probes |
|---|---|---|---|
| Branch | 237 / 243 | 18 | 11 |
| `main` | 237 / 243 | 18 | 8 |

Across all of these runs, **the original file's hash was unchanged in every one**.

## 1. Summary

| ID | Severity | Rule | Introduced here? | Finding |
|---|---|---|---|---|
| **H1** | **High** | 1 | **Yes** | The dedupe `removed` count leaves out blank rows. Dedupe does remove duplicate blank rows, and the reply then says there was nothing to remove, or understates the count. For a file whose only duplicates are two blank rows, the reply is **"Done — there were no duplicate rows to remove."** while the file went from 5 rows to 4. |
| **M1** | Medium | 1 (implied) | **Yes** (entries not in the design) | Three catalog closers imply the request is finished: `friend.closer.whats_next` ("What's next on the list?"), `friend.closer.anything_else` and `focus.closer.anything_else`. Their predicate only checks `open_findings == 0`, so they appear when a requested action never ran. This is the same flaw as the `nothing_to_remove` opener that was removed. |
| M2 | Medium | 1 | No (on `main` too; deferred to PR-B) | "Done —" comes from Brain's single intent, so a misclassified request still gets "Done —" for work it didn't ask for (N1, C3, and "dedupe and sort"). |
| M3 | Medium | 1 | No (on `main` too; not in the backlog) | Multi-sheet workbooks. The skills keep only the first sheet, and the reply calls the result "Your cleaned copy of report.xlsx" / "The copy has 2 row(s)". Sheet `Q2` was silently dropped. |
| L1 | Low | 1 (numbers) | No | A Skill's self-reported near-duplicate string, including its "match probability 0.97", reaches the delivered reply. It's labelled "self-reported". The implementer's "analysis findings are verifier-computed strings" isn't fully true. |
| L2 | Low | — | Yes | `turn_index` is always 0, because nothing calls `record_buddy_reply`. The implementer discloses this. |
| L3 | Low | 1 | No | With no step records, `_delivered_file_name` falls back to the *output's* name, giving "Your cleaned copy of out.xlsx is ready." A test enshrines this. It isn't reachable through `build_engine`. |

**On the five questions:**

1. **False claims and unmeasured numbers.**
   - **No model-written text reaches any reply.** The Voice model was called 0 times in the 270 branch turns where calls were recorded. On `main`, 84 of the 243 main-harness turns called it.
   - Every final-state count ("The copy has N row(s), …") matched pandas in all 127 verified deliveries.
   - But the branch has **one new code-rendered false claim (H1)**. The earlier residual claims (M2, M3) are also still there.
2. **The catalog.** Predicates are strict enough for every opener. The completion-implying closers aren't (M1). §3 goes entry by entry.
3. **Undelivered, stopped and unverified paths.**
   - **The replies are byte-identical to `main`**: 128 undelivered, 3 stopped, 3 unverified through `build_engine`, and 30 direct `SpeakingRequest` shapes.
   - The Voice model is never called on them.
4. **Test changes.** No changed test hides a regression. The new tests share one gap: no fixture has two blank rows, which is why H1 wasn't caught (§5).
5. **Suite.** `python -m pytest -q`: **689 passed**, 2 warnings (main: 593 passed). `ruff check buddy tests`: 22 errors (main: 28), none in new files.

---

## 2. Findings

### H1 (High, introduced): "there were no duplicate rows to remove" after dedupe removed one

**Where:**
- `buddy/inspector.py:158`: the removed count is `int(_without_empty_rows(original).duplicated().sum())`, which counts non-empty rows only.
- `buddy/inspector.py:332-333`: `_check_against_input` drops fully-empty rows from both sides before comparing. So the verifier never confirms how many *blank* rows the dedupe step removed.
- `buddy/outcome.py:355-360`: `_action_sentence` renders that number as "There were no duplicate rows to remove." or "Removed N duplicate row(s).". The text says rows removed; the measurement means non-empty duplicates in the input.
- `skills/excel/dedupe.py:101`: `original.drop_duplicates()` treats identical blank rows as duplicates and removes all but one.
- The design specifies the same thing (`docs/design/verified-outcome-design.md:50`, "measured on the non-empty rows"). It also claims "the number is confirmed rather than predicted". That's true for non-empty rows, not for rows removed.

**Scenario BT** (harness5): the message is "remove duplicate rows" and the intent is `remove_duplicate_rows`. The fixture has 5 rows: Alice, blank, Bob, blank, Carol. There are no duplicate data rows, and there are two identical fully-empty rows mid-file.

| | pandas, original | pandas, delivered | Reply (branch) |
|---|---|---|---|
| Single dedupe, `TemplateSpeaker` | 5 rows, `duplicated()` = 1, 2 empty | **4 rows**, 0 dups, 1 empty | "**Done — there were no duplicate rows to remove.** The copy has 4 row(s), 0 duplicate row(s) remaining. Saved as `report_deduplicated_S-task-1.xlsx`. I also noticed: …" |
| Same, friend | same | same | "Here's where things landed. Done — there were no duplicate rows to remove. The copy has 4 row(s)…" |
| Chain "remove duplicates then remove empty rows" | same | **3 rows** | "There were no duplicate rows to remove. Removed 1 fully-empty row(s). The copy has 3 row(s)…" (5 → 3, one row unaccounted for) |

On `main`, the same single-dedupe turn says "Done — output has 4 row(s), 0 duplicate row(s) remaining.", which is true. **This false sentence is new on this branch.**

**Scenario X-two-empty** (main harness, all three speakers): the fixture has 6 rows, 1 duplicate data row and 2 identical blank rows. The branch says "Done — removed 1 duplicate row(s). The copy has 4 row(s)…". Pandas shows 2 rows were removed. This was the only mismatch among 127 verified deliveries when I checked the rendered "Removed N" against the original-to-delivered row delta.

**Why it matters:**
- The rendered sentence is a statement about what happened to the user's file. The user can see it's wrong: 5 rows went in and 4 came back, yet "there were no duplicate rows to remove".
- It's deterministic. Nothing in this PR can make it correct, because it's the verifier's measurement that's mislabelled.
- Blank separator rows are common in real sheets, so this isn't an exotic input.
- It's N2's error in reverse: an understatement instead of an overstatement, now made by code instead of the model.

**Why the tests miss it:**
- Every measurement fixture has at most one blank row. For example, `_SIX_ROWS` at `tests/buddy/test_inspector.py:1092-1096`.
- The inspector test asserts `removed=int(original.dropna(how="all").duplicated().sum())` (`:1116`), which restates the implementation.
- The property test (`tests/buddy/test_outcome.py:384`) checks that every number is *a* measured field, not that the field means what the sentence says.

**What would fix it:** a small change, but it needs the design line changed too. Either of these:
- (a) Measure what the step actually removed. That's `original.duplicated().sum()` including blank rows, plus a check on the output's blank-row count. The trailing-blank-row round trip `_check_against_input` works around must still be accounted for, so the check would say how many blank duplicates are unaccounted for.
- (b) Make the sentence exactly as narrow as the measurement, and say separately what happened to blank rows. For example: "There were no duplicate data rows to remove; one of 2 identical blank rows was dropped as a duplicate." Any blank-row count in the reply needs a verifier measurement of its own.

Either way, add a test with two identical blank rows through `build_engine`, checked against pandas.

### M1 (Medium, introduced): closers that imply the request is finished, with no ledger to back it

**Where:**
- `buddy/voice_catalog.py:95-108`: `friend.closer.anything_else` ("Anything else you want me to look at?") and `friend.closer.whats_next` ("What's next on the list?").
- `:112`: `focus.closer.anything_else` ("Anything else?").
- The predicate `_asks_nothing_else` (`:71-73`) only checks `open_findings == 0`.
- These three entries aren't in the design's catalog (`verified-outcome-design.md:146-152`). There, closers are offers tied to a `NOT_DONE` action.

**Scenarios** (main harness, friend mode, default seed "S"):

| Scenario | What the user asked | What ran | Reply ends with |
|---|---|---|---|
| C1b | "remove duplicates then remove empty rows" | Only empty-row removal (the planner dropped dedupe); the delivered file has **2 duplicates** | "…Removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report.xlsx is ready. None of the issues I check for were found. **What's next on the list?**" |
| C3b | "delete the empty rows and remove duplicates" (Brain says `remove_empty_rows`) | Only empty-row removal | "Those blank rows were just taking up space. Done — removed 1 fully-empty row(s). … **What's next on the list?**" |
| N1 | "remove the duplicate rows please" (Brain says `remove_empty_rows`) | Only empty-row removal; the file still has **2 duplicates** | "… None of the issues I check for were found. **What's next on the list?**" |
| SEED-N1 (12 session ids) | same as N1 | same | 7 of 12 end with "Anything else you want me to look at?"; the rest have no closer |

**Why it matters:**
- "What's next on the list?" presupposes the current item is done.
- CLAUDE.md rule 1 covers "say **or imply**".
- The implementer removed `friend.opener.nothing_to_remove` for exactly this reason: its truth depends on what was *requested*, and PR-A has no record of that. These closers depend on the same missing fact.
- In C1b and N1 there's no "Done —", so the closer is the only thing in the reply that suggests the request was completed.

**What would fix it:** remove the three entries until PR-B's ledger can gate them on "every requested action is `DONE`". Alternatively, gate them on `report.lead_word == "Done"` as a stop-gap. That still misfires in N1 and C3 (M2), but not in C1b, N5 or C1.

### M2 (Medium, pre-existing, deferred to PR-B): "Done —" for a misclassified request

**Where:** `buddy/outcome.py:193-207` (`lead_word`) and `:249-253` (`requested` comes from Brain's single intent).

**Scenarios (branch):**

| Scenario | What the user asked | What ran | Reply |
|---|---|---|---|
| N1 | "remove the duplicate rows please" (Brain says `remove_empty_rows`, Think proceeds) | Empty-row removal; the delivered file has 5 rows and **2 exact duplicates** | "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. … None of the issues I check for were found." |
| C3 | "remove duplicates and also delete the empty rows" (Brain says `remove_duplicate_rows`) | Dedupe only; 1 empty row left | "Done — removed 2 duplicate row(s). …" |
| X-dedupe-plus-sort | "remove duplicates and sort by amount" (Brain says `remove_duplicate_rows`) | Dedupe only; not sorted | "Done — removed 2 duplicate row(s). …" |

**Comparison with `main`:** `main` gives the same "Done" through `TemplateSpeaker`. Through `PersonalityVoice`, `main` lets the model say "All done — duplicates removed." verbatim in N1 (3 of 3 model replies reached the user). **The branch is strictly better**: its claim is about the file, the model can't add one about the request, and the counts are measured.

"Done —" is still misleading about the request, though, and "None of the issues I check for were found" right after it reads as "no duplicates" (backlog L-4). The implementer discloses this; it's PR-B's ledger.

### M3 (Medium, pre-existing, not in the backlog): "cleaned copy" of a workbook whose other sheets were dropped

**Where:**
- `skills/excel/dedupe.py:94` and `:106`: `pd.read_excel(input_path)` reads only the first sheet, and `to_excel` writes one sheet named `Sheet1`. `remove_empty_rows.py` does the same.
- `buddy/outcome.py:374-378`: "Your cleaned copy of {file} is ready."
- `buddy/outcome.py:371`: "The copy has N row(s)…".

**Scenario X-multisheet:**
- Fixture: `report.xlsx` with sheets `Q1` (Alice, Bob, Alice) and `Q2` (Zed, Yan).
- Message: "remove duplicate rows"; intent `remove_duplicate_rows`.
- Delivered file: one sheet, `Sheet1`, with 2 rows. **`Q2` is gone.**

| Speaker | Branch reply |
|---|---|
| `TemplateSpeaker` | "Done — removed 1 duplicate row(s). The copy has 2 row(s), 0 duplicate row(s) remaining. Saved as …" |
| `PersonalityVoice` | "…Done — removed 1 duplicate row(s). The copy has 2 row(s)… Your cleaned copy of report.xlsx is ready." |

**Comparison with `main`:** `main`'s `PersonalityVoice` already appended "Your cleaned copy of report.xlsx is ready." in code (the model's text was "All done — your sheet is spotless."). `main`'s template says "output has 2 row(s)". So the claim is pre-existing. The branch newly has `TemplateSpeaker` call the file "the copy".

No reply on either branch mentions the dropped sheet. It isn't introduced here, but it's a rule-1 breach that isn't in `docs/backlog.md`. The fix belongs in the skills (keep every sheet, or refuse), or in a verifier that compares sheet lists. It shouldn't be fixed in this PR.

### L1 (Low, pre-existing): a Skill's self-reported number reaches a delivered reply

**Where:**
- `buddy/inspector.py:553-557`: near-duplicate findings are the Skill's `self_reported_findings`, passed through with a "(self-reported, not independently re-checked)" suffix.
- `buddy/voice.py:404`: renders them after "I also noticed: ".

**Scenario SELF** (harness4): Splink didn't fire on any fixture I tried, so I stubbed the near-duplicate analyzer in `skills/excel/analyze.py` to self-report one fixed string. Everything else was real `build_engine`. The branch reply:

> "Done — removed 2 duplicate row(s). … I also noticed: …; rows with name 'Alice' and 'Alicia' look like they might be the same (match probability 0.97) (self-reported, not independently re-checked). Want me to tackle any of these?"

`main` does the same. It's labelled and it isn't an outcome claim. But the implementer's checklist line "Analysis findings are verifier-computed strings, as before" is inaccurate for this category. The property test excludes analysis findings, so a number that isn't a verifier measurement can appear in a delivered reply. The other analysis numbers (missing-value percentages, outlier counts) are computed by `SpreadsheetAnalysisVerifier` itself.

### L2 (Low, disclosed): the phrase seed never varies within a session

**Where:** `buddy/voice.py:766` uses `state.context.buddy_reply_count()`. `grep` finds no caller of `Context.record_buddy_reply` in `buddy/`, `core/` or `scripts/`, so `turn_index` is 0 on every turn, and a session's seed is always `"<session_id>:0"`.

This is harmless to truthfulness. It's disclosed as deviation 8 in the impl doc.

### L3 (Low, not reachable through `build_engine`): the output's name presented as the original's

**Where:** `buddy/voice.py:174`. When no action step has an `input_reference`, `_delivered_file_name` returns the *output's* name.

**Evidence:** `tests/buddy/test_voice.py:702-722` (`test_personality_voice_is_unaffected_by_a_failing_provider`) asserts the reply is "…Your cleaned copy of out.xlsx is ready.", where `out.xlsx` is the output. Hands always records steps, so no `build_engine` run hit this path. It's the same on `main`.

---

## 3. Every catalog entry (`buddy/voice_catalog.py`)

**The question for each entry:** can its predicate be true while its text is false or misleading, given that PR-A only knows what ran, not what was requested? **Placeholders, digits and completion words:** none, as the implementer says; I checked by reading the file.

| Entry | Predicate | Can it be true while the text is false or misleading? | Evidence |
|---|---|---|---|
| `friend.opener.neutral` "Here's where things landed." | always | No. It makes no claim. | all friend runs |
| `friend.opener.rundown` "Okay, here's the rundown." | always | Only weakly: the "rundown" leaves out a dropped step (C1, N5). That's M2/PR-B territory, not the entry's own claim. | SEED-C1: 2 of 12 |
| `friend.opener.seeing_double` "That sheet was seeing double." | a done dedupe step measured `removed >= 1` | **No.** `removed` counts non-empty duplicates only, which makes this conservative. With H1's blank twins, `removed == 0` and it doesn't fire, which is correct. In a chain it's still true of the user's sheet, because removing blank rows can't create a non-empty duplicate. It's true in N5 too (the user didn't ask for dedupe, but the sheet did have duplicates). | SEED-C1: 3 of 12 |
| `friend.opener.blank_rows` "Those blank rows were just taking up space." | a done empty-rows step measured `removed >= 1` | The fact (blank rows existed and were removed) is always true when it fires. "Just taking up space" is an opinion. In N1 and C3b it praises the step the user *didn't* ask for, right before "Done —", which reinforces M2's misleading reading. Low, and it resolves with PR-B. | N1, C3b, SEED-N1: 7 of 12 |
| `friend.closer.none` (empty) | always | No. | — |
| `friend.closer.anything_else` "Anything else you want me to look at?" | `open_findings == 0` | **Yes (M1).** It implies the request is handled when a requested step never ran. | SEED-N1: 7 of 12 |
| `friend.closer.whats_next` "What's next on the list?" | `open_findings == 0` | **Yes (M1), and the strongest case.** It presupposes the item is complete. | C1b, C3b, N1 (friend, seed S) |
| `focus.opener.none` (empty) | always | No. | — |
| `focus.closer.none` (empty) | always | No. | — |
| `focus.closer.anything_else` "Anything else?" | `open_findings == 0` | Yes, the same as M1, though weaker. The default seed didn't pick it in my focus runs, but it's eligible in every M1 scenario. | by predicate |

The opener predicates are sound. The closer predicates check the wrong thing: they check whether the reply already asks something, when what they'd need is whether the request is complete.

---

## 4. Undelivered, stopped and unverified paths vs `main`

| Path | Turns compared | Replies identical to `main` | Voice model calls (branch / `main`) |
|---|---|---|---|
| Undelivered (A with any Brain outcome and clarify true/false, N3 turn 2, N7) | 128 | **128 / 128** | 0 / 0 |
| Stopped chain (N6: dedupe, then `email.send`) | 3 | **3 / 3** | 0 / 0 |
| Delivered but Inspector failed. I forced the final-file check to fail (`CompositeVerifier._verify_final_file` patched), in a real two-step chain. | 3 | **3 / 3** | 0 / 0 |
| Direct `SpeakingRequest`s (`TemplateSpeaker` and `PersonalityVoice`): `verification_result=None` with a delivered output; nothing delivered and no verification; `execution_result=None`; delivered with failed verification and no analysis; Hands errors. Each with the intents `remove_duplicate_rows`, `unclear` and `None`. | 30 | **30 / 30** | 0 / 0 |

**Every reply outside the verified-delivery path is byte-identical to `main`.** Two notes:

- **Where a single-step turn with a failed final check lands.** A single step has no final-file check, so that forced failure doesn't apply and the turn stays a verified delivery.
- **Re-check or initial-analysis failure (non-blocking).** These stay verified deliveries. The branch reply keeps `RECHECK_FAILED_SENTENCE` and `INITIAL_ANALYSIS_FAILED_SENTENCE`. It no longer repeats "the analysis reported failure", which the impl doc intends.

**Verified deliveries** (127 turns): every reply differs from `main`, as intended. None of them used model text. The "Removed N" counts and every "The copy has…" count matched pandas, apart from H1's blank-duplicate cases.

---

## 5. Test changes (`git diff origin/main...HEAD -- tests/`)

I read every removed `def test_` and `assert` line in `tests/buddy/test_voice.py`, and the changed tests in `tests/buddy/test_engine_factory.py`. `tests/buddy/test_inspector.py` has additions only. I found no `skip`, `xfail` or disabled tests.

| Change | Hides a regression? |
|---|---|
| **Inverted:** `…returns_the_models_reply…` became `…never_returns…`, and `…uses_the_models_reply…` became `…never_uses…` | No. That's decision D1 (design 4.2). The new tests assert that the provider is never called and that the reply equals the rendered reply. |
| **Undelivered and stopped PV tests** (`…says_where_the_chain_stopped…` `:843`, `…states_no_changes…` `:911`, `…nothing_was_delivered_when_the_last_step_stopped` `:1255`): prompt assertions became reply assertions | No. They assert no model call, and `:852` asserts `reply == TemplateSpeaker()(…)`. `:911` and `:1255` only check fragments, but §4 shows the full reply is unchanged. |
| **`startswith("Done")` became "no Done"** in `test_template_speaker_reports_a_failed_re_check…` and two engine-factory tests (all `multi_step_request`) | No hidden regression, but it is a visible behaviour change: a fully successful two-step chain no longer starts with "Done" (D4). The block lists each measured step instead. |
| **Count assertions dropped** where the fixture has no measurement (e.g. `"0 duplicate row(s) remaining"` became "…ran and passed its check") | No. Those fixtures now state no count, and new tests assert exact counted replies for the real shape (`test_a_single_step_verified_turn_still_says_done_and_names_the_file`, which I also reproduced through `build_engine` in N3 turn 1). |
| **Deleted:** `…leaves_a_reply_that_already_names_the_file`, `…prompt_carries_no_capability_tags_or_ids`, `test_mode_prompts_forbid_repeating_internal_labels` | No. The code they tested is gone, and the tag check survives at reply level. |
| `test_personality_voice_is_unaffected_by_a_failing_provider` (`:702`) | It enshrines L3 ("cleaned copy of out.xlsx", with the output presented as the original). Low. |
| **Gap (not a changed test):** no measurement, renderer or end-to-end test has two identical blank rows | This is why H1 passes CI. |

**Net:** no changed assertion hides a regression. The coverage gap behind H1 is in the new tests, not the changed ones.

---

## 6. The final review's scenarios, re-run (branch vs `main`)

| Scenario | `main` (`0b499fe`) | Branch (`03744e6`) |
|---|---|---|
| **A**: "remove the duplicate rows please". Brain gives `unclear`, raises, returns non-JSON, `Remove_Duplicate_Rows`, `deduplicate`, `remove_duplicates`, `""` or `null`. Think clarify true/false. Model replies (a), (b), (c). | Undelivered; model not called; factual reply | **Identical** (112 of 112 of these turns); model not called |
| **N1**: the same message, Brain gives `remove_empty_rows` and Think proceeds | The model's (a)/(b)/(c) reached the user verbatim (e.g. "All done — duplicates removed."); the file still has 2 duplicates | Model not called. "Done — removed 1 fully-empty row(s)…" (M2) plus, in friend mode, "What's next on the list?" (M1) |
| **C1/C2**: the planner drops empty-row removal | Template: "Done — output has 4 row(s)…". PV: "Done — duplicates and empty rows are gone from report." verbatim | No "Done". "Removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining…". No false claim. It says nothing about the dropped step (PR-B). |
| **C3**: Brain says `remove_duplicate_rows` for a compound request | "Done — …" (both speakers; PV verbatim model text) | "Done — removed 2 duplicate row(s)…" (M2) |
| **D**: reversed plan, 9 wordings × 3 speakers | 6 wordings corrected. In the 3 fall-backs the template states the order, but PV says "Done, both cleanups are finished." with no order | Same 6 corrected. In the 3 fall-backs, **both speakers state the order inside the block**. F4-with-PV is closed. |
| **N2**: dedupe on a file with no duplicates | PV: "Removed 3 duplicate rows — your sheet is clean." verbatim | "Done — there were no duplicate rows to remove. The copy has 4 row(s)…", which is correct for this fixture. **But see H1 for a file with blank twins.** |
| **N3**: two turns, Brain raises on turn 2, with and without reset | Turn 2: "I wasn't able to confirm that worked. No verifier has been configured. …" | Identical on turn 2. Turn 1 is rendered, and the model isn't called. |
| **N5**: "remove empty rows" planned as analyze + dedupe | PV: "Both done: empty rows and duplicates are gone." verbatim | "Removed 2 duplicate row(s). The copy has 4 row(s)… I also noticed: …". No false claim; the unrun request isn't mentioned (PR-B). |
| **N6**: dedupe, then `email.send` | Stopped; factual reply | **Identical** |
| **N7**: Think raises | Undelivered; factual reply | **Identical** |

**Rule 2:** the original's SHA-256 was unchanged in every run on both trees (§0).

**Model text in replies:** 0 Voice-model calls on the branch in any recorded scenario. On `main`, 84 of the 243 main-harness turns (plus 10 of the 18 forced-failure turns) called the model and used its text.

---

## 7. Verdict: **DON'T MERGE**

**What's solid, and verified by running it:**
- The structural change works. No turn calls the Voice model, and no model text can reach the user. That closes N1(a/b/c), N2's literal text, C2, N5's text, and F4 with `PersonalityVoice`.
- The undelivered, stopped and unverified paths are byte-identical to `main`.
- The final-state counts match pandas in every run.
- The suite passes (689), lint is down from 28 errors to 22, and rule 2 held in every run.
- Merged as-is, this branch would still be a large net reduction in rule-1 exposure compared with `main`.

**Why not merge yet:**
1. **H1 is a new rule-1 breach written by code, in the part of the PR whose whole point is that rendered facts are verifier-confirmed.** On a common input (two blank rows), the reply says "there were no duplicate rows to remove" while dedupe removed one and the user can see the row count drop. The design's own line 50 carries the same flaw, so the fix is a small design correction plus the measurement change, with a two-blank-row test through `build_engine`.
2. **M1 re-creates the flaw the implementer removed the `nothing_to_remove` opener for:** a phrase whose truth depends on what was requested, which PR-A doesn't know. Removing three catalog entries fixes it. That's one small commit.

**The re-review needs only H1 and M1 fixed.** M2 is PR-B's ledger. M3 is a skills/verifier issue that exists on `main` and should be tracked. The Low items can wait.

## Deferred

These aren't blocking this PR, but they should go into `docs/backlog.md` if they aren't there already:

- **M2**, which is PR-B: "Done —" from Brain's single intent on a misclassified request (N1, C3, "dedupe and sort").
- **M3** (new to the backlog): multi-sheet workbooks lose every sheet after the first, and the reply calls the file "a cleaned copy" / "the copy". The fix belongs in the skills (keep all sheets or refuse) or in a sheet-list check in the verifier.
- **L1**: self-reported near-duplicate numbers reach delivered replies (labelled). Either keep numbers out of self-reported findings, or state them as the Skill's claim.
- **L2**: `turn_index` is always 0 until the engine calls `record_buddy_reply`.
- **L3**: `_delivered_file_name` falls back to the output's name, and a test enshrines "cleaned copy of out.xlsx".

---

## Re-review of the fixes (`87f7168`)

Review only. No code was changed. This section is the only change to the repository.

> **Reviewer independence.** I have no memory of writing the fixes. I wrote the review above. I read `docs/reviews/verified-outcome-a-fixes.md` for the list of claims, and checked each one by running code.

### Method

**Trees:**
- Branch head: `87f7168`.
- Pre-fix: `1d57990`. It has the same code as `03744e6`, and its harness results are the ones from the review above.
- `main`: `0b499fe`.
- Pre-fix and `main` were checked out as temporary worktrees, removed afterwards.

**Harnesses:** the same scratchpad harnesses as the review (real `build_engine`, real skills, real `.xlsx`, fake LLM only), plus one new one:
- **Main harness:** 237 scenarios, 243 turns, run on the head and on `main`.
- **Forced-failure harness:** 18 turns.
- **Direct `SpeakingRequest` shapes:** 30.
- **Self-reported-finding probe (L1):** 2 turns.
- **New H1 edge harness:** 40 turns, on the head, pre-fix and `main`. The fixtures are:
  - BT (Alice, blank, Bob, blank, Carol);
  - X-two-empty;
  - Alice, blank, Alice: the blank that dedupe keeps ends up last, and the `.xlsx` round trip drops it;
  - Alice, Bob, blank, Alice: the same effect;
  - three identical blank rows plus one duplicate data row;
  - blank rows as the only duplicates.

  Each fixture ran as a single dedupe and as a chain (dedupe, then empty rows), with all 3 speakers. There are also 4 turns with a deliberately buggy dedupe (`drop_duplicates()` then `dropna(how="all")`), to check that an unconfirmable count is withheld.

**Checks on every delivered turn:**
- "Removed N" adds up to rows in minus rows out, measured by pandas.
- "The copy has N row(s)…" matches the delivered file.
- No "no duplicate rows to remove" when pandas finds duplicates.
- The Voice model is never called.
- The original is unchanged.

### H1: fixed

| Scenario | Pre-fix (`1d57990`) | Head (`87f7168`) | Pandas (in → out) |
|---|---|---|---|
| BT, single, template | "Done — **there were no duplicate rows to remove.** The copy has 4 row(s)…" | "Done — removed 1 duplicate row(s), including 1 repeated blank row. The copy has 4 row(s), 0 duplicate row(s) remaining. …" | 5 → 4 |
| BT, chain | "There were no duplicate rows to remove. Removed 1 fully-empty row(s). The copy has 3 row(s)…" | "Removed 1 duplicate row(s), including 1 repeated blank row. Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. …" | 5 → 3 |
| X-two-empty, single (main harness and edge harness) | "Done — removed **1** duplicate row(s). …" | "Done — removed 2 duplicate row(s), including 1 repeated blank row. The copy has 4 row(s)…" | 6 → 4 |
| X-two-empty, chain | "Removed 1 duplicate row(s). Removed 1 fully-empty row(s). …" | "Removed 2 duplicate row(s), including 1 repeated blank row. Removed 1 fully-empty row(s). The copy has 3 row(s)…" | 6 → 3 |
| Three blanks and a duplicate, single | "Done — removed 1 duplicate row(s). …" | "Done — removed 3 duplicate row(s), including 2 repeated blank rows. The copy has 3 row(s)…" | 6 → 3 |
| Blank rows are the only duplicates, single | "Done — there were no duplicate rows to remove. …" | "Done — removed 1 duplicate row(s), including 1 repeated blank row. …" | 4 → 3 |
| Alice, blank, Alice (the kept blank row is lost in the round trip), single | "Done — removed **1** duplicate row(s). The copy has 1 row(s)…" | "Done — the duplicate-row removal ran and passed its check. The copy has 1 row(s), 0 duplicate row(s) remaining. …" | 3 → 1 |
| Alice, Bob, blank, Alice (same), single | "Done — removed 1 duplicate row(s). The copy has 2 row(s)…" | "Done — the duplicate-row removal ran and passed its check. The copy has 2 row(s)…" | 4 → 2 |
| Buggy dedupe that drops every blank row (BT, X-two-empty) | "Done — there were no duplicate rows to remove." / "Done — removed 1 …" | "Done — the duplicate-row removal ran and passed its check. The copy has 3 row(s)…" | 5 → 3, 6 → 3 |

**What the counts show:**
- **Every counted reply** (the edge harness plus 127 main-harness and forced-failure deliveries) now adds up exactly to the pandas row delta.
- Every final-state sentence matches the delivered file.
- The two round-trip fixtures show that the pre-fix branch also had a third H1 shape, which the review above didn't list: "removed 1" when 2 rows disappeared. The new row-delta confirmation withholds the count there, as intended.

**Opener predicate.** `friend.opener.seeing_double` now requires `removed_data >= 1`. I called `eligible_phrases` directly:

| `removed` | `removed_blank` | `seeing_double` eligible? |
|---|---|---|
| 1 | 1 | no |
| 2 | 1 | yes |
| 0 | 0 | no |
| `None` | 0 | no |

That's correct: repeated blank rows aren't "the sheet seeing double".

**Tests:**
- The 15 new `test_h1_*` end-to-end tests fail on the pre-fix `buddy/`: I copied the head's `test_engine_factory.py` into the pre-fix worktree, and 15 of 15 failed.
- They pass on the head (15 of 15).

### M1: fixed

- `CATALOG` now holds exactly seven entries: the four friend openers, `friend.closer.none`, `focus.opener.none` and `focus.closer.none`.
- No reply in any of the head's turns contains "What's next on the list?", "Anything else you want me to look at?" or "Anything else?".

**The M1 scenarios, head vs pre-fix:**

| Scenario | Pre-fix ended with | Head |
|---|---|---|
| C1b friend | "…None of the issues I check for were found. What's next on the list?" | The same reply without the closer |
| C3b friend | "…What's next on the list?" | Without the closer |
| N1 friend (clarify=false) | "…What's next on the list?" | Without the closer |
| SEED-N1, 12 sessions | 7 of 12 ended with "Anything else you want me to look at?" | 0 of 12 have a closer. The opener distribution is unchanged: blank rows 7, rundown 3, neutral 2 |

The template and focus versions of C1b, C3b and N1 are byte-identical to pre-fix.

### Nothing else changed

**Code diff:** the diff from `1d57990` to `87f7168` touches only `buddy/inspector.py`, `buddy/models.py`, `buddy/outcome.py`, `buddy/voice_catalog.py` and `scripts/run_dedupe_manually.py` (a print line). The rest is tests and docs: the design, the backlog and the fixes doc.

**Main harness vs pre-fix** (261 turns, including forced-failure):

| Result | Turns |
|---|---|
| Byte-identical | 227 |
| Differ only by the removed closer | 31 |
| Other differences | 3, all X-two-empty (H1 above) |

**Paths that don't deliver a verified file:**
- All 134 non-delivered turns (undelivered, stopped, unverified) are byte-identical to `main`. So are the 30 direct `SpeakingRequest` shapes.
- The Voice model was called 0 times on the head.
- `main`'s own replies are unchanged between my two sessions.

**Rule 2:** the original's SHA-256 was unchanged in every run: 261 main-harness and forced-failure turns, and 40 edge turns on each tree.

**Suite and lint:** `python -m pytest -q` gives **710 passed**, 11 warnings. `ruff check buddy tests` finds 22 errors, the same as before, and none are new.

**Changed or removed test lines:**
- The only removed ones are `test_no_closer_question_when_the_reply_already_asks_about_findings` and the closer-variety assertion. Both test entries that no longer exist.
- The replacement, `test_no_closer_implies_the_request_is_finished` (`tests/buddy/test_voice_catalog.py:115`), is stricter: every closer, for every report and mode, is empty.

**Backlog:** M3 is item 1a, and L1–L3 are under item 5, as the fixes doc says.

### New observations (Low, not blocking)

- **R1.** When the kept blank row is lost in the `.xlsx` round trip (Alice, blank, Alice), the reply withholds the count but doesn't mention that a blank row the user didn't ask to remove is gone. Each sentence is true ("The copy has 1 row(s)"), so no claim is false.
  - This is `.xlsx` and skill behaviour, and it happens on `main` too.
  - Worth a backlog line with M3: the skills shouldn't silently lose rows the capability doesn't target.
- **R2.** A dedupe that drops non-duplicate blank rows still *passes* verification. The count is now withheld, but the verdict is still "passed".
  - The verifier can't fail it without also failing the legitimate round-trip case, because both show a larger row delta.
  - This gap predates PR-A, and its reply impact is now contained (no count is claimed).
- **Still open, as the review above concluded:** M2 ("Done —" on a misclassified request) remains, and is PR-B's. L1 is unchanged: the probe still shows the self-reported "match probability 0.97" in the delivered reply, with its label.

### Updated verdict: **MERGE**, after the owed live runs

**Why:**
- H1 and M1, the only two blockers, are fixed and verified end to end, including against pandas and on edge shapes the fix didn't list.
- Nothing else in behaviour changed.
- The undelivered, stopped and unverified paths are still byte-identical to `main`.
- The suite passes, and rule 2 held in every run.

**Condition, from `CLAUDE.md` rather than from a finding:** the fixes doc says the live Groq runs weren't repeated for `87f7168`. The delivered reply text changed: closers are gone, and blank-repeat counts are new. `CLAUDE.md` requires live runs before merging, so `docs/reviews/verified-outcome-a-live-runs.md` should get runs on `87f7168` before the user merges. The highest-value run is the default fixture with the message "remove duplicate rows", in both modes.

### Deferred (additions to the list above)

- R1 and R2 above. Both are Low and pre-existing. Suggested for the backlog next to item 1a (M3).
