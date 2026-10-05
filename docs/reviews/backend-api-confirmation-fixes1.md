# Fixes after review round 1: `feat/backend-api-confirmation`

**Review:** `docs/reviews/backend-api-confirmation-review.md`, round 1 (commit `3ef871c`): DON'T MERGE, one MEDIUM (M1), nine LOWs deferred.
**Fixed here:** M1 (the merge condition), and three of the deferred LOWs that sit in this PR's own new code: L2, L7, and part of L3 (tests only). The rest stay deferred, as the review lists them.

Everything below is the writer's account. Treat it as unverified.

## M1: a chain that can't finish is no longer put to the user, and doesn't start

**The fix is the review's first option.** In `Hands.__call__` (`buddy/hands.py`), every delegated request is resolved through the Dispatcher once, before any step runs (`_routes`), up to the first one it can't route. If that unroutable request comes after a request above READ tier (`_known_stop`), the chain can't finish:
- no plan is announced to a plan-aware provider (the plan is empty), so nobody is asked;
- no chain step runs. Each chain request before the unroutable one is recorded as skipped ("skipped because spreadsheet.sort can't be run"). The unroutable one is dispatched as before, fails as before, and is recorded as before (`failure_reason` "couldn't be run because no available skill can do it", `stopped_at`). Every request after it is skipped as before;
- read-only requests before it (the proactive analysis) run as usual.

The stop error counts the two kinds of skipped step apart ("…; 1 earlier step(s) weren't started and 1 later step(s) were skipped and no file was handed back"), and is unchanged when nothing was skipped before the stop. The reply is the existing stop reply, from the existing code; no new wording. For "remove duplicates and sort by amount" (planner: deduplicate, then `spreadsheet.sort`), in all three speakers:

> Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? I ran into a problem partway through. The sort step: couldn't be run because no available skill can do it. I stopped at the sort step, so deduplicate never ran and no file was handed back; your original file is unchanged.

`confirmation_plan` uses the same routes, so it returns no steps for such a chain, and the plan and the run can't disagree. A request the Dispatcher can't route *before* any chain request stops the turn as it always did (the plan was already empty there).

**This applies to every caller of `build_engine`, not only the API** (the review asks for that test with `NullConfirmationProvider`). It changes which steps run in a chain that stops anyway: before, deduplicate ran and wrote an intermediate file that was never handed back. "The chain stops on any failure … and then nothing is handed back" (`CLAUDE.md`, "Decided behaviour") still holds. **The review asks for the owner's confirmation of this change.** The alternative it names (list the unroutable step in the plan, with owner-reviewed wording, and keep running the earlier steps) needs new user-facing text, which I can't have reviewed by the owner in this loop; it's recorded for the owner in the design's PR-3 implementation notes, item 1.

**Existing tests changed** (each pinned "the earlier step runs, then the chain stops"; each keeps its point: the unroutable step is still recorded, so Inspector sees it, nothing is handed back, and no reply claims completion):
- `tests/buddy/test_hands.py`: `test_hands_leaves_a_next_step_with_no_skill_to_stop_the_chain_itself` (the earlier step is now skipped and never receives a task); `test_hands_records_the_failing_last_step_after_an_earlier_step_passed`, renamed `…_when_the_chain_cant_finish`, which also pins the new stop error; the `not-dispatchable` case of `test_a_chain_stopped_after_an_earlier_step_passed_hands_back_no_file` (no intermediate file exists now).
- `tests/buddy/test_engine_factory.py`: `test_full_slice_never_says_done_when_the_last_step_cant_be_run`, `test_full_slice_personality_voice_never_describes_a_three_step_chain_stopped_midway[3]` and `test_dedupe_and_sort_as_a_chain_hands_nothing_back_and_claims_nothing`: the stop sentence now also names deduplicate as never run.
- `tests/buddy/test_hands_confirmation_plan.py`: my own `test_a_step_the_dispatcher_cant_route_ends_the_plan`, replaced (below).

**New tests:**
- `tests/backend/test_confirmation_api.py::test_a_chain_that_cant_finish_is_never_put_to_the_user_and_writes_nothing[2 cases × 3 speakers]`: the review's `[deduplicate, spreadsheet.sort]` and `[deduplicate, remove_empty_rows, spreadsheet.sort]` never show `awaiting_confirmation`, write no spreadsheet, finish `not_delivered` with the pipeline's stop reply (the exact stop sentence above), `detail.confirmation` null, the chain steps `skipped`, the sort step `failure`, and the original unchanged.
- `tests/buddy/test_hands_confirmation_plan.py`: `test_a_chain_that_cant_finish_asks_nothing_and_runs_no_chain_step` (an empty plan, no `confirm`, only the analysis executes, the steps' statuses and the stop error); `test_a_step_the_dispatcher_cant_route_before_any_chain_step_stops_as_before`; `test_a_chain_that_cant_finish_doesnt_start_without_a_plan_aware_provider` (`NullConfirmationProvider`: nothing executes).

## L2: no step starts once the turn has finished or the server is stopping

`WebConfirmationProvider.confirm` (`backend/confirmation.py`) now refuses every step once the provider is closed, even after an approval. Before, a turn ended by its deadline after the user approved would still run its later steps and write into the finished turn's folder. The recorded decision stays the user's approval; the refusal changes nothing that was already decided or written. `close()`'s docstring already said this; the code now does it.

Tests: `test_confirmation_provider.py::test_once_closed_no_later_step_runs_even_after_an_approval` (replacing `test_closing_after_the_answer_changes_nothing`, which pinned the old behaviour), and through the API, `test_no_step_starts_after_the_deadline_ended_the_turn_even_if_approved` (a two-step plan approved, step 1 slower than the deadline: step 2 is refused).

## L7: the confirmation timeout has an upper bound

`Settings` refuses `confirmation_timeout_s` above `MAX_CONFIRMATION_TIMEOUT_S` (3600) at startup, so the wait always stays within what a thread can wait for. Tests: `test_an_invalid_setting_refuses_to_start` gains `3601` and `99999999999`; `test_the_confirmation_timeout_comes_from_its_variable` accepts 3600.

## L3 (partly): tests for guards no test exercised

- `test_a_worker_that_cant_start_finishes_its_turn_as_failed`: the turn's worker thread fails to start; the request is `500`, and the turn is finished `failed` with the fallback and nothing handed back. The session is free, the slot is released, and the next turn runs.
- `test_a_finished_turn_leaves_no_job_behind`: the runner keeps a job only while the turn is unfinished.
- `test_a_tier_asked_that_isnt_the_plans_is_refused_even_if_the_task_agrees` and `test_a_read_step_is_never_approved_even_if_a_plan_lists_it`: the two tier checks the review found redundant, each now with a case only it catches.
- `test_a_stored_running_turn_with_a_decision_is_not_restored`.
- `test_a_plan_is_never_shown_once_the_deadline_has_claimed_the_turn`: the deadline's finalization is held while the worker asks; the plan is never shown, and the step is refused.

Not added: tests for `_expire`'s generation check, `_settle`'s "first outcome stands", `_on_settle`'s field resets, `TurnRunner.answer`'s pending check and `save_turn`'s status mapping (redundant or race-only, as the review says). Still in `docs/backlog.md`.

## Not fixed here (deferred, as the review lists them)

L1 (a mismatch refusal reported as "wasn't confirmed"; not reachable with today's skills), L4 (duplicate JSON keys), L5 (pre-existing Voice wording), L6 (owner's call), L8 (external-effect tiers, before such a skill exists), L9 (pre-existing).

## Checks

- **New and changed tests on the round-1 code** (`3ef871c`, a worktree with this branch's `tests/`): 20 failed, 16 passed. The 20 are every M1, L2 and L7 test above: the three Hands tests; two of the three new Hands-plan tests; the three engine tests (five cases); the six API cases; the two L2 tests; and the two L7 cases. The 16 that pass are pre-existing cases in the same selection and `test_a_step_the_dispatcher_cant_route_before_any_chain_step_stops_as_before`, which pins unchanged behaviour. The L3 tests guard code that round 1 already had, so they pass there by design.
- **Self-mutations** (a throwaway worktree): removing the pre-check, showing the plan anyway, skipping read-only steps too, letting a closed provider approve, dropping the cap, ignoring `finishing` when publishing, dropping either tier check, never emptying `_jobs`, restoring a running turn with a decision, and not finishing a turn whose worker can't start: each is killed by a named test. One mutation survived because it was equivalent: `_known_stop`'s "a chain step comes first" condition changed nothing (with no chain step before the unroutable request there is nothing to pre-skip, and the plan is empty anyway). It's removed, which makes the code simpler.
- `python -m pytest -q` (Python 3.11.15): **2258 passed**, 11 warnings, 274 s.
- `ruff check buddy tests`: 22 errors, identical to `origin/main` by file and rule. `ruff check backend core skills scripts/live_api_runs.py tests/backend tests/buddy/test_hands_confirmation_plan.py`: clean. `python -m backend.export_openapi --check`: in sync.
- `origin/main` hasn't moved (`3ed023f`).
