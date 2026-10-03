# Implementation notes: `feat/backend-api-core` (PR-2 of the backend API design)

**Branch:** `feat/backend-api-core`, from `origin/main` (`6d000c5`).
**Design:** `docs/design/backend-api-design.md`, PR-2 (Section 8.3 item 2), with every "Decisions" section applied (later sections win), plus the owner decision recorded as "Decisions (2026-10-04)".
**Also required:** items 1 to 7 of "What a PR-2 author must do" in `docs/reviews/pipeline-failure-and-output-dir-review.md`, and its M1 hardening.
**Rule-touching:** yes, both rules. The API is where a reply and a file reach the user.
**Not in this PR:** PR-3 (real confirm/reject, `WebConfirmationProvider`, `awaiting_confirmation`, `pending_confirmation`'s shape, prompt templates) and PR-4 (demo password, cookie, rate limits, retention sweep, per-session quotas, hosted binding and CORS).

Everything below is the writer's account. Treat it as unverified.

## What's here

| Module | What it does |
|---|---|
| `backend/main.py` | `create_app(settings, engine_factory)`; the production engine factory (`build_engine` with `PersonalityVoice` on a provider built once from `GROQ_API_KEY`, else `ANTHROPIC_API_KEY`); the OpenAPI post-processing |
| `backend/__main__.py` | `python -m backend [--port]`: uvicorn on 127.0.0.1, one worker |
| `backend/settings.py` | env-driven settings, validated at startup; `.xlsx` only |
| `backend/errors.py` | the closed `ErrorCode` enum, statuses, fixed messages, the handlers |
| `backend/middleware.py` | `nosniff`/`no-store` on every response; the raw-stream body cap; a last-resort fixed 500 |
| `backend/logging_filter.py` | redaction by pattern (keys, `Bearer`, `api_key=`, a session id in a path), installed as a log-record factory |
| `backend/schemas.py` | Pydantic models; the turn is a union discriminated on `status` |
| `backend/sessions.py` | `SessionRecord`, `TurnRecord`, `UploadRecord`, `DeliveredFile`, the registry, ids |
| `backend/uploads.py` | name sanitization, the streamed size cap, the type and structure checks, the multi-sheet refusal, the `.xls` refusal |
| `backend/turns.py` | the turn procedure, worker thread, deadline, global cap, `decide()` and one-shot finalization |
| `backend/delivery.py` | `delivered_files()`, the file check, the rule-2 re-hash |
| `backend/confirmation.py` | the PR-2 stand-in confirmation provider (`WORKBUDDY_AUTO_APPROVE`) |
| `backend/store.py` | atomic persistence, validated lazy restore, interrupted-turn repair |
| `backend/views.py` | records to response models |
| `backend/routes.py` | the endpoints |
| `backend/export_openapi.py`, `backend/openapi.json` | the committed contract and its generator |

Changes outside `backend/`:
- `core/engine.py`: the M1 hardening (below).
- `buddy/voice.py`: a public `is_verified_delivery(state)`, the predicate the speakers already use, so the API's delivery decision can't disagree with the reply.
- `requirements.txt`: `fastapi>=0.142,<0.143`, `pydantic>=2.13,<2.14`, `uvicorn`, `python-multipart`, `httpx`; the stale header comment replaced.
- `README.md`: how to serve the API.
- `docs/design/backend-api-design.md`: "Decisions (2026-10-04)" appended.

## Endpoints

`GET /healthz`; under `/api/v1`: `POST /sessions`, `GET /sessions/{id}`, `POST /sessions/{id}/files`, `GET /sessions/{id}/files`, `GET /sessions/{id}/files/{file_id}/download`, `POST /sessions/{id}/turns` (202), `GET /sessions/{id}/turns/{turn_id}`. No confirmation endpoint (PR-3). Docs and `/openapi.json` are not served; `backend/openapi.json` is the contract.

## The turn procedure (design 3.3)

Under the session lock: `409` if a turn is running; `404 file_not_found` for an unknown `file_id`; `429 server_busy` with `Retry-After: WORKBUDDY_BUSY_RETRY_AFTER_S` at the global cap. Then the turn number is used up, `outputs/{key}/{turn_id}/` is created with `exist_ok=False` (mode `0o700`), the upload-time hashes are recorded, the state is deep-copied, and on the copy: `reset_transient()`, the trimmed message, the mode, the active file, `turn_output_dir`, `record_user_message`. The turn record is written (`running`) and the worker thread and deadline timer start. A failure anywhere in this rolls the turn back (no turn, no folder, the slot released).

The worker builds the engine from the factory and calls `engine.run` inside `try/except Exception/finally`. Finalization (worker or deadline, whichever comes first; one-shot under the session lock) uses `decide()`:

1. An upload's hash changed → `integrity_failure`, `FALLBACK_INTEGRITY_FAILURE`, nothing released, logged `CRITICAL`.
2. `engine.run` raised, the factory raised, a `BaseException`, or the deadline → `failed`, `FALLBACK_PIPELINE_ERROR`, `pipeline_fallback`. The state copy is discarded unread.
3. No reply on the state → the same fallback.
4. `state.failure` set → `failed`, the pipeline's own failure reply, no files.
5. `delivered_files()` returns files → `delivered`.
6. The pipeline's reply says it delivered (`is_verified_delivery`) but `delivered_files()` refused → `failed` with the fallback, because the reply would otherwise be false.
7. Otherwise `not_delivered`, the pipeline's reply.

On a commit (4, 5, 7) the reply is recorded with `record_buddy_reply`, the copy is `reset_transient()`ed and becomes the session's state. On a discard the pre-turn state stays, and only the message and the fallback reply are recorded (design 3.3 step 7); the turn's `mode`, being the user's setting, is applied either way. Delivered files are marked `0o444`.

## "What a PR-2 author must do", items 1 to 7, and M1

| Item | Where | Tests |
|---|---|---|
| 1. `delivered_files` tests `state.failure is None` first; never infers delivery from outputs or verification | `backend/delivery.py: delivered_files` | `test_delivery.py`: `test_failure_set_with_outputs_verified_and_inside_delivers_nothing` (the Inspector-after row), `test_each_rule_holds_on_its_own_even_if_the_reply_predicate_said_yes[failure]`; `test_turns.py`: `test_inspector_raising_after_a_real_output_delivers_nothing[*]` |
| 2. A run that raised is failed regardless of its state: discarded, `FALLBACK_PIPELINE_ERROR`, nothing delivered, `delivered_files` never called on it | `backend/turns.py: _run`, `decide` | `test_voice_raising_discards_the_turns_state_and_uses_the_fallback[*]`, `test_an_engine_factory_that_raises_fails_the_turn`, rule-1 scenario `voice_crash` |
| 3. Only `execution_result.outputs`; never `state.outputs` or per-step outputs | `delivered_files` | `test_only_execution_result_outputs_counts_never_legacy_or_step_outputs`, `test_a_chain_hands_back_only_its_final_file`, `test_a_failed_turns_file_and_a_chains_intermediate_file_are_unreachable` |
| 4. Finalize in `try/finally` | `TurnRunner._run` | `test_a_base_exception_in_a_stage_still_finishes_the_turn` |
| 5. `reset_transient()` before every turn | `TurnRunner.start` | `test_a_failed_turn_leaves_no_stale_failure_for_the_next_one` |
| 6. Session ids server-generated (`token_urlsafe`) | `backend/sessions.py: new_session_id` | `test_session_ids_are_128_random_bits_and_distinct`; malformed ids never looked up (`test_an_unknown_or_malformed_session_is_404`) |
| 7. Catch every exception from `Context.from_dict` at restore | `backend/store.py: load_session`, `_restore_context` | `test_any_exception_from_context_from_dict_is_caught[*]`, the 23-case `test_a_corrupt_session_record_is_a_clean_404` (the review's M2 table) |
| M1 hardening: Voice raising sets `state.failure` before the re-raise | `core/engine.py` | `tests/core/test_engine.py::test_voice_raising_sets_failure_before_propagating` (fails on the old engine; checked by stashing the change) |

For M2, beyond catching broadly, the restore validates the data first: aware timestamps, types, `uploaded_files` exactly the registry's upload paths, `active_file` and `analyzed_files` among them, `metadata` a dict, roles and modes from their closed sets.

## Deviations from the design, and why

1. **The session id is never used server side; a key is.** The design uses `session_id` as the folder name and `workspace_id` (3.1). But the skills put `task_id` = `{session_id}-task-{n}` into the output file name, Eyes and Brain send the upload *paths* to the model, and the Engine logs the session id. With the bearer id there, the credential would be in every downloaded file's name, in prompts sent to Groq, and in logs. So the server uses `key = sha256(session_id)[:32 hex]` for folders, as the pipeline's session and workspace id, and in logs; the id itself is never on disk (tested). This also satisfies review L3's precondition (no `../` in a session id reaches a skill).
2. **Daemon threads with a slot counter, not a `ThreadPoolExecutor`.** The cap is checked at submit (`429`), so an executor would never queue anyway. `ThreadPoolExecutor` threads are joined at interpreter exit, so a hung provider call would stop the process from exiting on restart; daemon threads don't. A slot is held until the worker *exits*, so hung workers count against the cap, as design 9.2 describes.
3. **Delivery adds a fifth rule: the reply must say it delivered** (`is_verified_delivery`), and the opposite mismatch (the reply says delivered but the files fail the path check) is replaced by the fallback. Design 5.3's four rules would otherwise allow a file to be listed while the reply says "No changes were made", or the reverse.
4. **`detail.steps[*]` has a `recheck` flag,** so the two `spreadsheet.analyze` entries (original and final file) can be told apart. On a turn where a stage raised, no step is `verified`. `detail` is `null` for fallback and integrity-failure turns (their state was discarded).
5. **`awaiting_confirmation` isn't in the PR-2 schema,** and `pending_confirmation` is typed `null`. Its shape is PR-3's (Decisions 2026-10-01, items 4 and 5); PR-3 regenerates `openapi.json`.
6. **No CORS middleware at all.** Section 6.1 asks for an explicit allowlist; with none configured, cross-origin reads are blocked by the browser, and the recommended deployment is same-origin. The allowlist and hosted binding are PR-4's.
7. **The OpenAPI schema is post-processed** to replace FastAPI's `HTTPValidationError` with `ErrorBody` on `422`, since the validation handler always returns `ErrorBody`.
8. **The upload route refuses a second file part,** or any extra file part, with `422 invalid_request` (design: "one part named `file`").
9. **The session id in a request path is redacted in logs** (uvicorn's access log records paths).
10. **Persistence order:** a turn's record is written when it starts (so a restart can repair it) and when it finishes. On restore, turn records on disk but missing from `session.json`, and turn folders already on disk, are both taken into account, so a turn number is never reused.

## Limits and settings

| Setting | Default | Env |
|---|---|---|
| Data root (absolute after startup) | `<repo>/workspace` | `WORKBUDDY_DATA_DIR` |
| Upload cap (`413` message built from it) | 5 MiB | `WORKBUDDY_MAX_UPLOAD_BYTES` |
| Raw body cap | upload cap + 64 KiB | (derived) |
| Message length | 4,000 after trimming | — |
| ZIP entries / declared uncompressed bytes | 1,000 / 200 MB | — |
| Turn deadline | 180 s | `WORKBUDDY_TURN_DEADLINE_S` |
| Global running turns | 2 | `WORKBUDDY_MAX_CONCURRENT_TURNS` |
| `Retry-After` on `429` | 10 s | `WORKBUDDY_BUSY_RETRY_AFTER_S` |
| Approve Write-tier steps | off | `WORKBUDDY_AUTO_APPROVE` |

The session response's `limits` reads `max_upload_bytes`, `accepted_extensions` and `max_message_chars` from the same `Settings` object; `retention_hours` is always `null`.

## Tests and lint (at the head of this file's commit)

- `python -m pytest -q`: **1246 passed** (151 s, Python 3.11.15). 303 of them are new, in `tests/backend/`; one new test in `tests/core/test_engine.py`.
- `ruff check buddy tests`: the same 22 errors as `origin/main`, by file and rule (no new ones). `ruff check backend core skills scripts tests/backend`: clean.
- `python -m backend.export_openapi --check`: in sync.

## Deferred, and not done

- Per-session upload count and byte quotas (design 6.5): PR-4, per "Decisions (2026-09-30)" item 1.
- The uncompressed-size guard for the demo (40 MB recommended) is still the owner's open question; the guard stays at Section 6.2's 200 MB.
- The rate-limit tests in "Decisions (2026-10-01)" item 6 ("each refusal used up a token from `general` and `uploads`"): PR-4.
- Python 3.13: the suite was run on 3.11 only in this session.
- Live runs (design 7.5: 1, 2, 5, 6, 7, 8, 9) happen after a MERGE verdict, on the final head.
