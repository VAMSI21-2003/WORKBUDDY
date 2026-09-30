# Backend API design: letting a web frontend use the pipeline

**Status:** design only. Nothing in this document is implemented. It covers backlog item 4 (the backend API, including F5 and R6) and the confirm/reject seam that replaces `NullConfirmationProvider`.

**Rule-touching:** yes, both rules. The API boundary is where rule 1 (no false claim reaches the user) and rule 2 (the original is never modified) are either kept or lost. PR-1 below also changes `core/engine.py`'s error contract and the skills' output path. Per `CLAUDE.md` and backlog item 4, this design needs an independent review before any implementation, and each implementation PR needs its own review and live runs.

---

## 0. Decisions in brief

1. **FastAPI, one process, one worker.** Pydantic models give the frontend a typed contract (OpenAPI), and an injectable engine factory lets every test run the real pipeline with a fake LLM. One worker, because session state and pending confirmations live in memory (Section 2).
2. **A turn is asynchronous.** `POST .../turns` returns `202` at once; the client polls. This is forced by confirm/reject: `Hands` asks for confirmation *in the middle* of a turn, through a synchronous call (`ConfirmationProvider.confirm`), so the turn thread must be able to wait for a human (Section 1.5).
3. **The API never authors outcome text.** `reply` is `state.reply` from the pipeline, byte for byte. When the pipeline itself can't produce one (a crash, a timeout), the fallback text comes from a constant in `buddy/` and is labelled `reply_source: "pipeline_fallback"`. The API module contains no sentence about what happened to the user's file (Section 5).
4. **Every turn writes to its own folder** `workspace/outputs/{session}/{turn}/`, passed to the skills explicitly, so a later turn can't overwrite an earlier delivery (F5, Section 3).
5. **Each turn runs on a deep copy of the session's `BuddyState`.** The copy is committed only if the turn finishes normally. A crash or a timeout can't corrupt the session, and a turn that outlives its deadline can't mutate anything (Sections 3 and 4).
6. **"Delivered" is decided by one function** in the API: the pipeline ended without a failure, `ExecutionResult.outputs` is non-empty, `VerificationResult.passed` is true, and every file exists under the turn's folder. Only those files can be listed or downloaded. Everything else on disk is invisible (Section 5).
7. **Four small changes outside the API are prerequisites** (PR-1): the engine turns a stage exception into a failure state and still runs Voice (R6); skills take an explicit output folder (F5); `Context` can be restored from disk; fallback reply constants. They are the rule-touching part.

**What this design does not solve.** There is no authentication (Section 6.1). The session id is a bearer secret, which is acceptable for a local or single-user deployment and **not** for the public internet. Horizontal scaling and durable job queues are deferred (architecture Section 20).

---

## 1. Endpoints

Base path `/api/v1`. JSON everywhere except upload (multipart) and download (file bytes). IDs are opaque strings matching `^[A-Za-z0-9_-]{1,64}$`; the server generates all of them.

Two kinds of text leave the API, and they must never be confused:

- **`reply`** (on a turn): the pipeline's answer about the work. The frontend shows it as the answer and does not edit, summarize or supplement it.
- **`error.message`** (on a non-2xx response): a fixed, API-authored sentence about a *rejected request* ("That file is larger than 25 MB."). It never describes the outcome of work on the user's file, because a rejected request did no work.

### 1.1 Create a session: `POST /sessions`

Request: `{ "mode": "friend" | "focus" }` (optional, default `"friend"`).

Response `201`:

```json
{ "session_id": "s_9f3c…", "mode": "friend", "created_at": "2026-09-30T10:00:00Z" }
```

The session id is 128 bits from `secrets.token_urlsafe`. It is the only credential in v1.

### 1.2 Upload a file: `POST /sessions/{session_id}/files`

`multipart/form-data`, one part named `file`. Response `201`:

```json
{
  "file_id": "f_27ab…",
  "name": "report.xlsx",
  "size": 18234,
  "sha256": "9c1f…",
  "kind": "upload",
  "uploaded_at": "2026-09-30T10:00:05Z"
}
```

`name` is the sanitized name the pipeline will use (and that replies will mention), not necessarily what the browser sent. `sha256` is the upload-time hash that rule 2's check compares against. Errors: `404 session_not_found`, `413 file_too_large`, `415 unsupported_file_type`, `422 invalid_file` (empty, or failed the structural check), `409 session_busy` (a turn is running; uploads mid-turn would change `uploaded_files` under it).

Uploading makes the file the session's active file (`Context.switch_active_file`), so `Delegate` targets it (it uses `active_file`, else the last uploaded file: `buddy/delegate.py:95`).

### 1.3 Run a turn: `POST /sessions/{session_id}/turns`

Request:

```json
{ "message": "remove duplicate rows, then remove empty rows",
  "mode": "focus",
  "file_id": "f_27ab…" }
```

- `message`: required, 1 to 4,000 characters after trimming.
- `mode`: optional; if present, sets the session's mode (it is a per-session setting, `Context.set_mode`).
- `file_id`: optional; if present it must be an upload in this session, and becomes the active file for this turn. If absent, the current active file is used. (Multi-file requests are out of scope: the pipeline has a single active-file pointer.)

Response `202`: the turn object in `running` state (Section 1.3.1). The API creates the turn's folder and record before returning.

Errors: `404 session_not_found`, `404 file_not_found`, `409 session_busy` (one turn at a time per session; matches the architecture's "a new message while a task is active is left for a future sprint"), `422 invalid_request`, `429 server_busy` (global cap on running turns reached; protects the free-tier Groq rate limit and the machine). A refused request creates **no** turn.

#### 1.3.1 The turn object

Returned by the turn endpoints and embedded, in summary form, in session state.

```json
{
  "turn_id": "t0002",
  "seq": 2,
  "status": "running | awaiting_confirmation | finished",
  "outcome": null,
  "message": "remove duplicate rows, then remove empty rows",
  "mode": "focus",
  "started_at": "2026-09-30T10:01:00Z",
  "finished_at": null,
  "reply": null,
  "reply_source": null,
  "delivered_files": [],
  "pending_confirmation": null,
  "detail": null
}
```

- `status`: `running` while the pipeline works, `awaiting_confirmation` while `Hands` waits for the user, `finished` when a reply exists. `finished` is the only state in which `reply` and `outcome` are set.
- `outcome` (set when finished, derived by code from the facts, never from the reply text):
  - `delivered`: at least one file passed the delivery rule in Section 5.3.
  - `not_delivered`: the pipeline finished normally and handed nothing back (clarification, no matching skill, a declined step, a failed verification). The reply explains why, in the pipeline's words.
  - `failed`: a stage raised, or the turn passed its deadline. The reply is the pipeline's failure reply or the fallback.
  - `integrity_failure`: the original upload's hash changed during the turn (Section 5.2). No files are released.
- `reply_source`: `"pipeline"` (Voice produced it) or `"pipeline_fallback"` (a constant from `buddy/`, used when Voice couldn't run).
- `delivered_files`: `[{ "file_id": "d_t0002_1", "name": "report_deduplicated_….xlsx", "size": 16011, "sha256": "…", "download_url": "/api/v1/sessions/s_…/files/d_t0002_1/download" }]`. Empty unless `outcome == "delivered"`.
- `pending_confirmation`: see Section 1.5. Non-null only while `awaiting_confirmation`.
- `detail`: the data behind the turn, for the frontend's "what happened" panel and for the live-run reports `CLAUDE.md` requires. Structured data only, no prose from skills:

```json
{
  "intent": "multi_step_request",
  "clarification_required": false,
  "steps": [
    { "capability": "spreadsheet.deduplicate", "permission_tier": "write",
      "status": "success", "verified": true,
      "input_name": "report.xlsx", "output_names": ["report_deduplicated_….xlsx"] }
  ],
  "stopped_at": null
}
```

`detail` is built from `BuddyState.understanding`, `reasoning`, `execution_result` and `verification_result`. It deliberately **omits** `StepResult.summary` and `self_reported_findings` (a Skill's unverified claims: verified-outcome-a L1) and every exception message. It contains file *names*, never paths.

### 1.4 Get state

- **`GET /sessions/{session_id}`** returns the whole session for a page load or a reconnect:

```json
{
  "session_id": "s_9f3c…",
  "mode": "focus",
  "busy": false,
  "active_file_id": "f_27ab…",
  "files": [ { "file_id": "f_27ab…", "name": "report.xlsx", "size": 18234, "kind": "upload", "uploaded_at": "…" } ],
  "turns": [ /* turn objects, oldest first, without `detail` */ ],
  "pending_confirmation": null
}
```

- **`GET /sessions/{session_id}/turns/{turn_id}`** returns one turn object including `detail`. This is what the client polls after `POST .../turns`; a finished turn is immutable.

Errors: `404 session_not_found`, `404 turn_not_found`. A session that belongs to someone else and a session that doesn't exist are indistinguishable (both `404`).

### 1.5 Confirm or reject a pending action

While a turn is `awaiting_confirmation`, its `pending_confirmation` is:

```json
{
  "confirmation_id": "c_5e1d…",
  "capability": "spreadsheet.deduplicate",
  "permission_tier": "write",
  "input_name": "report.xlsx",
  "prompt": "Remove duplicate rows from report.xlsx and save the result as a new file.",
  "expires_at": "2026-09-30T10:03:00Z"
}
```

`prompt` is built by code from a fixed template per capability, filled with the file name from the task. It describes a **proposed** action and claims no outcome. A test asserts every registered capability has a template; a missing one falls back to a generic "Run {capability} on {input_name}."

**`POST /sessions/{session_id}/turns/{turn_id}/confirmation`**

```json
{ "confirmation_id": "c_5e1d…", "decision": "approve" }
```

`decision` is `"approve"` or `"reject"`. Response `200`: the turn object as it stands after the decision (usually still `running`; the client keeps polling). Errors: `404` (unknown session or turn), `409 no_pending_confirmation` (none pending, already answered, or expired), `409 stale_confirmation` (the id isn't the current one), `422 invalid_request`.

How it works: the turn runs in a worker thread and `Hands` calls `confirmation_provider.confirm(task, tier)`. The API's `WebConfirmationProvider` publishes the pending confirmation on the turn record, sets the status to `awaiting_confirmation`, and blocks on a `threading.Event` with a timeout (default 120 s). The confirm endpoint sets the decision and releases the event.

- **Approve**: `confirm()` returns `True`; the pipeline continues unchanged.
- **Reject**: `confirm()` returns `False`; `Hands` already handles this by logging and skipping the step (`buddy/hands.py:256-268`). The reply then comes from the pipeline like any other turn. **What `Hands` and `Voice` do after a decline is not yet verified by any test I've read**; PR-3 must add one that shows the reply says the step wasn't done and no file is delivered for it.
- **Timeout or server shutdown**: treated as reject, never as approve. The turn's record notes `decision_source: "timeout"`.

A chain with several Write-tier steps asks once per step, as `Hands` does today. Whether the product should ask once per turn instead is an open question (Section 9).

### 1.6 List files: `GET /sessions/{session_id}/files`

```json
{ "files": [
  { "file_id": "f_27ab…", "name": "report.xlsx", "size": 18234, "kind": "upload",
    "uploaded_at": "…", "downloadable": false },
  { "file_id": "d_t0002_1", "name": "report_deduplicated_….xlsx", "size": 16011,
    "kind": "delivered", "turn_id": "t0002", "delivered_at": "…", "downloadable": true }
] }
```

Originals are listed so the frontend can show what's uploaded, but are **not downloadable** through the API: the user already has them, and the download route's invariant stays "delivered files only". Delivered files come from finished turns with `outcome == "delivered"` and nothing else.

### 1.7 Download a delivered file: `GET /sessions/{session_id}/files/{file_id}/download`

Streams the file with `Content-Type` fixed by extension, `Content-Disposition: attachment; filename*=UTF-8''<sanitized>`, `X-Content-Type-Options: nosniff`, and `Cache-Control: no-store`. Errors: `404 file_not_found` for an unknown id, an upload id, an id from another session, or a file that isn't on the delivered list. `410 file_unavailable` if the file is on the list but missing on disk, or its hash no longer matches the recorded delivery hash (Section 5.3).

### 1.8 Health: `GET /healthz`

`{ "ok": true, "llm_configured": true }`. Booleans only. It never reports key material, provider names with versions, or paths.

---

## 2. Framework choice

**FastAPI on uvicorn** (plus `python-multipart` for uploads).

| Need | Why FastAPI fits | Closest alternative and why not |
|---|---|---|
| A typed, documented contract for a frontend | Pydantic request/response models, OpenAPI for free; the frontend can generate its client | Flask: no built-in validation or schema; would need marshmallow/pydantic plus an OpenAPI add-on |
| Swap the engine for a fake-LLM engine in tests | `Depends` and `create_app(engine_factory=…)` | Flask can do this with app factories, but with more hand wiring |
| Test the real HTTP surface without a server | `TestClient` (uses `httpx`, already installed) | Same for Flask |
| Blocking pipeline (pandas, blocking LLM HTTP calls) | Turns run on an explicit, bounded `ThreadPoolExecutor`; the event loop stays free for polling | Flask is sync everywhere, which would also work, but has no upside here |
| Streaming uploads with a size cap | `UploadFile` plus chunked reads | Same for Starlette/Flask, with more code |

Django is far more than this needs (ORM, admin, auth) and would bring a second persistence story. Plain Starlette would work but means hand-writing the validation FastAPI gives.

Costs, stated plainly:

- **New dependencies:** `fastapi`, `uvicorn`, `python-multipart` (Pydantic is already installed transitively). `requirements.txt` carries a comment that says FastAPI is not yet used; that comment gets updated.
- **One worker.** Session registry, per-session locks and pending confirmations are in process memory. Running several uvicorn workers would split a session across processes and lose confirmations. Scaling out needs a shared store and a queue; that is architecture Section 20's direction and is out of scope.
- **Python version.** `pyproject.toml` requires 3.13 and cloud sessions run 3.11 (backlog item 6). FastAPI works on both, so this design doesn't depend on that decision, but PR-2's CI and live runs do run on whichever version is settled.
- **Threads, not processes.** A Python thread can't be killed. A hung LLM call keeps its thread until the provider's own timeout. The deadline in Section 4 protects the *user and the state*, not the thread. `buddy/llm.py` contains no `timeout` setting (a grep finds none), so the providers fall back to the SDK defaults; PR-1 must set an explicit request timeout.

---

## 3. Sessions, state and output folders

### 3.1 What persists, and where

Following architecture Sections 6 and 7 (`workspace/uploads|outputs|sessions`), with `workspace_id = session_id` in v1 (one workspace per session, no sharing). The data root is configurable (`WORKBUDDY_DATA_DIR`, default `workspace/`) and **absolute** after startup, because the skills' legacy default path is relative to the current working directory (`skills/excel/dedupe.py:167`).

```
workspace/
├── uploads/{session_id}/{file_id}/{sanitized_name}       written once, never modified
├── outputs/{session_id}/{turn_id}/…                      one folder per turn
└── sessions/{session_id}/
    ├── session.json                                      Context snapshot + file registry
    └── turns/{turn_id}.json                              the finished turn record
```

Each upload gets its own directory, so two uploads with the same name can't collide, and the stored file keeps its (sanitized) name so replies mention the real name.

### 3.2 Live state vs. durable state

- **Live:** a `SessionRecord` in memory: the `BuddyState`, a lock, the turn list, the pending confirmation. It holds everything the pipeline needs between turns.
- **Durable:** after each finished turn the API writes `turns/{turn_id}.json` and rewrites `session.json` (write to a temp file, then `os.replace`, so a crash can't leave a half-written file). On restart the API rebuilds `SessionRecord`s lazily from disk on first access.

What `BuddyState` carries across turns is `Context`: `uploaded_files`, `active_file`, `mode`, `analyzed_files` (files proactive analysis has already been verified on), `metadata`, and the conversation and execution history. Everything else on `BuddyState` is per-turn and cleared by `reset_transient()`.

`Context` has no serializer today: `snapshot()` omits `analyzed_files` and both histories, and the histories are private. PR-1 adds `Context.to_dict()` / `Context.from_dict()` (using the public `append_conversation_turn` and `mark_analyzed`), with a round-trip test. The restored `Context` replays history with its original timestamps passed through an injected clock.

Dropping `analyzed_files` on restart would make the pipeline re-queue proactive analysis for a file already analyzed, so it is part of the snapshot, not an optimization.

### 3.3 The turn procedure

For each turn, inside the session's lock:

1. Create `outputs/{session}/{turn_id}/` with `mkdir(exist_ok=False)`. `turn_id` is sequential (`t0001`, `t0002`, …), assigned under the lock. An existing folder means something is wrong; the turn is refused with an internal error rather than reusing it.
2. Record each upload's current hash comparison baseline (Section 5.2).
3. Take a **deep copy** of the session `BuddyState`. All work happens on the copy.
4. On the copy: `reset_transient()`, set `user_message`, apply `mode` and active file, set the turn's output folder (Section 3.4), `context.record_user_message(message)`.
5. Build the engine from the engine factory (Section 3.5) and call `engine.run(copy)` on a worker thread.
6. On a normal finish (including a pipeline failure reply): `context.record_buddy_reply(state.reply)`, then **commit** the copy as the session's `BuddyState`.
7. On a timeout, or if the engine raises despite the PR-1 change, **discard** the copy, keeping the pre-turn state, and record only the user message and the fallback reply in the conversation history.

Why copy-then-commit: the architecture requires that "a failure in one turn never corrupts the session's accumulated `short_memory` or `metadata`" (Section 17). `Context` is mutated *during* the turn (`mark_analyzed`, `switch_active_file`), so without a checkpoint a crash halfway through leaves a session that believes a file was analyzed when its analysis was never verified. It also makes the deadline safe: a thread still running after the deadline writes to a copy nobody reads.

**Recording the conversation changes pipeline input.** Brain and Think both feed `recent_conversation` into their prompts (`buddy/brain.py:169`, `buddy/think.py:199`), and nothing calls `record_user_message` or `record_buddy_reply` today. Wiring them in gives the model multi-turn context it doesn't have in the single-shot scripts, and it changes `PersonalityVoice`'s phrase seed (`buddy_reply_count()`), closing backlog L2. I expect this to be an improvement, but it is **a behavior change to classification, unmeasured**. PR-2's live runs must include a follow-up turn ("now remove the empty rows too") to check it, and the independent review should look at whether prior replies in the prompt can push Brain toward a wrong intent.

### 3.4 Per-turn output folders (F5)

Today skills build `workspace/outputs/{workspace_id}/{stem}_deduplicated_{task_id}.xlsx` with `task_id = "{session_id}-task-{index}"` (`buddy/hands.py:589`). Two turns in one session therefore write the same name for the same step, and the second overwrites a file already handed to the user.

**Decision:** make the output folder an explicit input, not something encoded in a name.

- `SkillTask` gains `output_dir: str | None = None`. Each skill's `_build_output_path` uses `Path(task.output_dir) / f"{stem}_{op}_{task_id}.xlsx"` when set, and the legacy path when not (so existing callers and tests are unchanged).
- `BuddyState` gains `turn_output_dir: str | None = None`, cleared by `reset_transient()`. `Hands._build_task` copies it onto each `SkillTask`.
- The API always sets it, to the absolute turn folder from Section 3.3.

Why not the alternatives:

| Option | Why not |
|---|---|
| Encode the turn in `task_id` only (`{session}-{turn}-task-{n}`) | Fixes collisions but leaves every turn's files in one flat folder. Names then carry the guarantee, and a later skill that ignores the convention breaks it silently |
| Use a per-turn `workspace_id` such as `{session}/{turn}` | No skill change, but `workspace_id` is the security boundary (`BUDDY_SKILLS.md` Section 12) and would be overloaded; `Context` and `Eyes` would also report a different workspace every turn |
| Delete the old output before writing | Destroys something the user may still be downloading |

The guarantee, then, is structural: a turn can only write into a folder that didn't exist when the turn began, and that folder's name is never reused. Belt and braces: after a turn the API marks delivered files read-only (`0o444`; this does nothing for a root process, so it is a mitigation, not the guarantee).

Intermediate chain files also live in the turn's folder. They are never listed (Section 5.3), and they can't collide with anything later.

### 3.5 Engine construction

The API receives an `engine_factory: Callable[[], Engine]` at `create_app` time. Production passes a function that calls `build_engine(llm_provider=provider, speaker=PersonalityVoice(provider), confirmation_provider=<per-turn>)`; tests pass one that uses a fake provider. The engine is built **per turn**. The objects are cheap to construct, and it avoids depending on every stage being stateless, which I haven't verified (for example whether `ClaudePlanner` or `PersonalityVoice` hold per-call state).

`build_engine` gains an optional `confirmation_provider` argument (default `NullConfirmationProvider`, so nothing else changes). Because the confirmation provider must know which turn it is serving, the factory takes the per-turn provider: `engine_factory(confirmation_provider) -> Engine`.

The LLM provider is built once at startup from the environment (`GROQ_API_KEY`, else `ANTHROPIC_API_KEY`). If neither is set the server still starts, and `/healthz` reports `llm_configured: false`; a turn then fails like any other failure (Section 4) instead of the process refusing to boot.

---

## 4. Errors: every failure still ends in an honest reply (R6)

### 4.1 The gap today

`Engine.run` logs a stage exception and **re-raises** (`core/engine.py:116-122`), so a verifier or skill that raises ends the turn with no `state.reply`; `tests/core/test_engine.py:91` asserts that re-raise. That contradicts architecture Section 17 ("control passes directly to Speak to deliver an honest, plain explanation"). If the API just let it propagate, the user would get a bare 500. If the API wrapped it with its own sentence, rule 1's "the reply text comes only from the pipeline" would be broken at the boundary. So the fix belongs in the pipeline, and the API needs only a last-resort layer.

### 4.2 Layer 1: the engine turns a stage failure into a reply (PR-1)

- `BuddyState` gains `failure: StageFailure | None` (cleared by `reset_transient()`), where `StageFailure` is `(stage: str, error_type: str)`. **No message and no traceback**, because exception text from an LLM or pandas error can contain file contents or request fragments.
- `Engine.run`: when a stage raises, log it (as now, with the traceback, server side only), set `state.failure`, skip the remaining stages *except* Voice, and run Voice.
- Voice, seeing `state.failure`, renders a fixed failure reply from code and never calls the model. Its content is limited to facts the code has: which stage, by a closed label map ("while checking the result", "while running the step"), that it can't confirm anything was done, and that no file from this turn is being handed back. The last statement is true by construction because the API withholds every file of a failed turn (Section 5.3).
- `_verified_delivery` (Voice) and `build_outcome_report` must treat a non-`None` `failure` as "not a verified delivery", whatever `execution_result` and `verification_result` hold. A test makes `Inspector` raise *after* Hands produced a real output, and asserts the reply claims nothing done.
- The existing engine test changes from "re-raises" to "sets `failure`, still runs Voice". This changes a documented contract, so it is called out for review.

### 4.3 Layer 2: the API's last resort

Layer 1 doesn't help if Voice itself raises, if the failing stage *is* Voice, or if the turn hangs. The API catches that case:

- `except Exception` around `engine.run`. If `state.reply` is empty afterwards: `reply = FALLBACK_PIPELINE_ERROR`, `reply_source = "pipeline_fallback"`, `outcome = "failed"`.
- **Deadline:** each turn has a wall-clock limit (default 180 s, excluding time spent `awaiting_confirmation`). When it passes, the API finalizes the turn as `failed` with the fallback reply, **discards the state copy**, and releases no files. The thread may still finish later; its result is ignored, because finalization is one-shot (guarded by the turn's lock and a `finished` flag).
- `FALLBACK_PIPELINE_ERROR` and `FALLBACK_INTEGRITY_FAILURE` (Section 5.2) are constants in `buddy/voice_catalog.py`, reviewed like the phrase catalog, not literals in the API. Example text for the first: "Something went wrong while I was working on this, so I can't confirm that anything was done. I haven't handed back any file from this turn." It has no model call and no per-request content, so it cannot carry a false claim about the work.

### 4.4 Non-turn errors

A uniform body for every non-2xx: `{ "error": { "code": "file_too_large", "message": "That file is larger than 25 MB." } }`. Codes are a closed enum; messages are fixed strings. Handlers exist for `HTTPException`, `RequestValidationError` and a catch-all for unhandled exceptions, which returns `500 internal_error` with a fixed message and logs the traceback server side only. **A request that never reached a turn gets an error body, not a reply**: no turn is created and nothing is claimed about work.

### 4.5 Partial files

A failed or undelivered turn can leave intermediate files in its folder (backlog R6). They are never listed and never downloadable (Section 5.3), and the folder is never reused (Section 3.4), so they can't be mistaken for a delivery or overwritten into one. They stay for debugging and are removed by the retention sweep (Section 9). The API does not delete them eagerly and says nothing about them to the user: a statement like "I cleaned up" would be a claim that has to be verified, and there's nothing the user needs to know.

---

## 5. Rule 1 and rule 2 at the API boundary

### 5.1 Rule 1: never claim an outcome that didn't happen

Invariants, each with a test (Section 7):

1. `turn.reply` equals the `state.reply` the pipeline produced, byte for byte, or one of the two fallback constants. The API never concatenates, trims, reformats, templates or appends to it. A test spies on the engine's final state and compares.
2. `reply_source` tells the frontend which it is; the frontend shows `reply` as given.
3. `outcome`, `delivered_files` and `detail` are derived from structured data (Section 5.3), never parsed out of reply text, and never the other way around.
4. `detail` excludes Skill-authored prose and exception text (Section 1.3.1).
5. The confirmation `prompt` describes a proposed action, from a fixed template plus the file name; it states no result.
6. `error.message` is about a rejected *request* only.
7. A turn is `finished` exactly once, and `reply`, `outcome` and the file list are written together in that one step, so a client can never see a reply without its matching outcome.

What the API can't prevent: a frontend that composes its own "Done!" banner from `outcome == "delivered"`. The API contract says the frontend must show `reply` and treat `outcome` as a hint for layout only; that obligation moves to the frontend design and its review.

### 5.2 Rule 2: never modify the user's original

1. **Write once.** An upload is written to `uploads/{session}/{file_id}/{name}`, hashed while streaming, and set read-only (`0o444`, directory `0o555`). The API never opens an upload for writing again.
2. **No route writes to uploads.** There is no replace, rename or delete-file endpoint in v1.
3. **Runtime check (new; today it is only checked by hand in live runs).** Before each turn the API records the expected sha256 of every upload in the session (the upload-time hashes). After the turn, before finalizing, it re-hashes them. If any differs: `outcome = "integrity_failure"`, `reply = FALLBACK_INTEGRITY_FAILURE` ("I can't vouch for this result: your original file changed while I was working. I've held back every file from this turn."), no files released, and an error is logged at `CRITICAL`. This is an independent check of the property rather than trusting the skills, and it also covers the "verify rule 2 at runtime" item deferred in backlog 1 at the boundary level. The Hands-level hash in that item remains open.
4. File permissions are defense in depth only: a process running as root ignores them (the cloud container does), so the hash comparison is the actual guard.

### 5.3 Downloads serve only delivered files

One function, `delivered_files(turn) -> list[DeliveredFile]`, is the only way a file ever reaches a response. A file is delivered iff **all** hold:

1. the turn finished normally: `state.failure is None`, no timeout, integrity check passed;
2. `execution_result.outputs` names it (the pipeline's own list of what it hands back: "Only the final file is handed back");
3. `verification_result` exists and `passed` is true;
4. its real path (after `resolve()`) is inside `outputs/{session}/{turn_id}/`, is a regular file (not a symlink), exists, and is non-empty.

Its `sha256` is recorded at that moment. Downloads re-check existence and hash and return `410` on mismatch. The delivered-file id (`d_{turn}_{n}`) is a key into the turn's record, never a path, so nothing the client sends is ever turned into a filesystem path. Uploads, intermediate files, and files from crashed, timed-out, failed-verification or integrity-failed turns are not delivered and therefore return `404`.

One assumption to confirm in PR-1: that `ExecutionResult.outputs` is empty when a chain stops on a failed step ("then nothing is handed back", `CLAUDE.md`). If it isn't, rule 3 above still blocks release through `verification_result.passed`, but the test suite must cover the stopped-chain case explicitly.

---

## 6. Security basics

### 6.1 Access and exposure

- **No authentication in v1** (decision needed, Section 9). The session id is a 128-bit bearer secret; requests for someone else's session look identical to requests for a session that doesn't exist (`404`). This is adequate for a local or single-user deployment and **not adequate for a public one**: anyone holding a session id can read and act on that session, and there's no rate limit per identity. Until auth exists, bind to `127.0.0.1` by default and put a reverse proxy with its own auth in front for anything else.
- **CORS:** an explicit origin allowlist from configuration (`WORKBUDDY_CORS_ORIGINS`); no `*`.
- **Headers:** `X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on every response.

### 6.2 Uploads

| Control | Setting |
|---|---|
| Max size | 25 MB by default (`WORKBUDDY_MAX_UPLOAD_BYTES`), enforced **while streaming** in chunks, not from `Content-Length`; the partial file is deleted on abort. `413` |
| Extension allowlist | `.xlsx`, `.xls`, derived from the suffixes the skills accept (`buddy/hands.py:_SPREADSHEET_SUFFIXES`). `.xls` needs `xlrd`, which `CLAUDE.md` says isn't installed, so `.xls` is accepted only if `xlrd` imports at startup. `415` |
| Content sniff | `.xlsx` must start with `PK\x03\x04`, be a valid ZIP, and contain `[Content_Types].xml`; `.xls` must start with `D0 CF 11 E0 A1 B1 1A E1`. A `.txt` renamed `.xlsx` gets `422` |
| Zip bomb guard | Sum of declared uncompressed sizes ≤ 200 MB and ≤ 1,000 entries, read from the ZIP directory without extracting. `422` |
| No parsing at upload | The API never calls `openpyxl`/`pandas` on an upload. Parsing untrusted content is the skill's job (architecture Section 19: "untrusted until validated by the Skill"), inside a turn, where failures become honest replies |
| Message length | 4,000 characters |
| Body size | A global request-body cap slightly above the upload cap, for every route |

### 6.3 Path traversal

- The client never supplies a path. Ids are server-generated and matched against `^[A-Za-z0-9_-]{1,64}$` before any lookup; lookups go through the session's registries (a dict keyed by id), not through string concatenation into a path.
- File names are **sanitized**, not trusted: take the last path component (splitting on both `/` and `\`), Unicode-normalize (NFKC), drop control characters and bidirectional override characters, replace anything outside `[A-Za-z0-9._ -]` with `_`, collapse dots at the start, cap at 100 characters, refuse an empty result. The sanitized name is what's stored, shown in replies and sent in `Content-Disposition`.
- Every path the API opens for reading is checked with `Path.resolve()` and `is_relative_to(data_root/…)`, and symlinks are refused (`is_symlink()` on the file and its parents under the root).
- The session's folders are created by the server with `mode=0o700`.

### 6.4 Secrets and logs

- Keys come from the environment only (`GROQ_API_KEY`, else `ANTHROPIC_API_KEY`). The API never reads them for any purpose except constructing the provider, never echoes them, never includes them, or any prefix or length of them, in a response, and exposes only `llm_configured: bool`.
- Error bodies use fixed strings; `str(exc)` never reaches a response. Tracebacks go to server logs only.
- **Log hygiene:** a logging filter redacts by pattern (`gsk_…`, `sk-ant-…`, `Bearer …`, `api_key=…`), which doesn't require reading the key's value. The API logs session and turn ids and message *length*, not message text, and file *names* at most at `DEBUG`. The existing `Engine` logs `session_id` only, which is fine.
- Tests use obvious placeholder keys, as `CLAUDE.md` requires, and assert that the placeholder never appears in any response body or captured log, including on the error paths.

### 6.5 Abuse and limits

- One in-flight turn per session (`409`), and a global cap on concurrent turns (default 2; `429 server_busy`). The cap exists because the free Groq tier rate-limits and because each turn is CPU- and memory-heavy (pandas, profiling).
- A per-session cap on uploads (default 20) and total upload bytes.
- Retention (TTL sweep of old sessions, uploads and outputs) is deferred but listed in Section 9; without it disk use grows without bound, so it is required before any shared deployment.

---

## 7. Testing

### 7.1 Test structure

Tests live in `tests/backend/` and run with the normal `python -m pytest -q`. All LLM calls are faked, using the queued fake-provider pattern in `tests/buddy/test_engine_factory.py` (`_QueuedLLMProvider`, `_chain_replies`, the `.xlsx` fixtures). `create_app(settings, engine_factory)` takes the fake engine factory, and uses a temporary data root (`tmp_path`).

Turns run on a real worker thread so the confirmation flow is tested as it runs in production; a helper polls `GET .../turns/{id}` until `finished`, with a timeout that fails the test rather than hanging it.

### 7.2 Unit tests

- `uploads`: name sanitization (table of hostile names: `../../x.xlsx`, `a\b.xlsx`, NUL, RTL override, 300 characters, empty, only dots, Windows device names); size cap with and without `Content-Length`; type and sniff matrix; zip-bomb rejection; the partial file is removed on abort.
- `delivered_files`: a table over (failure set / not, outputs present / not, verified / not, path inside / outside the turn folder, symlink, missing, empty file) asserting only the all-true row delivers.
- `Context.to_dict/from_dict` round trip, including `analyzed_files` and both histories.
- `SessionStore`: atomic write; restore after a simulated restart; a corrupt `session.json` yields `404`/a clean error, not a crash.
- `WebConfirmationProvider`: approve, reject, timeout (→ reject), shutdown (→ reject), double answer, stale id.
- Engine (PR-1): a stage raising sets `failure`, runs Voice, produces the failure reply, and `_verified_delivery` is false for it; Inspector raising after a real output still yields a non-claiming reply.
- Log filter: each redaction pattern.
- Confirmation-prompt templates: one per registered capability.

### 7.3 End-to-end through the API (fake LLM, real pipeline)

1. **Happy path:** create session, upload, turn (approve the confirmation), poll, download. The downloaded bytes equal the file on disk; the reply equals the pipeline's `state.reply` (engine factory spy).
2. **F5:** two turns in one session, the second of the same kind as the first. The file paths differ, are in different folders, and the first delivered file's hash is unchanged after the second turn. Also a chain turn followed by a single-step turn.
3. **R6, stage raises:** an engine factory whose Inspector raises. The turn is `finished`, `outcome == "failed"`, `reply` is non-empty and states nothing was confirmed, `delivered_files == []`, the original's hash is unchanged, and any intermediate file returns `404` on download.
4. **R6, Voice raises and R6, deadline:** `reply_source == "pipeline_fallback"`, the session state is the pre-turn state, and a late-finishing worker can't change the finished turn.
5. **Confirm, both answers:** approve delivers a file; reject delivers none, the reply says the step wasn't done, and the original is unchanged. Also timeout, stale id, wrong session.
6. **Rule 2:** a test skill that writes to the original; the turn is `integrity_failure`, nothing is released, and the reply is `FALLBACK_INTEGRITY_FAILURE`. Plus the normal run leaves the upload's hash unchanged.
7. **Rule 1, property-style:** a generated set of scenarios (delivered, nothing to remove, declined, failed verification, clarification, unclear intent, crash): in every one `reply` equals the pipeline's or a fallback constant, `outcome` agrees with the file list, and `detail` contains no Skill summary text.
8. **Isolation:** session B gets `404` for A's session, files, turns and downloads; uploads can't be downloaded; `../` and encoded variants in ids return `404`/`422`, never a file.
9. **Concurrency:** a second turn or an upload during a turn returns `409`; the global cap returns `429`.
10. **Multi-turn context:** the second turn's Brain prompt contains the first turn's messages (the queued fake records its prompts).
11. **Secrets:** a placeholder key never appears in any response body or captured log, including `500`, validation-error and fallback paths.
12. **Restart:** build a new app on the same data root; session, turn history and a prior delivered file's download still work.

### 7.4 What the existing suites must keep passing

`python -m pytest -q` (about 150 s, use a timeout of at least 5 minutes) must stay green; the only intentional test changes outside `tests/backend/` are the engine re-raise test and any test that asserts a literal output path (the legacy path stays the default, so none is expected). `ruff check buddy tests backend` must show no new errors.

### 7.5 Live runs (Groq, required by `CLAUDE.md` before merging)

Fake-LLM tests can't show that the real model's output survives the new multi-turn context or the real timing of confirmations, so live runs are required for **PR-1** and **PR-2/3** (the API carries the rule-touching boundary). For each run, report what `CLAUDE.md` lists: the intent, `clarification_required`, the steps executed, whether a file was delivered, an independent check of the file, the reply, whether it makes any false claim, and the original's hash before and after. Runs go through the real HTTP server, not `scripts/run_dedupe_manually.py`. Stop and report on the first `429`, with the count completed.

| # | Through the API | Shows |
|---|---|---|
| 1 | Upload the fixture, "remove duplicate rows", approve | The happy path end to end; the delivered file checks out; the original is unchanged |
| 2 | Same session, second turn "remove the empty rows" on the same upload | F5 with the real pipeline: run 1's file is byte-identical afterwards; run 2 lands in a different folder; multi-turn context |
| 3 | "Remove the empty rows and then the duplicates", approve each | A chain through the API: one final file; per-step confirmations |
| 4 | "Remove duplicate rows", **reject** | Reject with the real model: no file, and the reply doesn't say it was done |
| 5 | A greeting ("hi, what can you do?") | A non-delivering turn; no file, no claim |
| 6 | `no-duplicates` fixture, "remove duplicate rows" | N2 through the API: no "removed N" claim |
| 7 | Follow-up "now remove the empty rows too" in the session of run 1 | The multi-turn prompt change from Section 3.3 doesn't degrade classification |
| 8 | Kill the server mid-turn, restart, `GET` the session | Restart behavior: the interrupted turn is reported as failed, not missing |

Run 8 has a design consequence: a turn that was `running` when the process died has no record of finishing. On startup the store marks any turn without a finished record as `failed` with the fallback reply and no files (the restore path in PR-2), so a reconnecting user isn't left polling forever.

R6 is not live-testable with a real provider without test hooks in production code, which this design deliberately doesn't add. It is covered by the fake-LLM tests in 7.3 (items 3 and 4).

---

## 8. Files, size, and PR split

Sizes are rough estimates, not measurements; I haven't prototyped any of this.

### 8.1 New files (`backend/`, currently just `.gitkeep`)

| File | Purpose | Est. lines |
|---|---|---|
| `backend/__init__.py`, `backend/main.py` | `create_app(settings, engine_factory)`, router wiring, uvicorn entry | 90 |
| `backend/settings.py` | env-driven settings (data root, limits, CORS, deadlines), validated once at startup | 70 |
| `backend/schemas.py` | Pydantic request/response models, error enum | 160 |
| `backend/errors.py` | error codes, exception handlers, fixed messages | 90 |
| `backend/sessions.py` | `SessionRecord`, registry, per-session lock, state copy/commit | 200 |
| `backend/store.py` | atomic persistence, lazy restore, interrupted-turn repair | 170 |
| `backend/uploads.py` | streaming write, limits, sniffing, sanitization | 170 |
| `backend/turns.py` | turn procedure, executor, deadline, finalization, outcome derivation | 260 |
| `backend/delivery.py` | `delivered_files`, hashing, integrity check | 110 |
| `backend/confirmation.py` | `WebConfirmationProvider`, prompt templates | 130 |
| `backend/routes.py` | the endpoints | 200 |
| `backend/logging_filter.py` | redaction filter | 40 |
| **Backend total** | | **~1,700** |

Tests (`tests/backend/`): about 1,800 lines across the 7.2 and 7.3 lists.

### 8.2 Changes to existing files (PR-1)

| File | Change | Est. lines |
|---|---|---|
| `core/engine.py` | stage failure sets `state.failure`, Voice still runs | +35 |
| `buddy/state.py` | `failure` and `turn_output_dir` fields, reset | +20 |
| `buddy/voice.py`, `buddy/outcome.py` | `failure` means "not a verified delivery"; failure reply | +50 |
| `buddy/voice_catalog.py` | `FALLBACK_PIPELINE_ERROR`, `FALLBACK_INTEGRITY_FAILURE`, stage-label map | +30 |
| `buddy/hands.py` | copy `turn_output_dir` onto the task | +5 |
| `skills/base.py`, `skills/excel/dedupe.py`, `remove_empty_rows.py` | `SkillTask.output_dir`; use it when set | +30 |
| `buddy/context.py` | `to_dict` / `from_dict` | +60 |
| `buddy/engine_factory.py` | optional `confirmation_provider` argument | +5 |
| `buddy/llm.py` | request timeout, if not already set | +10 |
| `requirements.txt`, `pyproject.toml` | `fastapi`, `uvicorn`, `python-multipart`; fix the stale comment | +6 |
| `tests/core/test_engine.py` and new tests | updated contract, new cases | +250 |

### 8.3 Suggested PR split

Each PR on its own branch from `main` (per `CLAUDE.md`), merged only by the user.

1. **PR-1 `feat/pipeline-failure-and-output-dir`: core seams (rule-touching).** R6 in the engine and Voice, F5's `output_dir`, `Context` serialization, the fallback constants, LLM timeout, `build_engine`'s confirmation argument. No API code. Independent review required (it changes the engine's contract and the reply path); live runs with the existing script (a normal turn, plus two turns in one session showing distinct folders). Closes backlog R6 (pipeline half), F5 (skill half), L2's precondition.
2. **PR-2 `feat/backend-api-core`: sessions, uploads, turns, state, files, downloads (rule-touching).** Everything except real confirmation: a `NullConfirmationProvider`-equivalent approves automatically, **behind a setting that is off by default in production config** and on in tests, so the feature that's on when merged is not silently "approve everything". Includes persistence and restart repair, the delivery function, the integrity check, errors and security. Independent review required; live runs 1, 2, 5, 6, 7, 8.
3. **PR-3 `feat/backend-api-confirmation`: confirm/reject (rule-touching).** `WebConfirmationProvider`, the endpoint, prompt templates, the decline-path test that Section 1.5 says is missing. Independent review; live runs 3 and 4. After this, auto-approve is removed from the default configuration.
4. **PR-4 `feat/backend-retention` (optional, before any shared deployment):** TTL sweep, per-session quotas, per-identity limits when auth exists.

PR-2 and PR-3 could be one PR. I'd keep them apart because the confirmation flow is the riskiest concurrency code, and because it makes the review of "reply text comes only from the pipeline" independent of the threading review.

Backlog changes when these land: item 4 is closed by PR-1 through PR-3; backlog L2 (`turn_index`) is closed by PR-2; the "Verify rule 2 at runtime" bullet in item 1 is partially closed (boundary check in PR-2, Hands-level hash still open).

---

## 9. Open questions, risks and deferred items

### 9.1 Decisions the user needs to make

1. **Authentication.** None in v1, per Section 6.1. Is the first deployment local/single-user only? If it will be reachable by others, auth moves ahead of the frontend work.
2. **Confirmation policy.** Dedupe and empty-row removal are Write tier but only ever create a new file. Asking on every step will be noisy, particularly in a chain. Options: ask every step (what the architecture says and what this design implements), ask once per turn, or auto-approve skills that create new files and ask only for ones that could affect an original. That is a product decision that bears on rule 2's spirit, so I haven't chosen it for you; the design supports all three by changing only the provider.
3. **Accepted file types** beyond `.xlsx`/`.xls`, and whether `.xls` (needs `xlrd`) ships now.
4. **Upload size limit.** 25 MB is a guess; the pipeline's memory use on a large sheet isn't measured.
5. **Python version** (backlog item 6), needed before PR-2's CI and live runs are meaningful.

### 9.2 Risks

- **[Likely] Multi-turn context changes Brain and Think behavior.** It is the one change here that affects classification, and it's unmeasured (Section 3.3). Mitigation: live runs 2 and 7, and review attention.
- **[Likely] Threads can't be killed.** A hung provider call holds a worker until its own timeout; with a cap of 2 concurrent turns, two hung turns block the service (new turns get `429`). Mitigation: the LLM timeout in PR-1. A process-per-turn design would fix it and is out of scope.
- **[Possible] Confirmation `Event` waits hold a thread each** for up to 120 s. Fine at a cap of 2; wrong at scale.
- **[Possible] The deep copy of `BuddyState` may not copy cleanly** if a stage stores something uncopyable on it. PR-1 adds a test that copies a fully populated state; if it fails the fallback is a hand-written `Context` checkpoint via `to_dict`/`from_dict`.
- **[Certain] In-memory state means one worker, and a restart interrupts running turns.** Handled by repair on startup (Section 7.5, run 8), not prevented.
- **[Certain] The frontend can still misrepresent `outcome`** (Section 5.1). The API can't close that; the frontend's review has to.

### 9.3 Deferred

- Retention and quotas (PR-4); per-identity rate limits (need auth).
- Streaming progress events (SSE) in place of polling; the architecture's Event System (`core/events.py`) is still a stub.
- Multi-file turns (the pipeline has one active-file pointer).
- Deleting a session or an upload on request (a privacy feature; it needs care because delivered files depend on uploads' hashes).
- Horizontal scaling: shared store, queue and workers, per architecture Section 20.
- Hands-level runtime hash of the original (backlog item 1).

### 9.4 Evidence that would change this design

- If the review finds `Engine`'s failure contract can't change without breaking callers I haven't seen, Layer 2 (API catch plus fallback) becomes the only R6 mechanism, and the failure reply loses its stage detail.
- If live run 7 shows the conversation context degrading classification, the API stops recording conversation into `Context` for Brain/Think (one-line change) and multi-turn awareness is designed separately.
- If a measured large-sheet run exceeds the 180 s deadline routinely, the deadline and the concurrency cap need rework before the frontend ships, not after.
