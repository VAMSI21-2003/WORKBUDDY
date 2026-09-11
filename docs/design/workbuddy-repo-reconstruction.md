# WorkBuddy — Repository Reconstruction

**Purpose:** An evidence-based reconstruction of what WorkBuddy (`E:\WorkBuddy`)
actually is, built solely from the repository's current files — source
code, tests, and the docs shipped alongside it — as of this inspection.
Where a claim comes from running code or reading a specific file, it is
marked **Repository evidence**. Where it is an interpretation or a
conclusion drawn from that evidence, it is marked **Inference**. No claim
here is drawn from prior conversations about this project.

**Inspection method:** Full directory walk, read of every source file
under `buddy/`, `core/`, `skills/`, `tests/`, `scripts/`; read of `README.md`
and all files under `docs/`; a real run of the test suite in an isolated
virtual environment; and byte-level inspection of the two spreadsheet
artifacts left in the workspace.

---

## 0. Housekeeping finding: there is no git history

**Repository evidence:** `E:\WorkBuddy` has no `.git` directory. `git log`,
`git status`, and `git branch -a` all fail with `fatal: not a git
repository`. The task that produced this document asked for git log/history
as part of the reconstruction — there isn't any to read. The only substitute
for commit history in this repo is a set of hand-written "review record"
documents under `docs/design/` (`review-foundation-tests-batch-c.md`,
`review-items-4-5.md`, `review-items-6-9.md`, `review-items-10-15.md`,
`voice-prompts-review.md`), which read like changelog/PR-review entries and
happen to preserve now-dead file names (`understand.py`, `plan.py`,
`execute.py`, `verify.py`, `speak.py`) that were later renamed. These are
treated below as supporting evidence, not as authoritative history.

---

## 1. Directory structure (actual, not as documented)

**Repository evidence** (full file walk, noise directories `.git`,
`__pycache__`, `.pytest_cache` excluded):

```
WorkBuddy/
├── README.md, LICENSE, .gitignore, pyproject.toml, requirements.txt
├── backend/            .gitkeep only — no code
├── frontend/           .gitkeep only — no code
├── docs/
│   ├── WORKBUDDY_ARCHITECTURE.md
│   ├── BUDDY_PERSONALITY.md
│   ├── BUDDY_SKILLS.md
│   └── design/
│       ├── slice-1-dedupe-design.md
│       ├── multi-step-planner-design.md
│       ├── voice-prompts-review.md
│       ├── review-foundation-tests-batch-c.md
│       ├── review-items-4-5.md, review-items-6-9.md, review-items-10-15.md
│       └── (this file)
├── buddy/               # the "cognitive cycle" — 14 modules
│   ├── __init__.py, state.py, models.py, context.py, llm.py
│   ├── eyes.py, brain.py, think.py, planner.py, delegate.py,
│   │   hands.py, inspector.py, voice.py
│   ├── engine_factory.py
│   └── personality.py   # docstring only, no code
├── core/                 # engine + routing infra
│   ├── __init__.py, engine.py, dispatcher.py
│   ├── workflow.py       # docstring only, no code
│   └── events.py         # docstring only, no code
├── skills/
│   ├── __init__.py, base.py, registry.py
│   ├── dummy/            # docstring only, no code
│   ├── excel/            # __init__.py + dedupe.py — the one real skill
│   ├── pdf/               # docstring only, no code
│   └── word/              # docstring only, no code
├── scripts/
│   └── run_dedupe_manually.py   # manual, real-LLM verification script
├── tests/                # 215 tests, all passing (see §2)
├── workspace/            # runtime data — uploads/outputs/sessions/memory
│   └── outputs/manual-workspace/manual_report_deduplicated_manual-session-task-0.xlsx
└── scratch/uploads/manual_report.xlsx
```

**Inference:** The module names Buddy actually uses — `eyes.py`, `brain.py`,
`think.py`, `planner.py`, `delegate.py`, `hands.py`, `inspector.py`,
`voice.py` — do not match the names in `README.md`'s "Project Structure"
section or `docs/WORKBUDDY_ARCHITECTURE.md` (`observe.py`, `understand.py`,
`plan.py`, `execute.py`, `verify.py`, `speak.py`, plus a `router.py` that
does not exist at all). The `docs/design/slice-1-dedupe-design.md` design
record shows the project was built under the old names first
(`buddy/understand.py`, `buddy/plan.py`, `buddy/execute.py`,
`buddy/verify.py`, `buddy/speak.py`, `buddy/brain.py` as the *factory*, not
the Understand stage) and only later renamed to the current
body-part-themed names, without the top-level README or architecture doc
being updated to match. This is the single most visible symptom of the docs
lagging the code — not the code lagging the docs.

---

## 2. Feature-by-feature classification

Legend: **VERIFIED IMPLEMENTED** (real code, exercised by passing tests, and
in one case by a real manual run with output artifacts on disk) /
**PARTIALLY IMPLEMENTED** (real code exists but a documented behavior around
it is missing or not wired in) / **PLANNED/DOCUMENTED ONLY** (a doc
describes it; the file is empty or absent) / **UNKNOWN/STALE** (evidence is
ambiguous or the doc and code actively disagree with no clear winner).

### 2.1 The eight-stage cognitive cycle (Eyes → Brain → Think → Planner → Delegate → Hands → Inspector → Voice)

**VERIFIED IMPLEMENTED.**
- Every stage is a real class implementing `__call__(state) -> state`,
  wired together by `core/engine.py`'s `CognitiveCycle`/`Engine` and run in
  the fixed order `eyes, brain, think, planner, delegate, hands, inspector,
  voice` (`core/engine.py:64-75`).
- Each stage follows an identical, real pattern: a frozen `*Request`
  dataclass assembled mechanically from `BuddyState`, a `Protocol` seam
  (`Interpreter`, `Thinker`, `PlanningStrategy`, `Delegator`, `Verifier`,
  `Speaker`), a `Null*` honest-default implementation, and at least one real
  implementation — `buddy/eyes.py`, `buddy/brain.py`, `buddy/think.py`,
  `buddy/planner.py`, `buddy/delegate.py`, `buddy/hands.py`,
  `buddy/inspector.py`, `buddy/voice.py`.
- `tests/core/test_engine.py::test_engine_runs_all_eight_stages_in_documented_order`
  and `test_cognitive_cycle_has_exactly_the_eight_documented_stage_slots`
  assert exactly this shape and pass.
- Confirmed by a real, non-mocked run: `scripts/run_dedupe_manually.py` was
  executed at some point against a real LLM provider, producing
  `workspace/outputs/manual-workspace/manual_report_deduplicated_manual-session-task-0.xlsx`
  on disk — the task-id suffix (`manual-session-task-0`) matches exactly
  what `Hands._build_task` (`buddy/hands.py:136`) constructs
  (`f"{state.session_id}-task-{index}"`), which is strong evidence this
  artifact came from a real cycle run, not a hand-crafted file.

### 2.2 "Remove duplicate rows" — the one real vertical slice

**VERIFIED IMPLEMENTED.** This is the only feature built genuinely
end-to-end, LLM calls and all. See §4 for the full trace.
- `skills/excel/dedupe.py`'s `ExcelDeduplicateSkill` reads a real `.xlsx`
  with `pandas.read_excel`, drops exact duplicate rows with
  `drop_duplicates()`, writes a new file under `workspace/outputs/`, and
  never touches the original — `tests/skills/excel/test_dedupe.py` (11
  tests) covers this including "never writes to input path" and "output
  path is under workspace/outputs for this workspace id."
- `buddy/inspector.py`'s `SpreadsheetDedupeVerifier` independently re-opens
  the *output* file with a fresh `pandas.read_excel` call and recomputes
  `duplicated().sum()` itself — it never trusts the skill's own summary or
  metadata. `tests/buddy/test_inspector.py` and the design doc
  (`docs/design/slice-1-dedupe-design.md` §D) both confirm this is
  deliberate, not incidental.
- `tests/buddy/test_engine_factory.py::test_full_slice_removes_duplicate_rows_end_to_end`
  runs the real `build_engine()`-assembled cycle against a real temp
  `.xlsx`, with only the LLM provider faked, and independently re-checks
  the output file in the test itself. Passing.
- Byte-level evidence (this inspection): `scratch/uploads/manual_report.xlsx`
  has 6 rows (1 header + 5 data, 2 of them exact duplicates); the
  corresponding output file has 4 rows (1 header + 3 data) — exactly the
  expected dedup result, read directly from the `.xlsx` XML (no pandas
  needed to confirm this).

### 2.3 LLM-backed Understand ("Brain") and Think

**VERIFIED IMPLEMENTED.**
- `buddy/brain.py`'s `ClaudeInterpreter` and `buddy/think.py`'s
  `ClaudeThinker` build a system+user prompt, call an injected
  `LLMProvider`, and parse strict JSON into `Understanding` / `Reasoning`,
  falling back to a conservative, low-confidence result on any parse or
  network failure (never raising).
- Both are proven never to see file contents — only file names — by
  dedicated tests: `test_claude_interpreter_prompt_contains_only_message_and_file_references`
  (`tests/buddy/test_brain.py`).
- `buddy/llm.py` provides two real, working provider implementations —
  `ClaudeProvider` (Anthropic SDK, default model `"claude-sonnet-5"`) and
  `GroqProvider` (Groq SDK) — both reading their API key exclusively from
  environment variables and never logging prompt/response text.
- 17 tests in `tests/buddy/test_llm.py` pass.

### 2.4 Multi-step planning

**PARTIALLY IMPLEMENTED.** The *decomposition* of a compound request into
an ordered list of steps works and is tested; *executing* that list beyond
the one registered capability does not, because only one skill exists.
- `buddy/planner.py`'s `ClaudePlanner`, reached through `CompositePlanner`
  only when the cheap `RuleBasedPlanner` produces nothing, decomposes a
  request classified `"multi_step_request"` into an ordered
  `tuple[PlanningStep, ...]` with dependency edges
  (`depends_on_step_numbers`). Proven by
  `tests/buddy/test_engine_factory.py::test_full_slice_decomposes_a_compound_request_into_multiple_steps`,
  which produces 3 real steps with a real dependency edge.
- In that same test, `state.execution_result.success is False` and
  `len(state.execution_result.errors) == 3` — none of the three planned
  capabilities (`spreadsheet.column_check`, `spreadsheet.remove_column`,
  `spreadsheet.report`) have a registered skill, so `Hands` records a clean
  per-step error for each rather than crashing. This is the intended
  degrade-gracefully behavior, not a bug, but it means multi-step requests
  today can be *planned* but essentially never *completed*.
- **Documented, known gap** (`docs/design/multi-step-planner-design.md`,
  "Gap found" section): `PlanningStep.dependencies` (`buddy/models.py`) is
  silently dropped at the Delegate boundary — `CapabilityRequest`
  (`buddy/models.py`) has no `dependencies` field, and `Hands` executes
  `state.delegation` in plain list order. Step ordering is therefore
  advisory (steps happen to be listed in a sane order by the planner) and
  not actually enforced anywhere downstream.
- **Documented, known gap** (same doc): `MechanicalDelegator` resolves one
  target file per turn and assigns it to *every* step in a plan — a
  multi-file compound request would silently misbehave. Called out, not
  fixed.

### 2.5 Dispatcher / Skill Registry

**VERIFIED IMPLEMENTED**, matches the architecture doc closely.
- `core/dispatcher.py`'s `Dispatcher` locates skills by declared capability
  (`locate_by_capability`), filters to those whose `can_handle(task)`
  returns true, applies a swappable `SelectionStrategy` (default:
  `select_first_available`), and raises typed `NoCapableSkillError` /
  `SkillUnavailableError` rather than guessing.
- Never touches `BuddyState`, never calls `execute()` — confirmed by
  reading the file; nothing in it imports `buddy.state`.
- `skills/registry.py`'s `SkillRegistry` is a plain in-memory dict keyed by
  skill name, with explicit registration only (no filesystem/plugin
  discovery, matching the doc's stated scope).
- 13 tests in `tests/core/test_dispatcher.py`, 10 in
  `tests/core/test_registry.py`, all pass.

### 2.6 Skill self-check (`Skill.verify()`)

**PARTIALLY IMPLEMENTED.** The interface and one real implementation exist
and are unit-tested in isolation, but the pipeline never calls it.
- `skills/base.py`'s abstract `Skill.verify(task, result) -> bool` and
  `skills/excel/dedupe.py`'s concrete implementation (checks
  status==SUCCESS, exactly one output, output exists with nonzero size) are
  real and tested directly: `tests/skills/excel/test_dedupe.py::test_verify_true_for_well_formed_success`
  and two failure-path tests.
- **Finding:** `buddy/hands.py` — the only stage that calls into a skill —
  never calls `execution_request.skill.verify(...)`. A repo-wide search for
  `.verify(` shows the only call sites are inside the test files, not
  inside `buddy/hands.py` or anywhere in the runtime pipeline. Step 6 of
  the Skill Lifecycle in `docs/BUDDY_SKILLS.md` ("Output Validation") is
  therefore specified and even implemented at the skill level, but not
  actually exercised as part of a real turn — only `buddy/inspector.py`'s
  independent `Inspector` stage (§2.2) runs, and it does its own read of
  the file rather than consulting `skill.verify()` at all.

### 2.7 Permission tiers / confirmation before write

**PARTIALLY IMPLEMENTED — enforcement layer does not exist.**
- `skills/base.py`'s `PermissionTier` enum (`READ`, `WRITE`,
  `EXTERNAL_EFFECT`) is real and every `Skill` subclass declares one
  (`ExcelDeduplicateSkill.permission_tier == PermissionTier.WRITE`).
- **Finding:** `buddy/hands.py:_build_task` hard-codes
  `permission=PermissionTier.WRITE` for *every* task it builds
  (`buddy/hands.py:142`), regardless of what capability was requested or
  what tier the eventually-selected skill actually declares. There is no
  code anywhere in `buddy/`, `core/`, or `skills/` that reads a skill's
  `permission_tier` and gates execution on it, and no code that asks the
  user for confirmation before a Write- or External-Effect-tier action —
  the "Yes, always, before execution" rule in `docs/BUDDY_SKILLS.md` §13
  has no corresponding check anywhere in the runtime path. The permission
  *model* (the enum, the declared tier per skill) is real; the permission
  *enforcement* the docs describe is not.

### 2.8 Buddy's personality (Friend/Focus modes)

**VERIFIED IMPLEMENTED — but not where the docs say it lives, and not the
default.**
- `buddy/personality.py`, the file `README.md` and
  `docs/WORKBUDDY_ARCHITECTURE.md` both name as owning "Buddy's tone and
  conversational style," is a single docstring line with no code
  (`buddy/personality.py:1`).
- The actual, working implementation lives in `buddy/voice.py`:
  `FRIEND_MODE_PROMPT` / `FOCUS_MODE_PROMPT` (two full system prompts) and
  `PersonalityVoice`, a real `Speaker` backed by an `LLMProvider`, selected
  by `Context.mode` (`"friend"` or `"focus"`, settable via
  `Context.set_mode`). 16 tests in `tests/buddy/test_voice.py` cover both
  modes, including that `TemplateSpeaker` ignores mode entirely and that
  `PersonalityVoice` falls back to `TemplateSpeaker`'s deterministic output
  on any LLM failure or empty reply.
- **Finding:** `buddy/engine_factory.py::build_engine()` defaults `voice`'s
  speaker to `TemplateSpeaker()`, not `PersonalityVoice` — so out of the
  box, the assembled engine gives flat, templated replies with **no**
  personality, no jokes, no warmth. `PersonalityVoice` must be explicitly
  passed in (as `scripts/run_dedupe_manually.py` does). The friend/focus
  personality described at length in `docs/BUDDY_PERSONALITY.md` is real
  and tested, but opt-in, not the shipped default.
- **Also notable:** `docs/BUDDY_PERSONALITY.md` (373 lines, the dedicated
  product spec for tone) never mentions "Friend mode" or "Focus mode" by
  name — this is a case of the *code* being ahead of that particular doc,
  not behind it.

### 2.9 Core Engine error boundary

**UNKNOWN/STALE — doc and code actively disagree.**
- `docs/WORKBUDDY_ARCHITECTURE.md` §17 states: "if a stage in the cognitive
  cycle itself cannot proceed... the Core Engine's error boundary catches
  this, the cycle stops advancing further stages, and control passes
  directly to Speak to deliver an honest, plain explanation to the user."
- **Repository evidence:** `core/engine.py::Engine.run` has no such
  boundary. On a stage exception it logs which stage failed and then
  `raise`s — it does not catch, does not short-circuit to Voice, and does
  not produce any user-facing reply.
  `tests/core/test_engine.py::test_engine_propagates_stage_exceptions`
  confirms this is the *intended*, tested behavior of the current code
  (the test name says "propagates," not "recovers"). The doc describes a
  graceful degradation path that does not exist in the code as written.

### 2.10 Event System

**PLANNED/DOCUMENTED ONLY.**
- `docs/WORKBUDDY_ARCHITECTURE.md` §13 describes four categories of events
  (lifecycle, task, error, progress) meant to decouple Execute's progress
  reporting from Speak.
- `core/events.py` is a one-line docstring with zero code — no event
  classes, no emitter, no subscriber. No file in the repository imports
  from `core.events`. Long-running task progress (§16 of the architecture
  doc) is likewise undocumented in code: `BuddyState.active_task` is set
  and cleared around a skill call in `buddy/hands.py`, but nothing reads it
  to report progress to a user mid-task.

### 2.11 Workflow definition

**PLANNED/DOCUMENTED ONLY** (functionally superseded, not filled in).
- `docs/WORKBUDDY_ARCHITECTURE.md` §12 describes `core/workflow.py` as
  owning "the shape of the cognitive cycle."
- `core/workflow.py` is a one-line docstring, no code. In practice, the
  cycle's shape is defined by `core/engine.py`'s `CognitiveCycle` dataclass
  and its `stages()` method instead — a real, working substitute that
  simply isn't the file the docs point to.

### 2.12 PDF and Word skills

**PLANNED/DOCUMENTED ONLY.**
- `skills/pdf/__init__.py`: `"""PDF — skills for working with PDF
  documents. Not implemented in Sprint 1."""` — one line, no code.
- `skills/word/__init__.py`: `"""Word — skills for working with Word
  documents. Not implemented in Sprint 1."""` — one line, no code.
- `docs/BUDDY_SKILLS.md` §16 lists PDF, Word, PowerPoint, Email, SQL, OCR,
  Chart, and Calendar Specialists as "Examples of Skills." None of the
  seven beyond Excel has any code anywhere in the repository. Only Excel
  (and only one capability of it — deduplication) is real.

### 2.13 "Dummy" reference skill

**PLANNED/DOCUMENTED ONLY, and effectively unused.**
- `skills/dummy/__init__.py` is a docstring only: `"""Dummy — a minimal
  reference skill used to validate the skill interface."""` — no class, no
  code.
- `tests/skills/test_base.py` does exercise the `Skill` ABC's abstractness
  rules, but with private test-local classes (`_MinimalSkill`,
  `_IncompleteSkill`) defined inline in the test file — it does not import
  anything from `skills.dummy`. The package README promises exists as
  dead, unreferenced scaffolding.

### 2.14 Backend (API layer) and Frontend

**PLANNED/DOCUMENTED ONLY, and honestly labeled as such by the repo itself.**
- `backend/` and `frontend/` each contain only a `.gitkeep`.
- `.gitignore` labels them explicitly: `# Frontend (reserved for a future
  sprint)` and `# Backend (reserved for a future sprint)`.
- `requirements.txt`'s own header comment: "Runtime dependencies for
  still-unbuilt capabilities (PDF, Word, FastAPI, React) will be added in
  the sprints that introduce them" — no `fastapi`, no npm/`package.json`,
  no React anywhere in the repo.
- Consequently there is **no HTTP entry point, no auth, and no deployment
  path of any kind** — see §5.

### 2.15 Sessions / persistence

**PARTIALLY IMPLEMENTED — the data model exists; persistence to disk does
not.**
- `buddy/context.py`'s `Context` (conversation history, execution history,
  active task/file/document/sheet, per-session `mode`) is a real, well
  tested in-memory model (33 tests in `tests/buddy/test_context.py`).
- `workspace/sessions/` exists only as an empty directory with a
  `.gitkeep`. No code anywhere reads from or writes to it. "Sessions are
  persisted under `workspace/sessions/`" (`docs/WORKBUDDY_ARCHITECTURE.md`
  §6, §15) describes behavior that is not implemented — a session today
  lives only as long as the Python process holding its `BuddyState`.
- `workspace/memory/` is likewise an empty directory with no reader/writer
  in the codebase — "longer-lived context that outlives a single session"
  is aspirational only.

---

## 3. What WorkBuddy actually is, in plain language

**Inference**, drawn from the combination of the architecture docs and what
is genuinely built:

WorkBuddy is an early-stage attempt at an "AI coworker" for office-document
busywork — the kind of thing you'd otherwise hand to an assistant: "clean
up this spreadsheet," "get this ready for a meeting." **Buddy** is the
single conversational persona the user is meant to interact with — never a
list of tools or menus, always one voice that either does the work, asks a
clarifying question, or explains honestly why it can't.

Architecturally, Buddy is not one big prompt — it's a fixed pipeline of
eight small, single-purpose stages that all read and write one shared
object, `BuddyState`: **see** the raw input (Eyes), **understand** what the
user wants (Brain, LLM-backed), **think** through how to approach it
(Think, LLM-backed), **turn that into concrete steps** (Planner — cheap
rule-based mapping first, LLM-backed decomposition as a fallback for
compound requests), **turn steps into capability requests** (Delegate),
**find and run the right Skill** (Hands, via a Dispatcher and Skill
Registry), **independently double-check the result** rather than trust the
Skill's own self-report (Inspector), and finally **phrase the outcome for
the user** (Voice, optionally in a warm "friend" personality or a terse
"focus" mode).

The actual work — reading a spreadsheet, editing a PDF, drafting an email —
is deliberately never done by Buddy's core reasoning itself. It's delegated
to **Skills**: small, stateless specialists that receive a well-defined
task and return a well-defined result, and are explicitly forbidden from
talking to the user directly. Today there is exactly one working Skill:
removing duplicate rows from an Excel spreadsheet.

**The intended user experience**, per the docs, is: upload a file, say what
you want in plain language, and Buddy either does it (with an honest,
independently-verified confirmation and a short, personality-appropriate
reply) or tells you clearly why it can't — never a feature menu, never a
raw error, never silence. **What's actually buildable and demonstrable
today** is a narrower, real slice of exactly that experience for one
specific request — removing duplicate rows from an uploaded `.xlsx` — run
entirely from Python, with no web UI, no server, and no persistence between
process runs.

---

## 4. End-to-end trace: "upload a spreadsheet, remove duplicate rows"

This walks the one request this codebase can genuinely serve today, staged
by staged, citing exactly what runs and what's mocked, using
`scripts/run_dedupe_manually.py` and
`tests/buddy/test_engine_factory.py::test_full_slice_removes_duplicate_rows_end_to_end`
as the two points of real evidence (one a real manual run against a real
LLM, one an automated test with a faked LLM but a fully real everything
else).

| Step | What happens | Code | Real / mocked / missing |
|---|---|---|---|
| **0. Entry point** | There is no HTTP upload endpoint or UI. A caller constructs `BuddyState.new(session_id, workspace_id)` directly, sets `state.user_message`, and calls `state.context.add_uploaded_file(path)` / `switch_active_file(path)` in Python. | `buddy/state.py:122`, `buddy/context.py:192,160` | **Real** as a Python API; **missing** as a product surface — no backend/frontend exists (§2.14). |
| **1. Eyes (Observe)** | Mechanically records session id, workspace id, the raw message, each uploaded file, and any metadata as plain-string observations. No interpretation. | `buddy/eyes.py:23-42` | **Real.** No AI model involved by design. |
| **2. Brain (Understand)** | `ClaudeInterpreter` builds a prompt containing only the message text and file *names* (never file contents — enforced by a dedicated test), sends it to the configured `LLMProvider`, and parses the JSON reply into `Understanding(intent="remove_duplicate_rows", confidence=..., ...)`. | `buddy/brain.py:216-331` | **Real** LLM call in the manual script (Groq or Claude); **faked** in the automated test via `_QueuedLLMProvider` returning canned JSON — the stage logic itself is identical either way. |
| **3. Think** | `ClaudeThinker` takes the classified intent (never raw file content) and asks the model to reason about objective, assumptions, risks, and whether clarification is needed. Returns `Reasoning(clarification_required=False, confidence=0.9, ...)` for a clear dedupe request. | `buddy/think.py:248-377` | Same real/faked split as Brain. |
| **4. Planner** | `CompositePlanner` tries `RuleBasedPlanner` first: `"remove_duplicate_rows"` is in its closed intent→capability map, so it mechanically emits one `PlanningStep(capability="spreadsheet.deduplicate", ...)` with **no LLM call at all** — `ClaudePlanner` (the LLM-backed fallback) is never reached for this specific request. | `buddy/planner.py:84-127, 130-168` | **Real**, and deliberately non-LLM for this path. |
| **5. Delegate** | `MechanicalDelegator` turns the one `PlanningStep` into one `CapabilityRequest(capability="spreadsheet.deduplicate", target=<the .xlsx path>, parameters={})`, resolving `target` from `Context.active_file`. Never references a skill by name. | `buddy/delegate.py:68-108` | **Real.** |
| **6. Hands (Execute)** | For the one `CapabilityRequest`, builds a `SkillTask` (permission hard-coded to `WRITE` — see §2.7), calls `Dispatcher.dispatch(task, input_type="spreadsheet.deduplicate")`. | `buddy/hands.py:45-119` | **Real.** |
| **6a. Dispatcher** | Looks up `SkillRegistry.find_by_capability("spreadsheet.deduplicate")`, finds `ExcelDeduplicateSkill` (the only skill registered by `build_engine()`), confirms `can_handle(task)` is true, and returns an `ExecutionRequest` pairing task+skill. | `core/dispatcher.py:113-158`, `buddy/engine_factory.py:82-84` | **Real.** |
| **6b. Skill execution** | `Hands` calls `skill.execute(task)` itself (the Dispatcher never does). `ExcelDeduplicateSkill.execute` opens the upload read-only with `pandas.read_excel`, calls `drop_duplicates()`, and writes the result to a brand-new path (`workspace/outputs/{workspace_id}/{stem}_deduplicated_{task_id}.xlsx`) that can never collide with or overwrite the original. | `skills/excel/dedupe.py:65-123` | **Fully real.** Confirmed on disk: the fixture `.xlsx` (6 rows) and the produced output `.xlsx` (4 rows) both exist and were verified byte-for-byte in this inspection (§2.2). |
| **6c. `Skill.verify()`** | The skill's own self-check method exists and is unit-tested, but **is never called** by `Hands` during a real run — see §2.6. | `skills/excel/dedupe.py:125-147` | **Implemented but not wired in** — a real gap, not a mock. |
| **7. Inspector (Verify)** | `SpreadsheetDedupeVerifier` takes only the output path Hands recorded, opens *that file* fresh with its own `pandas.read_excel` call, and recomputes `duplicated().sum()` independently — it never reads the skill's `status`, `summary`, or `metadata`. Passes iff the recount is zero. | `buddy/inspector.py:74-124` | **Real**, and genuinely independent — this is the strongest-built part of the whole pipeline. |
| **8. Voice (Speak)** | Composes the final reply. `build_engine()`'s **default** is `TemplateSpeaker` — a deterministic, no-LLM string like `"Done — output has 3 row(s), 0 duplicate row(s) remaining. Saved as \`manual_report_deduplicated_manual-session-task-0.xlsx\`."` `scripts/run_dedupe_manually.py` explicitly swaps in `PersonalityVoice`, which makes one more real LLM call to phrase the same facts warmly (friend mode) or tersely (focus mode), falling back to `TemplateSpeaker`'s output if that call fails. | `buddy/voice.py:77-179` (template), `332-452` (personality) | **Real** either way; personality is opt-in, not default (§2.8). |
| **9. Response returned** | `state.reply` holds the final text. Nothing persists it — no session file is written under `workspace/sessions/`, and the caller (script or test) is responsible for whatever happens to `state.reply` next. | — | **Missing**: session persistence (§2.15) described in the architecture doc does not exist. |

**Net assessment of this one flow:** every step from Eyes through Voice
that the docs claim exists, genuinely exists and genuinely runs, with two
explicit soft spots — the skill's own `verify()` is dead code in the actual
run path, and the personality voice is opt-in rather than the shipped
default. Nothing in this specific trace is a stub or a lie; it is simply
the *only* trace this deep that currently works, because it is the only
capability (`spreadsheet.deduplicate`) with a registered Skill at all.

---

## 5. Capability matrix

| Capability | Status | Evidence |
|---|---|---|
| **File type support — Excel `.xlsx`/`.xls` (deduplicate)** | VERIFIED IMPLEMENTED | `skills/excel/dedupe.py`; 11 passing tests in `tests/skills/excel/test_dedupe.py`; real output artifact on disk (§2.2) |
| **File type support — Excel (any other operation: clean, summarize, chart, etc.)** | PLANNED/DOCUMENTED ONLY | `docs/BUDDY_SKILLS.md` §16 example row ("Clean up this sheet and summarize totals by region") has no corresponding code; only `deduplicate` exists |
| **File type support — PDF** | PLANNED/DOCUMENTED ONLY | `skills/pdf/__init__.py` is a one-line docstring, "Not implemented in Sprint 1" |
| **File type support — Word** | PLANNED/DOCUMENTED ONLY | `skills/word/__init__.py` is a one-line docstring, "Not implemented in Sprint 1" |
| **File type support — PowerPoint, Email, SQL, OCR, Chart, Calendar** | PLANNED/DOCUMENTED ONLY | Listed only in `docs/BUDDY_SKILLS.md` §16; no `skills/` package exists for any of them at all |
| **Personality — Friend mode** | VERIFIED IMPLEMENTED, opt-in | `buddy/voice.py` `FRIEND_MODE_PROMPT` + `PersonalityVoice`; 16 tests in `tests/buddy/test_voice.py`; not the default speaker in `build_engine()` |
| **Personality — Focus mode** | VERIFIED IMPLEMENTED, opt-in | Same file, `FOCUS_MODE_PROMPT`; same caveat |
| **Personality module (`buddy/personality.py`)** | PLANNED/DOCUMENTED ONLY | File is a one-line docstring; actual implementation lives in `buddy/voice.py` instead |
| **Single-step planning (known intent → one capability)** | VERIFIED IMPLEMENTED | `buddy/planner.py`'s `RuleBasedPlanner`; covered by `tests/buddy/test_planner.py` (25 tests) |
| **Multi-step planning (decomposition)** | PARTIALLY IMPLEMENTED | `buddy/planner.py`'s `ClaudePlanner`; produces real ordered steps with dependency edges, proven by `test_full_slice_decomposes_a_compound_request_into_multiple_steps` |
| **Multi-step execution (actually running >1 skill in one turn)** | PARTIALLY IMPLEMENTED / effectively blocked | Same test: 3-step plan → `execution_result.success is False`, 3/3 steps fail — only one capability (`spreadsheet.deduplicate`) has a registered skill; step ordering/`dependencies` are dropped at Delegate (§2.4) |
| **Skill self-verification (`Skill.verify()`)** | PARTIALLY IMPLEMENTED | Interface + real impl exist and are unit-tested; never called by `Hands` in the actual pipeline (§2.6) |
| **Independent outcome verification (Inspector)** | VERIFIED IMPLEMENTED | `buddy/inspector.py`'s `SpreadsheetDedupeVerifier`; genuinely re-reads the output file rather than trusting the skill (§2.2, §4 step 7) |
| **Permission tiers (declared)** | VERIFIED IMPLEMENTED | `skills/base.py`'s `PermissionTier` enum; every skill declares one |
| **Permission enforcement / user confirmation before Write/External-Effect actions** | PLANNED/DOCUMENTED ONLY | No code anywhere checks a skill's `permission_tier` before invoking it; `Hands` hard-codes `WRITE` for every task regardless (§2.7) |
| **Event system (lifecycle/task/error/progress events)** | PLANNED/DOCUMENTED ONLY | `core/events.py` is a one-line docstring; zero importers anywhere |
| **Workflow definition (`core/workflow.py`)** | PLANNED/DOCUMENTED ONLY (superseded) | One-line docstring; `core/engine.py`'s `CognitiveCycle` fills this role in practice |
| **Stage-level error recovery / graceful failure to the user** | PLANNED/DOCUMENTED ONLY (doc contradicts code) | `docs/WORKBUDDY_ARCHITECTURE.md` §17 vs. `core/engine.py::Engine.run`, which re-raises rather than recovering (§2.9) |
| **Session persistence (`workspace/sessions/`)** | PLANNED/DOCUMENTED ONLY | Directory exists (`.gitkeep`); zero readers/writers in the codebase |
| **Long-lived memory (`workspace/memory/`)** | PLANNED/DOCUMENTED ONLY | Directory exists (`.gitkeep`); zero readers/writers in the codebase |
| **Backend / API (FastAPI or otherwise)** | PLANNED/DOCUMENTED ONLY | `backend/` is empty; no `fastapi` in `requirements.txt`; `.gitignore` labels it "reserved for a future sprint" |
| **Frontend (React or otherwise)** | PLANNED/DOCUMENTED ONLY | `frontend/` is empty; no `package.json`, no JS/TS anywhere in the repo |
| **Auth** | PLANNED/DOCUMENTED ONLY | No auth code exists anywhere; there is no API surface for it to protect yet |
| **Deployment (CI/CD, Docker, etc.)** | PLANNED/DOCUMENTED ONLY | No `Dockerfile`, no `*.yml`/`*.yaml`, no `.github/` directory anywhere in the repo |
| **LLM integration — Claude** | VERIFIED IMPLEMENTED | `buddy/llm.py`'s `ClaudeProvider`, wraps `anthropic` SDK, default model `"claude-sonnet-5"` |
| **LLM integration — Groq** | VERIFIED IMPLEMENTED | `buddy/llm.py`'s `GroqProvider`, wraps `groq` SDK, OpenAI-compatible chat format handled correctly |
| **Test suite** | VERIFIED IMPLEMENTED | 215 tests collected across 18 files under `tests/`; **215 passed, 0 failed** in a fresh, isolated run performed for this inspection (`pytest -q`, 5.37s) |

---

## 6. Final summary

**Where the project actually stands:** WorkBuddy is a small but genuinely
well-built Python core — not a scaffold of empty files, and not vaporware.
The eight-stage cognitive pipeline is real, cleanly decoupled via
`Protocol` seams and a shared `BuddyState`, and is proven by 215 passing
tests plus one artifact on disk from a real, non-mocked run against a real
LLM. Exactly one end-to-end capability exists — removing duplicate rows
from an uploaded Excel file — and it works honestly, including an
independent, skeptical verification step that re-reads the output file
rather than trusting the skill that produced it. Everything else the
product docs describe (other file types, other Excel operations,
multi-step *execution*, permission enforcement, session persistence, the
event system, the backend, the frontend, auth, deployment) is either a
thin, honest stub (a one-line docstring, explicitly labeled "not
implemented" or "reserved for a future sprint") or entirely absent from the
filesystem. This is not a system pretending to be more finished than it
is — the repository is, if anything, unusually candid about its own gaps,
both in code comments and in its own design-review documents.

**The single biggest gap between what the docs/prior plans claim and what's
actually built:** it is not any one missing file — it's that WorkBuddy is
still a **single-capability command-line pipeline with no product surface
at all**, while `docs/WORKBUDDY_ARCHITECTURE.md`, `docs/BUDDY_SKILLS.md`,
and `docs/BUDDY_PERSONALITY.md` are written as a complete, multi-Skill,
multi-user, backend-and-frontend product specification. There is no way
for an actual end user to "upload a spreadsheet and say remove duplicate
rows" to WorkBuddy today — that request only exists as a Python function
call in a test file or a manual verification script. Every layer above
`core/` (backend, frontend, auth, deployment) and every capability besides
Excel deduplication is documented in detail but has zero corresponding
code. Anyone picking this project up should treat the architecture docs as
a target design, not a status report, and should expect the actual, provable
surface area of the system to be exactly what's in §2.2 and §4 — one
skill, one file type, one operation, running only from Python — until
proven otherwise by new code and new tests, the same standard this
document was held to.
