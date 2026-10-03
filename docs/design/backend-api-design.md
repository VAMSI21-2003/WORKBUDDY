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

- **Approve**: `confirm()` returns `True` for the first Write-tier step, and the provider remembers the approval for the rest of the turn (it does not ask again).
- **Reject**: `confirm()` returns `False`; `Hands` already handles this by logging and skipping the step (`buddy/hands.py:256-268`). The reply then comes from the pipeline like any other turn. **What `Hands` and `Voice` do after a decline is not yet verified by any test I've read**; PR-3 must add one that shows the reply says the step wasn't done and no file is delivered for it.
- **Timeout or server shutdown**: treated as reject, never as approve. The turn's record notes `decision_source: "timeout"`.

**Policy (decided 2026-09-30, see "Decisions"): one confirmation per turn.** Before the first file-changing step, the user sees the whole plan (every step, in order, with the file name) and approves or rejects it once. Approval covers every step of that turn's plan; reject means no step runs. The object above is therefore the *plan* confirmation: it carries a `steps` list (`[{ "capability": "spreadsheet.deduplicate", "prompt": "Remove duplicate rows from report.xlsx and save the result as a new file." }, …]`, in execution order), and `capability`, `permission_tier` and `prompt` describe the first step for compatibility. The mechanism is in "Decisions", item 2.

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

- **Superseded by "Decisions (2026-09-30)", item 1: v1 ships a shared demo password.** The text below is the original analysis and still explains why per-session ids alone are not enough. **No authentication in v1** (original wording). The session id is a 128-bit bearer secret; requests for someone else's session look identical to requests for a session that doesn't exist (`404`). This is adequate for a local or single-user deployment and **not adequate for a public one**: anyone holding a session id can read and act on that session, and there's no rate limit per identity. Until auth exists, bind to `127.0.0.1` by default and put a reverse proxy with its own auth in front for anything else.
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
| `requirements.txt`, `pyproject.toml` | `fastapi`, `uvicorn`, `python-multipart`, `xlrd`; `requires-python = ">=3.11"`, black/ruff/mypy targets 3.11; fix the stale comment | +12 |
| `tests/core/test_engine.py` and new tests | updated contract, new cases | +250 |

### 8.3 Suggested PR split

Each PR on its own branch from `main` (per `CLAUDE.md`), merged only by the user.

1. **PR-1 `feat/pipeline-failure-and-output-dir`: core seams (rule-touching).** R6 in the engine and Voice, F5's `output_dir`, `Context` serialization, the fallback constants, LLM timeout, `build_engine`'s confirmation argument. No API code. Independent review required (it changes the engine's contract and the reply path); live runs with the existing script (a normal turn, plus two turns in one session showing distinct folders). Closes backlog R6 (pipeline half), F5 (skill half), L2's precondition.
2. **PR-2 `feat/backend-api-core`: sessions, uploads, turns, state, files, downloads (rule-touching).** Everything except real confirmation: a `NullConfirmationProvider`-equivalent approves automatically, **behind a setting that is off by default in production config** and on in tests, so the feature that's on when merged is not silently "approve everything". Includes persistence and restart repair, the delivery function, the integrity check, errors and security. Independent review required; live runs 1, 2, 5, 6, 7, 8.
3. **PR-3 `feat/backend-api-confirmation`: confirm/reject (rule-touching).** `WebConfirmationProvider`, the endpoint, prompt templates, the decline-path test that Section 1.5 says is missing. Independent review; live runs 3 and 4. After this, auto-approve is removed from the default configuration.
4. **PR-4 `feat/backend-retention` (REQUIRED before the public demo deployment, per "Decisions"):** TTL sweep, per-session quotas, per-IP rate limit, demo-password check, hosted-demo binding/CORS. The demo password and rate limit may be split out as PR-4a if PR-4 grows past a reviewable size; nothing is deployed until both are merged.

PR-2 and PR-3 could be one PR. I'd keep them apart because the confirmation flow is the riskiest concurrency code, and because it makes the review of "reply text comes only from the pipeline" independent of the threading review.

Backlog changes when these land: item 4 is closed by PR-1 through PR-3; backlog L2 (`turn_index`) is closed by PR-2; the "Verify rule 2 at runtime" bullet in item 1 is partially closed (boundary check in PR-2, Hands-level hash still open).

---

## 9. Open questions, risks and deferred items

### 9.1 Decisions the user needs to make

**Answered on 2026-09-30 for items 1, 2, 3 and 5; see "Decisions (2026-09-30)" at the end of this document. Item 4 (upload size) is still open.**

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

---

## Decisions (2026-09-30)

The user's answers to Section 9.1. Where this section conflicts with an earlier section, **this section wins**; Sections 1.5, 6.1 and 8 have been edited to point here.

### 1. First deployment is a public demo link

Anyone with the URL can reach it, so "no auth, bind to localhost" (Section 6.1) no longer holds. v1 needs the following, and **PR-4 is now required before deployment**.

| Need | Design |
|---|---|
| Shared demo password | One password from `WORKBUDDY_DEMO_PASSWORD`. Checked on **every** API route except `GET /healthz`, via a FastAPI dependency applied to the whole router (not per route, so a new route can't forget it). Sent as `Authorization: Bearer <password>` (or a header named `X-Demo-Password`; pick one in PR-4 and keep it out of URLs, so it never lands in access logs). Compared with `hmac.compare_digest`. Wrong or missing gives `401 unauthorized` with a fixed body. **If the variable is unset, the app refuses to start unless `WORKBUDDY_ENV=dev`**, so a deployment can't silently be open. The password is never logged, echoed or returned, and the log redaction filter (Section 6.4) gets a pattern for the header. |
| Per-IP rate limit | Token bucket per client IP, in memory (one worker, Section 0), applied before the password check so guessing is throttled too; failed-password attempts count double. Defaults: 60 requests/min/IP overall, 10 turns/hour/IP, both configurable. `429 rate_limited` with `Retry-After`. The client IP comes from the proxy's forwarded header **only** when `WORKBUDDY_TRUSTED_PROXY_HOPS` says how many proxies to trust; otherwise from the socket. Getting this wrong makes every user share one bucket or lets a caller spoof theirs, so PR-4 tests both. |
| Retention / TTL sweep | Background task deletes sessions, uploads and outputs idle past `WORKBUDDY_SESSION_TTL_HOURS` (default 24). Deletion is by session folder under the data root, with the same resolve/`is_relative_to`/no-symlink checks as Section 6.3. Never touches a session with a running turn. |
| Per-session quotas | Max uploads (default 20, already in Section 6.5), total upload bytes per session (default 100 MB), max turns per session (default 50), and a global cap on total bytes under the data root; when the global cap is hit, new sessions get `503 storage_full` rather than the sweep deleting live sessions. |
| Binding / CORS | Bind `0.0.0.0` only when `WORKBUDDY_ENV=production` (still `127.0.0.1` by default). `WORKBUDDY_CORS_ORIGINS` is set to the exact demo origin(s); if the frontend is served by the same app (the recommended deployment, see the frontend design) CORS can be empty. No `*`. Add HSTS and a CSP when serving the static frontend. Docs/OpenAPI (`/docs`, `/openapi.json`) are disabled in production or put behind the password. |

Honest limits of this design, so nobody over-reads it:
- **[Certain]** A shared password is one secret for everyone. Once it is posted somewhere, it is public. It deters casual traffic and bots; it is not access control. Rotate it by changing the variable and restarting (which also ends live turns, Section 9.2).
- **[Certain]** The demo spends the owner's Groq quota. The per-IP limit, the global concurrent-turn cap (Section 6.5) and the turn quotas are the cost control; there is no per-user billing.
- **[Likely]** Users will upload real spreadsheets to a public demo. The frontend must say uploads are deleted after the TTL and that this is a demo, and the sweep must be tested (PR-4), because a retention promise that isn't enforced is a false claim in the product's own voice.
- **[Possible]** In-memory per-IP buckets reset on restart and don't work with more than one worker; acceptable at this scale.

### 2. Confirmation policy: once per turn

Before the first file-changing step, the user sees the whole plan (every step, in order, with the file name) and approves or rejects it once. **Approval covers every step of that turn's plan; reject means no step runs.**

**What I verified about the existing code.** `Hands` calls `confirmation_provider.confirm(task, tier)` once per Write-tier step, mid-loop, and `confirm` receives only the current `SkillTask` (`buddy/hands.py:250-256`). The provider therefore does not see the plan today. READ-tier steps (`spreadsheet.analyze`) are not confirmed at all and stay that way, since they change no file. I have not read how `Hands` treats the steps *after* a declined one beyond the `continue` at the decline site, so "reject means no step runs" needs a test rather than an assumption (below).

**Mechanism (no change to `ConfirmationProvider`'s signature):**
1. The turn procedure (Section 3.3) hands the plan to the `WebConfirmationProvider` before `Hands` runs, from the Planner's output on `BuddyState`: an ordered list of `(capability, input_name)` for the Write-tier steps. The provider is per-turn, so it has no cross-turn state.
2. On the **first** `confirm()` call in the turn, the provider publishes one `pending_confirmation` whose `steps` lists the whole plan (Section 1.5), sets `awaiting_confirmation`, and blocks as before (timeout 120 s, timeout = reject).
3. **Approve:** it records the decision and returns `True` for that and every later `confirm()` in the same turn without publishing anything.
4. **Reject or timeout:** it records the decision, returns `False` for that and every later call. So even if `Hands` were to keep iterating after a decline, no later step can run. This is the guarantee, and it holds in the provider rather than depending on `Hands`' loop behavior.
5. If the plan contains no Write-tier step, no confirmation is requested and none is shown.

**The plan shown to the user must equal the plan that runs.** The steps list is built from the same Planner output `Hands` executes, not from the user's message. If `Hands` ever asks to confirm a step that isn't in the published plan (a planner/executor mismatch), the provider returns `False` and the turn's failure path reports it, rather than approving something the user never saw. That guard is a required test in PR-3.

**Tests added to PR-3:**
- a two-step chain asks exactly once, and both steps run on approve;
- reject: no step runs, no file is delivered, and the reply comes from the pipeline and does not claim any change (this also closes the "decline path is unverified" gap in Section 1.5);
- timeout behaves as reject;
- an unlisted step gets `False`;
- a plan with only a READ-tier step never enters `awaiting_confirmation`.

**Rule 2 note.** Every current skill only writes a new file, so a single up-front approval does not weaken rule 2. If a future skill can affect an original or overwrite something, it must not ride on a plan approval: it would need its own tier and its own confirmation, and the provider must ask again for it. PR-3 adds a test that a step whose tier is not in the published plan's tiers is refused.

Sections 1.5 and 8 are updated to match: the pending confirmation carries `steps`; the "asks once per step" sentence is replaced; the PR-3 description gains the tests above.

### 3. Python: 3.11 and newer

PR-1 changes:
- `pyproject.toml`: `requires-python = ">=3.11"`; `[tool.black] target-version = ["py311"]`; `[tool.ruff] target-version = "py311"`; `[tool.mypy] python_version = "3.11"`.
- **Audit for 3.12/3.13-only syntax and APIs first.** Not yet done: PR-1 must grep for `type X =` aliases, PEP 695 generics (`def f[T]`), `typing.override`, f-string nesting that relies on PEP 701, and stdlib additions after 3.11, and fix them or the "3.11+" claim is false. Because ruff's `UP` rules follow the target version, lowering it will also change what `ruff check` suggests; PR-1 must not add new lint errors (`CLAUDE.md`).
- CI (when it exists) and the live-run procedure run on 3.11 **and** 3.13, so "3.11 and newer" is tested at both ends rather than assumed. Cloud sessions run 3.11 (backlog item 6), so the suite on 3.11 is the immediately checkable half; the 3.13 half needs the user's machine or a CI matrix.
- Backlog item 6 is closed by this decision plus PR-1.

### 4. File types: `.xlsx` and `.xls`

- `.xls` is accepted. PR-1 adds `xlrd` to `requirements.txt` (`xlrd>=2.0.1`, which reads `.xls` only, and that is all it is needed for).
- Section 6.2's "accepted only if `xlrd` imports at startup" stays as a defence, but in the demo deployment a missing `xlrd` is a startup **failure**, not a silent downgrade to `.xlsx`-only, so the UI's list of accepted types can't disagree with the server.
- **The pipeline's `.xls` behavior is untested by me.** `CLAUDE.md` says `.xls` reading needs `xlrd`; I have not confirmed what the skills *write* for an `.xls` input (the design in Section 3.4 assumes `.xlsx` output names). PR-2/PR-3 need a fixture-based test that an `.xls` upload is deduplicated, that the delivered file's name and format are what the reply says, and that the original `.xls` is byte-identical afterwards (rule 2). Until that passes, `.xls` support is not claimed in the frontend.
- Content sniffing for `.xls` is already specified in Section 6.2 (OLE2 header). Note that OLE2 is also the header for `.doc`/`.ppt`; the sniff proves "OLE2", not "spreadsheet", so a non-spreadsheet OLE2 file passes the API check and fails inside the skill, where it becomes an honest failure reply (R6).

### Consequences for the PR split (Section 8.3)

- PR-1 gains: Python 3.11 floor and audit, `xlrd`.
- PR-3 gains: the once-per-turn plan confirmation and its tests.
- PR-4 is required before deployment and gains the demo password, per-IP rate limit and hosted-demo binding/CORS.
- **Still open:** upload size limit (9.1 item 4), which the public demo makes more pressing: 25 MB times the per-IP rate is the memory and disk exposure, and it is unmeasured.

---

## Decisions (2026-09-30, part 2)

Resolves the conflicts and asks raised by the frontend design (`docs/design/frontend-design.md` on `design/frontend`, Sections 12 and 14) and the upload size left open in part 1. The rules in items 1 and 2 are the user's; the numbers in item 1 and the limit in item 3 are this design's recommendation, with the evidence given. Where this section conflicts with an earlier section, **this section wins**. Earlier text is not edited, so Section 6.2's "25 MB" and Section 9.1's "item 4 is still open" are superseded here rather than rewritten.

Nothing in part 1 is reversed. One wording is refined at the user's direction: part 1 item 1 said "60 requests/min/IP **overall**". The number stays; polls of an existing turn move to their own allowance (item 1 below).

### 1. Polling vs the per-IP rate limit

**The conflict (frontend Section 14):** the UI polls `GET .../turns/{turn_id}` every 1 s (2 s after 20 s). Under one 60/min bucket, one tab polling one turn uses the whole budget, and the next upload or confirmation is refused.

**Rule.** Every request is charged to exactly one of these per-IP token buckets (in memory, one worker, as in part 1), chosen by method, route template and whether the password is valid:

| Bucket | Charged for | Burst | Refill | Status |
|---|---|---|---|---|
| `general` | every `/api/v1` request that isn't a valid poll (below): `POST /sessions`, `GET /sessions/{id}`, `GET .../files`, downloads, and the mutating routes | 60 | 1/s (60/min) | number unchanged from part 1 |
| `poll` | `GET /api/v1/sessions/{sid}/turns/{tid}` **with a valid password** | 30 | 3/s (180/min) | new |
| `turns` | `POST .../turns`, *in addition to* `general` | 10 | 10/hour | unchanged from part 1 |
| `uploads` | `POST .../files`, *in addition to* `general` | 5 | 20/hour | new, sized in item 3 |
| `healthz` | `GET /healthz` (no password) | 10 | 1/s | new; keeps a host's health probe and users from starving each other |

1. **Polls of an existing turn get their own allowance.** A poll with a valid password takes one token from `poll` only. If the response is anything other than `200` (unknown session or turn, malformed id), one token is **also** taken from `general` after the response (clamped at zero). So the higher allowance serves turns that exist, and probing ids costs the strict rate. No session lookup happens before the password check to choose the bucket.
2. **Mutating requests are counted strictly.** `POST .../files`, `POST .../turns` and `POST .../confirmation` (and `POST /sessions`) take a token from `general`, plus `turns` or `uploads` where listed, **whatever the response**: a `409`, `413`, `415` or `422` still counts. All required buckets must have a token, or the request is refused before the handler runs.
3. **Password failures still count double.** A request whose password is missing or wrong takes **2** tokens from `general`, on every route **including the poll route**, so the poll allowance can't be used to guess the password. The limiter decides "valid" with the same function the auth dependency uses (one function, `hmac.compare_digest`), so the two can't disagree.
4. **A request refused with `429` consumes nothing.** That keeps `Retry-After` exact (item 2, ask 6).

**Why these numbers.**
- [Certain] The global cap on unfinished turns is 2 (Section 6.5), and a turn awaiting confirmation counts as unfinished. So no IP can legitimately have more than two turns to poll, and the frontend polls at most 1/s per turn: 2 polls/s is the legitimate maximum per IP. The `poll` refill is 3/s to leave margin for retries and clock jitter; burst 30 absorbs a reload or the "What happened" fetches.
- [Certain] The longest a turn can be polled is 180 s deadline + 120 s confirmation window = 300 s. At the frontend's cadence that is 20 + 140 = 160 polls, well inside `poll`.
- **Startup check:** the app refuses to start if `poll` refill per second is below the global concurrent-turn cap. Raising the cap without raising the poll allowance would bring the conflict back.
- A normal turn's non-poll traffic (turn, confirmation, a download, a file-list refresh) is under 10 `general` tokens.
- [Certain] Users behind one IP (an office, a school NAT) share every bucket, including 10 turns/hour. That limit is unchanged from part 1 and is the price of having no per-user identity.

**Not changed:** SSE stays deferred. With a separate poll allowance the conflict is resolved without it.

**Tested in PR-4** (injected clock, no sleeps):
1. One turn polled at the frontend's cadence for the full 300 s, plus upload, turn, confirmation and download from the same IP: no `429`.
2. Two such turns at once from one IP: no `429`. A third poll stream at 1 s eventually gets `429 rate_limited`, and while it does, a `general` request from the same IP still succeeds (the buckets are independent).
3. 300 valid polls don't reduce `general`.
4. Strict counting: uploads refused with `413`/`415`/`422`/`409` and turns refused with `409`/`422` each consume `general` (and `uploads`/`turns`); the 61st `general` request in a burst gets `429`; the 11th turn in an hour and the 6th upload in a burst get `429`.
5. Double count: wrong-password requests take 2 tokens on every route; 30 wrong-password polls exhaust `general`, and the `poll` bucket is untouched.
6. A poll of an unknown turn or session takes a token from `general` as well.
7. A `429` consumes nothing, and after advancing the clock by `Retry-After` the same request is accepted.
8. `/healthz` traffic doesn't touch `general`, and vice versa.
9. The startup check refuses a poll refill below the concurrent-turn cap.
10. `WORKBUDDY_TRUSTED_PROXY_HOPS` (part 1): with it unset, a spoofed `X-Forwarded-For` doesn't change the bucket; with it set, two clients behind the proxy get separate buckets.

**For the frontend design (not decided here):** a `429` on a poll is a refused request, not a lost connection. It should wait `Retry-After` and not count toward the "5 consecutive failures → I don't know how it ended" rule in frontend Section 1.6.

### 2. The frontend's asks 1, 2, 3, 4, 6 and 7 (Section 12): accepted

Ask 5 (download tokens, SSE) stays deferred, as the frontend design itself proposes.

**Ask 1: `pending_confirmation.steps`.** Already decided in part 1, item 2. Each entry is `{ "capability", "prompt" }`, in execution order, Write-tier steps only; `prompt` comes from the fixed per-capability template. The part 1 PR-3 tests (a two-step chain asks once; an unlisted step is refused) also assert that `steps` equals the Write-tier steps `Hands` executes, in the same order.

**Ask 2: limits and retention in the session response.** `POST /sessions` (`201`) and `GET /sessions/{id}` gain:

```json
{ "limits": { "max_upload_bytes": 5242880,
              "accepted_extensions": [".xlsx", ".xls"],
              "max_message_chars": 4000 },
  "retention_hours": 24 }
```

- Each value is read from the **same settings object** the enforcing code reads; nothing is a second copy. Tests: an upload of exactly `max_upload_bytes` is accepted and one byte more gets `413`; a message of `max_message_chars` is accepted and one more character gets `422`; every listed extension passes the type check and nothing unlisted does.
- `accepted_extensions` is the upload validator's allowlist itself. `.xls` is in it only in the PR that adds the `.xls` fixture test (part 1, item 4) and only if `xlrd` imports, so the UI can't advertise a type the server rejects or hasn't tested.
- **`retention_hours` is `null` until the retention sweep exists (PR-4).** The field is in the schema from PR-2 so the frontend's types don't change, but advertising "deleted after 24 hours" before anything deletes would be a false claim in the product's voice. PR-4 sets it from `WORKBUDDY_SESSION_TTL_HOURS`, the same value the sweep reads, and tests that a session idle for longer than `retention_hours` is gone after a sweep. (The frontend already shows no number when the value is missing.)
- The `413` message is built from the setting at startup ("That file is larger than 5 MB."), not a literal, so it can't disagree with the configured cap.

**Ask 3: a committed `openapi.json`.**
- Location: `backend/openapi.json`, generated by `python -m backend.export_openapi` (`app.openapi()` serialized with sorted keys, 2-space indent and a trailing newline).
- `tests/backend/test_openapi_sync.py` builds the app with test settings, regenerates the schema and compares it byte for byte with the committed file. On mismatch the failure message names the regeneration command. The frontend's build and CI generate types from this file.
- The schema must describe the **real** error bodies. FastAPI documents its own `HTTPValidationError` for `422` by default, which is not our `{ "error": { "code", "message" } }` body. Each route declares its error responses with the shared `ErrorBody` model, and a test walks the schema and fails if any 4xx/5xx response references anything else. `429` responses declare the `Retry-After` header.
- `fastapi` is pinned to a minor version in `requirements.txt`, so a library upgrade that changes the generated schema shows up as a deliberate diff, not CI noise.
- This is the repo copy. Serving `/openapi.json` in production is still off or behind the password (part 1, item 1).

**Ask 4: the auth header and the `401` body.** Decided: **`Authorization: Bearer <password>`**. `X-Demo-Password` is not accepted.
- Parsing: the scheme is matched case-insensitively (`Bearer`, per RFC 6750), and the credential is compared with `hmac.compare_digest` on UTF-8 bytes. Missing header, another scheme, an empty credential and a wrong password are all the same failure.
- Response: `401` with header `WWW-Authenticate: Bearer realm="workbuddy-demo"` and body `{ "error": { "code": "unauthorized", "message": "The demo password is missing or wrong." } }`. It's one body for every case, so a caller learns nothing about why it failed.
- Implemented with FastAPI's `HTTPBearer(auto_error=False)` and our own `401`, so the body is ours and not FastAPI's default. The scheme then appears in `openapi.json` as a `bearer` security scheme.
- The static frontend and `/healthz` are not behind it; every `/api/v1` route is (part 1).
- The log-redaction pattern for `Bearer …` from Section 6.4 covers it. A PR-4 test asserts the password never appears in a response or in captured logs, including on `401` and `429`.

**Ask 6: `Retry-After` on every `429`.** An integer number of seconds (never an HTTP date), at least 1.
- `rate_limited`: the exact wait until the refusing bucket (the longest, if several refuse) has enough tokens. Test 7 in item 1 checks it.
- `server_busy`: a fixed hint from `WORKBUDDY_BUSY_RETRY_AFTER_S` (default 10). It can't be exact, because turns finish unpredictably. It is "don't retry sooner", not a promise, and the error message doesn't promise a free slot either.
- If `WORKBUDDY_CORS_ORIGINS` is ever non-empty, `Access-Control-Expose-Headers: Retry-After` is sent. Otherwise a cross-origin page can't read the header. In the recommended same-origin deployment this doesn't arise.

**Ask 7: `reply_source` and `outcome` are always present on finished turns.** This was already the contract (Section 1.3.1: `finished` is the only state in which they are set; Section 5.1, invariant 7: set together in one step). What's new is that it's now enforced two ways:
- **Schema:** the turn model is a union discriminated on `status`. The `finished` variant has `reply: str`, `reply_source` and `outcome` as required, non-null enums. The JSON shape in Section 1.3.1 doesn't change (the other variants still carry those keys as `null`). [Likely] `openapi-typescript` renders this as a TypeScript union the frontend can narrow on. If it doesn't, the tests below are still the guarantee.
- **Tests (every way a turn can finish):** delivered; not delivered; stage raises (R6); Voice raises; deadline; integrity failure; restart repair (a turn marked `failed` with `reply_source: "pipeline_fallback"`); and, in PR-3, confirmation reject and timeout. Each asserts that `reply`, `reply_source` and `outcome` are all non-null once `status == "finished"`, and on no earlier poll.

### 3. Upload size limit: 5 MB (5,242,880 bytes) for the public demo

**Decision (recommended; the user can override it with `WORKBUDDY_MAX_UPLOAD_BYTES`): the default becomes 5 MiB everywhere, replacing Section 6.2's 25 MB guess.** Anyone running locally can raise it.

**What I measured.** This was not measured in the pipeline, the API or the demo host. Isolated `pandas` calls, in this cloud container (Python 3.11.15, pandas 2.3.3, openpyxl 3.1.5, Intel Xeon 2.8 GHz). Synthetic sheets: 14 columns (6 floats, 3 ints, 4 short strings, 1 date), 5% duplicate rows, one sheet. Each figure is one run in a fresh process; peak RSS includes about 67 MB for importing pandas.

| `.xlsx` size | rows | uncompressed | `read_excel` | read + dedupe + `to_excel` (peak RSS) | read + `ProfileReport(minimal)` (peak RSS) |
|---|---|---|---|---|---|
| 1.9 MB | 21,000 | 13 MB | 3.0 s | 7.8 s (187 MB) | 6.0 s (331 MB) |
| 5.0 MB | 54,600 | 35 MB | 7.9 s | 21.1 s (375 MB) | 11.6 s (379 MB) |
| 9.6 MB | 105,000 | 67 MB | 14.5 s | 42.0 s (648 MB) | 19.3 s (439 MB) |
| 19.3 MB | 210,000 | 136 MB | 29.4 s | 90.8 s (1,210 MB) | 35.4 s (519 MB) |

Separately, importing `buddy.engine_factory`, the excel skills and `ydata_profiling` gives 303 MB peak RSS before any work (FastAPI and uvicorn aren't installed yet, so they're not included).

**Reasoning.**
- **Time binds first, not memory.** [Likely] From the `read_excel` call sites (`skills/excel/analyze.py:353`, `skills/excel/dedupe.py:94`, `buddy/inspector.py:132, 209, 357, 579`), a one-step turn reads the file about 7 times: proactive analysis, the step, the Inspector reading output and input, and analysis re-run on the final file and verified. It also writes once. I counted call sites; I didn't trace a turn. Scaling the table gives, before any LLM time:

  | File size | One-step turn | Two-step chain |
  |---|---|---|
  | 5 MB | ~75 s | ~110 s |
  | 10 MB | ~140 s | ~210 s |

  So at 10 MB a two-step chain passes the 180 s deadline (Section 4.3) and ends as `failed`: honest, but a broken demo. 25 MB fails even single steps. 5 MB leaves roughly 40% margin for the chain, before the demo host's CPU speed (unknown) and Groq latency.
- **Memory is bounded by the concurrent-turn cap, not by the per-IP rate.** Uploads are streamed to disk and never parsed at upload time (Section 6.2), so only running turns hold data in memory, and at most 2 run at once. [Likely] At 5 MB: about 300 MB baseline + 2 × about 310 MB per-turn peak ≈ **0.9–1.0 GB**. At 25 MB, extrapolating the 19 MB row (1.2 GB for one dedupe): ≈ 300 MB + 2 × ~1.5 GB ≈ **3.3 GB**, which rules out the 1–2 GB fallback hosts in frontend Section 11.3.
- **Disk is bounded by the per-IP upload rate, not by the size cap.** Without an upload-specific bucket, `general` alone (60/min) allows 60 × 5 MB = 300 MB/min per IP (18 GB/hour), and at 25 MB, 90 GB/hour. That is why item 1 adds `uploads` (burst 5, 20/hour). Per IP per hour at 5 MB:
  - uploads: 20 × 5 MB = 100 MB;
  - outputs: 10 turns × at most 2 written files × ~5 MB ≈ 100 MB ([Possible] an output is about its input's size; unmeasured).
  
  That is ~200 MB/hour, or ~4.8 GB per IP held for the 24 h TTL if sustained. The per-session quotas (part 1) don't bound this, because sessions are free to create; the `uploads` and `turns` buckets do.
  - The global data-root cap (part 1, `503 storage_full`) is what protects the disk. **Its number is still undecided:** it depends on the host's disk, which isn't chosen.
  - [Certain] A handful of IPs at the maximum rate can fill a small cap within hours and lock out everyone until the sweep. The rate limits bound how fast that happens; they don't prevent it.
- **Upload spool.** [Likely] Starlette parses a multipart body into a spooled temp file before the handler runs, so the handler's streaming check alone doesn't stop an oversized body reaching temp disk. The global body cap (Section 6.2) must be enforced on the raw request stream, before multipart parsing. The `uploads` burst of 5 bounds concurrent uploads per IP. PR-2 test: a body over the cap, with no `Content-Length` and with a false one, is cut off and leaves nothing in the temp directory.
- **User cost.** At the measured shape, 5 MB is about 55,000 rows × 14 columns. [Guessing] Most sheets people try in a demo are smaller. A too-large file fails at once with a clear `413`, which is better than a turn that runs for three minutes and then fails.

**Still open, said plainly:**
- **Uncompressed-size guard.** Parse time follows the *uncompressed* XML (7× the file size here). Section 6.2's 200 MB guard would admit a highly compressible 5 MB file whose reads take ~40 s each, so ~7 reads miss the deadline. I recommend 40 MB for the demo (8× the cap), but this changes a Section 6.2 value, so it's **left for the user to decide**.
- **Unmeasured:**
  - the real pipeline end to end (the read count, LLM latency, `splink` if it's imported);
  - process RSS with FastAPI and two concurrent turns;
  - the demo host's CPU;
  - other sheet shapes: wide sheets, long text, formulas and styles, several sheets;
  - `.xls` through `xlrd`;
  - output sizes.
- **Evidence that would change this:** PR-2 live run 9 (below). If a two-step chain on a 5 MB fixture takes over 120 s through the API (two-thirds of the deadline), lower the default to 2 MB before deployment. If it takes under 60 s on the demo host, 10 MB can be reconsidered, but only after a 10 MB chain is measured under 120 s there.

**Consequences:** the frontend's blob-download memory (frontend Section 14) is now bounded by 5 MB, not 25 MB. The uploads bucket (item 1) is part of this decision, not an extra.

### 4. PR split, updated (adds to Section 8.3 and part 1's "Consequences")

| Item | PR |
|---|---|
| Session response `limits` (`max_upload_bytes`, `accepted_extensions`, `max_message_chars`), all from the enforcing settings, with the boundary tests | **PR-2** |
| `retention_hours` in the schema, always `null` | **PR-2** |
| `retention_hours` populated from the TTL, plus the sweep test | **PR-4** |
| `backend/openapi.json`, `backend/export_openapi.py`, the sync test, the `ErrorBody`-only error-schema test, `fastapi` minor pin | **PR-2** (each later PR regenerates the file and its diff is reviewed) |
| Finished-turn union schema; the "all three set on finish" tests for delivered, not delivered, R6, Voice raise, deadline, integrity failure, restart repair | **PR-2** |
| The same tests for reject and confirmation timeout; `steps` equals the executed Write-tier steps | **PR-3** |
| `Retry-After` on `429 server_busy` (`WORKBUDDY_BUSY_RETRY_AFTER_S`) | **PR-2** |
| Upload cap default 5 MiB, `413` message from the setting, raw-stream body cap and its temp-directory test | **PR-2** |
| Live run 9: 5 MB fixture, "remove duplicate rows" and a two-step chain through the API; record wall time per turn and peak process RSS alongside the usual `CLAUDE.md` fields | **PR-2** |
| `Authorization: Bearer`, the `401` body and `WWW-Authenticate`, `HTTPBearer(auto_error=False)`, the no-password-in-logs test | **PR-4** (PR-4a if split) |
| The five buckets, strict counting, double count on the poll route, post-charge for non-`200` polls, exact `Retry-After` on `rate_limited`, the startup check, and tests 1–10 of item 1 | **PR-4** (PR-4a if split) |
| Repeat live run 9 on the chosen demo host before the link goes public; apply the 2 MB fallback if it exceeds 120 s | **PR-4** (deployment checklist) |

**Still undecided after this section:**
- the uncompressed-size guard for the demo (item 3);
- the global data-root byte cap (depends on the host);
- how the frontend treats a `429` on a poll (frontend design, noted in item 1).

---

## Decisions (2026-10-01)

This section covers only what the revised frontend design needs from the backend (`docs/design/frontend-design.md` on `design/frontend`, revised 2026-10-01). It follows the owner's decision:
- the UI is htmx with Jinja templates, served by this FastAPI process;
- the demo password is exchanged once for an HttpOnly, Secure, SameSite=Strict cookie.

Where this section conflicts with an earlier one, **this section wins**. Earlier text isn't edited. Item 8 lists exactly which statements are superseded.

### 1. The access cookie (replaces Bearer for the browser)

**The route: `POST /api/v1/access`.**
- **Request:** `application/x-www-form-urlencoded`, with one field, `password`. That's what an HTML form and htmx send by default, and `python-multipart` is already a dependency. Any other content type gets `422 invalid_request`.
- **The check** is the same function Bearer uses: `hmac.compare_digest` on UTF-8 bytes, against `WORKBUDDY_DEMO_PASSWORD`.
- **Responses:**
  - `204 No Content` with `Set-Cookie`, on success.
  - `401`, for a wrong or missing password. It has the same header and body as every other `401` (part 2, ask 4): `WWW-Authenticate: Bearer realm="workbuddy-demo"` and `{ "error": { "code": "unauthorized", "message": "The demo password is missing or wrong." } }`.
  - `403 forbidden_origin`, when the `Origin` check fails (item 2).
  - `429 rate_limited`, per item 3.
- **In `WORKBUDDY_ENV=dev`** with no password set, the route issues a cookie without checking anything, so the UI flow is the same in dev.
- **In `openapi.json`,** it's documented with its form body, the `204`, and `ErrorBody` for `401`, `403`, `422` and `429`.

**The cookie:**

```
Set-Cookie: __Host-wb_access=<token>; Path=/; Secure; HttpOnly; SameSite=Strict
```

- **The `__Host-` prefix.** [Likely] Browsers accept a cookie with this prefix only if it has `Secure`, `Path=/` and no `Domain`. So a sibling subdomain can't set or overwrite it.
- **No `Max-Age` or `Expires`,** so the browser drops the cookie when it closes, which helps on shared machines (frontend Section 6).
- **The server enforces the lifetime itself:** an absolute expiry of `WORKBUDDY_ACCESS_TTL_HOURS` from issue (default 12). It isn't renewed by use.
- **`Secure` needs HTTPS.** In production, the app refuses to start unless `WORKBUDDY_PUBLIC_ORIGIN` is an `https://` origin (item 2).
- **The value** is `secrets.token_urlsafe(32)` (256 bits). It's never the password and never derived from it.

**The token store:**
- **In memory,** like all other live state (one worker).
  - The key is the token's SHA-256, so the store never holds the cookie's value.
  - The value is the expiry time.
- **Bounded:** at most `WORKBUDDY_ACCESS_MAX_TOKENS` live tokens (default 10,000).
  - When the store is full, the token closest to expiry is evicted, and its user sees the gate again.
  - Expired tokens are dropped when looked up, and by a periodic sweep.
  - The `access` bucket (item 3) limits how fast tokens can be created.
- **[Certain] A restart invalidates every cookie,** so users see the gate again.
  - That's also how the password is rotated: changing `WORKBUDDY_DEMO_PASSWORD` and restarting (part 1, "honest limits") ends every existing cookie.
  - On an ephemeral-disk host, a restart loses the sessions anyway, so nothing more is lost.
- **No logout route in the demo.** It's deferred.

**Which routes accept which credential:**
- **Need a valid credential:** every `/api/v1` route except `/healthz` and `/api/v1/access`, and every HTML route (`/ui/*`) the frontend adds. A credential is either:
  - the access cookie; or
  - `Authorization: Bearer <password>`.
- **Bearer is kept for scripts, tests and live runs,** unchanged from part 2, ask 4. That's simpler: the test suite and the live-run scripts need no cookie jar and no `Origin` header.
- **Precedence.** If an `Authorization` header is present, only that header is checked, and the cookie is ignored. A wrong Bearer therefore gets `401` even alongside a valid cookie. Without an `Authorization` header, the cookie is checked.
- **Need no credential:** `GET /healthz`, `POST /api/v1/access`, `/static/*`, and `GET /`. Without a valid credential, `GET /` renders the gate. It renders the workspace only with one, decided by the same function.
- **One function decides:** `check_credential(request) -> CredentialResult(valid: bool, kind: "bearer" | "cookie" | None)`. The auth dependency, the rate limiter (item 3) and the CSRF check (item 2) all use it, so they can't disagree.
- **One failure response.** The `401` header and body are unchanged, and are the same for every failure: a missing credential, a wrong Bearer, an unknown or expired cookie, or another scheme. A caller can't tell an expired cookie from a wrong password.
- **Logs:**
  - the redaction filter (Section 6.4) gains a pattern for `__Host-wb_access=…`;
  - the API never logs `Cookie` or `Set-Cookie` headers;
  - the PR-4 test that the password never appears in a response or a log is extended to the token value.

### 2. CSRF for cookie authentication

The browser sends a cookie automatically, so a request that the cookie authenticates needs CSRF protection. A Bearer request doesn't: a page on another origin can't add an `Authorization` header without a CORS preflight, and CORS is empty in the demo deployment. The protection has four parts:

1. **SameSite=Strict** on the cookie (item 1). [Likely] Current browsers don't send it on any request that another *site* starts.
2. **An exact `Origin` check,** because SameSite is about the site, not the origin. [Possible] If the host's parent domain (for example `hf.space`) isn't on the Public Suffix List, every other app on that domain counts as the same site.
   - **Which requests:** every request with a method other than `GET`, `HEAD` or `OPTIONS` that the cookie authenticates. `POST /api/v1/access` is checked too, even though it takes no credential.
   - **The rule:** the `Origin` header must equal `WORKBUDDY_PUBLIC_ORIGIN` exactly (scheme, host and port), compared after lower-casing the scheme and host.
   - **On failure** (a missing `Origin`, `Origin: null`, or any other value), the response is `403` with `{ "error": { "code": "forbidden_origin", "message": "This request didn't come from the demo page." } }`, and the handler doesn't run.
   - [Likely] Current browsers send `Origin` on every POST, including same-origin ones. The frontend's e2e tests run in Chromium and would show it if not.
   - **`WORKBUDDY_PUBLIC_ORIGIN`** is required in production: the app refuses to start without it, as it does without the password. It must be `https://`. In dev it defaults to `http://127.0.0.1:8000`. It's configuration, and never derived from the `Host` header, which a proxy can rewrite.
   - **The check runs in the router-wide dependency that does auth,** so a new route can't skip it.
3. **No GET route changes state.**
   - `GET /` doesn't create a session. The page creates it with `POST /ui/sessions`.
   - Polls, downloads and session reads only read.
   - Each route declares whether it's mutating (item 3), and a startup check refuses a `GET` route declared mutating.
4. **Clickjacking.** The CSP has `frame-ancestors 'none'`. Part 1, item 1 adds a CSP for the static frontend, and frontend Section 11.2 gives the exact policy.

**Not used: a synchronizer CSRF token.** With Strict cookies, the exact `Origin` check and no GET route that changes state, it would add code without closing any gap I can name. Evidence that would change this: a supported browser that leaves out `Origin` on a same-origin POST.

### 3. Rate limits, kept consistent with part 2

Part 2's numbers and rules stand. What changes is how a request is classified, plus two new buckets.

1. **"Valid password" now means "valid credential".** Where part 1 and part 2 say that a request has a valid password, or a missing or wrong one, read "credential", as `check_credential` decides it (item 1). That covers the `poll` bucket's condition and the double count. A valid cookie or a valid Bearer is a valid credential. An unknown or expired cookie counts as a wrong password, and takes 2 tokens from `general`.
2. **A new `access` bucket** (burst 10, refill 20/hour).
   - `POST /api/v1/access` takes 1 token from `general` (2 if the password is wrong, as on every route) and 1 from `access`, whatever the response.
   - A legitimate browser needs one exchange per browser session. It needs another after a restart, or after arriving from a link (frontend 1.2).
   - Guessing is limited to 10 in a burst and then 20 an hour per IP, far below the 30/min that `general`'s double count allows on its own.
   - [Certain] Users behind one IP share this bucket, as they share every bucket. So 20/hour is how many new browser sessions an office NAT can start in an hour.
3. **A new `page` bucket,** for `GET /` and `/static/*` (burst 30, refill 1/s). These requests don't touch `general`. A page load (the page and three static files) costs 4 tokens. Reloads therefore can't use up the `general` budget a turn needs, just as `healthz` keeps probes out of it.
4. **The HTML routes map onto the existing buckets** by what they do, not by their path:

   | HTML route (frontend Section 2) | Charged as |
   |---|---|
   | `POST /ui/sessions` | `POST /sessions`: `general` |
   | `POST /ui/s/{sid}/files` | `POST …/files`: `general` + `uploads` |
   | `POST /ui/s/{sid}/turns` | `POST …/turns`: `general` + `turns` |
   | `GET /ui/s/{sid}/turns/{tid}/poll` | The poll route: `poll` with a valid credential, plus one `general` token after a non-`200` (part 2 item 1, rule 1) |
   | `POST /ui/s/{sid}/turns/{tid}/confirmation` | `POST …/confirmation`: `general` |
   | `GET /ui/s/{sid}/transcript` | `GET /sessions/{id}`: `general` |

   - Strict counting (part 2, rule 2) and "a `429` consumes nothing" (rule 4) apply unchanged.
   - A request refused with `403 forbidden_origin` counts as a mutating request. It takes 1 token from `general` (plus `turns` or `uploads` where the table lists them), not 2, because its credential was valid.
5. **Every route declares its buckets, and whether it's mutating,** in route metadata the limiter reads. The app refuses to start if any route lacks the declaration. This replaces part 2's classification "by method, route template and whether the password is valid" with an explicit list, so no HTML route that FE-1 adds can slip through without a bucket.
6. **The poll numbers still hold, with more margin.** Part 2 sized `poll` for a frontend that polls once a second. The revised frontend polls every 2 s, then every 3 s (frontend 1.6), so two unfinished turns need at most 1 poll/s against a refill of 3/s. The startup check (poll refill at least the concurrent-turn cap) is unchanged.

**Tests (PR-4, with part 2's injected clock):**
- a full turn through the HTML routes with the cookie, at the frontend's cadence for 300 s, plus a page load, an upload, a confirmation and a download, gets no `429`;
- of 11 wrong passwords in a burst on `/api/v1/access`, the 11th gets `429`, and each one took 2 `general` tokens;
- an expired cookie on a poll takes 2 `general` tokens, and none from `poll`;
- page loads don't touch `general`;
- `create_app` fails for a route without a bucket declaration.

### 4. Plan text: chain-aware steps and a fixed plan-level line (frontend M2)

This supersedes the template wording in Section 1.5 ("…and save the result as a new file."), and the example prompts in part 1 item 2 and part 2 ask 1.

**Why the old wording was wrong.**
- [Certain] Step 2 of a chain runs on step 1's output, not on the upload (`buddy/hands.py:208`: `input_reference = chain_input or request.target`).
- Only the chain's final file is handed back (`CLAUDE.md`).
- The old template named the upload in every step, and promised a new file for each one.

**Templates.** Each capability has two fixed templates in `backend/confirmation.py`, reviewed like the phrase catalog:

| Capability | First step | A later step, at position *k* |
|---|---|---|
| `spreadsheet.deduplicate` | "Remove duplicate rows from {input_name}." | "Then remove duplicate rows from the result of step {k−1}." |
| `spreadsheet.remove_empty_rows` | "Remove empty rows from {input_name}." | "Then remove empty rows from the result of step {k−1}." |
| Any other (the fallback) | "Run {capability} on {input_name}." | "Then run {capability} on the result of step {k−1}." |

- **`k`** is the step's position in `steps`, counting from 1. `steps` holds only the Write-tier steps, in execution order (part 2, ask 1). The frontend renders `steps` as a numbered list in that order, so "step {k−1}" names the row above.
- **What no template says:** "save", "new file", or anything about the original. None states a result.
- **Why every later step uses the "Then" template:** every multi-step plan runs as a chain (`CLAUDE.md`: "Multi-step requests run as a chain").

**`plan_note`.** `pending_confirmation` gains `plan_note: string | null`:
- **For two or more steps,** it's the fixed constant `PLAN_NOTE_CHAIN`: "The steps run in this order, each on the previous step's result. Only the final file is handed back."
- **For one step,** it's `null`.
- **Where it comes from:** a constant in `backend/confirmation.py`, never built from request data.
- **What it deliberately leaves out:**
  - "nothing runs if you reject", because `spreadsheet.analyze` (READ tier) runs either way;
  - "your original won't change", a promise the card can't verify.

**Tests (PR-3):**
- every registered Write-tier capability has both templates;
- for a two-step chain:
  - `steps[0].prompt` names the upload;
  - `steps[1].prompt` names "the result of step 1", and not the upload's name;
  - `plan_note` equals `PLAN_NOTE_CHAIN`;
- for one step, `plan_note` is `null`;
- no prompt or note contains "save" or "new file";
- a spy on `Hands` shows that, in the same run, each step k ≥ 2 takes step k−1's output as its input, so the wording matches the wiring. If a future planner produces a multi-step plan that isn't a chain, this test fails, and the wording has to be revisited before that ships.

### 5. `pending_confirmation` in the public schema: compatibility fields dropped (frontend M3)

This supersedes Section 1.5's object, and its sentence that `capability`, `permission_tier` and `prompt` "describe the first step for compatibility". The public shape is now:

```json
{
  "confirmation_id": "c_5e1d…",
  "steps": [
    { "capability": "spreadsheet.deduplicate",
      "prompt": "Remove duplicate rows from report.xlsx." },
    { "capability": "spreadsheet.remove_empty_rows",
      "prompt": "Then remove empty rows from the result of step 1." }
  ],
  "plan_note": "The steps run in this order, each on the previous step's result. Only the final file is handed back.",
  "expires_at": "2026-10-01T10:03:00Z"
}
```

- **Removed from the public schema:** the top-level `capability`, `permission_tier`, `input_name` and `prompt`.
  - Nothing reads them: the frontend renders only `steps` and `plan_note`.
  - A client that falls back to step 1's `prompt` is exactly what M3 warns about: a user could approve a two-step plan after seeing one step.
  - The provider keeps whatever it needs internally, such as the published plan's tiers for part 1's "a tier not in the published plan is refused" guard.
- **`steps` is required and non-empty** whenever `pending_confirmation` isn't `null`. The schema says `minItems: 1`, and a test asserts the provider never publishes an empty plan. Part 1 item 2, step 5 already says nothing is published without a Write-tier step.
- **`openapi.json`** is regenerated in PR-3, and the diff is reviewed (part 2, ask 3).
- **The same shape everywhere:** `GET …/turns/{id}`, `GET /sessions/{id}` and the confirmation response.

### 6. Refusing a multi-sheet `.xlsx` at upload (a stopgap for backlog 1a, frontend H3)

[Certain, from backlog 1a / verified-outcome-a M3] Dedupe and remove-empty-rows read only the first sheet and write a single sheet, and the reply calls the result "your cleaned copy". Until per-sheet cleaning exists (backlog 1a), uploads refuse any workbook with more than one sheet, so the public demo can't produce that reply.

**The check (PR-2, in `backend/uploads.py`).** It runs after the content sniff, in the same pass as the zip-bomb guard (Section 6.2), which already reads the ZIP's central directory (`ZipFile.infolist()`) without extracting anything. It counts two kinds of entry:
- **worksheets:** names matching `^xl/worksheets/[^/]+\.xml$`, which includes hidden and very hidden sheets;
- **other sheet types,** which the skills would also drop: names matching `^xl/(chartsheets|dialogsheets|macrosheets)/[^/]+\.xml$`.

| What's in the directory | Result |
|---|---|
| Exactly one worksheet, and no other sheet | Accepted |
| More than one sheet in total | `422` with `{ "error": { "code": "multiple_sheets", "message": "This demo works on one sheet at a time, and this workbook has more than one. Save the sheet you want in its own workbook and upload that." } }` |
| No worksheet at all | `422 invalid_file`, with Section 6.2's existing message. The structural check failed: the file may store its sheets somewhere non-standard, and the demo doesn't guess |

- **Repeated names count separately.** A ZIP that repeats an entry name counts each copy, so the check fails closed.
- **No parsing.** Nothing is decompressed, and `openpyxl` and `pandas` still never run at upload (Section 6.2, "No parsing at upload").
- **A refused upload stores nothing.** There's no `file_id`, and the partial file is deleted, as for every refused upload.
- **It's charged like any refused upload:** `general` + `uploads` (part 2 item 1, rule 2).
- **`multiple_sheets` joins the closed enum** of error codes.

**What I checked, and what I didn't:**
- [Certain] I ran openpyxl 3.1.5 and pandas 2.3.3 in this container:
  - a three-sheet workbook from openpyxl, with one sheet hidden, stores `xl/worksheets/sheet1.xml` to `sheet3.xml`;
  - a workbook with a chartsheet stores it as `xl/chartsheets/sheet1.xml`;
  - a single-sheet file from `DataFrame.to_excel` has exactly one `xl/worksheets/*.xml`.
- [Likely] Excel, LibreOffice and Google Sheets exports use the same part names.
- **The real names come from the workbook's relationships file, which this check doesn't read.** A producer that put a second sheet under another path would get through. I haven't seen one, so the remaining risk is a multi-sheet file from an unusual producer.

**`.xls` isn't covered.**
- An `.xls` file is an OLE2 container with no ZIP directory. Counting its sheets would mean parsing the workbook stream, which Section 6.2 forbids at upload.
- This section doesn't change part 1 item 4 (`.xls` is accepted), or part 2 ask 2 (`.xls` is listed only in the PR that has its fixture test).
- Whether `.xls` may be turned on for the public link before its sheets can be checked is the owner's decision. It's listed in the frontend design's deployment preconditions (Section 13.2, item 3), and isn't decided here.

**When backlog 1a lands,** that PR removes this refusal (or keeps it on purpose, as a product limit) and inverts its tests. Until then, the tests below stay.

**Tests (PR-2),** with fixtures generated in the test by openpyxl:
- one sheet: accepted;
- two sheets: `422 multiple_sheets`, with the fixed message;
- one visible and one hidden sheet: `422 multiple_sheets`;
- one worksheet plus a chartsheet: `422 multiple_sheets`;
- a ZIP with `[Content_Types].xml` and no worksheet: `422 invalid_file`;
- after each refusal, nothing is left under the data root or in the temp directory;
- each refusal used up a token from `general` and from `uploads`.

### 7. Which PR does what

| Item | PR |
|---|---|
| The multi-sheet refusal (item 6), the `multiple_sheets` code, and its tests | **PR-2** |
| Chain-aware templates, `plan_note`, the removed compatibility fields, `minItems: 1`, the regenerated `openapi.json`, and their tests (items 4 and 5) | **PR-3** |
| `POST /api/v1/access`, the cookie and token store, `check_credential`, Bearer kept with its precedence, and log redaction (item 1) | **PR-4** (PR-4a if split) |
| The `Origin` check, `WORKBUDDY_PUBLIC_ORIGIN`, and the startup check for mutating GET routes (item 2) | **PR-4** (PR-4a if split) |
| The `access` and `page` buckets, the per-route bucket declarations, the HTML-route mapping, classification by credential, and their tests (item 3) | **PR-4** (PR-4a if split) |

FE-1 merges after PR-4a (frontend 13.1), because its gate needs the access route and its H2 handling needs the real buckets. The HTML routes belong to FE-1. PR-4a provides the dependency they attach to and the mechanism for declaring buckets. FE-1's tests cover the HTML-route rows of the table in item 3.

### 8. What this section supersedes

Earlier text isn't edited. These statements no longer hold as written:
- **Part 2, ask 4** ("Decided: `Authorization: Bearer <password>`"), as the browser's mechanism.
  - The browser uses the access cookie (item 1). Bearer stays for scripts and tests, parsed the same way, and `X-Demo-Password` is still not accepted. The `401` header and body are unchanged.
  - "The static frontend and `/healthz` are not behind it; every `/api/v1` route is" now reads: `/healthz`, `/static/*`, `POST /api/v1/access` and the gate view of `GET /` aren't behind it; every other `/api/v1` route, and every HTML route, is.
- **Part 1, item 1** ("Checked on **every** API route except `GET /healthz`", and "Sent as `Authorization: Bearer <password>` (or a header named `X-Demo-Password`…)"). The password is now also sent once, in the body of `POST /api/v1/access`, the only route that accepts it there.
- **Part 1, item 1 and part 2, item 1:** in the rate-limit rules, "valid password" and "password is missing or wrong" are read as "credential" (item 3).
- **Part 2, item 1** ("chosen by method, route template and whether the password is valid"). Buckets are now declared per route (item 3, point 5), and the table gains `access` and `page`. Its numbers are unchanged.
- **Part 2, item 1, "Why these numbers"** ("the frontend polls at most 1/s per turn"). The frontend now polls at most once every 2 s. The numbers stand, with more margin.
- **Part 2, ask 3** ("The frontend's build and CI generate types from this file"). The frontend no longer generates types (htmx). The committed `openapi.json` and its sync test stay, because scripts and reviews read them.
- **Part 2, item 3, "Consequences"** (blob-download memory in the frontend). It no longer applies, because downloads are plain links.
- **Section 1.5:** the `pending_confirmation` object, its example prompt ("…and save the result as a new file.") and the compatibility sentence are replaced by items 4 and 5.
- **Part 1, item 2 and part 2, ask 1:** the example prompts. The `{ capability, prompt }` entry shape is unchanged, and the wording is now item 4's.
- **Section 1.2's upload errors and Section 6.2's table** gain `422 multiple_sheets` and the sheet-count check (item 6). No existing row changes.

Not changed: same-origin deployment with empty CORS (Section 6.1, part 1 item 1), the 5 MiB upload cap, the five original buckets and their numbers, and every rule-1 and rule-2 invariant in Section 5.

### 9. Still undecided

**Owner decisions:**
- whether `.xls` may be on for the public link before its sheets can be checked (frontend 13.2, item 3);
- going public while PR-B / verified-outcome-a M2 is open (frontend 13.2, item 4). It's to be recorded in a later Decisions section;
- code visibility, and with it the host (frontend 11.3 and 13.2, item 5).

**Still open from part 2:**
- the uncompressed-size guard (40 MB recommended);
- the global byte cap on the data root, which depends on the host.

**Not addressed here: the frontend review's C5.** The plan is built before `Hands` runs, but a step's permission tier is only known after dispatch (`buddy/hands.py:240-252`; `CapabilityRequest` has no tier). PR-3 must resolve tiers through the Dispatcher before publishing the plan, or the plan can list the wrong steps. That's for PR-3's design and review.

---

## Decisions (2026-10-04)

An owner decision, recorded with PR-2 (`feat/backend-api-core`). Where this section conflicts with an earlier one, **this section wins**. Earlier text isn't edited.

### 1. `.xls` is out of scope

- **Accepted extensions are `[".xlsx"]` only.** That is the upload validator's allowlist itself (`backend/settings.py`, `ACCEPTED_EXTENSIONS`), and what `limits.accepted_extensions` reports in the session response, from the same object.
- **`xlrd` is not added** to `requirements.txt` or `pyproject.toml`.
- **An `.xls` upload is refused** with `415 unsupported_file_type` and a fixed message of its own (`backend/errors.py`, `XLS_MESSAGE`): "Older .xls workbooks aren't supported. Save the file as .xlsx (Excel Workbook) and upload that." The check is on the sanitized name's extension, case-insensitively, before anything is written. Every other extension outside the allowlist gets the code's general message ("Only .xlsx files can be uploaded."). OLE2 content under an `.xlsx` name fails the content sniff (`422 invalid_file`), so an `.xls` can't get in renamed either.

### What this supersedes

- **Part 1, item 4** (".xls is accepted", "PR-1 adds `xlrd`", the `.xls` fixture test for PR-2/PR-3, and "a missing `xlrd` is a startup failure"). None of it applies: there is no `.xls` path.
- **Section 6.2**: the extension row ("`.xlsx`, `.xls` … `.xls` is accepted only if `xlrd` imports at startup") now reads `.xlsx` only, and the `.xls` half of the content-sniff row (OLE2 header) no longer applies.
- **Part 2, ask 2**: the example `"accepted_extensions": [".xlsx", ".xls"]` is `[".xlsx"]`, and "`.xls` is in it only in the PR that adds the `.xls` fixture test" no longer arises.
- **Section 9.1, item 3** (whether `.xls` ships now): answered, no.
- **"Decisions (2026-10-01)", item 6, ".xls isn't covered"**, and **item 9's owner question** (whether `.xls` may be on for the public link before its sheets can be checked): answered, no. The multi-sheet refusal only ever has to handle `.xlsx`.

### Not changed

The skills still refuse an OLE2 workbook inside a turn ("older .xls workbooks … aren't supported yet"), and backlog 1a's note stands: don't add `xlrd` until the sheet count covers chart sheets, macro sheets and VB modules, which `xlrd` skips. Turning `.xls` on later needs a new owner decision, that work, and a fixture test that an `.xls` upload is cleaned, delivered under the name and format the reply states, and left byte-identical.
