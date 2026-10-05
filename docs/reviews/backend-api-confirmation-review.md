# Review: `feat/backend-api-confirmation` (round 1, commit `3ef871c`)

**Verdict: DON'T MERGE.** No HIGH. One MEDIUM (M1) that an ordinary request reaches: for "remove duplicates and sort by amount" the plan card leaves out the step that will stop the turn, so the user approves a plan that can't hand anything back. The fix is under M1.

**Base:** `origin/main` `3ed023f`. **Design:** `docs/design/backend-api-design.md` with every "Decisions" section. **Scope:** the whole diff `origin/main...3ef871c` (28 files). I treated `docs/reviews/backend-api-confirmation-impl.md` as unverified. Probes: `scripts/review_harness/feat-backend-api-confirmation/` (README there); their outputs are in its `out/`.

## What I ran

| What | Result |
|---|---|
| `python -m pytest -q` (Python 3.11.15) | head **2241 passed** (266.6 s); base 2142 passed (239.2 s) |
| `ruff check buddy tests` | 22 errors on head and on base, identical by file and rule: **no new lint error**. `ruff check backend core skills scripts/live_api_runs.py`: clean on both |
| `python -m backend.export_openapi --check` | in sync; the confirmation route declares `ErrorBody` for 404/409/413/422/500, `steps` has `minItems: 1` |
| `api_probe.py`: 25 turns x 3 speakers, on head and on base | Approve, reject and timeout behave as designed in all three speakers. Head vs base (`out/api_probe_head_vs_base.txt`): the only differences are the dropped "I ran into a problem partway through." on a declined first step, and catalog openers/closers seeded by the random session id. Scenarios 19, 21, 25: M1 |
| `adversarial_probe.py`: 13 cases x 3 speakers (head only: the base has no endpoint) | Answer vs timeout (36 trials, approve sent 60 ms before to 60 ms after `expires_at`): a file is delivered iff the recorded decision is approve-by-user; no approve accepted after expiry. Approve vs reject at once (36 trials): exactly one `200`, outcome matches it. Stale ids, cross-turn ids, 13 malformed bodies (`422`, the plan keeps waiting), an encoded `../` turn id (`404`), other methods (`405`), shutdown while waiting and before asking, stage raising after the answer (no exception text in any reply), session busy/`429` while waiting, `file_id`: as designed. Findings L1, L2, L4, L6, L7 |
| `decline_reply_probe.py` (pipeline only, head and base, 3 speakers) | Same as the API: only the "problem" lead differs, and only when the first file-changing step was declined. L5 |
| `live_plan_check.py`: 2 turns with the real model (Groq via the session proxy) | M1 reproduced, reject and approve (`out/live_plan_check_*.json`). No 429 |
| `mutations.py`: 63 one-line mutations of the new guards, each run against `tests/backend` and the Hands, Voice and engine tests (`out/mutations.txt`) | **49 killed, 14 survived** (L3). Every guard whose removal would let a step run unapproved, approve on a timeout or shutdown, accept a stale or second answer, change the plan's steps or text, or change a reply is killed by a test that fits it (`out/mutations_rerun.txt` names the test for six). The survivors are redundant, race-only or untested-but-correct (L3) |
| `new_tests_on_base.py`: this branch's `tests/` over the base code (`out/new_tests_on_base.txt`) | Every new or changed test file fails there: the three new modules can't import, the new Voice test fails on "problem", `test_app_basics.py` 9 failed, `test_openapi_sync.py` 4 failed |
| Earlier reviews' pipeline harnesses, head vs base (`out/older_harnesses.txt`) | `feat-backend-api-core/pipeline_compare.py`: 21 of 21 the same. PR-B `harness.py`: 532 turns identical, 0 Voice model calls. `round5.py`: 228 turns, the only difference is the dropped "problem" lead in its decline scenario. No regression outside the plan flow |
| Rule 2 | the original's SHA-256 was unchanged in all **315** runs, head and base (`out/rule_checks.txt`) |
| Rule 1 | no reply claims a change that didn't happen. All 67 delivered files, read with openpyxl, match the reply's row, duplicate and empty-row counts; no reply without a delivery contains "Done", "Removed", "Saved as" or "is ready" |

## Findings

### M1 (MEDIUM, reachable): the plan leaves out a step that will stop the turn, and asks for an approval that can't lead to a file

- **Where:** `buddy/hands.py:516-519`, `Hands.confirmation_plan` (`except DispatchError: break`), which `Hands.__call__` hands to `WebConfirmationProvider.expect_plan` (`buddy/hands.py:228-230`).
- **Scenario [Certain]:** "remove duplicates and sort by amount". With the real model, the intent is `multi_step_request` and the planner's steps are deduplicate then `spreadsheet.sort`, which no skill can do. The card shows one step, "Remove duplicate rows from manual_report.xlsx.", with `plan_note: null`. On approve, deduplicate runs and writes a file, the sort step can't be dispatched, the chain stops, and nothing is handed back. The reply is: "... I ran into a problem partway through. The sort step: couldn't be run because no available skill can do it. I stopped at the sort step and no file was handed back; your original file is unchanged." The reply is true; the card misled the approval. With two supported steps before the unsupported one (probe scenario 25), the card shows both steps and `PLAN_NOTE_CHAIN` ("… Only the final file is handed back."), both steps run, and no file is handed back.
- **Why it matters:** the pipeline knows before asking that the turn can't hand anything back, whatever the answer (the Dispatcher already refused the step in `confirmation_plan`). The design requires "the whole plan (every step, in order …)" and "The plan shown to the user must equal the plan that runs" ("Decisions (2026-09-30)", item 2). The implementation notes' deviation 2 only considers the steps after the unroutable one.
- **Reachability [Certain]:** an ordinary request: any supported step followed by one the skills can't do. The planner's prompt tells the model to keep such a step ("Never omit a requested action just because no matching capability is listed", `buddy/planner.py:613-619`). Seen live here and in `docs/reviews/verified-outcome-b-live-runs.md`, run 4.
- **Fix:** don't ask for, or run, a chain that is known to stop. In `Hands.__call__`, when the Dispatcher can't route a delegated request and a request before it is above READ tier, run no chain step and ask nothing: record that request's failure as today (same `failure_reason` and `stopped_at`) and every chain request as skipped; read-only steps run as today. The existing stop reply then applies ("The sort step: couldn't be run … I stopped at the sort step, so deduplicate never ran and no file was handed back; your original file is unchanged."). Tests that fail today: through the API in all three speakers, the planner's `[deduplicate, spreadsheet.sort]` and `[deduplicate, remove_empty_rows, spreadsheet.sort]` never enter `awaiting_confirmation` and write no file; through `build_engine` with `NullConfirmationProvider`, no chain step runs. This changes which steps run in a chain that will stop anyway ("The chain stops on any failure … and then nothing is handed back" still holds), so the owner should confirm it. If the owner prefers to keep running the earlier steps, the alternative is to list the unroutable step in `steps` with an owner-reviewed template saying it can't be done and nothing will be handed back, and not to show `PLAN_NOTE_CHAIN` for such a plan.

### LOW (deferred)

- **L1. A step refused for not matching the approved plan is reported as "wasn't confirmed".** `backend/confirmation.py:252` (`confirm`) and `:321` (`_is_next_step`). Probe `mismatch_after_approval` (step 2 resolves at run time to a skill of another tier): the user approved both steps; the reply says "I ran into a problem partway through. The remove empty rows step: wasn't confirmed, so it didn't run. …" while `detail.confirmation` is `{approve, user}`. Not reachable with the registered skills (their `can_handle` checks only the extension) [Certain]. It's also a design deviation: "the turn's failure path reports it". Fix: on that refusal record a distinct decision source (for example `plan_mismatch`) and finish the turn `failed` with `FALLBACK_PIPELINE_ERROR` in `decide`; test with the probe's two-registry setup.
- **L2. After the deadline (or shutdown) has ended a turn, an approved plan's later steps still run.** `confirm` returns the stored approval after `close()` (`backend/confirmation.py:266-267`). Probe `deadline_after_approval`: a two-step chain, approved, step 1 slower than the deadline: the turn finishes `failed` with the fallback (correct), then the worker runs step 2 and writes a second file into the finished turn's folder. Nothing is delivered; the original is unchanged. `close()`'s docstring says a later question is a rejection. Fix: in `confirm`, return `False` once `_closed_by` is set, even after an approval; test that no step runs after the turn finished.
- **L3. Guards no test exercises (14 of 63 mutations survive, `out/mutations.txt`).**
  - Redundant with another check today (7): in `_is_next_step`, `tier is expected.tier` and `tier in _CONFIRMED_TIERS`; `_settle`'s "first outcome stands"; `_on_settle` clearing `pending_confirmation` and resetting the status; `TurnRunner.answer`'s pending check; `save_turn`'s status mapping.
  - Race guards (4): `_expire`'s generation check and its `finishing` flag, `_on_publish`'s `finishing` check, and closing the plan in `_finalize`.
  - Untested behavior (3): finishing a turn whose worker thread fails to start (my probe: `500`, the turn `failed` with the fallback, the session free, the slot released, as the implementation notes say); dropping a finished turn's job from `_jobs` (without it every turn's state stays in memory); refusing a stored running turn that carries a decision.
  - Fix: tests for each. Notably the thread-start failure; `_jobs` empty after a turn finishes; `confirm(_task(1, WRITE), EXTERNAL_EFFECT)` and a plan announced with a READ step; and a runner test that holds the deadline's `_finalize` (for example inside `uploads_unchanged`) while the worker asks, asserting the plan is never shown.
- **L4. Duplicate keys in the confirmation body: the last one wins.** `{"confirmation_id": …, "decision": "reject", "decision": "approve"}` gets `200` and approves. The frontend sends one key; a proxy or log that reads the first key would disagree with the server. Fix: refuse duplicate keys with `422`.
- **L5. A declined plan whose initial analysis failed: the reply mentions no problem at all.** `TemplateSpeaker._stopped_message` never mentions a failed analysis (pre-existing); the dropped "problem" lead was the only hint. Reply: "The deduplicate step: wasn't confirmed, so it didn't run. I stopped at the deduplicate step and no file was handed back; your original file is unchanged." Fix: append `INITIAL_ANALYSIS_FAILED_SENTENCE` there when `_initial_analysis_failed(verification_result)`.
- **L6. Shutdown before the plan is asked: "wasn't confirmed", though nobody was asked.** Probe `shutdown_before_asked`: no plan shown, `decision_source: shutdown`, the decline reply. Only during a server shutdown, and the record rarely outlives the process. Fix (owner's call): finish such a turn `failed` with the fallback.
- **L7. `WORKBUDDY_CONFIRMATION_TIMEOUT_S` has no upper bound.** Above `threading.TIMEOUT_MAX` (about 9.2e9 s) the server starts, and every file-changing turn fails ("Something went wrong while running the steps …", `decision_source: turn_finished`) because `Event.wait` raises `OverflowError`. Operator error only. Fix: cap it in `Settings.__post_init__` (for example at 3600) and test it.
- **L8. External-effect steps ride on the plan approval.** `_CONFIRMATION_REQUIRED_TIERS` and `_CONFIRMED_TIERS` include `EXTERNAL_EFFECT`. Part 2 ask 1 says "Write-tier steps only", and the 2026-09-30 rule-2 note says a skill that can affect an original "must not ride on a plan approval". No such skill exists. Fix, before one is added: ask for such a step separately, or refuse it.
- **L9 (pre-existing, seen while probing).** A chain of five or more steps fails with "could not write the cleaned copy", because each step appends `_<op>_<key>-task-N` to the name until it passes 255 bytes (`many_steps`). The reply is honest.

## Design deviations

- **Implementer's 1 (the plan is built and announced by Hands, not by the turn procedure):** justified by C5 ("Decisions (2026-10-01)", item 9); no gap found.
- **Implementer's 2 (an unroutable step ends the plan):** M1.
- **Implementer's 3 (a refused unlisted step finishes `not_delivered` with the decline reply):** L1.
- **Implementer's 4 to 9** (`detail.confirmation` and `turn_finished`; the decline lead; `WORKBUDDY_AUTO_APPROVE` ignored with a warning; the response typed as the turn union; a malformed id is `422`; no per-turn `version`): as described. `detail.confirmation` is absent whenever `detail` is `null` (fallback-finished turns); the stored record still has the decision, which is all Section 1.5 asks.
- **Not listed:** L8.
- Everything else checked matches: templates and `PLAN_NOTE_CHAIN` word for word ("Decisions (2026-10-01)", item 4); the public shape without step-1 fields and with `minItems: 1`, the same on all three routes (item 5); the deadline paused while waiting (4.3); timeout and shutdown as rejections, and the two `409` codes (1.5); the PR-3 tests that "Decisions (2026-09-30)" item 2 and part 2 ask 7 require; R3-L4.

## Deferred

L1 to L9 above, added to `docs/backlog.md` (section 5).

## Not run

- Python 3.13, Windows, a non-root user.
- The writer's live runs 3, 4, T and C (they follow a MERGE verdict). My two live turns only check M1.
- Shutdown through a real uvicorn process and a signal: shutdown was probed through the app's lifespan (`TestClient` exit) only.
- Load beyond two concurrent sessions.

---

# Review round 2 (commit b1059d3)

**Verdict: MERGE.** No HIGH, and no MEDIUM that an ordinary request reaches. Round 1's M1 is fixed: a chain the Dispatcher can tell won't finish is never put to the user and doesn't start. I confirmed this through the API in all three speakers, through `build_engine` alone, and live with Groq. L2 and L7 are fixed, and L3 is mostly closed. Five LOWs (one of them pre-existing) and one pre-existing rule-1-class item are deferred. One item is for the owner: the M1 fix changes which steps run in a chain that stops anyway (see "For the owner").

**Scope:** the diff `3ef871c..b1059d3` (`buddy/hands.py`, `backend/confirmation.py`, `backend/settings.py`, tests, docs), round 1's open findings (M1, L1 to L9), and regression against `origin/main` `3ed023f`, which hasn't moved since round 1. I treated `backend-api-confirmation-fixes1.md` as unverified. Probes are in `scripts/review_harness/feat-backend-api-confirmation/` (see "Round 2" in its README), and their outputs in its `out/r2/`.

## What I ran (round 2)

| What | Result |
|---|---|
| `python -m pytest -q` (Python 3.11.15) | **2258 passed** (274.4 s) |
| `ruff check buddy tests` | 22 errors on head, on round 1's head and on base, identical by file and rule (the one in `buddy/hands.py` is the same pre-existing line). **No new lint error.** `ruff check backend core skills scripts/live_api_runs.py tests/backend tests/buddy/test_hands_confirmation_plan.py` and my scripts are clean. `python -m backend.export_openapi --check`: in sync |
| Round 1's `api_probe.py`, 25 turns x 3 speakers (`out/r2/api_probe_*`) | Against round 1's head, only scenarios 19, 21 and 25 differ (the M1 fix), once the catalog's seeded openers and closers are set aside. Against base, those three differ, plus the decline turns' dropped "problem" lead (known since round 1) |
| Round 1's `adversarial_probe.py`, 13 cases x 3 speakers (`out/r2/adversarial_*.jsonl`) | Same as round 1, except `deadline_after_approval` (step 2 is now `declined` and writes nothing: L2 is fixed) and `settings_from_env` (`99999999999` is refused: L7 is fixed). In the races, answer vs timeout held its invariant in 36 of 36 trials and approve vs reject in 36 of 36; no approval was accepted after `expires_at`. L4 still reproduces |
| `r2_probe.py`: 16 cases x 3 speakers, through the API and through `build_engine` with its default `NullConfirmationProvider`, on head, round 1's head and base (`out/r2/r2_probe_*`) | I tried every chain with an unroutable step after a file-changing step: the step last, in the middle, after two steps, after a repeated step, or after an analysis the plan names; a misspelled, mis-cased, read-like or hostile id; an unconfirmed order; a header-only or duplicate-free file; a second turn after a delivery. In every one: no plan, no status but `running`, only the analysis runs, nothing is written to the turn folder, the outcome is `not_delivered`, `detail.confirmation` is null, and the API gives the same reply as the engine. On round 1's head, every one of them showed a plan that left out the step that would stop the turn, and wrote intermediate files; base ran the earlier steps. The two controls (unroutable step first; a supported chain) are identical on all three trees |
| `r2_l2_probe.py` (`out/r2/l2_probe_*.jsonl`) | Found R2-L1 and R2-L2 |
| `live_plan_check.py` with Groq, 3 turns (`out/r2/live_*.json`) | "remove duplicates and sort by amount", approved: no plan shown; analysis `success`, deduplicate `skipped`, sort `failure`; nothing written; original unchanged. "remove duplicate rows, then remove empty rows", approved: the two-step plan with `PLAN_NOTE_CHAIN`, then delivered; openpyxl reads 3 rows, 0 duplicates and 0 empty rows, as the reply says. A message naming a claim as a step: the planner wrote `general.confirmation`, so R2-P1 wasn't reached. No 429 |
| `r2_new_tests_on_old.py` on round 1's head (`out/r2/new_tests_on_r1head.txt`) | 20 of the 37 test cases fail there, which covers every M1, L2 and L7 test. The fixes file says 20 failed and 16 passed; I count 17 passed. The cases that pass are pre-existing parameter cases and the L3 tests, which guard code round 1 already had |
| `r2_mutations.py`: 17 mutations of the round-1 fixes, plus round 1's 14 survivors again (`out/r2/mutations.txt`) | Of the 17 new mutations, 16 are killed, each by a test that fits it. The one that survives is equivalent (`index <= blocked_at`: the unroutable step's route is `None`, which `_changes_files` already excludes). Of round 1's 14 survivors, 9 are now killed. The 5 that still survive are the redundant or race-only guards the fixes file lists as untested: `_expire`'s generation check, the two field resets in `_on_settle`, `TurnRunner.answer`'s pending check, and `save_turn`'s status mapping |
| Earlier harnesses, head vs base (`out/r2/pipeline_compare_head_vs_base.txt`, `out/r2/older_harnesses_head_vs_base.txt`) | `pipeline_compare.py`: 21 of 21 the same. PR-B `harness.py`: 532 turns, 518 identical. The 14 that differ are all `r-chain-stop` (planner: deduplicate, then sort): deduplicate is now `skipped`, and the reply adds "so deduplicate never ran". 0 Voice calls. `round5.py`: 228 turns, 210 identical. The rest are `d03-decline`'s dropped "problem" lead (known since round 1) and temp-folder paths in `d01` and `d02` |
| Rule 2 (`r2_rule_checks.py`, `out/r2/rule_checks.txt`) | The original's SHA-256 was unchanged in all 2,037 runs checked: head, round 1's head and base; the API, the engine and the earlier harnesses |
| Rule 1 (same) | I made 1,252 checks of delivered files read with openpyxl. Each file matches its reply's row, duplicate and empty-row counts, and the number of rows the reply says were removed (the removed count only where the harness recorded the original's counts). No reply that delivered nothing says "Done", "Removed", "Saved as", "is ready" or "cleaned copy", with two exceptions. One is R2-P1's planted capability text (18 replies, identical on base). The other is round 1's L9 reason "could not write the cleaned copy", which is true |

## Round 1's findings

| | Status (my evidence) |
|---|---|
| **M1** | **Fixed [Certain].** `Hands.__call__` resolves every request once (`_routes`, `buddy/hands.py:546-564`). When one can't be routed (`_known_stop`), the plan is empty, and every file-changing step before it is recorded `skipped` and never runs (`:275`). The evidence is in the table: the API in all three speakers, the engine path, and the live run. Every mutation of its guards is killed except the one equivalent mutation |
| L1 | Open, deferred (not reachable with today's skills) |
| **L2** | **Fixed for every later step [Certain]** (`backend/confirmation.py:266-270`). `deadline_after_approval` now refuses step 2 and writes one file, not two. The provider test and the API test fail on round 1's code, and the three mutations that bring back the old behaviour are killed. A narrow residual is R2-L1, and a side effect at shutdown is R2-L2 |
| L3 | **Mostly closed [Certain]:** 9 of round 1's 14 surviving mutations are now killed by the new tests. The 5 that survive are redundant or race-only. Deferred |
| L4 | Open, deferred: duplicate keys are still accepted, and the last one wins |
| L5, L6, L8, L9 | Open, deferred. R2-L2 is a sibling of L6 |
| **L7** | **Fixed [Certain]** (`backend/settings.py:103-108`). 3601 and 99999999999 are refused at startup and 3600 is accepted. The tests fail on round 1's code, and the three mutations are killed |

## New findings (all LOW, deferred)

- **R2-L1. The step the user just approved can still start after the provider is closed.**
  - **Where:** `backend/confirmation.py:283-288`. After `self._answered.wait(...)`, `confirm` returns the stored decision without checking `_closed_by`.
  - **Scenario:** if the deadline's finalization (or a shutdown) closes the provider after the user answers but before the worker takes the lock back, the first step starts after the turn has finished.
  - **Evidence:** [Certain] at the provider. `r2_l2_probe.py` holds the lock across `answer("approve")` and `close(BY_TURN_END)`, and `confirm` returns `True`; step 2 then gets `False`. [Likely] rare through the API. I forced the working time to zero when the plan was published: in 60 of 60 trials step 1 started before the finalization completed, and never after it. Nothing was delivered, and every original was unchanged.
  - **Effect:** a stray intermediate file in a finished turn's folder, never handed back.
  - **Fix:** in `confirm`, after settling the timeout case, add `if self._closed_by is not None: return False`, and test it with the probe's sequence. I prototyped this in a throwaway copy: the probe then gets `False`, and `tests/backend/test_confirmation_provider.py`, `test_confirmation_api.py` and `test_turns.py` pass (222).
- **R2-L2. A shutdown after the user approved now ends the turn with "wasn't confirmed".** This is a side effect of the L2 fix.
  - **Scenario:** the server shuts down while step 1 of an approved two-step chain is running.
  - **Result:** step 2 is refused. If the worker finishes before the process exits, the turn is `not_delivered` with "The remove empty rows step: wasn't confirmed, so it didn't run.", while `detail.confirmation` is `{approve, user}` [Certain, `out/r2/l2_probe_head.jsonl`, all three speakers]. On round 1's head, the same chain finished and delivered.
  - **Reachability:** only during a server shutdown. Under uvicorn the worker is a daemon thread, so it rarely outlives the process.
  - **Fix (owner's call, together with L6):** finish any turn whose provider was closed by `shutdown` as `failed`, with `FALLBACK_PIPELINE_ERROR`. For example, `TurnRunner._run` could finalize with `state=None` when the gate was closed by `BY_SHUTDOWN`.
- **R2-L3. The wording of the M1 stop reply.**
  - **Scenario:** "remove duplicates and sort by amount".
  - **Reply:** "… I ran into a problem partway through. The sort step: couldn't be run because no available skill can do it. I stopped at the sort step, so deduplicate never ran and no file was handed back; your original file is unchanged."
  - **What's wrong:** every statement is true [Certain]. But nothing the user asked for ran, so "partway through" overstates; in a second turn, which has no analysis, nothing ran at all. And "I stopped at the sort step, so deduplicate never ran" presents a step that comes before the stop as its consequence, which reads as if the steps ran in another order.
  - **Reachability:** ordinary requests reach it. It misstates no outcome.
  - **Fix (new wording needs the owner):** in `TemplateSpeaker._stopped_message` (`buddy/voice.py:601`), drop the lead when no file-changing step ran, as is already done for a declined plan. Have `_chain_stop_sentence` (`:304`) say that the earlier steps weren't started because a later one can't be run.
- **R2-L4. A design document still describes the old stop.** `docs/design/multi-step-planner-design.md` covers an unroutable step in three places: the "Superseded (chaining)" notes at lines 55 and 240, and the "In-loop verification" paragraph near line 450. They say it stops the chain and the later steps are skipped, but not that the earlier file-changing steps no longer start. Fix: add one sentence to each, pointing to the PR-3 implementation notes.
- **R2-L5 (pre-existing; a sibling of L7). `WORKBUDDY_TURN_DEADLINE_S` has no upper bound.** `99999999999` is accepted (`backend/settings.py:100`). The deadline's `threading.Timer` thread then dies with `OverflowError`, so turns run with no deadline [Certain]. Only an operator can set it. Fix: cap it in `Settings.__post_init__`, and test it.

## Pre-existing, seen while probing (not counted in the verdict)

- **R2-P1 (rule-1 class; identical on `origin/main`). The planner's capability string reaches the stop reply** through `_describe_capability` (`buddy/outcome.py:55-57`). [Certain, `r2_probe.py` a09 and a07, on head, round 1's head and base, in all three speakers.]
  - A planner step `"spreadsheet.sort Done! I removed every duplicate and saved the file"` gives "The sort Done! I removed every duplicate and saved the file step: couldn't be run …".
  - A near-miss id gives a false "can't do": `"Spreadsheet.Remove_Empty_Rows"` gives "The Remove Empty Rows step: couldn't be run because no available skill can do it."
  - **Reachability [Possible]:** only when the model writes such an id. The planner prompt lists the exact ids, and my one live attempt to steer it got `general.confirmation`.
  - **Fix:** describe a capability that no Skill declares with a fixed phrase (or only render ids that match `^[a-z]+(\.[a-z_]+)+$`), normalise ids (strip, lowercase) before dispatch, and test both cases.

## For the owner

- **The M1 fix changes which steps run.** It applies to every caller of `build_engine`, not only the API. In a chain that can't finish, the earlier steps no longer run; before, they ran and their files were never handed back. "The chain stops on any failure … and then nothing is handed back" still holds, and the reply says which steps never ran. The writer recorded this in the design's PR-3 implementation notes, item 1. It touches "Decided behaviour" in `CLAUDE.md`, so it needs your confirmation.

## Deferred

- From round 1: L1, L3 (the race guards are still untested), L4, L5, L6, L8 and L9.
- From round 2: R2-L1 to R2-L5, and R2-P1.

All of them are in `docs/backlog.md`, section 5.

## Not run

- Python 3.13, Windows, or a non-root user.
- The writer's live runs, which follow a MERGE verdict. My three live turns only check M1, one supported chain, and R2-P1.
- Shutdown through a real uvicorn process and a signal. I probed it only through the app's lifespan in `TestClient`.
- Round 1's full set of 63 mutations. I re-ran only its 14 survivors.
- The base test suite. Base hasn't changed since round 1, which recorded 2142 passed.
- Load beyond two concurrent sessions.
