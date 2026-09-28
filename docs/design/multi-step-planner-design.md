# Multi-step Planner design — decomposing compound requests

**Status:** Design only. Nothing in this document has been implemented.
Per instruction, this covers Step 1 (downstream verification) and Step 2
(design), and stops there.

## Step 1 — Downstream support verification

Read `buddy/delegate.py` and `buddy/hands.py` in full before proposing
anything. Findings:

**`MechanicalDelegator` (in `buddy/delegate.py`) fully supports multiple
steps.** Its `__call__` builds one `CapabilityRequest` per step via
`tuple(CapabilityRequest(...) for step in request.planning)` — a
generator over the entire `planning` tuple, not just index 0. `Delegate`
(the stage class) passes `state.planning` through to `DelegationRequest`
and stores the full result back into `state.delegation`, both
unfiltered. N steps in, N `CapabilityRequest`s out.

**Real constraint found:** `target_file` is resolved exactly once per
turn — from `active_file`, falling back to the most recent upload — and
that same single file is assigned to *every* step in the plan.
`PlanningStep` has no `target` field; `MechanicalDelegator` is the only
place a target is ever assigned. It would silently misbehave for a
hypothetical request spanning multiple files; that is still unaddressed.

> **Superseded (chaining).** This section originally called the shared
> target "a non-issue" for a compound request against one file. It
> wasn't: every file-changing step read the *original* upload and wrote
> its own separate file, so "remove the empty rows and then the
> duplicates" produced two files, each with only one change. Steps now
> **chain** in `Hands` (Delegate can't do it — outputs don't exist until
> a step runs): each file-changing step (Skill tier above READ) reads the
> previous one's output, and `ExecutionResult.outputs` holds only the
> final file; intermediates stay on each `StepResult.outputs`. READ-tier
> steps (proactive `spreadsheet.analyze`) stay outside the chain and
> always read the original. `ClaudePlanner` enforces the order the user
> stated by locating each step's quoted `source_phrase` in the message
> (falling back to the model's order when that can't be checked).
> Verification checks each step against its own input, then the final
> file against every step's claim.

**`Hands` (in `buddy/hands.py`) fully supports multiple steps, with
correct per-item error isolation.** Its `__call__` loops
`for index, request in enumerate(requests):` over the entire
`state.delegation` tuple. Each iteration dispatches using *that
request's own* `request.capability` (not a fixed one), and wraps the
dispatch call in `try/except DispatchError: errors.append(...); continue`
— a step with no matching skill is recorded as an error and execution
continues to the next step, rather than aborting the turn. This is
exactly the "no skill for this yet, surfaced cleanly, not a crash"
mechanism the design goal depends on. **Already correct, no changes
needed.**

> **Superseded (chaining).** Per-item isolation no longer holds for
> file-changing steps: since each reads the previous step's output, a
> step that can't be dispatched, isn't confirmed, fails, or fails
> verification (checked inside `Hands` via the same `CompositeVerifier`
> Inspector uses) **stops the chain**. Later steps are recorded as
> `StepResult(status="skipped")`, an error names the step that stopped
> it, no file is handed back, and the original upload is untouched. A
> no-skill step is still surfaced cleanly, not a crash — but it now ends
> the chain rather than being skipped past.

One aggregation detail relevant to later stages (not this design, but
worth recording): `ExecutionResult.success = not errors` is a strict AND
— if 3 of 4 steps succeed and 1 has no matching skill, the overall
`execution_result.success` is `False` even though 3 real outputs exist.
Not a bug, not in scope to fix here, just a fact anyone building on top
of multi-step planning should know.

**Gap found, not asked about but directly relevant:
`PlanningStep.dependencies` is silently dropped at the Delegate
boundary.** `PlanningStep` already has a `dependencies: tuple[str, ...]`
field (in `models.py`). `CapabilityRequest` — what `MechanicalDelegator`
actually produces, and the only thing `Hands` ever sees — has no
`dependencies` field at all. So even a `PlanningStrategy` that correctly
identifies "step 2 must run after step 1" cannot make that ordering
binding anywhere downstream today: `Hands` just executes
`state.delegation` in list order. This design proceeds knowing that —
see "Step ordering and dependencies" below for how it's handled without
requiring a Delegate/Hands change.

**Downstream implication flagged but explicitly out of scope for this
design:** once `Hands` regularly returns *multiple* outputs from
multiple steps, `Inspector`'s `SpreadsheetDedupeVerifier`
(`execution_result.outputs[0]`) and `Voice`'s `TemplateSpeaker`/
`PersonalityVoice` (which also key off `outputs[0]`/a single
`verification_result`) will only ever look at the *first* output and
silently ignore the rest. Multi-step Planning alone does not make a
"check column / remove / report / dashboard" feature usable end to end —
Inspector and Voice would need multi-output awareness too. That's real,
necessary follow-up work, but it's outside "Planner needs a new
strategy," so it's not part of this design's file list. Recording it
here so it isn't mistaken for solved.

> **Superseded (chaining).** Nothing keys off `outputs[0]` as "the"
> result of a multi-step turn any more. `ExecutionResult.outputs` holds
> only the chain's final file; each step's own output is on its
> `StepResult`, `CompositeVerifier` verifies every step separately plus
> the final file (`final_output`), and Voice reads the per-step
> breakdown. See "In-loop verification, the final-file re-check, and the
> Voice safeguards" at the end of this document.

---

## Step 2 — Design

### 1. Does `PlanningRequest` need the raw user message?

**Yes.** Add `user_message: str`.

`PlanningRequest` currently carries `understanding` (a single
closed-vocabulary `intent` string plus confidence/flags),
`reasoning`, `uploaded_files`, `active_file` — no raw text anywhere.
Compound detail like "this column" cannot be reconstructed from a single
classified intent string; the LLM doing the decomposition needs the
actual sentence to work from. This isn't a new pattern in this codebase
— `UnderstandingRequest` already carries `user_message` directly for the
same reason (Brain needs the real sentence to classify it). Adding it to
`PlanningRequest` extends the same established pattern, not a new one.

No `BuddyState` change is needed to support this: `state.user_message`
already exists and is already populated by the time Planner runs;
`Planner._build_request` just needs one more line reading it.

**Privacy note, carried forward from the existing Understand/Think
discipline:** `user_message` is the raw *sentence* only. Nothing in
Eyes/Brain's pipeline ever reads spreadsheet cell or row content, so
adding this field introduces no new path for file content to reach an
LLM prompt.

### 2. Does `Understanding.intent`'s shape need to change?

**No — the `Understanding` dataclass stays exactly as it is.** But I'm
recommending one small, additive change to `Brain`'s closed *vocabulary*
(the set of legal string values, not the field's type), and want to lay
out the reasoning rather than just assert it, since you asked for that
explicitly.

**Why not restructure `Understanding` itself (e.g. a list of sub-intents):**
- `docs/WORKBUDDY_ARCHITECTURE.md` Section 4 already frames decomposition
  as *Plan's* job, using almost exactly this example: `"prepare this for
  a meeting" becomes an explicit list: clean the data, summarize it,
  build a chart, assemble a one-pager`. That's the architecture doc's own
  worked example of compound decomposition — and it's attributed to Plan,
  not Understand. Growing Planner's capability without growing
  Understanding's shape is consistent with the documented design intent,
  not a deviation from it.
- Restructuring `Understanding` ripples: `Brain`'s system prompt and JSON
  schema, `ClaudeInterpreter._parse_reply`, `RuleBasedPlanner`'s
  dict-lookup logic (which reads `understanding.intent` as a single
  string), and potentially `Think`'s reasoning about a now-richer
  Understanding — multiplying the change surface across stages for what
  is fundamentally a Planner-layer capability.
- Free-text compound detail ("this column") can't be encoded in a closed
  vocabulary without turning `Understanding` into an open-ended parse
  tree, which defeats its purpose as a *coarse* classification.

**Why a small, additive vocabulary extension in `Brain` is still
warranted (not zero changes to Brain):**
`ClaudeInterpreter`'s current `_SYSTEM_PROMPT` only allows
`"remove_duplicate_rows"` or `"unclear"`, and describes `"unclear"` as
"the request does not clearly match a known intent, **or is too
ambiguous to act on**." For "check if this column exists, remove it, and
give me a report and a dashboard," Brain would almost certainly land on
`"unclear"` today — there's no other legal bucket — and because
`"unclear"` is explicitly framed as an ambiguity signal, `ClaudeThinker`
would very plausibly follow its own prompt's logic and set
`clarification_required=True`, which would block *any* planning strategy
that respects that flag (as it should). Without an explicit way for
Brain to say "this is not ambiguous, it's just compound," Think's own
honest judgment would choke the request off before Planner ever gets a
chance — not because the request is actually unclear, but because
Brain's vocabulary has nowhere to put "clear, but multi-part."

**Recommendation:** add one new legal value —
`"multi_step_request"` — meaning "the request clearly names more than
one distinct action, but is not itself ambiguous." This is a text-only
change to the closed vocabulary listed inside `ClaudeInterpreter`'s
`_SYSTEM_PROMPT` string (the same class of change as adding a new key to
`RuleBasedPlanner`'s `_INTENT_CAPABILITIES` dict) — no change to
`Understanding`'s dataclass shape, `_parse_reply`'s logic, or the
`Interpreter`/`NullInterpreter`/`Brain` classes. I'm flagging this
explicitly as a deliberate, minimal touch to `buddy/brain.py` beyond
"planner.py needs a new strategy" — since it's genuinely necessary for
the new strategy to ever fire in the real pipeline, not incidental scope
creep.

**Alternative considered and rejected:** have the new strategy trigger on
*any* non-`"remove_duplicate_rows"` intent where `clarification_required`
happens to be `False`, without touching Brain at all. Rejected because
it depends on ClaudeThinker's own judgment coincidentally not flagging
`"unclear"` as needing clarification — an implicit, fragile signal rather
than an intentional, testable contract. The explicit vocabulary value
costs one more file touch but is measurably more correct.

### 3. How does the new strategy know what capabilities exist?

**Neither raw option cleanly — a third option, and a real architecture
conflict found along the way.**

`docs/WORKBUDDY_ARCHITECTURE.md` Section 10 states directly: *"The
Dispatcher (Section 11) consults the Registry to find which Skill... can
satisfy a given task; **nothing else queries the Registry directly**."*
Injecting `SkillRegistry` straight into the new Planner strategy — the
obvious first idea — would make it a second direct consumer of the
Registry, contradicting that documented rule. Flagging this as a genuine
architecture conflict rather than silently picking a side.

**Resolution:** the wiring layer (`build_engine()` in
`buddy/engine_factory.py`) reads `registry.available_skills()` **once**,
at engine-construction time, and builds a plain, immutable snapshot —
`tuple[tuple[str, str], ...]` of `(capability, description)` pairs — that
gets passed into the new strategy's constructor. The strategy itself
never holds a live `SkillRegistry` reference and never queries it at
request time; it only ever sees a static list of strings, like any other
injected dependency. This preserves "nothing but the Dispatcher queries
the Registry directly" — a factory reading the registry once at startup
to build a configuration snapshot is a different thing from a
request-time consumer holding a live handle into it, the same way
`Understanding`/`Reasoning`/`PlanningStep` already exist so each stage
hands the next a purpose-built snapshot rather than a live reference into
another layer's internals.

**Honesty requirement — steps that don't map to a real capability are
still produced, never dropped.** The snapshot is presented to the LLM as
"capabilities that currently have a real skill" — guidance, not a hard
filter. The prompt explicitly instructs: if a requested action doesn't
match anything in that list, still include the step, naming the most
sensible capability string using the existing `domain.action` convention
(e.g. `spreadsheet.report`, `spreadsheet.dashboard`) rather than silently
omitting it. This is exactly why Step 1 mattered: `Hands` already turns
an unmatched capability into a clean, per-item, non-fatal
`NoCapableSkillError`-derived message rather than a crash — the new
strategy's job is just to be honest about what was asked, and let that
existing mechanism do its job. No new "capability unavailable" concept
needs inventing.

> **Superseded (chaining).** An unmatched capability is no longer a
> per-item, non-fatal error: it **stops the chain**. `Hands` records it
> as a `StepResult` with `status="failure"`, no skill, and a
> plain-words `failure_reason`, so Inspector fails that step; later
> steps are skipped and no file is handed back. Still a clean message,
> not a crash.

### 4. Keeping `RuleBasedPlanner`/`NullPlanner` untouched, while actually using the new strategy

Both stay byte-for-byte unchanged in behavior. But `Planner` (the stage
class) takes exactly one `PlanningStrategy` in its constructor — so
"alongside, not a replacement" needs a real answer for what actually gets
wired into the running engine, not just "both classes still exist
untested-but-unused."

**Proposed new piece: `CompositePlanner`.** A third, small
`PlanningStrategy` implementation that tries `RuleBasedPlanner` first
(cheap, deterministic, zero LLM calls for the common single-intent case)
and only calls the fallback strategy if that returns `()`. This is the
same extensibility model `docs/WORKBUDDY_ARCHITECTURE.md` Section 18
already documents for the Dispatcher's selection strategy — composed
behavior built by adding a new strategy object, never by modifying the
stage that calls it. `Planner`'s stage class needs zero changes.
`build_engine()` wires `Planner(planner=CompositePlanner(RuleBasedPlanner(), ClaudePlanner(...)))`
instead of `Planner(planner=RuleBasedPlanner())`.

I'm flagging `CompositePlanner` explicitly as a design decision beyond
the literal ask ("planner.py needs a new strategy") — it's what makes
"new addition alongside" actually mean something in the running system,
rather than leaving `ClaudePlanner` built but never reachable. Confirm
before Step 3.

**Consequence worth stating plainly:** because `RuleBasedPlanner` still
handles `"remove_duplicate_rows"` without ever invoking the fallback, the
*existing* single-step end-to-end test in `test_engine_factory.py` needs
**no change** — it still only queues 2 canned LLM replies (Brain, Think).
Only a *new* test for the compound-request path needs a 3rd canned reply,
for Planner's own LLM call.

### 5. Step ordering and dependencies

Given Step 1's finding that `PlanningStep.dependencies` is dropped at the
Delegate boundary and never consulted by `Hands`, this design does not
attempt to make dependency-aware scheduling real — that would mean
changing `CapabilityRequest`'s shape and `Hands`'s execution loop, neither
of which this design touches.

Instead: `ClaudePlanner` still populates `PlanningStep.dependencies`
where the LLM identifies a genuine ordering rationale (e.g. "remove the
column" depending on "check the column exists") — this is honest,
correctly-derived metadata even though nothing consumes it yet. There's
already a precedent for this in the codebase: `ExecutionResult.duration`
exists in `models.py` and is never populated by `Hands` today either;
fields existing ahead of their consumer isn't a new pattern here.

What *is* load-bearing today: **list order is de facto execution order**
(`MechanicalDelegator` preserves `request.planning`'s order via
`for step in request.planning`; `Hands` executes `state.delegation` via
`enumerate()` in order). So `ClaudePlanner` returns its steps already
sorted into the order they should run, using `priority=index` (0-based,
matching list position) — the same field `RuleBasedPlanner` already sets
to `0` for its single step. This guarantees correct execution order today
without requiring any dependency-consuming code to exist.

### 6. `ClaudePlanner` — concrete design

**Naming:** `ClaudePlanner`, matching the `Claude<domain-noun>` pattern
established by `ClaudeInterpreter`/`ClaudeThinker` — even though, like
those two, it's actually provider-agnostic (works with `GroqProvider` too
via the shared `LLMProvider` protocol). Keeping the existing naming
convention rather than introducing a new one, consistent with how
`ClaudeInterpreter`/`ClaudeThinker` already carry the same historical
"Claude"-prefix despite being provider-agnostic in practice.

**Constructor:**
```python
def __init__(
    self, provider: LLMProvider, *, known_capabilities: tuple[tuple[str, str], ...]
) -> None: ...
```
`known_capabilities` — the registry snapshot from item 3 above.

**Trigger conditions** (mirrors `RuleBasedPlanner`'s existing gates,
plus the new intent check):
- `understanding.intent == "multi_step_request"` — for anything else
  (including `"remove_duplicate_rows"`, which `CompositePlanner` already
  routes to `RuleBasedPlanner`, and `"unclear"`), return `()` immediately
  with **no LLM call** — cheap, consistent with `RuleBasedPlanner`'s
  early-return style.
- `not reasoning.clarification_required` — same absolute gate
  `RuleBasedPlanner` already respects. Never overridden.
- A target file must be resolvable (`active_file` or most recent
  upload) — same gate `RuleBasedPlanner` uses. Per Step 1's finding, this
  single file is what every produced step will end up targeting anyway,
  so there's no point proceeding without one.

**Prompt shape** (system prompt = static instructions; user prompt =
per-request data, matching `ClaudeInterpreter`/`ClaudeThinker`'s existing
split):
- System prompt: explains the task (decompose a multi-part request into
  ordered steps, one `capability` per step), the honesty requirement (a
  step can name a capability that isn't in the known-capabilities list —
  never omit a requested action for that reason), the JSON response
  shape, and — matching every other LLM-backed piece in this codebase —
  that it never sees file content, only names and text.
- User prompt: `user_message` (the actual sentence), `understanding.intent`/
  `confidence`, `reasoning.objective`/`recommended_strategy` (so
  `ClaudePlanner` builds on Think's conclusion rather than re-deriving
  from scratch — this also narrows the "two LLM calls might disagree"
  risk, since Planner sees and can lean on what Think already decided),
  the target file, and the `known_capabilities` list with descriptions.

**Response JSON shape:**
```json
{
  "steps": [
    {
      "description": "Check whether the column exists",
      "capability": "spreadsheet.column_check",
      "depends_on_step_numbers": []
    },
    {
      "description": "Remove the column",
      "capability": "spreadsheet.remove_column",
      "depends_on_step_numbers": [1]
    }
  ]
}
```
`depends_on_step_numbers` uses 1-based indices into the model's own
`steps` list — simpler and less error-prone than asking the model to
invent string IDs. `ClaudePlanner._parse_reply` assigns real `id`s
(`step-1`, `step-2`, ...) in list order itself, then translates each
`depends_on_step_numbers` entry into the corresponding `step-N` string
when building each `PlanningStep.dependencies`.

**Honest-fallback contract, matching `ClaudeInterpreter`/`ClaudeThinker`/
`PersonalityVoice` exactly:** on LLM failure, malformed JSON, or an
unexpected shape, log the failure (never the prompt or reply content —
same logging discipline as every other LLM-backed piece: model name,
elapsed time, token counts only) and return `()`. This is the same
"produce nothing rather than guess" signal `NullPlanner` and
`RuleBasedPlanner`'s own refusal paths already use — no new fallback
shape needed anywhere in this design.

### 7. `CompositePlanner` — concrete design

```python
class CompositePlanner:
    def __init__(self, primary: PlanningStrategy, fallback: PlanningStrategy) -> None: ...

    def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
        steps = self._primary(request)
        if steps:
            return steps
        return self._fallback(request)
```
Deliberately this simple — no new protocol, no new state, just
sequential delegation to two already-independent strategies. Both
`primary` and `fallback` are plain `PlanningStrategy`s, so `CompositePlanner`
itself is trivially testable with fakes, without needing either real
`RuleBasedPlanner` or `ClaudePlanner` in its own unit tests.

---

## File-by-file plan

| # | File | Reason | Test file |
|---|---|---|---|
| 1 | `buddy/planner.py` (modify) | Add `user_message: str` to `PlanningRequest`; add `ClaudePlanner`; add `CompositePlanner`. `NullPlanner`, `RuleBasedPlanner`, `PlanningStrategy`, and the `Planner` stage class's *behavior* are unchanged — `Planner._build_request` gets one line added to populate the new field. | `tests/buddy/test_planner.py` (modify — add `ClaudePlanner` tests: trigger-condition gating, prompt content, JSON parsing incl. `depends_on_step_numbers` → `dependencies` translation, honest fallback on LLM failure/malformed JSON, never-logs-content; add `CompositePlanner` tests: primary-wins-when-non-empty, fallback-invoked-when-primary-empty; update the `_request()` helper to supply a `user_message` default so existing `RuleBasedPlanner`/`Planner`-stage tests keep working unchanged, same pattern as `test_voice.py`'s `_request()` helper gaining a `mode` default) |
| 2 | `buddy/brain.py` (modify) | Add `"multi_step_request"` as a new legal value in `ClaudeInterpreter`'s `_SYSTEM_PROMPT` closed vocabulary, with a one-line description of when to use it. No change to `Understanding`'s shape, `_parse_reply`, or any class structure. | `tests/buddy/test_brain.py` (modify — one new test confirming a canned reply with `intent="multi_step_request"` round-trips through `ClaudeInterpreter` exactly like any other intent string; existing tests unaffected) |
| 3 | `buddy/engine_factory.py` (modify) | Build the `(capability, description)` snapshot from `registry.available_skills()` once at `build_engine()` time; wire `Planner(planner=CompositePlanner(RuleBasedPlanner(), ClaudePlanner(provider, known_capabilities=snapshot)))` in place of today's `Planner(planner=RuleBasedPlanner())`. | `tests/buddy/test_engine_factory.py` (modify — add one new end-to-end test for a compound request, using a 3rd canned LLM reply for Planner's own call; existing single-step test needs **no change**, since `RuleBasedPlanner` still handles it via `CompositePlanner` without any extra LLM call) |
| — | `docs/design/multi-step-planner-design.md` (this file) | The design record itself. | — |

**Explicitly not touched by this design**, and why: `buddy/delegate.py`,
`buddy/hands.py` (Step 1 confirmed both already work correctly for
multiple steps — no changes needed); `buddy/models.py` (`PlanningStep`
already has everything needed; `Understanding`'s shape deliberately
unchanged per item 2 above); `buddy/inspector.py`, `buddy/voice.py`
(flagged in Step 1 as needing multi-output awareness eventually, but
that's separate follow-up work, not part of "Planner needs a new
strategy"); `scripts/run_dedupe_manually.py` (not part of this ask).

## Open items requiring your sign-off before Step 3

1. The new `"multi_step_request"` intent value and the resulting touch to
   `buddy/brain.py` (item 2).
2. The registry-snapshot approach for capability discovery, resolving the
   Section 10 conflict (item 3).
3. `CompositePlanner` as the thing that actually wires `ClaudePlanner`
   into the running engine (item 4) — without it, `ClaudePlanner` would
   be built and tested but never reachable from `build_engine()`.
4. The JSON response shape and `depends_on_step_numbers` → `dependencies`
   translation (item 6).

Pending sign-off, Step 3 would implement one file at a time per the table
above, showing each file plus its tests before moving to the next, per
the project's established implementation rule.

---

## In-loop verification, the final-file re-check, and the Voice safeguards

Added with step chaining; see also `docs/reviews/chaining-wip-review.md`
and `docs/reviews/chaining-wip-fixes.md`.

**In-loop verification (`Hands`).** `build_engine` gives `Hands` the same
`CompositeVerifier` Inspector uses, as `step_verifier`. After each
file-changing step succeeds, `Hands` verifies that step against its own
input before the next step may read its output. A failure stops the
chain. So does a step that can't be dispatched, isn't confirmed, reports
failure, fails its own well-formedness check, or whose output can't feed
the next step (anything but exactly one file of the input's type). The
step that stopped the chain is always recorded as a `StepResult`
(`"failure"`, or `"declined"` for a declined confirmation) with a
plain-words `failure_reason` when there's no Skill result to check, so
Inspector sees a failing step. Later steps are `"skipped"`, and
`ExecutionResult.outputs` is `()`. Inspector still re-verifies everything
afterwards and is the final word.

**Final-file checks (`CompositeVerifier._verify_final_file`).** When at
least two file-changing steps chained into the delivered file, every
step's verifier is re-run on that final file with no input reference, so
the file the user receives satisfies every step's claim at once. It's
recorded as the `final_output` verdict.

**Final-file re-check (`Hands._recheck_final_file`).** Read-only steps
(proactive `spreadsheet.analyze`) run on the original upload, outside the
chain. Once the chain completes, each is re-run on the final file, so the
findings the reply reports describe the file actually handed back. These
steps are marked `recheck=True`. A failed re-check is a Hands warning,
never an error, and Inspector records it as a non-blocking finding: it
never fails the turn, since the delivered file was already verified. A
re-check never marks the original as analyzed (only an analysis whose
`input_reference` is the active file does).

**Voice safeguards.**
- Replies route on "was anything delivered?" first
  (`_nothing_delivered`: the chain stopped, no output, or no action step
  passed). When nothing was delivered, the reply never says "Done" or
  implies completion. A stopped chain says which step stopped it and why,
  and that the original is unchanged.
- One rule for "passed" (`_step_passed`): a step passed only if its Skill
  reported success *and* Inspector has a passing verdict for that step.
- `PersonalityVoice` checks the model's reply: when nothing was
  delivered, a reply with completion wording is replaced by
  `TemplateSpeaker`'s factual reply. When a file was delivered, the reply
  must mention it, or a sentence naming it is appended.
- A failed final-file re-check is said in plain words, and the delivered
  file is still named.
- No raw capability id (`spreadsheet.analyze`, and any other id in a
  known domain) reaches a reply: leading `[tag]`s are stripped and ids
  anywhere in findings, warnings and errors are replaced with plain words.
