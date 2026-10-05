# Implementation notes: `feat/backend-api-confirmation` (PR-3 of the backend API design)

**Branch:** `feat/backend-api-confirmation`, from `origin/main` (`3ed023f`, PR-2 merged as #10).
**Design:** `docs/design/backend-api-design.md`, PR-3 (Section 8.3 item 3), with every "Decisions" section applied (later sections win): "Decisions (2026-09-30)" item 2, part 2 asks 1 and 7 and item 4, "Decisions (2026-10-01)" items 4, 5, 7 and 9 (review C5).
**Also in this PR:** R3-L4 from `docs/reviews/backend-api-core-review.md` (a raise inside finalization left the turn running and the session blocked). No other deferred item was worked on.
**Rule-touching:** yes, both rules. The confirmation decides whether a file-changing step runs at all, and `buddy/hands.py` and `buddy/voice.py` change.
**Not in this PR:** PR-4 (demo password and access cookie, CSRF check, rate limits and buckets, retention sweep, per-session quotas, hosted binding and CORS).

Everything below is the writer's account. Treat it as unverified.

## What changed

| File | Change |
|---|---|
| `buddy/hands.py` | `ConfirmationStep`; `PlanAwareConfirmationProvider` (a protocol with `confirm` and `expect_plan`); `Hands.confirmation_plan` resolves every delegated step through the Dispatcher before any runs; `__call__` hands that plan to a plan-aware provider before its first step. `ConfirmationProvider.confirm` is unchanged |
| `backend/confirmation.py` | Rewritten: the two templates per capability and the fallback, `PLAN_NOTE_CHAIN`, `build_plan`, `WebConfirmationProvider`, and `StaticConfirmationProvider` (now a test setting only) |
| `backend/turns.py` | One provider per turn; `answer` (the endpoint's call) and `shutdown`; the deadline paused while the plan waits; R3-L4 and the finishing-block guards |
| `backend/routes.py` | `POST /api/v1/sessions/{id}/turns/{turn_id}/confirmation` |
| `backend/schemas.py`, `backend/views.py` | `PendingConfirmation` (`confirmation_id`, `steps` with at least one `{capability, prompt}`, `plan_note`, `expires_at`), the `awaiting_confirmation` turn variant, `SessionState.pending_confirmation`, `ConfirmationRequest`, `detail.confirmation` |
| `backend/sessions.py` | `TurnRecord.status` may be `awaiting_confirmation`; `pending_confirmation` (live only) and `confirmation` (the decision, persisted) |
| `backend/store.py` | Writes the decision with the turn; an unfinished turn is always written as `running`; PR-2 records (no `confirmation` key) still restore; a malformed stored decision fails restore |
| `backend/errors.py` | `409 no_pending_confirmation`, `409 stale_confirmation` |
| `backend/settings.py` | `confirmation_timeout_s` (`WORKBUDDY_CONFIRMATION_TIMEOUT_S`, default 120); `WORKBUDDY_AUTO_APPROVE` is no longer read (a warning if set) |
| `backend/main.py` | A lifespan hook: on shutdown every waiting or not-yet-asked plan is rejected |
| `backend/openapi.json` | Regenerated |
| `buddy/voice.py` | A chain stopped because its first file-changing step wasn't confirmed no longer opens with "I ran into a problem partway through." |
| `scripts/live_api_runs.py` | No `WORKBUDDY_AUTO_APPROVE`; answers each plan over HTTP with the run's decision and records it; runs 3, 4, T (timeout) and C (two-step chain approved once) |
| `README.md`, `docs/backlog.md`, `docs/design/backend-api-design.md` | The flow; statuses; "Implementation notes, recorded with PR-3 (not owner decisions)" at the end of the design |

## The flow

1. `POST …/turns` starts the turn (`running`) with its own `WebConfirmationProvider`, whose lock is the session's lock.
2. Hands, before its first step, calls `confirmation_plan` and gives the provider every step above READ tier, in order, each with the task id it will run under and the tier of the Skill the Dispatcher selects.
3. The read-only analysis runs. At the first file-changing step Hands calls `confirm`. The provider builds the plan from the templates, and publishes it: the turn becomes `awaiting_confirmation` with `pending_confirmation`, and its deadline timer is disarmed, keeping the working time left. The worker then waits, without the lock, up to `confirmation_timeout_s`.
4. `POST …/confirmation` with `{confirmation_id, decision}` records the answer under the lock. The plan stops being pending before the response is built, so the `200` body is the turn as it now stands (`running`, `pending_confirmation: null`). The deadline is re-armed with the time left, and the worker wakes.
5. Approve: `True` for that step and every later step of the plan. Reject, timeout (`decision_source: "timeout"`), shutdown (`"shutdown"`), or a turn whose deadline already claimed it (`"turn_finished"`): `False` for that step and every later one. Hands records the step as declined, the chain stops, nothing is handed back, and the reply is the pipeline's.
6. The turn finishes as before (one-shot, under the lock). The decision is written with the finished record and shown as `detail.confirmation`.

A turn whose plan has no step above READ tier (only the analysis, or nothing) never asks and never shows `awaiting_confirmation`.

## Design requirements and where they're tested

| Requirement | Where | Tests |
|---|---|---|
| One plan per turn, shown before the first file-changing step, approved or rejected once (09-30 item 2) | `WebConfirmationProvider.confirm`, `Hands.__call__` | `test_confirmation_api.py`: `test_the_plan_is_shown_before_any_file_changes_and_approving_it_delivers` (no spreadsheet written while waiting), `test_a_two_step_chain_asks_once_and_the_plan_is_what_runs`; provider: `test_approve_runs_every_step_of_the_plan_after_one_question` |
| Reject: no step runs, no file, the reply from the pipeline claims no change (09-30 item 2; Section 1.5's missing decline test) | provider; `buddy/voice.py` | `test_a_rejected_plan_runs_no_step_and_the_pipelines_reply_says_so[3 speakers]`, `test_a_rejected_chain_runs_neither_step`, rule-1 scenarios `declined` and `chain_declined` |
| Timeout behaves as reject | provider | `test_no_answer_before_the_plan_expires_is_a_rejection[3 speakers]`, provider `test_no_answer_in_time_is_a_rejection`, rule-1 scenario `timed_out` |
| An unlisted step gets `False`; a tier not in the published plan is refused (09-30 item 2, rule-2 note) | `_is_next_step` | provider: `test_an_unlisted_step_is_refused_without_asking`, `test_a_step_whose_tier_is_not_the_published_plans_is_refused[2]`, `…out_of_order…`, `…past_the_end…`, `…without_a_plan…`; through real Hands: `test_a_step_that_resolves_to_another_tier_at_run_time_is_refused_not_run` |
| A READ-only plan never enters `awaiting_confirmation` | `Hands.confirmation_plan` | `test_a_turn_without_a_file_changing_step_never_waits_for_an_answer`, `test_a_plan_with_only_read_steps_asks_nothing` |
| `steps` equals the Write-tier steps Hands executes, in order (part 2 ask 1) | Hands builds the plan from its own delegation and Dispatcher | `test_a_two_step_chain_asks_once_and_the_plan_is_what_runs`, `test_hands_announces_every_confirming_step_before_any_step_runs` |
| Reply, source and outcome set only once finished, for reject and timeout (part 2 ask 7) | — | `wait_finished` checks every poll, and every awaiting poll's plan shape |
| Templates for every registered Write-tier capability; step 1 names the upload; step 2 says "the result of step 1" and not the upload; `plan_note` is `PLAN_NOTE_CHAIN` for 2+ steps and `null` for one; no "save" or "new file" (10-01 item 4) | `backend/confirmation.py` | `test_confirmation_provider.py` template tests; the chain API test |
| Each step k ≥ 2 reads step k−1's output, in the same run (10-01 item 4) | Hands' chain | `test_a_two_step_chain_asks_once_and_the_plan_is_what_runs` (from the run's own step results), `test_each_later_step_reads_the_previous_steps_output_as_the_plan_text_says` |
| No compatibility fields; `steps` required and `minItems: 1`; the same shape on the turn, the session and the confirmation response (10-01 item 5) | `schemas.py`, `views.py` | `test_openapi_sync.py`: `test_pending_confirmation_has_the_plan_and_no_step_one_only_fields`, `test_only_the_awaiting_turn_carries_the_plan_and_there_it_is_required`; `wait_finished`'s key check on every awaiting poll; `test_a_plan_is_never_built_without_a_step`, `test_an_empty_plan_publishes_nothing` |
| `WebConfirmationProvider`: approve, reject, timeout, shutdown, double answer, stale id (7.2) | provider | the matching tests in `test_confirmation_provider.py` |
| Confirm both answers, timeout, stale id, wrong session (7.3 item 5) | endpoint | the API tests above, `test_a_stale_id_is_409_and_the_plan_keeps_waiting`, `test_another_sessions_answer_never_decides_this_plan` |
| `decision_source: "timeout"` noted in the turn's record (1.5) | `store.py`, `views.py` | the timeout tests, `test_the_decision_is_kept_with_the_turn_across_a_restart` |
| Shutdown treated as reject (1.5) | `main.py` lifespan, `TurnRunner.shutdown` | `test_a_server_shutdown_rejects_the_waiting_plan` |
| Deadline excludes time awaiting confirmation (4.3) | `_on_publish` / `_on_settle` | `test_the_deadline_doesnt_count_the_wait_for_an_answer`, `test_the_deadline_counts_the_work_after_the_answer`, `test_the_working_time_before_and_after_the_wait_adds_up`, `test_working_time_under_the_deadline_finishes_however_long_the_wait`, `test_a_plan_asked_after_the_deadline_is_never_shown` |
| Auto-approve off by default, a test setting only (8.3) | `settings.py`, `turns.py` | `test_auto_approve_is_never_read_from_the_environment[5]`, `test_each_turn_gets_its_own_provider_and_auto_approve_is_only_a_test_setting` |
| `openapi.json` regenerated | `export_openapi.py` | `test_the_committed_schema_matches_the_app_byte_for_byte`, `test_the_confirmation_route_is_declared_with_its_errors` |
| R3-L4 | `_finalize` | `test_a_raise_inside_finalization_still_finishes_the_turn[3 helpers × Exception/BaseException]`, `test_a_raise_while_finishing_still_finishes_the_turn_as_decided[2]` |

Also: a waiting turn keeps its session busy and its slot under the global cap (`test_a_waiting_turn_keeps_its_session_busy_and_its_slot`); two answers at once record exactly one (`test_two_answers_at_once_record_exactly_one`, also at the provider level); a restart while the plan waits reports the turn as failed and never approved (`test_a_restart_while_the_plan_waits_reports_the_turn_failed`); every invalid body is `422` and leaves the plan waiting.

**`Settings(auto_approve=False)` now means "ask the user", not "decline".** It's the default, and a turn with a file-changing step waits for an answer (up to `confirmation_timeout_s`). Probe scripts from PR-2's review that passed `auto_approve=False` to get a decline now need to answer the plan (or set a short timeout).

**The API suite now runs the real flow by default.** `tests/backend/conftest.py`'s `settings` no longer sets `auto_approve`, and `wait_finished` answers each plan it sees (approve unless told otherwise). So every existing API test's turn goes through `awaiting_confirmation` and the endpoint, as in production (design 7.1). Tests that used "auto-approve off" to get a decline now reject through the endpoint.

**Self-check.** In a throwaway worktree I removed or inverted 20 guards one at a time (the R3-L4 `except BaseException`, the `make_read_only` guard, the next-step and tier checks, timeout → approve, shutdown and close not rejecting, the deadline pause and its carried-over time, Hands not announcing the plan, an unroutable step not ending the plan, the "problem" lead, the session's `pending_confirmation`, the later-step template, the stored decision, the stale-id check, `WORKBUDDY_AUTO_APPROVE` read again, both finishing-block guards, an empty plan). A named test failed for each. The script isn't committed; the reviewer runs their own.

## Deviations from the design, and interpretations

1. **The plan is built in Hands, not by "the turn procedure".** "Decisions (2026-09-30)" item 2 says the turn procedure hands the plan to the provider before Hands runs; item 9 of "Decisions (2026-10-01)" (C5) then leaves it to PR-3 to resolve tiers through the Dispatcher first. The turn procedure can't step in between Planner and Hands (it calls `engine.run` once), and the architecture says Hands is the Dispatcher's only caller. So Hands resolves the plan with its own Dispatcher and hands it over (`expect_plan`) before its first step. `confirm`'s signature is unchanged. Recorded in the design's PR-3 implementation notes, item 1.
2. **A step the Dispatcher can't route ends the plan.** Hands stops the chain there, so a later step could never run; listing it would show the user a step that won't happen.
3. **"The turn's failure path reports it" (09-30 item 2).** A refused unlisted step is recorded by Hands as declined, so the chain stops and the turn finishes `not_delivered` with the pipeline's decline reply ("… wasn't confirmed, so it didn't run … no file was handed back …"), not `failed` with the fallback. The reply is true; the refusal is logged at `ERROR`. If the reviewer reads the design as requiring `failed`, that's a one-line change in `decide`.
4. **`decision_source` is shown in `detail.confirmation`** as well as written with the turn record. Section 1.5 only says "the turn's record notes" it; the live-run reports need it. A fourth value, `turn_finished`, covers a plan that the turn's deadline ended before it could be asked.
5. **The decline reply.** With rejection now an ordinary path, "I ran into a problem partway through." misrepresented a user's "no". It's dropped when the first file-changing step was declined; the rest of the reply is unchanged (implementation note 5 in the design).
6. **`WORKBUDDY_AUTO_APPROVE` is ignored, not refused.** A deployment that still sets it gets the safe behaviour (it asks) and a warning, rather than a failed start.
7. **The confirmation response is declared as the turn union.** In practice it's always the `running` variant: the answer is recorded and the response built under the same lock, before the worker can move on.
8. **A malformed `confirmation_id` is `422`,** checked by the request schema (`^[A-Za-z0-9_-]{1,64}$`) before any state is looked at. A well-formed id that isn't the waiting plan's is `409 stale_confirmation`.
9. **Not added:** the per-turn `version` counter the frontend design mentions for its HTML layer (frontend 1.6, 1.7). It belongs to FE-1. The confirmation route isn't rate-limited (PR-4).

## R3-L4, and the finishing block

- Before the finishing block: `uploads_unchanged` and `decide` share one `try/except BaseException`, `make_read_only` has another; both fall back to `failed` with `FALLBACK_PIPELINE_ERROR` and nothing handed back (the reviewer's fix, as written).
- The deadline's arming and the worker's start are inside one `try`, so a failure to start either finishes the turn as failed instead of leaving it running.
- Inside the finishing block, the two things PR-3 adds or touches that could raise before the turn is marked finished, closing the plan and recording the turn in the session's context, are each guarded. The turn then finishes as decided (memory and disk still agree: the finished record is written first, as before).

## Results

- `python -m pytest -q` (Python 3.11.15): **2241 passed**, 11 warnings, 269 s. `origin/main` (`3ed023f`) gives 2142 passed in this container, so this branch adds 99 tests (and changes some existing ones; see above).
- `ruff check buddy tests`: the same 22 errors as `origin/main`, by file and rule (none new). `ruff check backend core skills scripts/live_api_runs.py tests/backend tests/buddy/test_hands_confirmation_plan.py`: clean.
- `python -m backend.export_openapi --check`: in sync.

## Not done here

- **Live runs with Groq.** By the review loop they come after a MERGE verdict: the design's runs 3 and 4, a confirmation timeout, and a two-step chain approved once, through the real HTTP server (`scripts/live_api_runs.py --runs 3,4,T,C`).
- **Python 3.13, Windows, a non-root user:** not run (this container runs 3.11.15 as root).
