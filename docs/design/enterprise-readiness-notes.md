# Enterprise Readiness Notes

**Purpose:** Documentation snapshot of the org-deployment vision discussed
for WorkBuddy and the conclusion of an architecture audit against that
vision. No code changes were made to produce this document, and none are
proposed by it — it's a record of a decision, not a plan.

---

## 1. The org-deployment vision

The discussed target deployment shape for an organization adopting
WorkBuddy:

- **Fully private per employee.** Each employee's workspace, uploads, and
  conversation state are isolated from every other employee's — one
  person's data is never visible to another, including other employees at
  the same org.
- **Browser-only access.** Employees interact with WorkBuddy through the
  browser; there's no requirement for a local install or file-system
  access on the employee's machine beyond what the browser mediates.
- **File copies, never originals.** When WorkBuddy operates on an
  uploaded file, it works against a copy. The employee's original file is
  never mutated in place — a property already true of the current
  single-user code (see the Skills layer's write-a-new-file convention)
  and one the org-deployment vision depends on continuing to hold.
- **Third-party AI is acceptable at current scale**, but the codebase
  must stay provider-agnostic so a future self-hosted model option is a
  swap, not a rewrite. This isn't a request to add self-hosting now — it's
  a constraint on how today's provider integration is written, so that
  door stays open.

---

## 2. Architecture audit conclusion

The audit's conclusion: the current codebase already has the right
foundations for this vision. Specifically:

- **`workspace_id`-scoped storage.** `BuddyState` is keyed by
  `workspace_id` from construction (`BuddyState.new(session_id,
  workspace_id)`, `buddy/state.py:123`), with `Context.active_workspace`
  carrying it through the rest of state. This is the seam per-employee
  isolation would attach to — workspace scoping is already a first-class
  concept, not something bolted on later.
- **The swappable `LLMProvider` pattern.** `LLMProvider` is a `Protocol`
  (`buddy/llm.py:37`), not a concrete binding to any one vendor. Anything
  that depends on an LLM depends on this protocol, so a self-hosted model
  is a new implementation of the same interface — the provider-agnostic
  requirement from the vision is already satisfied by the existing
  design, not something that needs to be added.
- **The never-log-content discipline.** `buddy/llm.py` documents and
  enforces that prompt and response text are never logged — only model
  name, elapsed time, and token usage counts are (`buddy/llm.py:72-74`,
  `143-144`), since spreadsheet content and other user data may pass
  through prompts. This is exactly the discipline a multi-tenant,
  privacy-sensitive deployment needs, and it already exists.
- **The injectable `ConfirmationProvider`.** `ConfirmationProvider` is a
  `Protocol` (`buddy/hands.py:16`) injected into `Hands` rather than
  hardcoded, gating `WRITE`/`EXTERNAL_EFFECT`-tier actions
  (`_CONFIRMATION_REQUIRED_TIERS`). This is the seam a browser-based
  confirmation UI (or an org-specific approval policy) would attach to.

**Conclusion: no code changes are needed now.** These four properties are
already correct as foundations for the vision above; the audit's job was
to confirm this, not to propose new work.

---

## 3. Future watch-items

Two things the audit flagged as *not yet* covered, to revisit when their
triggering phase arrives — not to act on now:

1. **Real access-control enforcement arrives with the auth/backend
   phase.** `workspace_id` scoping today is a data-modeling convention,
   not an enforced boundary — nothing in the current single-user codebase
   stops one workspace's state from being read using another's
   `workspace_id`, because there's no auth layer yet to make that
   distinction meaningful. This is expected and fine for now; it becomes
   a real requirement only once an auth/backend phase introduces actual
   multi-tenant request handling, and enforcement should land as part of
   that phase rather than being retrofitted piecemeal beforehand.

2. **Tracing/observability tooling needs deliberate content-capture
   scoping when it's added.** The never-log-content discipline above
   covers today's logging. Tracing or observability tooling added later
   (request tracing, prompt/response capture for debugging, analytics)
   is a distinct surface that doesn't automatically inherit that
   discipline — it needs its own deliberate decision about what content
   it's allowed to capture and retain, made at the time it's introduced,
   not assumed to be safe by default because the logging layer is.

---

## 4. On this document's contents

This note is documentation only. It captures a conversation and its
conclusion so the vision and the audit result aren't lost between
sessions; nothing here changed `buddy/state.py`, `buddy/llm.py`,
`buddy/hands.py`, or any other source file.
