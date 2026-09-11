# Slice 1 Design — "Remove Duplicate Rows" End-to-End

**Status:** Design record, pending approval. No implementation code has been
written yet. This document is the complete Step 2 design for the first real
vertical slice through Buddy's cognitive pipeline:

> User uploads a `.xlsx` file and says "remove duplicate rows."

This must flow through the real pipeline — Observe, Understand (real LLM
call), Think (real LLM call), Plan, Delegate, Dispatcher, Execute (real
Excel skill), Verify (real independent check), Speak — not a mock.

## Preceding decisions this design builds on

Two architectural conflicts were found and resolved before this design was
written, in line with the project's rule that overlaps get surfaced and
decided explicitly rather than silently merged or redesigned:

1. **`core/router.py` — dead, unreferenced, duplicate of `core/dispatcher.py`.**
   Decision: delete it. It is dead, unreferenced, undocumented, and
   duplicates `core/dispatcher.py`'s stated responsibility. If a distinct
   routing concern (e.g. an HTTP-layer router in a future API layer)
   emerges later, it will be created fresh with its own documented purpose
   at that time.

2. **`core/engine.py`'s `CognitiveCycle.dispatch` field vs.
   `docs/WORKBUDDY_ARCHITECTURE.md`.** The architecture doc is explicit and
   detailed (Sections 4, 5, 11) that the Dispatcher is *not* a pipeline
   stage — it never reads or writes `BuddyState`, and Execute is its only
   caller and only consumer. But `CognitiveCycle` had a 9th field,
   `dispatch: Stage`, wired into the stage sequence between `delegate` and
   `execute`, treating the Dispatcher as if it were itself a `Stage`
   running against `BuddyState`. Decision: **follow the doc.** Remove the
   separate `dispatch` field from `CognitiveCycle`; `Execute` alone calls
   `core.dispatcher.Dispatcher` internally, exactly as Section 11
   specifies. The Dispatcher stays fully stateless. This means editing
   `core/engine.py` (removing one field + updating `stages()` to the
   documented 8-stage order) as part of this slice.

3. **Dead-field bug in `BuddyState`.** `Understand._apply` and
   `Think._apply` currently write only to legacy fields
   (`state.intent`/`state.confidence`/`state.metadata["understanding"]` and
   `state.reasoning_trace`/`state.metadata["reasoning_result"]`) despite
   `BuddyState` already having dedicated typed fields (`state.understanding`,
   `state.reasoning`) for exactly this data. Decision: fix this as part of
   this work — the typed fields are the source of truth going forward; keep
   the older fields only where something downstream still reads them.

Both decisions are reflected throughout the design below and in the
file-by-file plan (Section E).

---

## A. LLMProvider

**Lives in:** `buddy/llm.py` (new). Not folded into `understand.py` or
`think.py` — it's shared infrastructure both stages depend on, the same
tier as `models.py`.

```python
@dataclass(frozen=True)
class LLMMessage:
    role: str          # "user" | "assistant"
    content: str

class LLMConfigurationError(Exception): ...

class LLMProvider(Protocol):
    def complete(self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024) -> str:
        """Send a request, return the model's raw text reply."""
        ...
```

Kept to exactly this one method — no streaming, no tool-use, no
multi-provider abstraction — because that's all Understand/Think need right
now: send a prompt, get text back, parse it themselves. Building out
OpenAI/Gemini/Ollama providers is explicitly out of scope for this slice;
only the interface and one real implementation are built now.

**`ClaudeProvider(LLMProvider)`** wraps the `anthropic` SDK's
`messages.create()`, concatenating the text blocks of the response into a
single string.

- **API key**: read exclusively from the `ANTHROPIC_API_KEY` environment
  variable (`os.environ.get(...)`), with an `api_key` constructor override
  that exists *only* so tests can inject a dummy value without touching the
  environment. No literal key ever appears in source. A missing key raises
  `LLMConfigurationError` immediately at construction time, not on first
  call.
- **Logging**: `ClaudeProvider` logs model name, elapsed time, and token
  usage counts only — never the key, and never raw prompt/response text
  (spreadsheet contents may flow through these prompts, so this is a
  data-hygiene call as well as a credentials one).
- **Model**: defaults to `claude-sonnet-5`, overridable via constructor
  argument.

**`ClaudeInterpreter`** (added to `buddy/understand.py` as a new class;
`Understand`, `NullInterpreter`, and `Interpreter` are left untouched) and
**`ClaudeThinker`** (added to `buddy/think.py`, same pattern) each:

1. Build a system prompt + one user message from their `*Request` object
   (`UnderstandingRequest` / `ThinkingRequest`).
2. Call `provider.complete(...)`.
3. Parse the reply as strict JSON into `Understanding` / `Reasoning`.
4. On a malformed/non-JSON reply or a provider exception: **never raise** —
   log a warning and return a conservative, low-confidence result
   (`Understanding(intent=None, confidence=0.0, missing_information=(...))`
   / `Reasoning(clarification_required=True, confidence=0.0, risks=(...))`),
   the same honesty contract `NullInterpreter`/`NullThinker` already
   follow.

For this slice, `ClaudeInterpreter`'s system prompt constrains intent to a
**closed vocabulary** — currently just `"remove_duplicate_rows"` plus
`"unclear"` — rather than open-ended free text, because Plan (Section B)
needs a reliable, finite set of intent strings to map from, and open-ended
intent classification is out of scope for a single slice.

**Injection**: `ClaudeInterpreter`/`ClaudeThinker` take an `LLMProvider` in
their constructor; `Understand(interpreter=ClaudeInterpreter(provider))` /
`Think(thinker=ClaudeThinker(provider))`. This replaces
`NullInterpreter`/`NullThinker` purely via dependency injection — no changes
to `Understand`'s or `Think`'s stage logic (`__call__`, `_build_request`,
the mechanical parts of `_apply`) are needed to make this swap. All wiring
happens once, in `buddy/brain.py`'s `build_engine()` factory (see Section
E, item 13). Tests inject a `FakeLLMProvider` returning canned JSON, so the
test suite never calls the real API or needs a key.

---

## B. Plan, Delegate, Execute, Verify, Speak

Same shape throughout, matching the existing `Understand`/`Think`
framework: a `*Request` dataclass (mechanically assembled from
`BuddyState`, no interpretation performed by the stage itself), a
`Protocol`, a `Null*` default that's honest about doing nothing, one real
implementation for this slice, and a stage class with
`_build_request`/`_apply`.

### Plan (`buddy/plan.py`)

- `Planner` protocol: `(PlanningRequest) -> tuple[PlanningStep, ...]`.
  `PlanningRequest` carries `understanding`, `reasoning`, `uploaded_files`,
  `active_file` — read from the now-fixed typed fields (`state.understanding`,
  `state.reasoning`), not from `metadata`.
- `NullPlanner`: returns `()`.
- Real implementation: **`RuleBasedPlanner`** — a small dict mapping known
  intent → capability (`{"remove_duplicate_rows": "spreadsheet.deduplicate"}`),
  emitting one `PlanningStep` when the intent matches, a file is available,
  and Think didn't flag `clarification_required`; otherwise `()`.
  **No LLM call** — turning one known intent into one step is mechanical
  translation, not judgment. This is a deliberate scope choice for this
  slice, not an oversight; a richer `Planner` is a natural future swap-in
  behind the same protocol as more intents are added.
- `Plan` stage writes `state.planning` (typed, source of truth) and mirrors
  a plain-language description into `state.plan` (legacy list) for
  continuity, consistent with how Understand/Think currently keep their
  legacy fields populated alongside the typed ones.

### Delegate (`buddy/delegate.py`)

- `Delegator` protocol: `(DelegationRequest) -> tuple[CapabilityRequest, ...]`.
- `NullDelegator`: returns `()`.
- Real implementation: **`MechanicalDelegator`** — produces one
  `CapabilityRequest(capability=step.capability, target=<file>, parameters={})`
  per `PlanningStep`. `target` is resolved from `state`/`context` (active
  file, or the most recent upload) rather than from `PlanningStep` itself —
  `PlanningStep` has no target field, and this mirrors how Understand/Think
  already pull file references straight from state rather than threading
  them through every model.
- **Skill-agnosticism, confirmed concretely**: `capability` is always a
  domain string (`"spreadsheet.deduplicate"`), never a skill name.
  `buddy/delegate.py` has no import of, and no reference to, `skills.excel`
  anywhere. If a second skill later declared the same capability, Delegate's
  code would not change at all — only the Dispatcher's `find_by_capability`
  lookup and its selection strategy would see two candidates and choose
  between them.
- **New `BuddyState` field required**: there is currently no typed slot for
  Delegate's output among the five typed fields already on `BuddyState`.
  This design adds `state.delegation: tuple[CapabilityRequest, ...] | None
  = None`, added to `state.py` and cleared in `reset_transient()`, mirrored
  into legacy `state.tasks` for continuity.

### Execute (`buddy/execute.py`)

Redesigned per the "follow the doc" decision above:

- No `Executor` protocol / `Null` variant. The architecture doc frames the
  Dispatcher itself as Execute's one required collaborator, not a
  swappable strategy, so `Execute.__init__(self, dispatcher:
  core.dispatcher.Dispatcher)` takes it directly.
- For each `CapabilityRequest` in `state.delegation`: build a `SkillTask`
  (`permission=PermissionTier.WRITE`, since dedup produces a new file),
  call `dispatcher.dispatch(task, input_type=request.capability)`, then
  call `execution_request.skill.execute(execution_request.task)` itself —
  Execute is the only place that crosses into a Skill, exactly as
  documented in Section 4/11 of the architecture doc.
- Tracks `state.active_task` while running, appends to `state.outputs`,
  and writes the typed `state.execution_result`.
- If `state.delegation` is empty, no-ops with
  `ExecutionResult(success=True, outputs=(), warnings=("nothing to execute",))`
  — no Dispatcher call is made. Constructing Execute with a `Dispatcher`
  backed by an empty `SkillRegistry` already produces correct "nothing
  configured" behavior for free (it raises `NoCapableSkillError` for any
  real request), so no new `NullExecute`-style class is needed.

### Verify (`buddy/verify.py`)

- `Verifier` protocol: `(VerificationRequest) -> VerificationResult`.
  `NullVerifier` returns `passed=False, confidence=0.0,
  findings=("no verifier has been configured",)` — it never claims success
  it didn't check, the same honesty rule as the other `Null*` types.
- Real implementation: `SpreadsheetDedupeVerifier` — specified concretely
  in Section D.

### Speak (`buddy/speak.py`)

- `Speaker` protocol: `(SpeakingRequest) -> str`. `NullSpeaker` returns a
  fixed, honest "I wasn't able to generate a response" string.
- Real implementation: **`TemplateSpeaker`** — deterministic templating
  over `execution_result`/`verification_result` ("Done — removed N
  duplicate row(s) from `<file>`, saved as `<output>`." on pass; an
  explanation plus `verification_result.recommendations` on fail).
  **No LLM call** here either — same reasoning as Plan: templating a
  known-shape success/fail message doesn't need language generation for
  this slice.

### Dead-field fix, and one consequence found while designing it

Fixing `Understand._apply`/`Think._apply` to set
`state.understanding`/`state.reasoning` isn't quite sufficient on its own —
`Think._build_request` currently reads `state.metadata.get("understanding")`,
not `state.understanding`. That read gets repointed to the typed field too;
otherwise Think keeps depending on the metadata bucket even after Understand
starts populating the real field.

No downstream code was found that reads `state.metadata["understanding"]`
or `state.metadata["reasoning_result"]` other than that one spot in
`Think._build_request`. Once it's repointed, those two metadata writes
become dead and are removed — consistent with `state.py`'s own docstring,
which says metadata is "not... an overflow bucket for structured stage
output that belongs in one of these dedicated fields."

`state.intent`, `state.confidence`, and `state.reasoning_trace` are kept —
nothing reads them yet, but they're cheap, harmless, and explicitly
documented as coexisting during the transition, so they are not removed
without being asked to.

---

## C. Excel skill

**New file**: `skills/excel/dedupe.py`, defining
`ExcelDeduplicateSkill(Skill)`. `skills/excel/__init__.py` becomes a thin
re-export (`from skills.excel.dedupe import ExcelDeduplicateSkill`) rather
than holding the logic itself — Excel will likely grow more capabilities
later, and one file per capability scales better than piling everything
into `__init__.py` now.

**Declared shape:**

- `name = "excel.deduplicate"`
- `supported_inputs = ("spreadsheet.deduplicate",)` — a capability string,
  not a file extension. This leans on `Dispatcher`'s own docstring, which
  describes `input_type` as "the capability to look for, *expressed as* an
  input type (e.g. a file extension)" — treating capability and input-type
  as the same concept, extension being only one possible shape of it. This
  is a design interpretation of that docstring, called out explicitly for
  approval rather than assumed silently.
- `permission_tier = PermissionTier.WRITE` — it produces a new file, so it
  isn't a `READ`-tier skill even though the source file itself is never
  modified.
- `can_handle(task)`: true only if `task.input_references` contains exactly
  one path ending in `.xlsx`/`.xls`.

**`execute(task)` — the read/write path, stated explicitly:**

```python
def execute(self, task: SkillTask) -> SkillResult:
    input_path = task.input_references[0]           # workspace/uploads/<...>.xlsx
    df = pandas.read_excel(input_path)               # READ-ONLY open of the upload
    deduped = df.drop_duplicates()

    output_path = self._build_output_path(task)       # workspace/outputs/<...>.xlsx — always a new path
    deduped.to_excel(output_path, index=False)         # WRITE only ever targets output_path

    ...
    return SkillResult(status=SkillStatus.SUCCESS, outputs=[str(output_path)], ...)
```

**Explicit confirmation of write behavior, per the requirement that this
be stated in writing before approving a Write-permission skill:** the skill
**never** opens the original upload with write intent, and never writes to
any path derived from `task.input_references`. `pandas.read_excel(input_path)`
is inherently read-only — that call has no write mode. The only write
call in the entire method is `deduped.to_excel(output_path, ...)`, and
`output_path` is always constructed fresh under `workspace/outputs/`,
computed from the task/workspace id — it is never set to, derived from, or
allowed to collide with `input_path`. There is no code path in this skill
that opens the upload file in `'w'`/`'a'` mode, calls `to_excel()` back onto
`input_path`, or deletes/renames the original. The upload is treated as
immutable input for the entire method body. In short: **read from
`workspace/uploads/`, write only to a new file under `workspace/outputs/` —
never in-place, never back to the original.**

**Why pandas over openpyxl**: dedup is a row-level tabular operation that's
one call (`drop_duplicates`) in pandas; openpyxl alone would mean
hand-rolling row-by-row comparison for the same result — more code and more
bug surface for nothing gained. Pandas uses openpyxl internally as its
`.xlsx` engine, so it's still the dependency doing the underlying file I/O
— just not driven by hand.

**Output path naming convention — flagged for explicit approval:**
`workspace/outputs/{workspace_id}/{original_stem}_deduplicated_{task_id}.xlsx`,
creating the workspace subfolder if it doesn't already exist.

Full reasoning: `docs/WORKBUDDY_ARCHITECTURE.md` Section 7 documents the
`workspace/` tree as flat — `workspace/outputs/` with no further structure
specified — because Sprint 1 was scoped to a single, implicit workspace.
This slice is the first code that actually needs to write a file into
`workspace/outputs/` for a specific workspace, so a convention has to be
chosen now. Three options were considered:

1. **Flat, no subfolder** (`workspace/outputs/{stem}_deduplicated.xlsx`) —
   simplest, matches today's literal flat tree, but breaks the moment two
   workspaces (or two uploads with the same filename in one workspace)
   produce output at the same time — a real collision risk once WorkBuddy
   is multi-tenant, which the architecture doc's own Section 7 already
   anticipates ("The workspace boundary is also the security boundary...
   a Skill is only ever given references scoped to one workspace").
2. **`{workspace_id}/{stem}_deduplicated_{task_id}.xlsx`** (the option
   chosen) — scopes output to the workspace it belongs to (matching the
   workspace-as-security-boundary framing in Section 7), and the `task_id`
   suffix guarantees no collision even if the same file is deduplicated
   twice in the same workspace. This is the minimal structure that's
   actually safe under the constraints the doc already states, without
   inventing anything the doc doesn't already imply.
3. **`{workspace_id}/{session_id}/{stem}_deduplicated.xlsx`** — adds a
   session-scoped layer on top of option 2. Rejected for this slice as
   more structure than is currently justified — nothing in the doc ties
   output file identity to session, only to workspace, and session-scoped
   nesting can be added later without breaking option 2's paths if it
   turns out to be needed.

Option 2 is the one used in the design above; it is called out explicitly
because it is a new convention this slice introduces, not one already
established in the codebase or docs.

- `verify(task, result)` (the skill's own well-formedness self-check, per
  `Skill`'s contract — distinct from Buddy's independent Verify stage):
  confirms `status == SUCCESS`, exactly one output path, and that the file
  exists on disk with nonzero size. It does **not** check row content or
  duplicates — that claim belongs entirely to Buddy's Verify stage
  (Section D), precisely so the skill's self-report is never the thing
  that decides success.
- Errors (bad file, unreadable, empty sheet, etc.) are caught and returned
  as `SkillResult(status=SkillStatus.FAILURE, errors=[...])`, never raised
  past the skill boundary — consistent with Execute's role of collecting
  results rather than crashing the cycle on a skill-level failure.

---

## D. Verify — concretely

**`SpreadsheetDedupeVerifier`** (the real `Verifier`, defined in
`buddy/verify.py`) is deliberately blind to everything the skill *said*
about itself — it never reads `SkillResult.status`, `.summary`, or
`.metadata["removed_count"]`. It works from `state.execution_result` (the
typed result Execute recorded) and does its own independent read:

```python
class SpreadsheetDedupeVerifier:
    def __call__(self, request: VerificationRequest) -> VerificationResult:
        if not request.execution_result or not request.execution_result.success \
                or not request.execution_result.outputs:
            return VerificationResult(
                passed=False, confidence=1.0,
                findings=("execution did not produce an output file",),
            )

        output_path = request.execution_result.outputs[0]
        df = pandas.read_excel(output_path)          # fresh, independent read of the artifact
        duplicate_count = int(df.duplicated().sum())  # recomputed from scratch, not trusted from the skill
        passed = duplicate_count == 0

        return VerificationResult(
            passed=passed,
            confidence=1.0,
            findings=(f"output has {len(df)} row(s), {duplicate_count} duplicate row(s) remaining",),
            recommendations=() if passed else ("re-run deduplication; duplicates remain",),
        )
```

So the check is: take the path the skill claims it wrote to, open *that
specific file* fresh, and run pandas's own `.duplicated().sum()` against it
independently — the same class of check a human reviewer would do by
opening the output and eyeballing it, just automated. If that recomputation
disagrees with what the skill's `metadata["removed_count"]` implied,
Verify's answer wins; nothing in this path consults the skill's claim at
all.

`NullVerifier` (the default when nothing real is injected) returns
`passed=False, confidence=0.0, findings=("no verifier has been configured",)`
— it never reports success it didn't check, the same honesty rule as
`NullInterpreter`/`NullThinker`.

---

## E. File-by-file plan — complete, items #1–15

Build order (leaf infra → stage plumbing → skill → wiring → the two
already-approved cleanups last, since they're independent of the slice
itself):

| # | File | Reason | Test file |
|---|---|---|---|
| 1 | `requirements.txt` (modify) | add `anthropic`, `pandas`, `openpyxl` | — |
| 2 | `buddy/llm.py` (new) | `LLMMessage`, `LLMProvider` protocol, `LLMConfigurationError`, `ClaudeProvider` | `tests/buddy/test_llm.py` |
| 3 | `buddy/state.py` (modify) | add `state.delegation: tuple[CapabilityRequest, ...] \| None` field, clear it in `reset_transient()` | `tests/buddy/test_state.py` |
| 4 | `buddy/understand.py` (modify) | dead-field fix (`_apply` sets `state.understanding`; drop now-redundant `metadata["understanding"]` write); add `ClaudeInterpreter` | `tests/buddy/test_understand.py` |
| 5 | `buddy/think.py` (modify) | dead-field fix (`_apply` sets `state.reasoning`; drop `metadata["reasoning_result"]` write); repoint `_build_request` to read `state.understanding`; add `ClaudeThinker` | `tests/buddy/test_think.py` |
| 6 | `buddy/plan.py` (new content) | `PlanningRequest`, `Planner` protocol, `NullPlanner`, `RuleBasedPlanner`, `Plan` stage | `tests/buddy/test_plan.py` |
| 7 | `buddy/delegate.py` (new content) | `DelegationRequest`, `Delegator` protocol, `NullDelegator`, `MechanicalDelegator`, `Delegate` stage | `tests/buddy/test_delegate.py` |
| 8 | `skills/excel/dedupe.py` (new) | `ExcelDeduplicateSkill` — the real dedupe logic described in Section C | `tests/skills/excel/test_dedupe.py` |
| 9 | `skills/excel/__init__.py` (modify) | re-export `ExcelDeduplicateSkill` | covered by #8's import |
| 10 | `buddy/execute.py` (new content) | `Execute` stage — calls `core.dispatcher.Dispatcher` directly (no separate Dispatch stage, per the decision above) | `tests/buddy/test_execute.py` |
| 11 | `buddy/verify.py` (new content) | `VerificationRequest`, `Verifier` protocol, `NullVerifier`, `SpreadsheetDedupeVerifier` (Section D), `Verify` stage | `tests/buddy/test_verify.py` |
| 12 | `buddy/speak.py` (new content) | `SpeakingRequest`, `Speaker` protocol, `NullSpeaker`, `TemplateSpeaker`, `Speak` stage | `tests/buddy/test_speak.py` |
| 13 | `buddy/brain.py` (new content) | `build_engine(*, llm_provider: LLMProvider \| None = None) -> Engine` factory: constructs `ClaudeProvider` (or an injected fake), `ClaudeInterpreter`/`ClaudeThinker`, `Understand`/`Think`/`Plan`(`RuleBasedPlanner`)/`Delegate`(`MechanicalDelegator`)/`Execute`(real `Dispatcher` wired to a `SkillRegistry` with `ExcelDeduplicateSkill` registered)/`Verify`(`SpreadsheetDedupeVerifier`)/`Speak`(`TemplateSpeaker`), assembles the (now 8-stage) `CognitiveCycle`, returns `Engine(cycle)` | `tests/buddy/test_brain.py` — end-to-end integration test: construct a `BuddyState` with an uploaded temp `.xlsx` (containing known duplicate rows) and `user_message="remove duplicate rows"`, inject a **fake** `LLMProvider` (canned JSON responses standing in for Claude, so the test suite never makes a real network call or costs money), run `Engine.run(state)`, and assert `state.verification_result.passed is True`, that the output file on disk actually has zero duplicate rows (re-checked directly in the test, independent of the app's own Verify stage), and that `state.reply` contains an appropriate message. This is the one test that proves the whole slice end-to-end without hitting the real Anthropic API. |
| 14 | `core/engine.py` (modify, per decision #2 above) | remove `CognitiveCycle.dispatch` field; update `stages()` to the documented 8-stage order; update docstrings accordingly | `tests/core/test_engine.py` — verifies `CognitiveCycle` now has 8 stage slots in the documented order, and `Engine.run()` threads state through all 8 in order with stub stages |
| 15 | `core/router.py` (delete, per decision #1 above) | dead, unreferenced, duplicates `core/dispatcher.py`'s stated responsibility | — |

**Side note, not part of this slice's scope**: `tests/` currently contains
nothing but an empty `__init__.py` — there is zero existing coverage for
any already-implemented file (`observe.py`, `context.py`, `models.py`,
`dispatcher.py`, `registry.py`, `base.py`, or the pre-existing parts of
`understand.py`/`think.py`). This design only adds tests for files this
slice creates or modifies, per the file-by-file plan above; the pre-existing
test gap is noted here as an observation, not silently folded into this
slice's scope.

---

## Open items requiring explicit sign-off before Step 3

1. Adding `state.delegation: tuple[CapabilityRequest, ...] | None = None`
   to `BuddyState` (Section B, Delegate).
2. Removing the `state.metadata["understanding"]` and
   `state.metadata["reasoning_result"]` writes as part of the dead-field
   fix (Section B, closing note).
3. Treating `supported_inputs` as holding capability strings (not only file
   extensions) for `ExcelDeduplicateSkill` (Section C).
4. The `workspace/outputs/{workspace_id}/{original_stem}_deduplicated_{task_id}.xlsx`
   naming convention (Section C), including the three alternatives
   considered and why option 2 was chosen.

Pending approval of the design above (and rulings on the four items just
listed), implementation proceeds file by file per Section E, showing each
file plus its unit test before moving to the next, per the project's
implementation rule.
