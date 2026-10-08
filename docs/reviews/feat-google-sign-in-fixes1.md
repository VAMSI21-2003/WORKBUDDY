# feat/google-sign-in: fixes for review round 1

Review: `docs/reviews/feat-google-sign-in-review.md`, round 1 (commit 948ec6c): DON'T MERGE, one HIGH. Each item below is the writer's fix; the next reviewer decides whether it closes the finding.

## H1: a second holder of the demo password landed in the first holder's session

`index()` handed every principal `registry.latest_for(owner)`, including the shared `demo`, `dev` and `operator`. Fixed in two places, so neither can bring it back alone:
- `backend/web/routes.py` `index()`: the landing applies only when `principal.kind == "google"`.
- `backend/sessions.py` `latest_for()`: returns `None` for any owner that isn't `google:<subject>`.
- `backend/identity.py` docstring corrected (the reviewer was right that "that is what those modes always were" was false: a session id being secret is what kept them apart).

Tests that fail on the old code (`tests/web/test_shared_principals.py`): two demo-password browsers (the second's `GET /` shows nothing of the first's file name or session id and sets no pointer), two dev browsers, an operator script after an operator script, and `latest_for` for `demo`/`dev`/`operator`. I ran them against the commit before the fix: all four fail there.

## L1: lint

An f-string in `tests/deploy/test_deploy_files.py`, a wrapped line in `tests/deploy/test_smoke_auth.py`, and five lines wrapped under `backend/`. `python scripts/check_ruff_baseline.py` passes, and `ruff check backend` is clean. (My earlier "no new errors" was true of the run before I added those two test files; I did not run the baseline check again after adding them.)

## L2: guard-test gaps

Each mutation was applied in place and the new test shown to fail:
- M08, single use of a pending login: the app-level replay cannot show it (a successful callback clears the binder cookie, so a second callback is refused before the pending store is asked), so the test is on `PendingLogins` itself (`test_a_pending_login_can_be_taken_once_even_with_the_right_binder`), and the replay test also asserts no second `POST /token`.
- M12: `test_the_callback_clears_the_last_persons_session_pointer`.
- M19: the landing test now has Bob's session newer than Alice's.
- M26: `test_a_discovery_document_for_another_issuer_is_refused`.
- M30: `test_an_email_is_stored_and_shown_in_lower_case`.

## L3: a stale callback broke a sign-in in progress

`_refused(..., clear_login=False)` for `no_pending_login`. Test: `test_a_stale_callback_does_not_break_a_sign_in_still_going_on` (it failed before).

## L4

`scripts/live_stack/stack_flow.py` uses the operator token when the stack has one (the password for a stack from before sign-in). `scripts/live_api_runs.py`, `live_frontend_runs.py` and `measure_container.py` are not changed: they are in the backlog (the entry the reviewer added), because each has many password-specific scenarios. The implementation record's two wrong statements are corrected in place.

## L5

The design note is corrected (sign-out is htmx; production accepts any `https://` issuer because the live runs use the production image, with a warning logged at start when it isn't Google's).

## Not done

Nothing skipped. Open for the next reviewer's judgement: the choice to accept a non-Google issuer in production (with a warning) rather than refuse it.
