# Fixes for review round 1: `feat/backend-api-core`

**Review:** `docs/reviews/backend-api-core-review.md`, round 1 (commit `f3c5ba3`), verdict DON'T MERGE.
**Fixes start from:** `ae734ae` (the review committed as written).
**State:** the round-1 fixes below are complete. The next step is review round 2 with a new reviewer agent.

The writer's account. Treat it as unverified. I disagree with no finding.

## M1: a turn in which nothing ran said "No verifier has been configured"

- **Change.** `buddy/voice.py`, `TemplateSpeaker.__call__`: the first thing in the `_nothing_delivered` branch. When the execution result has no step, no output and no error, the reply is `NOTHING_RAN_REPLY` and nothing else (no verifier wording, no order sentence). `PersonalityVoice` uses `TemplateSpeaker` for every undelivered turn, so this covers all three speakers.
- **The constant** is in `buddy/voice_catalog.py`, with the reviewed texts: "I didn't run anything on a file this turn, so no file was changed." It matches no `_COMPLETION_CLAIM`, and has no number or placeholder.
- **Not done:** the reviewer's optional item 4 (refuse a turn when the session has no upload). Turns without an upload still run, and now get this reply.
- **Tests:**
  - `tests/buddy/test_nothing_ran_and_from_original.py`: the speakers directly, and the real pipeline with no upload, in all three speakers. A turn where a step ran keeps its own reply.
  - `tests/backend/test_turns.py::test_a_turn_where_nothing_ran_says_so_plainly`: through the API, in 3 speakers × 4 cases: "hi" and "remove duplicate rows" before any upload, "thanks!" after a delivery, and "yes, go ahead" after the default config declined. Each asserts the reply equals the constant and the pipeline's own reply, `not_delivered`, and no "verifier" or "confirm that worked".
  - `test_rule_1_holds_in_every_scenario` gains `no_upload`, `thanks_after_delivery` and `yes_after_decline`, and now asserts that no undelivered reply mentions a verifier.

## M2: "now remove the empty rows too" worked from the original, and the reply didn't say so

The owner hasn't chosen between (a) later turns reading the last delivered file and (b) reading the original and saying so. The review says to implement (b) absent a decision, so this does (b). It is listed for the owner in the final report.

- **Change.**
  - `buddy/state.py`: `BuddyState.earlier_deliveries: int = 0`, cleared by `reset_transient()`.
  - `backend/turns.py`, `TurnRunner.start`: sets it on the turn's copy to `len(record.delivered())`, the number of files the session handed back in earlier turns.
  - `buddy/voice.py`: `SpeakingRequest.earlier_deliveries`, filled by `Voice._build_request`. `_verified_delivery_reply` (used by every speaker for a verified delivery) adds `FROM_ORIGINAL_SENTENCE` right after the outcome block when it's above 0.
  - The sentence, in `buddy/voice_catalog.py`: "I made this copy from your original upload, not from a file I handed back earlier." It's true by construction: Delegate always targets the upload (`context.active_file`), and the API never makes a delivered file an input. It doesn't say that earlier changes are missing, because a turn can redo the same kind of change.
- **Tests:**
  - `tests/buddy/test_nothing_ran_and_from_original.py`: in all three speakers, the delivered reply has the sentence with `earlier_deliveries=1` and not with `0`, and is otherwise byte-identical; an undelivered reply never has it; `reset_transient` clears the field.
  - `tests/backend/test_turns.py::test_a_follow_up_delivery_says_it_was_made_from_the_original`: dedupe, then "now remove the empty rows too", in all three speakers. The first reply has no sentence, the second has it, and the second file has 5 rows with the 2 duplicates (so the sentence is true).
  - The rule-1 property test gains `followup_after_delivery`, asserting the sentence on a delivered second turn.
  - `tests/buddy/test_state.py`'s fully populated fixture sets the new field (its guard test required that).

## M3: a finished record that couldn't be saved was published anyway

- **Change.** `backend/turns.py`, `_finalize`: under the session lock, after the one-shot check, the finished record is written (`_save_finished`, a copy of the turn, so nothing is visible yet) **before** anything is published. If that write fails, the result becomes `failed` / `FALLBACK_PIPELINE_ERROR` / no files / state discarded, a best-effort write of that is tried, and that is what's published. A restart reports the same, from the `running` record written at the start. The session record is written after publishing; if that write fails, only the conversation misses the turn after a restart. `SessionRegistry` gains `save_turn` and `save_session`.
- **Test:** `tests/backend/test_turns.py::test_a_finished_record_that_cant_be_saved_finishes_as_failed`. With `save_turn` failing for finished records, a real verified file is written, but the turn is `failed` with the fallback and no files, the file list has only the upload, and a new app on the same data root reports the same.

## LOW items fixed here (not merge conditions)

| Item | Change | Test |
|---|---|---|
| L1 | `backend/logging_filter.py`: `_redact_record` redacts `msg` and each string arg in place, keeping `record.args`' shape. It collapses to the redacted formatted message only if a secret would otherwise survive (for example inside a non-string arg). | `test_a_redacted_access_log_record_still_formats_with_uvicorns_formatter` (uvicorn's `AccessFormatter`); `test_a_secret_inside_a_non_string_argument_is_still_masked`. On a real `python -m backend`: 0 "Logging error" lines, and paths print as `/api/v1/sessions/[REDACTED]`. |
| L2 | none needed (the guard was right) | `test_a_verified_delivery_the_api_cant_hand_back_gets_the_fallback`: a Hands wrapper puts the verified output outside the turn folder; the pipeline's reply says done, the turn is `failed` / fallback / no files |
| L3 | none needed | `test_the_worker_and_the_deadline_racing_finish_the_turn_once`: a barrier in `decide` holds the timer until the worker arrives, so both pass the first check; exactly one finished record is written, and the turn never changes afterwards |
| L4 | none needed | `test_an_upload_that_finishes_while_a_turn_started_is_409_and_kept_nowhere` |
| L5 | none needed | `test_auto_approve_is_off_unless_set` (the dataclass default) |
| L6 | none needed | `test_a_path_through_a_symlinked_sibling_folder_delivers_nothing` |
| L7 | `FALLBACK_INTEGRITY_FAILURE` now says "your original file no longer matches the one you uploaded" (not "changed while I was working") | the existing catalog checks (no completion claim, "held back every file") |
| L8 | `backend/routes.py`: a message that isn't valid Unicode (a lone surrogate) is `422 invalid_request`; `backend/errors.py`: `405` keeps Starlette's `Allow` header | `test_a_message_with_a_lone_surrogate_is_422`; `test_a_wrong_method_gets_the_uniform_error_body` asserts `Allow: GET` |
| L11 | `backend/store.py`: a stored `detail` must validate strictly as `TurnDetail`, or the session isn't restored (clean `404`) | two new cases in `test_a_corrupt_turn_record_is_a_clean_404` |

Still open: L9, L10, L12 and P1, as the review deferred them (they're in `docs/backlog.md`).

## Do the new tests fail on the old code?

- In a scratch worktree at `ae734ae` (the pre-fix code), with the new tests copied in and only the new names added (the two constants, and `save_turn`/`save_session` as plain pass-throughs, with no behaviour change): **47 of the new tests fail.** That covers every M1, M2 and M3 test, L1, L8, L11 and the L3 race test. The L3 test fails there because the old `_finalize` had no `save_turn` call to count, not because of a race, so its real check is the mutation below.
- The tests for guards that already existed (L2, L4, L5, L6) pass on the old code, as they should. Removing each guard in that copy makes its test fail:
  - L2: removing `decide()`'s mismatch branch fails it.
  - L4: removing the register-time busy check fails it.
  - L5: defaulting `auto_approve` to `True` fails it.
  - L6: removing the name-based parent check fails it.
- L3: on the fixed code, removing the second one-shot check in `_finalize` makes `test_the_worker_and_the_deadline_racing_finish_the_turn_once` fail (two finished records written).

## Tests and lint at the head of this round

- `python -m pytest -q`: 1301 passed (Python 3.11.15).
- `ruff check buddy tests`: the same 22 errors as `origin/main`, by file and rule. `ruff check backend core skills scripts tests/backend` and the new buddy test file: clean.
- `python -m backend.export_openapi --check`: in sync (the schema is unchanged).
