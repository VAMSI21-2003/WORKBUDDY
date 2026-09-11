# WorkBuddy Architecture

**Status:** v1.1 — Sprint 1 foundation
**Owner:** WorkBuddy Architecture
**Audience:** Engineers joining the WorkBuddy project
**Revision v1.1:** Introduces the Dispatcher as a first-class routing
component sitting between Delegate and Execute. Affected sections: 3, 4,
5, 8, 9, 10, the new Section 11, and 14, 16, 18, 19, 20, 21.
**Scope:** This document describes the complete system architecture —
components, data flow, and lifecycles. It is an architecture
specification, not an implementation guide. It contains no code and no
language-specific detail. For Buddy's tone and behavior, see
`BUDDY_PERSONALITY.md`. For the Skill contract, see `BUDDY_SKILLS.md`.
This document is the map that connects both to the rest of the system.

---

## 1. Vision

WorkBuddy is a system built around one idea: a user should be able to
describe work in plain language and have it done, the way they'd hand it
to a capable coworker — not navigate a feature set to do it themselves.
Buddy is the coworker; everything documented here is the system that makes
Buddy capable of actually being one, reliably, at software scale.

## 2. Product Philosophy

Users should never have to think about features — only about their work.
Architecturally, this means WorkBuddy is built **intent-first**, not
**feature-first**. A feature-first system exposes its capabilities and
asks the user to compose them. WorkBuddy instead exposes a single
conversational surface and pushes the complexity of "which capability, in
what order, with what parameters" into the system itself — specifically
into the cognitive pipeline described in Section 4. Every architectural
choice in this document exists in service of keeping that complexity off
the user.

## 3. High-Level System Architecture

WorkBuddy is organized in layers, each with a narrow responsibility. A
request enters at the top and flows down; a response is produced and
flows back up.

```
+-----------------------------------------------------------+
|  Frontend                                    (future sprint) |
|  Where the user talks to Buddy                                |
+---------------------------+---------------------------------+
                            |
+---------------------------v---------------------------------+
|  Backend / API                               (future sprint) |
|  Receives requests, manages auth, exposes Buddy over HTTP      |
+---------------------------+---------------------------------+
                            |
+---------------------------v---------------------------------+
|  Core Engine                                  (core/)          |
|  Runs the cognitive cycle end-to-end                            |
+---------------------------+---------------------------------+
                            |
+---------------------------v---------------------------------+
|  Buddy                                        (buddy/)         |
|  Observe -> Understand -> Think -> Plan -> Delegate ->            |
|  Execute -> Verify -> Speak, all over BuddyState                   |
+---------------------------+---------------------------------+
                            |
+---------------------------v---------------------------------+
|  Dispatcher                          (core/dispatcher.py)      |
|  Routes a delegated task to a capable Skill — never runs it      |
+---------------------------+---------------------------------+
                            |
+---------------------------v---------------------------------+
|  Skills                                       (skills/)        |
|  Specialists that perform domain-specific work                  |
+---------------------------+---------------------------------+
                            |
+---------------------------v---------------------------------+
|  Workspace                                    (workspace/)     |
|  Files, sessions, and memory — the durable substrate             |
+-----------------------------------------------------------+
```

Sprint 1 implements the middle layers only (`core/` — including the
Dispatcher — `buddy/`, `skills/`, and the `workspace/` structure). The
Backend and Frontend layers are deliberately out of scope until Buddy's
core is proven. The Dispatcher is drawn as its own layer here because of
where it sits in the flow of control — between Buddy's Delegate and
Execute stages — even though it physically lives in `core/`, not
`buddy/`; see Section 11 for the full picture.

## 4. Cognitive Pipeline

Every unit of work Buddy performs moves through eight stages, always in
the same order, always over the same shared object (`BuddyState`, see
Section 5):

```
Observe -> Understand -> Think -> Plan -> Delegate -> Execute -> Verify -> Speak
   ^                                                                          |
   +--------------------------- next user message ---------------------------+
```

**Observe** (`buddy/observe.py`) — Takes in the raw input for this turn:
the user's message, any newly uploaded files, and relevant workspace
state. Observe does not interpret anything; it only records what came in,
writing to `BuddyState.observations` and `user_message`.

**Understand** (`buddy/understand.py`) — Interprets the observations into
a concrete statement of user intent, written to `BuddyState.intent`, along
with a `confidence` score. This is where "make this ready for tomorrow's
meeting" becomes a structured understanding of what "ready" means in this
context.

**Think** (`buddy/think.py`) — Reasons about the intent to determine what
actually needs to happen — weighing ambiguity, missing information, and
constraints. Think's reasoning trace is recorded in
`BuddyState.reasoning`, so the logic behind a decision is inspectable
after the fact.

**Plan** (`buddy/plan.py`) — Converts Think's conclusion into a concrete,
ordered sequence of steps, written to `BuddyState.plan`. Plan is where
"prepare this for a meeting" becomes an explicit list: clean the data,
summarize it, build a chart, assemble a one-pager.

**Delegate** (`buddy/delegate.py`) — Converts plan steps into task records,
written to `BuddyState.tasks`, each describing the capability a step
requires. As of v1.1, Delegate does not itself resolve a task to a
specific Skill instance — it hands that resolution to the Dispatcher
(Section 11), which is the routing authority against the Skill contract
in `BUDDY_SKILLS.md`.

**Execute** (`buddy/execute.py`) — Asks the Dispatcher to select a capable
Skill for each delegated task, then invokes that Skill itself, tracks the
one currently running in `BuddyState.active_task`, and collects results
into `BuddyState.outputs`. Execute is the only stage that crosses out of
Buddy's core into a Skill — and it only ever does so through a
Dispatcher-prepared request, never by looking up a Skill on its own.

**Verify** (`buddy/verify.py`) — Independently checks the collected
outputs against the *original* intent captured back in Understand — not
against what the Skills merely reported. This is the check described in
`BUDDY_SKILLS.md` Section 9: a Skill's self-reported success is necessary
but never sufficient.

**Speak** (`buddy/speak.py`) — Converts the verified outcome into the
message the user actually sees, written to `BuddyState.reply`, following
the tone rules in `BUDDY_PERSONALITY.md`. Speak is the only stage that
produces user-facing language.

## 5. BuddyState

`BuddyState` (`buddy/state.py`) is the single object every stage reads
from and writes to. There is no other channel for passing information
between stages.

This is a deliberate architectural choice, not a convenience:

- **One source of truth.** At any point in the cycle, the complete picture
  of what Buddy knows and is doing is in one place — never scattered
  across function arguments or hidden module state.
- **Inspectability.** Because every stage's output lives in `BuddyState`,
  the entire reasoning path for a piece of work — from raw observation to
  final reply — can be inspected after the fact for debugging or auditing.
- **Decoupling.** Stages don't call each other directly; they each operate
  on `BuddyState` in sequence. A stage can be modified, tested, or replaced
  without changing the signature every other stage depends on.
- **Honest verification.** Because Understand's `intent` is preserved
  alongside Execute's `outputs` in the same object, Verify can compare the
  two directly rather than trusting a summary passed down through
  intermediate stages.

`BuddyState` distinguishes session-level fields (`session_id`,
`workspace_id`, `uploaded_files`, `short_memory`, `metadata`) from
per-cycle fields (`observations`, `intent`, `reasoning`, `plan`, `tasks`,
`outputs`, `reply`, and others), which is why it exposes a
`reset_transient()` operation — a session persists across many cycles, but
each cycle starts from a clean per-turn slate.

**A deliberate exception:** the Dispatcher (Section 11) never reads from
or writes to `BuddyState`, directly or otherwise. It operates purely on
the `SkillTask` it's handed and returns an `ExecutionRequest` — a
stateless routing lookup, not a stage. Only Delegate and Execute touch
`BuddyState.tasks`, `active_task`, and `outputs`.

## 6. Sessions

A session is a continuous interaction between one user and Buddy, scoped
to one workspace and identified by `session_id`. A session is the
container for everything that should persist across multiple user
messages: recent conversational history (`short_memory`), auxiliary
context (`metadata`), and awareness of previously uploaded files.

Each user message triggers one full pass through the cognitive pipeline
using the same `BuddyState` instance for that session — transient fields
are reset beforehand, session-level fields carry forward. Sessions are
persisted under `workspace/sessions/`, so a session can be resumed rather
than reconstructed from scratch.

A session does not span workspaces — if a user starts working in a
different workspace, that's a different session.

## 7. Workspace

The workspace is the sandboxed boundary within which all of a user's data
and all Skill activity is contained. Every workspace is identified by
`workspace_id` and maps to a fixed structure:

```
workspace/
├── uploads/    Original files the user has provided — never modified in place
├── outputs/    Files produced by Skills during Execute
├── sessions/   Persisted session state, keyed by session_id
└── memory/     Longer-lived context that outlives a single session
```

The workspace boundary is also the security boundary described in
`BUDDY_SKILLS.md` Section 12: a Skill is only ever given references scoped
to one workspace, and never has a path to reach outside it.

## 8. Skills

Skills are the Specialists Buddy delegates domain-specific work to —
Excel, PDF, Word, Email, and so on. Buddy orchestrates; Skills execute.
The full contract governing Skill behavior, lifecycle, inputs, outputs,
permissions, and error handling is defined in `BUDDY_SKILLS.md` and is not
repeated here. Architecturally, what matters in this document is where
Skills sit: they are invoked exclusively by Execute, selected on Execute's
behalf by the Dispatcher (Section 11) using the Registry (Section 10), and
never have a path back into `BuddyState` except through the result they
return.

## 9. Core Engine

The Core Engine (`core/engine.py`) is what actually runs the cognitive
cycle end-to-end. It is responsible for:

- Loading or initializing the `BuddyState` for a session.
- Running each stage of the pipeline in order.
- Enforcing the top-level error boundary — a failure inside one stage
  should not crash the engine (see Section 17).
- Coordinating with Workflow (Section 12), the Dispatcher (Section 11),
  and the Event System (Section 13) as the cycle runs.

The Core Engine is deliberately thin. It does not contain reasoning logic
(that belongs to `buddy/`) or domain logic (that belongs to `skills/`) —
it is the thing that turns the crank.

## 10. Registry

The Skill Registry (`skills/registry.py`) is where every Skill declares
itself when WorkBuddy starts: its identity, its capabilities, and its
permission tier (see `BUDDY_SKILLS.md` Section 13). The Dispatcher
(Section 11) consults the Registry to find which Skill — or Skills — can
satisfy a given task; nothing else queries the Registry directly.

The Registry is what makes Skills additive: a new Specialist becomes
available to Buddy by registering itself, not by anyone editing Buddy's
reasoning code.

## 11. Dispatcher

**Why it exists.** Delegate decides *what* capability a plan step
requires. Something else has to decide *which registered Skill instance*
actually satisfies that capability, confirm it's genuinely able to handle
this specific task, and hand it off for execution — without that lookup
logic leaking into Delegate's reasoning or into Execute's invocation code.
The Dispatcher (`core/dispatcher.py`) is that something else: a narrow,
single-purpose routing component sitting between Delegate's output and
Execute's work. It is not a cognitive stage — it is a Core Engine
component that Execute calls at the moment it's ready to act on a
delegated task.

**Responsibilities.** The Dispatcher:

- Locates every registered Skill that declares the capability a task
  requires, via the Registry.
- Validates that at least one located Skill is actually available and
  capable of the specific task (`Skill.can_handle()`), not just capable of
  the general category of work.
- Selects the correct Skill among multiple capable candidates, using a
  swappable selection strategy (see Section 18).
- Raises clear, typed errors when no capable Skill exists or an explicitly
  requested Skill is unavailable.
- Prepares an `ExecutionRequest` — a task paired with the Skill selected
  to run it — for Execute to carry out.

**What it never does.** The Dispatcher **never executes work** — it does
not call a Skill's `execute()` method. It **never verifies work** —
checking a result against the user's intent belongs entirely to Verify
(Section 4). It **never retries** — retry policy is owned by Execute, per
`BUDDY_SKILLS.md` Section 11, not by the routing step that ran before a
Skill was even invoked.

**Relationship to Delegate.** Delegate produces task records in
`BuddyState.tasks`, each describing the capability needed. Delegate does
not resolve those tasks to a concrete Skill instance itself — it hands
that resolution to the Dispatcher.

**Relationship to the Skill Registry.** The Dispatcher is the Registry's
primary consumer (Section 10). Every lookup the Dispatcher performs — by
capability or by name — is answered by the Registry; the Dispatcher never
maintains its own list of Skills.

**Relationship to Skills.** The Dispatcher only ever calls a Skill's
declared, static interface — `supported_inputs`, `can_handle()` — to
judge fitness. It never touches a Skill's domain logic and never calls
`execute()` or `verify()`.

**Relationship to Execute.** Execute is the Dispatcher's only caller and
its only consumer. Execute asks the Dispatcher for an `ExecutionRequest`,
then — and only then — invokes the selected Skill's `execute()` method
itself, tracks it in `BuddyState.active_task`, and writes the result into
`BuddyState.outputs`.

**Relationship to BuddyState.** None, directly (see Section 5). The
Dispatcher operates purely on the `SkillTask` it's given and returns an
`ExecutionRequest` — it never reads or writes `BuddyState`. Only Delegate
and Execute touch `BuddyState.tasks`, `active_task`, and `outputs`; the
Dispatcher sits statelessly between them.

## 12. Workflow

Workflow (`core/workflow.py`) defines the *shape* of the cognitive cycle —
the stage ordering, and the rules for what happens on transitions between
stages (for example, whether a trivial request can short-circuit some
stages, or how the cycle behaves when a stage reports it cannot proceed).

The distinction from the Core Engine matters: Engine *executes* the cycle;
Workflow *defines* it. Keeping these separate means the shape of Buddy's
reasoning process can be reconsidered without touching the machinery that
runs it.

## 13. Event System

The Event System (`core/events.py`) defines the event types exchanged
across Core as the cycle runs — for example, a stage starting or
completing, a Skill being invoked, a task making progress, or an error
occurring. Events exist to decouple components: Execute doesn't need to
know that Speak will eventually turn a progress event into a user-facing
message (per `BUDDY_PERSONALITY.md`'s Progress Update Style) — it just
emits the event.

Event categories:

- **Lifecycle events** — a stage or cycle started or completed.
- **Task events** — a Skill was invoked, is progressing, or finished.
- **Error events** — something failed, at the stage or Skill level.
- **Progress events** — incremental status for long-running work.

## 14. File Lifecycle

A file's path through WorkBuddy is deliberately linear and one-directional
with respect to originals:

```
User uploads a file
        |
        v
workspace/uploads/  <-- referenced in BuddyState.uploaded_files
        |
        v
Execute asks the Dispatcher to select a capable Skill (Section 11)
        |
        v
Execute invokes that Skill, passing a scoped, read-only reference
        |
        v
Skill performs its work, writes a NEW file
        |
        v
workspace/outputs/  <-- referenced in BuddyState.outputs
        |
        v
Speak references the output file when replying to the user
```

Originals in `uploads/` are never modified in place — Skills produce new
files in `outputs/`. This is consistent with the Write-tier permission
rules in `BUDDY_SKILLS.md`: overwriting a user's original file is
significant enough that it always requires explicit confirmation, never an
implicit side effect of a Skill doing its job.

## 15. User Request Lifecycle

Tracing one message from the user through the system:

1. The user sends a message (via a future Frontend/Backend, or directly in
   Sprint 1's scope, via the Core Engine's entry point).
2. The Core Engine loads the session's existing `BuddyState`, or creates a
   new one if this is the first message in the session, and calls
   `reset_transient()` to clear the previous turn's per-cycle fields.
3. The engine runs the full cognitive cycle: Observe through Speak. Within
   it, Execute consults the Dispatcher (Section 11) once per delegated
   task to obtain the Skill that will actually do the work.
4. `BuddyState.reply` is returned to the user.
5. The turn's relevant history is folded into `short_memory`, and the
   session is persisted under `workspace/sessions/`.
6. The session remains open for the next message, repeating from step 2.

## 16. Long-running Tasks

Some Skill work takes real time — a large spreadsheet, a multi-step
request touching several Specialists. Architecturally, Execute is expected
to track the currently running task in `BuddyState.active_task` and to
surface progress through the Event System (Section 13) rather than leaving
the user staring at silence, consistent with the Performance Expectations
in `BUDDY_SKILLS.md`. The Dispatcher itself is not part of this: skill
selection is a fast, in-memory lookup, not long-running work.

Sprint 1 treats the cognitive cycle as a single continuous pass per user
message; a task does not yet run independently of the request that started
it. How Buddy handles a user sending a new message while a long task is
still active is intentionally left for a future sprint (see Section 20)
rather than decided prematurely here.

## 17. Error Recovery

Errors are handled at three distinct levels, and they are not treated the
same way:

- **Routing errors** — if the Dispatcher (Section 11) cannot find any
  registered Skill capable of a task, it raises a typed error immediately,
  before any Skill is ever invoked. This is distinct from a Skill failing
  mid-execution: nothing has been attempted yet, so there is nothing to
  roll back.
- **Skill-level errors** — handled per the contract in `BUDDY_SKILLS.md`
  (recoverable vs. non-recoverable, bounded retries owned by Core, never
  by the Skill itself).
- **Stage-level errors** — if a stage in the cognitive cycle itself cannot
  proceed (for example, Understand cannot resolve any usable intent), the
  Core Engine's error boundary catches this, the cycle stops advancing
  further stages, and control passes directly to Speak to deliver an
  honest, plain explanation to the user — following the Error Message
  Style in `BUDDY_PERSONALITY.md`.

In every case — routing, Skill, or stage — the failure surfaces to Speak
through the same path, so the user always hears one honest explanation
rather than a raw exception, regardless of which layer produced it.

In every case, `BuddyState` as it existed at the point of failure is
preserved rather than discarded, so the failure is debuggable, and a
failure in one turn never corrupts the session's accumulated
`short_memory` or `metadata`.

## 18. Extensibility

WorkBuddy is architected for additive change:

- New Skills are added by registering them (Section 10) — no change to
  `buddy/` is required.
- The Dispatcher's Skill-selection policy (Section 11) is a swappable
  strategy, not logic hard-coded into the Dispatcher itself — future
  policies such as priority ordering, load balancing across multiple
  Specialists for the same capability, or dispatching to cloud workers can
  be introduced without changing the Dispatcher's interface.
- New event types can be added to `core/events.py` without altering
  existing event consumers.
- Changes to the shape of the cognitive cycle live in `core/workflow.py`,
  isolated from the Core Engine that runs it.
- Changes to Buddy's tone live in `buddy/personality.py` and
  `BUDDY_PERSONALITY.md`, isolated from reasoning logic.

The guiding rule: a new capability should be addable to WorkBuddy by
adding a new, self-contained piece — not by modifying the shared core that
everything else depends on.

## 19. Security Considerations

- **Workspace sandboxing** — a Skill is never given a path outside its
  assigned workspace (Section 7; `BUDDY_SKILLS.md` Section 12).
- **Routing has no data access** — the Dispatcher (Section 11) only ever
  handles task metadata and Skill references; it never reads file
  contents, never touches `BuddyState`, and is not part of the permission
  check itself — it only ever finds and prepares, it never authorizes.
- **Permission tiers** — Read, Write, and External Effect actions carry
  different authorization requirements before Execute will invoke them
  (`BUDDY_SKILLS.md` Section 13).
- **Session and workspace isolation** — one user's workspace is never
  reachable from another session, by construction of how `workspace_id`
  scopes every file reference.
- **Least privilege per invocation** — a Skill receives only the
  references and credentials strictly required for its current task, never
  broad standing access.
- **Untrusted input by default** — every uploaded file is treated as
  untrusted until validated by the Skill responsible for handling it.

## 20. Future Distributed Architecture

Sprint 1 runs as a single process: Core Engine, Buddy, and Skills all
execute in-line, and the workspace is local disk. This is intentional —
distributing the system before the core reasoning is proven would add risk
without validated benefit.

The architecture is nonetheless shaped so a future distributed form is a
natural extension, not a rewrite:

```
Future, exploratory — NOT current architecture

+------------+     +----------------+     +--------------------------+
|  Backend    | --> |  Core Engine     | --> |  Task Queue / Dispatcher   |
|  (API)      |     |  (service)        |     |  (routes Skill work)        |
+------------+     +----------------+     +------------+-------------+
                                                          |
                              +---------------------------+---------------------------+
                              |                            |                            |
                       +------v------+             +------v------+             +------v------+
                       |  Excel Skill  |             |   PDF Skill   |             |  Email Skill  |
                       |   (worker)     |             |    (worker)    |             |   (worker)     |
                       +-------------+             +-------------+             +-------------+
                                                          |
                                                +---------v---------+
                                                |  Object Storage       |
                                                |  (replaces local        |
                                                |   workspace/ disk)       |
                                                +---------------------+
```

In this shape, today's in-process Dispatcher (Section 11) is what grows
into the Task Queue's routing logic — the same responsibility (locate,
validate, select, prepare) at a different scale, still stopping short of
executing or verifying anything itself. The Registry becomes a
service-discovery mechanism, the Event System becomes a real message bus,
and Skills become independently deployable workers consuming tasks from a
queue rather than in-process calls. This section documents direction, not
a commitment — no part of it is approved for implementation.

## 21. Design Principles

Cross-cutting principles that every part of this architecture is expected
to uphold:

1. **Shared state over parameter passing.** Every stage reads and writes
   one `BuddyState`, rather than threading arguments through call chains.
2. **Orchestration over direct execution.** Buddy decides what needs to
   happen; Skills are the only place actual domain work happens.
3. **Explicit contracts over assumed behavior.** Every boundary — Skill
   input/output, event types, workspace access — is a declared contract,
   not an inferred convention.
4. **Honest failure over silent failure.** Every layer, from a Skill to
   the Core Engine, fails loudly and reports clearly rather than guessing
   or hiding a problem.
5. **Sandboxed by default.** Workspace and permission boundaries are the
   default, not an opt-in safeguard.
6. **Additive extensibility.** New capability is added, not woven into
   existing core logic.
7. **One voice to the user.** Regardless of how many stages or Skills were
   involved, the user experiences a single, consistent coworker.
8. **Verification independent from execution.** Nothing marks itself as
   successful — success is judged independently against the user's
   original intent.
9. **Routing is separate from execution.** The Dispatcher decides which
   Skill should run a task; only Execute actually runs it. Selection and
   execution are never the same step, and the component that picks a
   Skill is never the one held responsible for what that Skill does.
