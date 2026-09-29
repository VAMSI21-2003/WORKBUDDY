# Verified-outcome design: the reply's facts come from code, never from the model

**Status:** design only. Nothing in this document is implemented. It covers backlog item 1 (F2 / N1 / N2, plus C1–C3 and N5 from `docs/reviews/chaining-wip-final-review.md`).

**Rule-touching:** yes, rule 1. The implementation needs an independent review and live runs before merging (see `CLAUDE.md`). It also changes a "Decided behaviour" line in `CLAUDE.md` (Section 7), which needs the user's approval.

---

## 0. Decision in brief

1. **The outcome block is built by code.** On a delivered turn, every reply has three parts: an optional **opener**, the **outcome block**, and an optional **closer**. The outcome block states what was done, the counts, what wasn't done and why, the order when it wasn't confirmed, and the file. Code renders it from a new `OutcomeReport`. That report is built only from `ExecutionResult`, `VerificationResult` (extended with structured counts measured by the verifiers) and a ledger of requested actions. `TemplateSpeaker` and `PersonalityVoice` use the same renderer.
2. **The model never writes reply text.** It picks an opener and a closer **by ID** from a reviewed, per-mode phrase catalog. Each catalog entry has an eligibility predicate over the `OutcomeReport`, and code checks it. Code drops any ID that is unknown, belongs to the wrong mode or slot, or isn't eligible. Placeholders in the text are filled by code from the report. The main safeguard is therefore structural: the set of possible outputs is closed and every item in it has been reviewed. There is no word list involved.
3. **The requested actions are recorded as a ledger with two sources.** Brain returns `requested_actions`, drawn from a closed vocabulary, each with a verbatim `source_phrase`. A separate, deterministic mention detector adds any action the message names that Brain left out. The report compares the ledger with the steps that passed, which yields three kinds of line: **done**, **not done, with a reason code**, and **done but not requested**.

**What this does not fix.** If neither Brain nor the mention detector notices a request (for example Brain misclassifies it *and* the user paraphrases it), the reply says nothing about that request. It still can't claim the request was done, because nothing in the reply can state an action whose step didn't pass. So the leftover failure is an omission, not a false claim.

---

## 1. Where the breach is today

| Location | What happens |
|---|---|
| `buddy/voice.py:956-984` (`PersonalityVoice.__call__`) | After `_model_may_describe_outcome` passes, the model's text is the reply. The only thing code adds is the file sentence, and only if the model left it out. The prompt says "Never invent … a fact", but nothing enforces that. |
| `buddy/voice.py:682` (`TemplateSpeaker._success_message`) | Starts with "Done" whenever verification passed, whatever was requested (C1, C3). |
| `buddy/voice.py:1030-1038` | The "requested action was NOT performed" line only exists for intents in `_INTENT_CAPABILITIES`, and only in the prompt, so never for `multi_step_request`. |
| `buddy/inspector.py:139-160`, `:205-226` | The dedupe and empty-row verifiers only report `output has N row(s), K … remaining` as a **string**. The input row count is read inside `_check_against_input`, but it's thrown away. So even code can't say "removed 2" from verifier data today. The only count is the Skill's self-reported `removed_count` (`skills/excel/dedupe.py:113-119`), which Voice must not trust. |

N2 needs only one model error because the model is the **only** component that states a count in the reply. N1, C2 and N5 go through the same unchecked text.

---

## 2. Part 1: the outcome statement comes only from execution and verifier data

### 2.1 Verifiers return structured counts

Add to `buddy/models.py`:

```python
@dataclass(frozen=True)
class StepMeasurement:
    """Counts a verifier measured itself, by reading files fresh. Never copied from a Skill."""
    target: str               # "duplicate_rows" | "empty_rows"
    output_rows: int          # rows in the output, as read by the verifier
    remaining: int            # target rows still in the output
    input_rows: int | None = None   # rows in the step's input; None when there's no input (final-file check)
    removed: int | None = None      # target rows the input had, which the step had to remove
```

- `VerificationResult.measurement: StepMeasurement | None = None` and `StepVerification.measurement: StepMeasurement | None = None`. `_aggregate_verdicts` copies it from each step's result, the same way it copies `findings`.
- **Dedupe verifier.** `_check_against_input` already reads the input and computes `expected = len(original) - duplicated`. Return `removed = int(original.duplicated().sum())` and `input_rows = len(original)`, measured on the non-empty rows, exactly as the existing check does. `removed` is set **only when the verifier passes**. The pass condition already proves the output has exactly `input − removed` non-empty rows, all taken from the input, so the number is confirmed rather than predicted.
- **Empty-rows verifier.** The same, with `removed = int(original.isna().all(axis=1).sum())`.
- **Final-file check** (`_verify_final_file`). `output_rows` and `remaining` for each chained step's target. `removed` is `None`.
- The existing finding strings stay unchanged, so Inspector's other consumers and tests are unaffected.

Nothing in the outcome path reads `StepResult.summary`, `self_reported_findings` or Skill metadata. A unit test asserts this: a Skill that reports `removed_count=99` still gets the verifier's number in the reply.

### 2.2 The `OutcomeReport`

New module `buddy/outcome.py`. It holds a pure function `build_outcome_report(request: SpeakingRequest) -> OutcomeReport` and a pure renderer `render_outcome(report, file_style) -> str`. Neither has any model or I/O dependency.

```python
DONE, NOT_DONE, DONE_UNREQUESTED = "done", "not_done", "done_unrequested"
# NOT_DONE reason codes; each one is true by how it's derived:
NOT_PLANNED          # in Brain's ledger; no step for its capability ran
NOT_READ_AS_REQUEST  # only the mention detector has it; Brain read the request as something else
NO_SKILL             # Brain listed it as "unsupported": no capability exists

@dataclass(frozen=True)
class ActionOutcome:
    action: str                          # e.g. "remove_duplicate_rows"
    status: str                          # DONE | NOT_DONE | DONE_UNREQUESTED
    reason: str | None = None            # a NOT_DONE reason code
    measurement: StepMeasurement | None = None
    user_phrase: str = ""                # only for NO_SKILL: the user's own words, sanitised

@dataclass(frozen=True)
class OutcomeReport:
    actions: tuple[ActionOutcome, ...]   # ledger order (the user's order), then unrequested ones
    brain_reading: tuple[str, ...]       # the actions Brain read, for NOT_READ_AS_REQUEST wording
    final_rows: int | None               # from the final-file / last step measurement
    residuals: tuple[StepMeasurement, ...]  # final-file counts for NOT_DONE targets (2.5)
    order_used: str | None               # today's _order_used_sentence(request)
    delivered_file: str | None           # today's _delivered_file_name(request)
    output_name: str | None
```

**Derivation rules.** They reuse the existing single "passed" rule, `_step_passed`, and `_is_action_step`.

| Ledger has the action? | A step for its capability… | Status |
|---|---|---|
| yes | ran, passed (`_step_passed`), and the turn delivered | `DONE`, with that step's measurement |
| yes, from Brain | didn't run | `NOT_DONE` / `NOT_PLANNED` |
| yes, from the mention detector only | didn't run | `NOT_DONE` / `NOT_READ_AS_REQUEST` |
| yes, as `unsupported` | (no capability) | `NOT_DONE` / `NO_SKILL` |
| no | ran and passed | `DONE_UNREQUESTED` |

The report is only built for turns that delivered a verified file (`_model_may_describe_outcome` is true today). Undelivered, stopped and unverified turns keep `TemplateSpeaker`'s existing factual paths unchanged. They already never claim completion (verified in V1 and V5 of the final review), so this change doesn't touch them.

The mapping from action to capability is `_INTENT_CAPABILITIES`, moved to a small shared table, `buddy/actions.py`. That table also holds each action's plain wording ("remove duplicate rows" / "duplicate rows" / "removed … duplicate row(s)") and its mention pattern (3.2), so a new skill is added in exactly one place.

### 2.3 Rendering

The renderer is deterministic, and its wording is fixed in code. Each sentence comes from exactly one report field:

| Report content | Rendered sentence |
|---|---|
| `DONE`, `removed > 0` | "Removed 2 duplicate row(s)." |
| `DONE`, `removed == 0` (**N2**) | "There were no duplicate rows to remove." |
| `DONE`, no measurement (e.g. a non-composite verifier) | "Removed the duplicate rows; the result was checked." |
| `DONE_UNREQUESTED` | "I also removed 2 duplicate row(s), though I didn't read that as part of your request." |
| `NOT_PLANNED` | "I didn't remove the empty rows: that step was left out of what I ran." |
| `NOT_READ_AS_REQUEST` | "Your message also mentions duplicate rows, but I didn't remove any. I read your request as: remove empty rows." |
| `NO_SKILL` | "I can't do this part yet: “give me a report”." |
| final state | "The copy has 4 row(s), 0 duplicate row(s) remaining." (the verifier's existing wording, so continuity is kept) |
| residual (2.5) | "It still has 1 fully-empty row(s)." |
| `order_used` | today's `_order_used_sentence` text, now always present in the reply for both speakers |
| file, `file_style="generated"` (Template) | "Saved as \`report_deduplicated_S-task-1.xlsx\`." (as today) |
| file, `file_style="plain"` (PersonalityVoice) | "Your cleaned copy of report.xlsx is ready." (as today) |

**Lead word.** The block starts with "Done — " only when the ledger is non-empty, every ledger item is `DONE`, and nothing is `DONE_UNREQUESTED`. It starts with "Partly done — " when at least one requested item is `DONE` and at least one is `NOT_DONE`. Otherwise there is no lead word. So a bare "Done" can't appear for a compound request with an item left unaddressed, which is one of the backlog's acceptance criteria.

**Unchanged parts.** The analysis preface ("Before anything else, here's what I noticed: …"), `INITIAL_ANALYSIS_FAILED_SENTENCE` and `RECHECK_FAILED_SENTENCE` keep their current code-rendered text and position.

**Wording rules.** The wording states what Buddy did and how Buddy read the request. It never states what the user wanted ("which you didn't ask for"). A ledger with imperfect recall can back up the first, but not the second.

**No new unverified claims.** The block adds nothing that isn't measured. For example, it does **not** add "your original is unchanged" to delivered turns. That is true by rule 2, but it isn't checked at runtime. See Deferred item 5.

### 2.4 What the model may still contribute, and how it's kept from stating facts

**What the model contributes.** The model chooses tone: an opener (a greeting or acknowledgement) and a closer (a next-step question). It chooses them from `buddy/voice_catalog.py`:

```python
@dataclass(frozen=True)
class Phrase:
    id: str                                   # "friend.opener.twins"
    mode: str                                 # "friend" | "focus"
    slot: str                                 # "opener" | "closer"
    text: str                                 # may contain {object}, {count}: filled by code from the report
    when: Callable[[OutcomeReport], bool]     # eligibility, checked by code
```

Illustrative entries:

| ID | Text | Eligible when (code) |
|---|---|---|
| `friend.opener.neutral` | "Here's where things landed." | always |
| `friend.opener.twins` | "That sheet had a real twin problem." | a dedupe `DONE` with `removed >= 3` |
| `friend.opener.little_to_fix` | "Good news, sort of: there wasn't much to fix." | every `DONE` has `removed == 0`, and there's no `NOT_DONE` |
| `friend.opener.partial` | "Heads-up: only part of this went through." | any `NOT_DONE` |
| `friend.closer.offer` | "Want me to {verb} {object} on the new copy too?" | a `NOT_DONE` action has a capability, and no analysis-preface question is already in the reply |
| `focus.closer.offer` | "{Verb} {object} now?" | same |
| `focus.opener.none` | (empty) | always; Focus mode normally uses no opener |

**How it's enforced.** One function, `assemble_reply(report, selection, mode) -> str`, applies all of these:

1. The model must return JSON of the form `{"opener": <id|null>, "closer": <id|null>}`. Anything else, including free text, a JSON parse failure or extra keys, means both slots are dropped. The reply is then the outcome block alone, which is identical to `TemplateSpeaker`'s delivered reply apart from `file_style`.
2. Each ID must exist, match the mode and slot, and satisfy `when(report)`. Otherwise **that slot** is dropped.
3. Placeholders are filled only from report fields: `{object}` and `{verb}` from `buddy/actions.py`, and `{count}` from a measurement. The model never supplies a value.
4. Reply order: `opener`, analysis preface, outcome block, `closer`. The outcome block can't be shortened, reordered or replaced by anything the model returns.

**Why this holds without a word list.**
- Every string that can reach the user is either code-rendered from measured data, or a catalog entry a human reviewed together with its predicate.
- A catalog entry that says something factual ("twin problem") is only eligible when the report makes it true.
- The model's whole output space is a choice among the IDs code has already filtered, so it can't bring in a new claim.
- The review burden moves from "every possible model sentence" to "each catalog row and its predicate", which is a finite list checked into the repo.
- `_COMPLETION_CLAIM` stays only as a secondary lint in the catalog's tests: entries eligible when something is `NOT_DONE` must not match it. It isn't the safeguard.

**The prompt.** The system prompt (per mode) gives the persona and the JSON contract. The user prompt holds the **rendered outcome block**, so the model sees exactly what the user will see, plus the eligible entries (ID and text). Only eligible entries are shown, so choosing any other ID is a hallucination and gets dropped.

**Fallback.** A `DeterministicPhraseSelector` (Friend: `neutral` plus the first eligible closer; Focus: no opener plus the first eligible closer) runs on any model failure. It has the same interface, so removing the model call later is a one-line wiring change. See 6.6.

### 2.5 Residual counts for requested actions that weren't done (recommended, small)

For C1, C3 and N1, the most useful true sentence is "the copy still has 1 empty row". After Inspector's verifier runs, a `ResidualMeasurer` in `buddy/inspector.py` (turn level only, **not** inside `CompositeVerifier`, because Hands calls that per step) takes each ledger action that has a verifier and no passing step. It re-runs that verifier's measurement on the delivered file, with no input. The result goes on `VerificationResult.residuals`. It never fails the turn. It only reads the final file, so rule 2 isn't affected. If the read fails, there's no residual sentence and nothing is claimed.

---

## 3. Part 2: how Buddy knows what the user requested

### 3.1 Source A: Brain's `requested_actions`

Extend Brain's JSON contract (`buddy/brain.py`, `_SYSTEM_PROMPT` and `_parse_reply`):

```json
"requested_actions": [
  {"action": "remove_duplicate_rows" | "remove_empty_rows" | "unsupported",
   "source_phrase": "<the user's exact words asking for it>"}
]
```

- It's stored as `Understanding.requested_actions: tuple[RequestedAction, ...] = ()`, where `RequestedAction(action, source_phrase, source="brain", phrase_found: bool)`.
- `action` outside `ACTION_VOCABULARY` becomes `unsupported`, keeping the phrase. It's never dropped, because dropping it would lose a request.
- `phrase_found` records whether the phrase occurs in the message (case-insensitive). A missing phrase **doesn't drop the entry**. On the "not done" side, a false entry only adds a true "I didn't…" sentence, whereas a dropped entry hides one.
- If the field is missing or malformed (older replies, test fakes), the ledger is built from `intent` alone: single-action intents map to one entry, and `multi_step_request`, `unclear` and `None` map to no entries. So the existing `_QueuedLLMProvider` fixtures still parse.
- **This doesn't change routing.** Brain's `intent` still drives the planner exactly as today. The ledger is read only by Voice and `ResidualMeasurer`. Execution is identical, so rule 2 isn't affected.

**Why Brain, and not the Planner or Think:**
- **Planner.** Its `source_phrase`s come from the same call that dropped the step in C1, so a check built from them is circular. It also only runs for `multi_step_request`.
- **Think.** Its prompt includes "Classified intent: …" (`buddy/think.py`), so its reading is anchored on Brain's. That's the wrong property for catching a Brain misclassification. Its output is also prose.
- **Brain.** It's already the closed-vocabulary classifier, and the ledger is one more field in a call that already happens. That means no extra LLM call and no extra 429 exposure.

### 3.2 Source B: a deterministic mention detector (independent of every model)

`buddy/actions.py` gives each action a mention pattern for its **object**. For example:
- `remove_duplicate_rows`: `\b(duplicates?|dupes?|dedup\w*|duplicated rows?)\b`
- `remove_empty_rows`: `\b(empty|blank)\s+(rows?|lines?)\b`

A `MentionCrossCheck(inner: Interpreter)` wraps `ClaudeInterpreter`, in the same composition style as `ProactiveAnalysisPlanner`. It adds `RequestedAction(action, source_phrase=<matched text>, source="mention")` for every action whose object is mentioned but which Brain didn't list. It runs even when Brain fails (intent `None`). That's harmless, because such turns don't deliver and don't use the report.

**Why this isn't "a word list as the main safeguard".** The detector guards nothing against claims, because claims are closed off structurally by Part 1. It can only add items to the **not done** side, and it works in the direction where errors are safe:
- **False positive** (for example "remove empty rows but keep the duplicates"): the reply says "Your message also mentions duplicate rows, but I didn't remove any." That's true, and here it's even reassuring. The cost is some noise.
- **False negative** (a paraphrase such as "get rid of the repeats"): the reply is silent about that item, as it is today. Still no false claim.

Its limits: it's English only, it has to be maintained for each action, and it only knows actions Buddy has a skill for. `unsupported` requests rely on Brain alone.

### 3.3 Comparison and the misclassification case (N1)

N1: "remove the duplicate rows please". Brain returns `intent=remove_empty_rows`, Think proceeds, and the empty-rows skill runs and is verified. Assume the worst case, where Brain's `requested_actions` makes the same error: `[remove_empty_rows]`.

- The mention detector adds `remove_duplicate_rows`, marked `NOT_READ_AS_REQUEST`.
- Report: `remove_empty_rows` is `DONE` (removed 1). `remove_duplicate_rows` is `NOT_DONE` (`NOT_READ_AS_REQUEST`). The residual is 2 duplicate rows.
- Reply, all code-rendered: "Partly done — removed 1 fully-empty row(s). Your message also mentions duplicate rows, but I didn't remove any. I read your request as: remove empty rows. The copy has 5 row(s), 0 fully-empty row(s) remaining. It still has 2 duplicate row(s). Saved as \`…\`."
- The review's three model replies, (a) "All done — duplicates removed.", (b) and (c), can't appear: they aren't JSON, so both slots drop. Even a valid selection can't bring in a duplicate claim, because every catalog entry about duplicates requires a dedupe `DONE`.

**If the detector also misses** (a paraphrase): the reply is "Done — removed 1 fully-empty row(s). …". That's true about what happened, but the "Done" is misleading about the request. This is the one residual case, and it's listed in 6.9 and in "What would change this design".

**A known limitation, not addressed here:** asking the user when Brain and the detector disagree, instead of acting and then reporting (6.7).

### 3.4 The other review scenarios

The fixture is the review's: 6 rows, 2 exact duplicates, and 1 empty row that dedupe doesn't remove.

| # | Setup | Ledger | Report | Reply, in essence (code) |
|---|---|---|---|---|
| C1 | "remove duplicates then remove empty rows"; the planner returns only dedupe; `TemplateSpeaker` | Brain: dedupe, empty | dedupe `DONE` (2); empty `NOT_PLANNED`; residual 1 empty | "Partly done — removed 2 duplicate row(s). I didn't remove the empty rows: that step was left out of what I ran. The copy has 4 row(s)…; it still has 1 fully-empty row(s). Saved as …" |
| C2 | As C1, with `PersonalityVoice`; the model says "Done — duplicates and empty rows are gone from report." | same | same | The C1 block, plus only eligible slots. The model's string can't appear (it isn't JSON). A JSON selection of an "all done" opener is ineligible, so it's dropped. |
| C3 | "remove duplicates and also delete the empty rows"; Brain returns `remove_duplicate_rows` | Brain: dedupe; mention: empty | dedupe `DONE`; empty `NOT_READ_AS_REQUEST`; residual 1 | "Partly done — removed 2 duplicate row(s). Your message also mentions empty rows, but I didn't remove any. I read your request as: remove duplicate rows. …" |
| N2 | Correct plan; dedupe on a file with no duplicates; the model says "Removed 3 duplicate rows — your sheet is clean." | Brain: dedupe | dedupe `DONE`, `removed=0` | "Done — there were no duplicate rows to remove. The copy has N row(s), 0 duplicate row(s) remaining…" (N from the verifier's read). The count comes from the verifier, and "3" can't appear. |
| N5 | "remove empty rows"; the planner returns analyze + dedupe | Brain: empty | dedupe `DONE_UNREQUESTED`; empty `NOT_PLANNED`; residual 1 | "I also removed 2 duplicate row(s), though I didn't read that as part of your request. I didn't remove the empty rows: that step was left out of what I ran. …" (no lead word) |

---

## 4. Files, rough size, and the tests that change

### 4.1 Source (about 650 lines added, about 150 removed)

| File | Change | Rough size |
|---|---|---|
| `buddy/models.py` | `StepMeasurement`, `RequestedAction`; `measurement` on `VerificationResult` and `StepVerification`; `residuals` on `VerificationResult`; `requested_actions` on `Understanding` | +60 |
| `buddy/actions.py` (new) | Action table: capability, plain wording, mention pattern. `_INTENT_CAPABILITIES` moves here (`planner.py` and `voice.py` import it) | +70 |
| `buddy/brain.py` | Prompt field, parsing and validation of `requested_actions`, fallback from `intent`, `MentionCrossCheck` wrapper | +80 |
| `buddy/inspector.py` | Measurements in both verifiers and the final-file check; `_aggregate_verdicts` copies them; `ResidualMeasurer` | +80 |
| `buddy/outcome.py` (new) | `OutcomeReport`, `build_outcome_report`, `render_outcome` | +250 |
| `buddy/voice_catalog.py` (new) | `Phrase`, the catalog for both modes, `DeterministicPhraseSelector` | +120 |
| `buddy/voice.py` | `TemplateSpeaker`'s delivered path calls `render_outcome`. `PersonalityVoice` gets a new prompt, JSON parsing and `assemble_reply`. `_mentions_file` and the file-append go (now structural). `FRIEND/FOCUS_MODE_PROMPT` are rewritten for selection. `SpeakingRequest` gains `requested_actions`. `_model_may_describe_outcome` is renamed `_model_may_choose_phrases` (same truth table) | +90 / −150 |
| `buddy/engine_factory.py` | Wraps the interpreter in `MentionCrossCheck`; wires `ResidualMeasurer` into `Inspector` | +8 |
| `scripts/run_dedupe_manually.py` | `--fixture {default,no-duplicates,no-empty}`; prints the ledger, the `OutcomeReport` and the model's raw selection | +40 |
| Docs | `docs/BUDDY_PERSONALITY.md` §14 (success style), `docs/WORKBUDDY_ARCHITECTURE.md` (Voice section), `CLAUDE.md` "Decided behaviour" (**needs the user's approval**, Section 7), `docs/backlog.md` | text |

### 4.2 Existing tests that change

`tests/buddy/test_voice.py`:
- `test_personality_voice_returns_the_models_reply_stripped_for_a_verified_delivery` (≈394) and `test_personality_voice_uses_the_models_reply_for_a_verified_delivered_turn` (≈1483): **inverted**. The model's free text never appears, and the reply equals the outcome block plus the eligible slots.
- `test_personality_voice_reply_always_mentions_the_file_handed_back` (≈1000): the expected string changes.
- `test_personality_voice_leaves_a_reply_that_already_names_the_file` (≈1012): **deleted**, because there's no model text left to inspect. The file sentence is always rendered.
- `test_personality_voice_prompt_contains_only_understanding_execution_and_verification_facts` (≈405), `…prompt_states_the_file_handed_back` (≈984), `…reports_analysis_of_the_final_file_not_the_original` (≈1022), `…prompt_carries_no_capability_tags_or_ids` (≈597): these are re-pointed at the new prompt (the rendered block plus eligible phrases). Where they asserted a fact in the prompt, the assertion moves to the reply, which is stronger.
- `…does_not_present_an_unrun_intent_as_done` (≈486), `…reply_for_unrun_dedupe_does_not_claim_duplicates_removed` (≈509), `…flags_a_dedupe_step_that_ran_but_failed_verification` (≈525): the "(deduplicate) was NOT performed" prompt line is replaced by ledger lines in the reply. The undelivered cases stay on the template path, and their reply assertions keep passing.
- `test_personality_voice_prompt_states_the_order_used_when_it_couldnt_be_confirmed` (≈1661) and the PersonalityVoice half of `test_the_order_used_is_not_stated_otherwise`: they become **reply-level**. This closes the backlog's F4-with-PersonalityVoice item.
- `test_model_may_describe_outcome` (≈1557): renamed; the truth table is unchanged.
- The `TemplateSpeaker` success tests (≈93, ≈124, ≈1256): `"Done" in reply`, the output names and "0 duplicate row(s)" keep passing (the lead word and the verifier wording are kept). Assertions on the exact sentence change.
- Unchanged: the mode-prompt identity tests (≈376, ≈385), the fallback tests (≈653, ≈666), all undelivered/stopped/unverified tests (≈1393, ≈1459, ≈1513), and the logging test (≈678).

`tests/buddy/test_engine_factory.py`:
- ≈995 (`state.reply == "Both cleanups went through. Your cleaned copy of report.xlsx is ready."`): the free text is gone, so the expected value becomes the rendered block.
- ≈1326 (`_RecordingPersonalityVoice("Both cleanups went through.")`): the same kind of change.
- ≈960, ≈1050, ≈1217 (`startswith("Done")` / `"Done" in`): these still pass when every ledger item is `DONE`. Check each fixture's Brain reply maps to a ledger with no gaps.
- ≈132, ≈451-456, ≈579-580, ≈826-828, ≈885-903, ≈1088-1090, ≈1184-1185, ≈1296: expected to pass unchanged (undelivered paths, the preface, file names).

`tests/buddy/test_inspector.py`: the equality assertions at ≈519/522 use fake verifiers, so `measurement` stays `None` and they pass unchanged.

`tests/buddy/test_brain.py`: no existing assertion changes, because the field is optional.

The implementer must run the full suite and list every test that changed in the fixes doc, as the earlier reviews did. The list above comes from reading the tests, not from running them.

---

## 5. Verification

### 5.1 Unit tests (all LLM calls faked)

**Measurements (`test_inspector.py`)**
- The dedupe verifier on 6 rows with 2 duplicates and 1 empty row gives `removed=2`, `remaining=0`, and an `input_rows` that matches a fresh pandas read.
- On a file with no duplicates it gives `removed=0` (N2's data).
- A Skill whose `summary` or `metadata` says 99 still gets the verifier's number in the reply.
- A failing verifier has `removed=None`.

**The report and the renderer (`tests/buddy/test_outcome.py`, new)**
- A table-driven test for every row of the tables in 2.2 and 2.3.
- The lead-word rules.
- `NOT_READ_AS_REQUEST` wording names Brain's reading.
- `NO_SKILL` sanitises the user's phrase: at most 80 characters, no backticks or newlines.

**Numbers can only come from measurements (property test)**
- For a grid of about 200 generated `OutcomeReport`s × every catalog selection × both modes, every integer in the assembled reply must equal a field of the report.
- The generated output name's digits are excluded by stripping the file sentence first.

**The model's output can't reach the reply (`test_voice.py`)**
- Feed `PersonalityVoice`:
  - the review's literal replies (N1 a/b/c, N2, C2, N5);
  - random strings;
  - JSON with unknown IDs, wrong-mode IDs, ineligible IDs, extra keys, and IDs carrying injected text.
- Assert `reply == assemble_reply(report, s, mode)` for some selection `s` drawn from the eligible set (empty slots included), and that the model's string isn't in the reply.

**The catalog (`test_voice_catalog.py`, new)**
- Every entry's placeholders are fields the renderer supplies.
- Every entry with a factual placeholder or a target noun has a predicate that references that target.
- Entries eligible when anything is `NOT_DONE` don't match `_COMPLETION_CLAIM` (the secondary lint).
- IDs are unique.

**The ledger (`test_brain.py`)**
- `requested_actions` is parsed. An unknown action becomes `unsupported`. `phrase_found` is set correctly. A missing field falls back to `intent`. `MentionCrossCheck` adds only actions Brain left out.
- The negation case adds a `mention` entry, and the resulting reply sentence is true.

**End to end, through `build_engine`** (in `tests/buddy/test_engine_factory.py`, with the existing `_QueuedLLMProvider` and real skills on a real `.xlsx`). One test per review scenario, **N1, N2, C1, C2, C3 and N5**, each parametrised over the review's exact model replies and both modes. Each test asserts:
1. none of the review's false phrases are in `state.reply` ("duplicates removed", "Removed 3", "empty rows are gone", "Both done");
2. every count in the reply equals a fresh pandas read of the delivered file (and of the original, for "removed");
3. every `NOT_DONE` item from 3.4 has its sentence;
4. there's no bare "Done" whenever a ledger item is `NOT_DONE`;
5. the original is byte-identical afterwards (rule 2).

**Also covered:**
- The order sentence appears with `PersonalityVoice` even when the model's selection has nothing to do with order.
- A regression test: a turn where every requested action is done still gets "Done — …" and the file sentence.

### 5.2 Live runs with Groq (before merging; results in `docs/reviews/design-verified-outcome-live-runs.md` or the implementation branch's equivalent)

N1, C1–C3 and N5 need a model **error**, so they can't be forced live. The fake-provider tests above cover them. The live runs check what fakes can't:
- that real models return valid selection JSON;
- that Brain's real `requested_actions` match the message;
- that the end-to-end numbers are right.

| # | Mode | Fixture | Message | What it checks |
|---|---|---|---|---|
| 1 | focus | default | "remove the duplicate rows" | Baseline: "Removed 2", and selection validity |
| 2 | friend | default | same | Friend-mode catalog selection |
| 3 | focus | **no-duplicates** | "remove the duplicate rows" | **N2 live:** "no duplicate rows to remove"; no invented count |
| 4 | focus | default | "remove the duplicate rows and then remove the empty rows" | Chain, both `DONE`, "Done — " |
| 5 | friend | default | "remove the empty rows and then remove the duplicate rows" | Chain in the other order |
| 6 | focus | default | "remove the duplicate rows and then give me a report" | `NO_SKILL` (overlaps L-3) and no bare "Done" |
| 7 | focus | default | "remove the empty rows but keep the duplicates" | A mention-detector false positive gives a true sentence |
| 8 | focus | default | "get rid of the repeated entries" | Brain ledger recall on a paraphrase |
| 9 | friend | default | "hi, what can you do?" | The undelivered path is unchanged, and the model isn't called |
| 10 | focus | default | "remove duplicates, and delete the blank rows" | Order-unconfirmed sentence with `PersonalityVoice` |

Each run is reported per `CLAUDE.md`: the intent, `clarification_required`, the steps executed, whether a file was delivered, an independent pandas check of the file, the reply, the **ledger**, the **model's raw selection and whether it was accepted**, whether the reply makes any false claim, and the original's hash before and after. On a 429, stop and report how many runs completed.

**Acceptance for merging:**
- 0 false claims.
- 100% of counts match the independent check.
- The original is unchanged in every run.
- Also report (it doesn't block merging): the selection-acceptance rate, and how often Brain's ledger matches the message. The selection rate feeds the decision in 6.6.

**Independent review.** Written by a separate session to `docs/reviews/<branch>-review.md`. It should re-run the review's harness (scenarios A, C, D and N1–N7) against the implementation.

---

## 6. Trade-offs and rejected alternatives

**6.1 Free-text reply, checked against a word list or regex.** Rejected. It's the approach that already failed: F1 showed "spotless" and "pesky dupes" get past any list, and N2's "Removed 3" contains no forbidden word at all. Numbers in particular can't be word-listed.

**6.2 Free-text reply, plus a second model judging entailment against the facts.** Rejected as the *main* safeguard:
- It's probabilistic.
- It adds a call, with latency and 429 exposure (earlier live runs hit 429s mid-turn).
- The judge can fail the same way the writer does.
- It could be defence in depth if free-text slots are ever reintroduced (see "What would change this design").

**6.3 Free-text opener and closer with structural limits** (≤ 8 words, no digits, the closer must end in "?"). Rejected. "Want anything else now that it's spotless?" meets every limit. Limits narrow what a claim can look like, but they don't prevent one.

**6.4 A model with no facts, writing free-text greetings.** Rejected. A model that isn't told what happened still writes generic completion lines ("All done!"), and probably *more* often.

**6.5 The model paraphrases the code-rendered block, and the numbers are checked afterwards.** Rejected. A paraphrase can change the meaning without changing any number ("Removed 0 duplicate rows" becomes "No duplicates left, all clean!").

**6.6 No model at all on delivered turns** (always `TemplateSpeaker` plus a deterministic phrase selector). This is the simplest and safest option, and it removes one Groq call per delivered turn. It's not the default only because the decided behaviour and `BUDDY_PERSONALITY.md` call for a model-voiced personality. **Recommendation:** if live runs show the model's selections are rarely better than the deterministic selector's, drop the call. The design makes that a one-line wiring change.

**6.7 Ask the user when Brain and the detector disagree, rather than act and report.** This is probably the better user experience for N1. It changes routing, though, and it needs backlog item 2 (relaying a clarifying question). **Follow-up.**

**6.8 Stop unrequested write steps before they run** (drop plan steps whose capability isn't in the ledger). This would prevent N5's unrequested work instead of just reporting it. It's deferred because the ledger's recall on paraphrases hasn't been measured (live run 8), and a low-recall ledger would drop steps that were really requested. **Follow-up**, once ledger accuracy is known.

**6.9 A separate model call just to extract requested actions.** Rejected:
- It's an extra call with extra 429 exposure.
- Its errors would be correlated with Brain's, since it's the same model on the same message.
- One more field in Brain's existing call does the same job for free.

**Costs of the chosen design, stated plainly:**
- **Personality.** Friend mode loses specific, improvised humour ("Third time's the charm…"). All humour is now pre-written and gated by a predicate. That's the price of making the reply's facts checkable.
- **Upkeep.** Each new skill needs an action-table row (wording and mention pattern), a verifier that returns a `StepMeasurement` (without one, the reply falls back to the uncounted "…the result was checked" sentence), and catalog entries.
- **Reach of the mention detector.** It's English only.
- **Leftover case.** If Brain misclassifies *and* the message is paraphrased, the reply can say "Done — removed 1 fully-empty row(s)" for a dedupe request. That is true about the file and misleading about the request. It's rarer than N1 today (it needs two misses), and it contains no false outcome claim.

---

## 7. Changes to `CLAUDE.md` "Decided behaviour" (need the user's approval)

The current line reads: "The Voice model phrases a turn only when a verified file was delivered and Inspector passed it."

Proposed replacement: "The Voice model never writes reply text. On a turn that delivered a verified file, it may choose an opener and a closer from the phrase catalog. The outcome (what was done, counts, what wasn't done and why, the order when it wasn't confirmed, the file) is rendered by code from execution and verifier data."

---

## 8. What would change this design

- **Product decides free-text personality is a requirement.** The only version I'd accept is a free-text opener with an entailment judge (6.2), falling back to the catalog, and **only after** a measured false-claim rate of 0 on an adversarial set of at least 100 cases. That isn't recommended now.
- **Live run 8 shows Brain's ledger reliably catches paraphrases** (for example, ≥ 95% on a labelled set of about 50 messages). Then 6.8 (stopping unrequested steps) becomes safe to do.
- **Live runs show the model's selections add nothing noticeable.** Then take 6.6 and drop the call.

---

## Deferred

These are kept out of this change on purpose. Each should be added to `docs/backlog.md` when this design is accepted.

1. Asking the user when the ledger sources disagree (6.7). It depends on backlog item 2.
2. Stopping unrequested write steps before execution (6.8). It depends on a measured ledger recall.
3. Comparing the ledger's phrase order with the executed order, as an order check that's independent of the planner. This would help with the real F4 fix.
4. Ledger lines on **undelivered** turns (for example "I didn't remove duplicates: I needed to ask you something first"). Those replies are already honest, so this is an improvement, not a rule-1 fix.
5. Verifying rule 2 at runtime: hash the upload before and after in Hands. The existing chain-stop sentence already claims "your original file is unchanged" without a runtime check.
6. Reporting "give me a report" as a delivered report (backlog item 3). This design only makes it an honest `NO_SKILL` line.

---

## Decisions (2026-09-30)

The design is approved with these decisions. Where they conflict with the sections above, these win.

- **D1. Adopt 6.6: no model call on delivered turns.** Code picks the opener and closer from the reviewed catalog using the eligibility predicates, deterministically but with variety (for example seeded by session id and turn index, so tests stay stable). The JSON selection contract (2.4, "How it's enforced", step 1, and "The prompt") is removed. Model-written personality returns later only on explicit conversation turns (backlog item 2).
- **D2. Actions found only by the mention detector (`NOT_READ_AS_REQUEST`) never produce "Partly done —".** They suppress the "Done —" lead word, and the reply gets no lead word.
- **D3. Reply order:** opener, outcome block, analysis preface/findings, closer. The outcome comes before the analysis findings (this replaces the order in 2.4, step 4, and the "Unchanged parts" position in 2.3).
- **D4. Implement in two PRs.** PR-A: verifier measurements, `OutcomeReport` and `render_outcome` for `DONE` actions, final-state, order-used and file sentences, the phrase catalog with a deterministic selector, and both speakers' delivered paths on the renderer. No ledger in PR-A: until PR-B, a `multi_step_request` turn gets no "Done —" lead word, and single-intent turns whose capability passed keep "Done —". PR-B: the ledger (Brain's `requested_actions`, the mention detector), `NOT_DONE` and `DONE_UNREQUESTED` lines, "Partly done —", and residual counts.
