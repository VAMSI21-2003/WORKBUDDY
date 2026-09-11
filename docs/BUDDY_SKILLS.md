# Buddy Skills Specification

**Status:** v1.0 — Sprint 1 foundation
**Owner:** WorkBuddy Architecture
**Scope:** This document defines what a Skill is, how Buddy relates to
Skills, and the contract every Skill must satisfy. It is a product and
architecture specification, not an implementation guide. It contains no
code, no class definitions, and no language-specific detail — those
decisions belong to the modules under `skills/` and are made separately,
against this document.

**Why this document exists:** Buddy never performs work directly. Buddy
observes, reasons, plans, and delegates — the actual work of manipulating a
spreadsheet, reading a PDF, or drafting an email is always done by a
Specialist. That split is the single most important architectural decision
in WorkBuddy. This document is the source of truth for how that split
works, so every Specialist built — this sprint or five years from now —
behaves consistently.

---

## 1. What is a Skill?

A Skill is a self-contained Specialist capable of performing one category
of work on the user's behalf — for example, the Excel Specialist, the PDF
Specialist, or the Calendar Specialist. A Skill is Buddy's hands for a
specific domain: Buddy decides *what* needs to happen and *why*; the Skill
knows *how* to actually do it.

A Skill is not a personality, does not talk to the user, and does not
reason about intent. It receives a well-defined task, does the work within
its domain, and reports back a well-defined result. Everything else —
understanding what the user wants, deciding which Skill to use, deciding
whether the result actually satisfies the user — stays with Buddy.

## 2. Why Skills Exist

Buddy's cognitive cycle (Observe, Understand, Think, Plan, Delegate,
Execute, Verify, Speak) must stay domain-agnostic. If Buddy's core knew how
to build a pivot table, parse a PDF's text layer, and format an email, it
would grow a new dependency and a new failure mode every time WorkBuddy
gained a capability, and every domain change would risk destabilizing
Buddy's reasoning.

Skills exist to contain that domain complexity:

- **Isolation.** A bug in the PDF Specialist cannot corrupt Buddy's
  understanding of a user's intent.
- **Independent evolution.** The Excel Specialist can be rebuilt, upgraded,
  or replaced without touching `buddy/brain.py` or any other Skill.
- **Parallel ownership.** Different Skills can eventually be built and
  maintained by different engineers or teams without stepping on each
  other.
- **Predictable growth.** Adding a new capability to WorkBuddy means adding
  a new Skill, not modifying Buddy's core reasoning.

## 3. Core Design Principles

1. **Buddy orchestrates. Skills execute.** Buddy never bypasses a Skill to
   do domain work itself, and a Skill never reasons about user intent.
2. **Single responsibility.** Each Skill owns one domain. A Skill that
   starts absorbing responsibilities from another domain is a sign the
   boundary needs to be redrawn, not stretched.
3. **Statelessness between invocations.** A Skill does not privately
   remember anything about a session. Anything that must persist lives in
   `BuddyState` or the workspace, not inside the Skill.
4. **Explicit contracts over assumptions.** A Skill declares what it needs
   and what it returns. Buddy never assumes a Skill's internal behavior.
5. **Fail loud, not silent.** A Skill that cannot complete its task says so
   clearly. It never returns a partial or wrong result disguised as
   success.
6. **One voice to the user.** Skills never communicate with the user
   directly. All user-facing communication flows through Buddy's Speak
   stage, so the user always hears one consistent coworker, not a chorus
   of specialists.
7. **No lateral calls.** Skills never invoke other Skills directly. If a
   task needs multiple Specialists, Buddy sequences them through Plan and
   Delegate.

## 4. Skill Lifecycle

Every Skill invocation moves through the same lifecycle, regardless of
domain:

1. **Registration** — The Skill declares itself, its capabilities, and its
   permission tier to the Skill Registry when WorkBuddy starts up.
2. **Discovery** — During Plan and Delegate, Buddy's Router consults the
   Registry to find which registered Skill(s) can satisfy a given task.
3. **Selection** — Buddy selects the Skill (or ordered set of Skills) best
   suited to the task and constructs a task record for each.
4. **Input Validation** — The selected Skill validates the task it has been
   given against its own input contract before doing any work.
5. **Execution** — The Skill performs its domain-specific work.
6. **Output Validation** — The Skill checks its own output is well-formed
   before returning it (see Section 9).
7. **Verification** — Buddy's Verify stage independently checks the result
   against the user's original intent — not just whether the Skill
   reported success.
8. **Completion or Failure** — The task is marked complete, partially
   complete, or failed, and the outcome is recorded in `BuddyState`.
9. **Cleanup** — Any temporary resources the Skill created during execution
   are released.

## 5. Skill Responsibilities

A Skill is responsible for:

- Validating that the task it received is one it can actually perform.
- Performing the work correctly within its declared domain.
- Producing output that conforms to the Skill Output Contract.
- Reporting success, partial success, or failure honestly.
- Cleaning up any temporary files or resources it created.
- Operating only within the workspace boundaries it was given.
- Reporting progress for long-running work, so Buddy can keep the user
  informed.

A Skill is explicitly **not** responsible for deciding whether its result
satisfies the user's actual goal — that judgment belongs to Verify.

## 6. What Skills Must Never Do

- Never communicate with the user directly, in any form.
- Never modify `BuddyState` directly — a Skill returns results through its
  output contract; only Buddy's core writes them into state.
- Never access files or resources outside the workspace boundaries it was
  explicitly given.
- Never call another Skill directly.
- Never retry its own failed operation indefinitely or silently.
- Never report success when the outcome is incomplete, uncertain, or
  unverified.
- Never take an irreversible external action (sending, deleting,
  publishing) without the permission tier that explicitly allows it.
- Never persist sensitive file contents or credentials beyond what the
  current task strictly requires.

## 7. Skill Input Contract

Every task handed to a Skill is explicit and self-contained. A Skill should
never need to infer missing context from anywhere outside what it was
given. At minimum, a task passed to a Skill includes:

- **Task description** — what outcome is being asked for, in structured
  form (derived from Buddy's Plan stage).
- **Workspace references** — pointers to the specific files or resources
  the Skill is authorized to read, scoped to the current session's
  workspace.
- **Parameters** — any task-specific detail needed to perform the work
  (for example, a target date range or output format).
- **Permission scope** — what this invocation is authorized to do (read
  only, write, or trigger an external effect).
- **Execution context** — session and workspace identifiers, used solely
  for scoping and logging, never for the Skill to look up additional state
  on its own.

A Skill that receives a task outside its declared capability, or missing
required input, rejects it during Input Validation rather than guessing.

## 8. Skill Output Contract

Every Skill returns a structured result, whether it succeeded or not. At
minimum, that result includes:

- **Status** — success, partial success, or failure.
- **Produced outputs** — references to files or data the Skill created or
  modified, not raw content dumped into the response.
- **Summary** — a short, plain-language description of what was done,
  written so Buddy's Speak stage can use it without translation.
- **Confidence or caveats** — anything the Skill is uncertain about in its
  own result (for example, "three rows had ambiguous dates and were left
  unchanged").
- **Errors or warnings** — structured, not raw stack traces, describing
  anything that went wrong or was skipped.
- **Execution metadata** — timing and resource information, used for
  logging and performance monitoring.

## 9. Verification Responsibilities

Verification happens in two layers, and they are deliberately not the same
check:

- **Skill self-check (Output Validation):** Did the Skill produce
  something well-formed? Does the output file exist, open correctly, and
  match the expected type? This is a mechanical check the Skill performs
  on itself.
- **Buddy's Verify stage:** Does the result actually satisfy what the user
  asked for? This check is independent of the Skill and is performed by
  Buddy against the original intent recorded in `BuddyState`, not against
  the Skill's own report of success.

A Skill's self-reported success is a necessary signal, never a sufficient
one. Verify exists precisely because a Skill can complete without error and
still miss the point of the request.

## 10. Error Handling

- Skills distinguish between **recoverable** errors (a locked file, a
  transient timeout) and **non-recoverable** errors (an unsupported format,
  a corrupt file).
- Errors are returned through the Output Contract in structured form —
  never as raw exceptions or stack traces surfaced to Buddy or the user.
- A failing Skill invocation is isolated: it never crashes Buddy's
  cognitive cycle or affects unrelated tasks.
- A Skill never leaves a partially written or corrupted output in place of
  a clear failure signal. Partial work is either clearly labeled as
  partial or discarded.

## 11. Retry Policy

- Retry decisions belong to Buddy's core (Delegate/Execute), never to the
  Skill itself. A Skill does not decide on its own to try again.
- Only recoverable errors are eligible for retry; non-recoverable errors
  fail immediately.
- Retries are bounded — a small, fixed number of attempts, never an
  unbounded or silent loop.
- After retries are exhausted, the failure is surfaced clearly rather than
  attempted again silently in the background.

## 12. Security Rules

- Skills operate exclusively within the workspace sandbox they are given —
  no access to the filesystem outside it.
- No network access unless a Skill's declared capability explicitly
  requires and is granted it.
- Skills never execute arbitrary code, macros, or scripts embedded in user
  files without isolation appropriate to that risk.
- All file inputs are validated and treated as untrusted before processing.
- Skills never receive more credentials or secrets than the minimum
  required for the current task, and never persist them beyond it.

## 13. Permission Model

Not all Skills carry the same risk, so Skills are grouped into permission
tiers that determine how much authorization is required before Buddy will
invoke them:

| Tier | Description | Example | Confirmation required? |
|---|---|---|---|
| **Read** | Reads or analyzes data without changing anything | PDF text extraction, OCR | No |
| **Write** | Creates or modifies files within the workspace | Excel formatting, chart generation | Not for the first action; yes before overwriting user-original files |
| **External Effect** | Takes an action with impact outside the workspace | Sending an email, creating a calendar event | Yes, always, before execution |

Buddy's Delegate stage checks a Skill's declared permission tier against
what has been authorized for the current task before invoking it. A
Write-tier or External-Effect-tier action is never silently taken — this is
the architectural backbone of the "no irreversible action without
confirmation" rule defined in `BUDDY_PERSONALITY.md`.

## 14. Logging Expectations

- Every Skill invocation is logged with structured metadata: which Skill,
  which task, timing, and outcome.
- Logs are traceable back to a session and task identifier, so any
  invocation can be reconstructed for debugging.
- Logs never include sensitive raw file contents — only what's needed to
  diagnose behavior (file type, size, outcome, error category).
- Logs distinguish between Skill-internal failures and failures caused by
  invalid input or missing permission, so issues can be routed to the
  right place.

## 15. Performance Expectations

- Long-running Skill work reports intermediate progress so Buddy can keep
  the user informed, consistent with the Progress Update Style defined in
  `BUDDY_PERSONALITY.md`.
- Every Skill invocation has a reasonable timeout, so a stuck Skill cannot
  stall Buddy's entire cognitive cycle.
- Skills are expected to handle realistically large inputs without
  requiring the caller to know their internal resource strategy.
- A Skill that cannot finish in full degrades gracefully — returning a
  clearly labeled partial result — rather than hanging indefinitely.

## 16. Examples of Skills

| Skill | Responsibility | Permission tier | Example task |
|---|---|---|---|
| Excel Specialist | Reads, cleans, and formats spreadsheet data | Write | "Clean up this sheet and summarize totals by region" |
| PDF Specialist | Extracts and interprets content from PDF documents | Read | "Pull the key figures out of this report" |
| Word Specialist | Creates and formats Word documents | Write | "Turn these notes into a formatted memo" |
| PowerPoint Specialist | Builds and formats presentation slides | Write | "Make a three-slide summary of this data" |
| Email Specialist | Drafts and sends email on the user's behalf | External Effect | "Send this summary to my manager" |
| SQL Specialist | Queries structured data sources | Read | "Get last month's sales by product" |
| OCR Specialist | Extracts text from scanned images or documents | Read | "Read the numbers off this scanned invoice" |
| Chart Specialist | Builds charts and visualizations from data | Write | "Turn this table into a bar chart" |
| Calendar Specialist | Creates, updates, or checks calendar events | External Effect | "Block an hour tomorrow to prep for this" |

## 17. Example Execution Flow

Walking through: *"Buddy, I have a meeting tomorrow. Can you make this
ready?"*, with a spreadsheet already uploaded to the workspace.

1. **Observe** notes the user's message and the presence of an uploaded
   spreadsheet.
2. **Understand** interprets the intent: prepare the uploaded file for
   presentation in a meeting.
3. **Think** reasons that "meeting-ready" implies cleaned data, a summary,
   and a visual the user can walk through.
4. **Plan** breaks this into steps: clean and summarize the data, generate
   a chart, assemble a one-page view.
5. **Delegate** assigns the cleaning and summarizing step to the Excel
   Specialist (Write tier) and the visualization step to the Chart
   Specialist (Write tier), each with a scoped task record.
6. **Execute** invokes each Skill in sequence. Each Skill validates its
   input, performs its work, and returns a result through the Output
   Contract, including a plain-language summary.
7. **Verify** checks the combined result against the original intent: is
   this actually something the user could walk into a meeting with?
8. **Speak** reports back to the user in plain language, referencing the
   finished file — never mentioning which Skills were involved.

At no point does the user see "Excel Specialist" or "Chart Specialist" —
those names exist for engineers building WorkBuddy, not for the person
using it.

## 18. Future Expansion

Explicitly out of scope for this sprint, reserved for future evaluation:

- A Skill marketplace allowing third-party Specialists.
- Skill chaining or composition, where one Skill's output is piped directly
  into another without a full Buddy cycle in between.
- Skill versioning and rollback, so a Skill can be upgraded without
  breaking in-flight tasks.
- Organization-specific or custom Skills built for a particular customer.
- Capability negotiation, where a Skill can report its own confidence for a
  given task type before being selected.
- Parallel execution of independent Skills within a single task.

None of these are approved for implementation. Any change to the Skill
contract itself — as opposed to these future extensions — requires
updating this document first.

## 19. Design Decisions

- **Why Skills never talk to the user directly:** A single, consistent
  voice matters more than domain-specific personality. Routing everything
  through Speak keeps Buddy's tone (defined in `BUDDY_PERSONALITY.md`)
  consistent regardless of which Specialist did the work.
- **Why Skills are stateless between invocations:** Statelessness makes
  Skills safer to retry, easier to test in isolation, and impossible to
  leave in an inconsistent internal state across sessions.
- **Why permission tiers exist:** "Can technically do this" and "is allowed
  to do this without asking" are different questions. Separating them
  keeps irreversible actions from ever happening as a side effect of a
  Skill simply being capable of them.
- **Why verification is separate from a Skill's self-check:** A Skill
  grading its own homework against the user's actual intent is a conflict
  of interest by construction — it can only check that its output is
  well-formed, not that it's what was actually needed.
- **Why retries are owned by Buddy's core, not the Skill:** Centralizing
  retry policy prevents runaway loops inside individual Skills and keeps
  retry behavior consistent and auditable across every Specialist.

## 20. Summary

Buddy never performs work directly — it observes intent, reasons about it,
and delegates to Specialists built for that domain. Every Specialist is a
Skill, and every Skill follows the same lifecycle, the same input and
output contracts, the same permission model, and the same rule: do the
work honestly, report the result honestly, and never speak to the user
directly. This separation is what lets WorkBuddy grow new capabilities
over time without ever destabilizing Buddy's core judgment — Buddy stays
the same coworker no matter how many Specialists are working behind it.
