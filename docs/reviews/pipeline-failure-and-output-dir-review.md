# Independent review: `feat/pipeline-failure-and-output-dir` (PR-1 of the backend API design)

**Reviewer:** a separate session with no memory of writing this code. I treated the implementer's account (`pipeline-failure-and-output-dir-impl.md`) and live-run file as unverified claims and checked each one myself.
**Head reviewed:** `9077a4c`, diffed against `origin/main` (`310f660`).
**Design:** `docs/design/backend-api-design.md` on `origin/design/backend-api`, Sections 3.2–3.5, 4, 5.3, 7.2, 8.2 and 8.3 (PR-1), plus the Decisions sections that name PR-1.
**Rule-touching:** yes, both rules. It changes the Engine's error contract and the reply path (rule 1), and where skills write their output (rule 2).
**Probe scripts:** I wrote them in the session scratchpad and didn't commit them. Each probe is described below in enough detail to rerun it. I modified no file under `buddy/`, `core/`, `skills/` or `tests/`.

## Verdict in one line

**MERGE.** I found no HIGH issue. The two MEDIUM issues are not regressions that reach a user through this PR. They are requirements that PR-2 must meet, listed under "What a PR-2 author must do". M1 has a one-line hardening I recommend, but I don't make it a condition of merging.

## 1. Tests, lint, and Python versions

| Check | Result |
|---|---|
| `python -m pytest -q`, system Python 3.11.15 | **781 passed** in 86 s (exit 0) |
| `python -m pytest -q`, fresh `uv` venv, Python 3.11.15, `-r requirements.txt` (setuptools resolved to 80.10.2) | **781 passed** |
| `python -m pytest -q`, fresh `uv` venv, Python 3.13.14, `-r requirements.txt` (setuptools 80.10.2) | **781 passed** |
| The same 3.13 venv **without** the setuptools line | **fails**: `tests/skills/excel/test_analyze.py::test_missing_value_analyzer_flags_a_column_with_missing_values` raises `ModuleNotFoundError: No module named 'pkg_resources'` (run with `-x`, 727 passed before it) |
| `ruff check buddy tests` | 22 errors on the branch, 22 on `main`. Grouped by file and rule (ignoring line numbers), the two sets are **identical**. No new lint errors. |
| `ruff check core skills scripts` | clean on both |

**The setuptools pin is needed and works on both versions. It is a little narrower than necessary, and its comment is inaccurate.**
- **[Certain]** I bisected in a 3.13 venv. `pkg_resources` is present in setuptools 80.10.2 and 81.0.0, and absent from 82.0.0, 83.0.0 and 84.0.0. `<81` is therefore one release more conservative than needed. `<82` would also work. Both are correct.
- **[Certain]** The `requirements.txt` comment and the implementer's notes say a fresh 3.12+ install "can't import `skills/excel/analyze.py`". That's inaccurate. The module imports fine, because `ydata_profiling` is imported lazily inside `MissingValueAnalyzer`. The failure happens when analysis actually **runs**, which in production would be every proactive analysis. The comment also names setuptools 84 as the release that removed `pkg_resources`; it was removed in 82.
- **[Certain]** A `uv venv` on Python 3.11 also has no setuptools (I checked). So the pin matters for a fresh `uv` install on 3.11 too, not only on 3.12+. A stdlib `python -m venv` on 3.11 does seed setuptools, which is probably why the comment says "3.12+".
- These are wording problems (L1). The pin itself is right.

## 2. R6, stage by stage, through a real `build_engine` run

**Probe.** For each of the seven non-Voice stages (`Eyes`, `Brain`, `Think`, `Planner`, `Delegate`, `Hands`, `Inspector`), I replaced the class's `__call__` with one that raises a `RuntimeError`. The error message contains a secret cell value, the upload's path and the session id. I ran each stage in two variants:
- **before:** the stage raises before doing any work;
- **after:** the stage runs the real code, so the state is fully mutated, and then raises.

I ran both variants with `TemplateSpeaker`, `PersonalityVoice` friend and `PersonalityVoice` focus: 42 runs in total. All of them used the real skills on a real `.xlsx` with `turn_output_dir` set. Every LLM call was faked by a queue that counts any call made while Voice runs.

**All 42 runs passed every check:**
- `state.failure == StageFailure(stage=<the slot name>, error_type="RuntimeError")`, and the stage name comes from the closed set `_STAGE_NAMES`.
- Voice ran, and `state.reply == failure_reply(stage)`.
- No model call was made during Voice.
- The reply contains no exception text, no secret value, no upload or output name, no `.xlsx` and no error type.
- `_COMPLETION_CLAIM` matches nothing in the reply.
- The original's SHA-256 is unchanged.

Example reply: "Something went wrong while checking the result, so I can't confirm that any change was made. I haven't handed back any file from this turn."

**"I haven't handed back any file" is only true if the caller withholds the files.** The probe shows that on failed turns, real, correct files remain on the state:

| Failing stage | `execution_result.outputs` names a real file on disk | `verification_result` |
|---|---|---|
| Eyes, Brain, Think, Planner, Delegate | no | `None` |
| Hands, raising before its work | no | `None` |
| Hands, raising after its work | **yes** | `None` |
| Inspector, raising before its work | **yes** | `None` |
| Inspector, raising after its work | **yes** | **`passed=True`** |

The last row is the hazard. A caller that delivers files on "outputs non-empty and verification passed" without also checking `state.failure` would hand over a file while the reply says it hasn't.

**Every way a caller could hand that file to a user.** I searched the whole repo (`grep` for `build_engine`, `Engine(`, `engine.run`, `.outputs`, `switch_active_file`, `record_execution`).
- `scripts/run_dedupe_manually.py` is the only caller of `build_engine` outside the tests. It checks `state.failure` and prints the outputs as "withheld (the turn failed)" with "files handed back: 0". This is correct.
- `Engine.run` returns the state, and the file is reachable from it in four places:
  - `state.execution_result.outputs` (the final file);
  - `state.outputs` (the legacy raw-results list Hands mirrors);
  - `state.execution_result.steps[*].outputs` (intermediate files);
  - `state.verification_result.steps[*].input_reference` of the re-check (the final file again).
- Nothing in the pipeline moves an output into `Context`: `active_file` and `execution_history` aren't touched by Hands. So a failed turn's file doesn't leak into the next turn through the session state.
- **When Voice itself raises** (see M1 below), the Engine re-raises with `state.failure` still `None` and the state looking like a verified delivery: `outputs` names a real file and `verification_result.passed` is `True`.

### What a PR-2 author must do

1. `delivered_files` must test **`state.failure is None` first**, exactly as design 5.3 rule 1 says. It must never infer delivery from `outputs`, from `verification_result.passed`, or from both together. Make the table test in design 7.2 include the row "failure set + outputs present + verified + path inside the folder", and assert that it delivers nothing. That is the Inspector-after row above, and it is the case most likely to slip through.
2. If `engine.run` raises, treat the turn as failed **regardless of what the state holds**: discard the copy (design 3.3 step 7), reply with `FALLBACK_PIPELINE_ERROR`, and deliver nothing. Never call `delivered_files` on a state from a run that raised (M1).
3. Read only `execution_result.outputs`. Never read `state.outputs` or per-step outputs, which include intermediate files.
4. Finalize the turn in a `try/finally`, not only in `except Exception`. A `BaseException` subclass escapes both the Engine and an `except Exception` (L2). Without the `finally`, a turn could stay unfinished.
5. Call `reset_transient()` before every turn. The design's turn procedure already does. A stale `failure` would make the next turn skip every stage (D2 / L4).
6. Keep session ids server-generated (`secrets.token_urlsafe`). Skills put the task id, which includes the session id, into the output file name unchecked (L3).
7. Catch every exception from `Context.from_dict` when restoring a session, not only `ValueError` and `KeyError` (M2).

## 3. Trying to break it

| Attack | What happens | Verdict |
|---|---|---|
| A stage mutates the state and then raises | Covered by the "after" variants above. The mutations stay on the state, `failure` is set, and Voice ignores them. | As designed |
| A stage raises `KeyboardInterrupt`, `SystemExit`, `GeneratorExit`, or a custom `class X(BaseException)` | It escapes `Engine.run`, with `reply=None` and `failure=None` | L2: consistent with the design's `except Exception`, and correct for `KeyboardInterrupt` and `SystemExit`. PR-2 needs a `finally`. |
| Voice raises with no earlier failure | It re-raises (`ZeroDivisionError` in my probe), and the state is left with `failure=None`, a real file in `outputs` and `verification_result.passed=True` | M1 |
| Voice raises after an earlier failure | It re-raises (covered by the implementer's test, and I confirmed it) | As designed |
| `failure` already set before the run, with no `reset_transient()` | Every stage is skipped, no file is written, and Voice says "Something went wrong while checking the result…" for a turn in which nothing ran. After `reset_transient()`, the turn runs normally. | L4 (= D2): the reply claims no outcome, but it names a stage that didn't run. Reachable only through a caller that skips `reset_transient()`. |
| The confirmation provider raises on step 1 (single step), step 1 (chain) or step 2 (chain) | `failure=hands`, the failure reply, `execution_result=None`. On step 2, the intermediate file from step 1 stays in the turn folder, unlisted. Original unchanged in all three. | As designed (design 4.5) |
| Inspector raises after a real, correct output | `failure=inspector`, the failure reply, `verification_result=None` (when it raises before its work) or `passed=True` (when it raises after). The file is on disk. | The reply is honest. Delivery depends on PR-2 item 1. |
| A stage returns `None` or a `dict` instead of the state | `Engine.run` raises `AttributeError` from its own loop, with no reply | L5: no real stage does this |

## 4. F5 and rule 2: output folders

**Same-kind turns in one session (probe, real skills, fake LLM).** Four turns in one session alternated between two uploads with the same file name in different folders (`uploads/f1/report.xlsx`, then `uploads/f2/report.xlsx`, then f1, then f2), which matches how the API stores uploads. I hashed every delivered file after all four turns.

| Run | Turn 1 file afterwards | Turn 3 file afterwards | Originals |
|---|---|---|---|
| `main` (legacy path) | **overwritten** by turn 2 (`5b9dacb5…` → `fc939d18…`, same path `…sess-task-1.xlsx`) | **overwritten** by turn 4 (`…sess-task-0.xlsx`) | unchanged |
| branch, `turn_output_dir` unset (legacy) | **overwritten**, the same as on main | **overwritten** | unchanged |
| branch, a fresh `turn_output_dir` per turn | **unchanged** | **unchanged** | unchanged |

- **[Certain]** The claim that "main overwrote a previous turn's file" is **confirmed**, and the overwrite can also replace a file with **different content**, not only an identical rewrite. The task-index mechanism the implementer describes holds as well: turn 1 is `task-1` because analysis runs first, and later turns on an already-analyzed file are `task-0`.
- The legacy path still overwrites. That is expected, because the design keeps it for existing callers. The live-run script's single-turn mode uses it, which is harmless for one turn.

**Path traversal (probe, calling the skills directly).**
- **`output_dir` set to the upload's own folder.** The output is `{stem}_deduplicated_{task_id}.xlsx`, a different name, so the original was untouched. The implementer's "always longer than the input name" argument holds for both writing skills.
- **`output_dir` containing `..`.** The skill writes wherever the caller points it. That's by design: `output_dir` is trusted caller input, and the API builds it.
- **A task id containing `../`, for example session id `x/../../../uploads/f1/evil`.** The output path resolves outside the turn folder and the dedupe skill **overwrote an upload named `evil-task-0.xlsx`**. Both writing skills build the name the same way; I confirmed the overwrite on the dedupe skill. This is **pre-existing**: `main`'s legacy path joins the same `task_id` and `workspace_id`.
  - It isn't reachable through the designed API, whose session ids are `token_urlsafe` and server-generated, or through the script.
  - The skill has no containment check of its own, so I'm recording it as L3.

**Live run 2** (Section 8) shows two turns in different folders, with turn 1's file byte-identical after turn 2.

## 5. `Context.to_dict` / `from_dict`

**Round trip.** I built a fully populated context: two uploads, one analyzed, an active file with document and sheet, an active task, a topic, nested metadata, both conversation roles and an execution record. After a JSON round trip:
- `to_dict()` is equal;
- `snapshot()` is equal;
- `buddy_reply_count()` is equal;
- `recent_conversation()` is equal, timestamps included;
- `analyzed_files` is kept.

`to_dict` covers every instance attribute except the clock and the two private histories, which it serializes separately.

**Hostile or corrupted input.** Nothing is executed:
- there is no `eval`, `pickle` or import;
- extra keys, including `"clock"` and a string containing `__import__`, are ignored;
- `metadata` is deep-copied plain data.

**But `from_dict` accepts a lot that it shouldn't, and its "Raises" docstring is incomplete:**

| Input | Result |
|---|---|
| `version: True` or `1.0` | accepted (`True == 1`); `"1"` is rejected |
| `uploaded_files: "abc"` | accepted as three files `'a'`, `'b'`, `'c'` |
| `uploaded_files: {"p": 1}` or `[1, None]` | accepted as given |
| `active_file: "../../etc/passwd"`, or `5`; `analyzed_files: ["/etc/passwd"]` | accepted. No check that the file is among the uploads. |
| `message: 123` | accepted |
| `metadata: [1, 2]` | `AttributeError` (not listed in the docstring) |
| `created_at: 0` | `TypeError` (not listed) |
| `created_at` without a time zone | accepted. A later `created_at < last_updated_at` raises `TypeError: can't compare offset-naive and offset-aware datetimes`. |
| Timestamps in year 9999, `created_at` after `last_updated_at`, history out of order | accepted |
| A 50 MB message, 200,000 uploads | accepted, instantly. No size limit. |
| Bad mode, bad role, a missing key, a garbage timestamp | rejected with `ValueError` / `KeyError`, as documented |

The only producer of this data is the server's own `to_dict`, so none of this is reachable from a client today. It is M2 because the docstring tells PR-2 to expect `ValueError`/`KeyError`, while design 7.2 requires a corrupt `session.json` to produce "a clean error, not a crash".

## 6. LLM timeout

- **[Certain]** It's applied on both providers. I constructed each provider with a placeholder key:
  - `ClaudeProvider()._client.timeout == 60.0` and `GroqProvider()._client.timeout == 60.0`;
  - with `timeout=7.5`, both report `7.5`;
  - `max_retries` stays at the SDK default of 2.
- **What happens to the pipeline when a call times out.**
  - **Probe:** each real provider was pointed, through `with_options(base_url=…)`, at a local socket that accepts connections and never answers, with `timeout=1.0` and `max_retries=0`. I ran a single-step request and a two-step request on each provider, through `build_engine` with `PersonalityVoice`.
  - Each SDK raised its own `APITimeoutError`. Brain logged "LLM call failed" and recorded no intent. Think logged "LLM call failed" and set `clarification_required=True`. The planner ran only proactive analysis. No R6 failure was raised, and no file was written. The original was unchanged.
  - The reply was "None of the issues I check for were found. No changes were made to any file this turn." for all four runs.
  - The claim "degrade honestly" is literally true: no change was made. But the reply doesn't tell the user that their request wasn't understood or acted on. A user who asked to remove duplicates from a file that has them could read it as "no duplicates found".
  - **This is pre-existing.** I ran the same probe on `main`, with the timeout set through `with_options`, and got the identical reply 4/4. The relay of Think's clarification is already backlog item 2. I list it under Deferred (D-R1) so it isn't lost, but it isn't this PR's regression.
- **[Likely]** The worst case is 3 calls (Brain, Think, planner) × 3 attempts × 60 s, plus backoff, which is about 9 minutes. That's well past the design's 180 s turn deadline. The deadline is PR-2's, and the implementer flagged this correctly.

## 7. "Other deviations" and the `xlrd` decision

| Claim | Check | Result |
|---|---|---|
| `fastapi`, `uvicorn`, `python-multipart` not added | grep over `requirements.txt` and `pyproject.toml` | **confirmed** absent |
| `build_outcome_report` untouched (D1) | `git diff origin/main...HEAD -- buddy/outcome.py` is empty. Its call sites can't run on a failed turn. `buddy/voice.py:395` is inside `_verified_delivery_reply`, reached only through `_verified_delivery(request)` at `voice.py:490` (`TemplateSpeaker`) and `voice.py:842` (`PersonalityVoice`), which is false whenever `failure` is set. The script's call is behind the same check, and `Voice` returns the failure reply before any speaker runs. | **confirmed**: not reachable through Voice |
| Voice raising re-raises | probe in Section 3, and `test_voice_raising_still_propagates` | **confirmed** |
| `xlrd` must not be in `requirements.txt` (`.xls` out of scope) | grep | **confirmed** absent. The design's Decisions still say PR-1 adds it; the implementer recorded the owner's decision and left the design doc alone. The design should record it when it's next edited. |
| Only `test_engine_propagates_stage_exceptions` was changed among existing tests | the removed lines in `git diff origin/main...HEAD -- tests` | **confirmed**: apart from import lines, that test is the only removal |
| Design 5.3's assumption: a stopped chain leaves `outputs` empty | `Hands` only fills the final outputs when `stopped_because is None`. The implementer's five-reason test passes, and my confirmation-raises probe on step 2 left `execution_result=None`. | **confirmed** |

## 8. Live runs (real Groq)

**Setup.**
- **Date:** 2026-10-01. **Provider:** `GroqProvider` (`openai/gpt-oss-120b`), with the key attached by the proxy. **Python:** 3.11.15.
- **Runs:** 2 of 2 completed (3 turns, 6 Groq calls), with no 429 and no other provider error.
- **Fixture:** `default`, rewritten at the start of each run: 6 rows, 2 exact duplicates, 1 empty row.
- **Independent check:** besides the script's own check, I re-read every delivered file with pandas myself and compared it with `original.drop_duplicates()` or `original.dropna(how="all")`.

### Run 1: `--mode focus --message "remove duplicate rows from this file"` (legacy path)
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false. **Failure:** none.
- **Steps:** analyze (original) → deduplicate → analyze (re-check), all succeeded. Inspector passed.
- **Delivered:** yes, `workspace/outputs/manual-workspace/manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Independent check:** the original has 6 rows, 2 duplicates and 1 empty row. The output has 4 rows, 0 duplicates and 1 empty row; it equals `original.drop_duplicates()`. Each column is missing 1 of 4 values (25.0%).
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. Every number matches pandas.
- **Original hash:** `35e7190fc527bec8` → `35e7190fc527bec8` (unchanged).

### Run 2: `--mode focus --message "remove duplicate rows from this file" --second-message "now remove the empty rows"`
Run folder: `workspace/outputs/manual-workspace/manual-session/run-20261001T214737824659/`.

**Turn 1:**
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false. **Failure:** none.
- **Steps:** analyze → deduplicate → analyze (re-check). Inspector passed.
- **Delivered:** yes, `…/t0001/manual_report_deduplicated_manual-session-task-1.xlsx`, inside the turn's folder.
- **Independent check:** 4 rows, 0 duplicates, 1 empty row; equals `original.drop_duplicates()`.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none.
- **Original hash:** `723695fb8380fafd` → `723695fb8380fafd`.

**Turn 2:**
- **Intent:** `remove_empty_rows` (0.99). **`clarification_required`:** false. **Failure:** none.
- **Steps:** `remove_empty_rows` only; analysis was skipped because the file was already analyzed. Inspector passed.
- **Delivered:** yes, `…/t0002/manual_report_no_empty_rows_manual-session-task-0.xlsx`, inside the turn's folder.
- **Independent check:** 5 rows, 0 empty rows, 2 duplicates. It equals `original.dropna(how="all")` apart from the `amount` dtype (int64 vs float64; the values are identical).
- **Reply:** "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready."
- **False claim:** none. The reply says nothing about duplicates. As the implementer recorded (D3), turn 2 worked on the original, not on turn 1's file.
- **Original hash:** `723695fb8380fafd` → `723695fb8380fafd`.

**F5 check:** different paths and different folders (`t0001` vs `t0002`). Turn 1's file was `ff14cee0f904573e` → `ff14cee0f904573e` after turn 2. The original was unchanged across the whole run.

**What these runs can't show:** R6 with a real provider. As the implementer says, that would need a fault hook in production code. R6 is covered by the 42-run probe in Section 2 and by the fake-LLM tests.

My runs reproduce the implementer's live-run results: the same intents, numbers and replies, and the same F5 outcome.

## Findings

### HIGH
None.
- No false claim reached a reply in any probe or live run.
- No original changed through any path this PR adds or that the designed API can reach.
- No delivered file was overwritten when `turn_output_dir` is set.
- No `Exception` escapes `Engine.run` except Voice's own, which the design assigns to the API.

### MEDIUM

**M1. When Voice raises, the state it leaves behind looks like a verified delivery.**
- In my probe, `failure` was `None`, `execution_result.outputs` named a real file and `verification_result.passed` was `True`, while `reply` was `None`.
- The Engine docstring is accurate ("`state.failure` is set if a stage other than Voice raised"), and design 3.3 step 7 says to discard the copy, so the design already prevents delivery. But a caller has to know to ignore a state that `failure` describes as healthy.
- **Recommended hardening (non-blocking):** in `core/engine.py`, set `state.failure = StageFailure(stage=name, error_type=type(exc).__name__)` before the `raise` in the Voice branch, so the state describes itself. Add a test asserting that `state.failure.stage == "voice"` after the re-raise. PR-2 item 2 is required either way.

**M2. `Context.from_dict`'s documented failure modes are incomplete, and it accepts wrongly typed data silently** (Section 5).
- PR-2's corrupt-`session.json` handling (design 7.2) must not rely on the docstring.
- **Fix, here or in PR-2:** either validate types (strings in `uploaded_files`, `active_file` and `analyzed_files` among the uploads or `None`, aware timestamps, `metadata` a dict) and raise `ValueError`, or correct the docstring to "may raise any exception on malformed input" and have PR-2 catch `Exception` around the restore. Recommended: validate in PR-2, since that's where the file is first read from disk.

### LOW

**L1. The setuptools comment and notes are inaccurate.**
- `pkg_resources` was removed in 82, not 84.
- The failure happens when analysis runs, not when `analyze.py` is imported.
- `uv` venvs on 3.11 also lack setuptools.
- The pin itself (`>=68,<81`) is correct on both versions. `<82` would be the exact bound.

**L2. A `BaseException` subclass raised by a stage escapes the Engine with no reply.** This is consistent with the design and right for `KeyboardInterrupt`. PR-2 needs a `finally` (item 4).

**L3. Pre-existing: the writing skills put `task_id` (and therefore `session_id`) into the file name unchecked.** With a `../` session id, the dedupe skill overwrote an upload. It isn't reachable with server-generated ids. Suggested hardening for a later PR: in `_build_output_path`, check that `path.resolve().is_relative_to(Path(output_dir).resolve())` and fail the task otherwise.

**L4 (= D2). A stale `failure` names a stage that didn't run this turn.** It's already in the backlog.

**L5. A stage returning a non-`BuddyState` makes `Engine.run` raise `AttributeError`.** No real stage does this. Optional: check `isinstance(state, BuddyState)` after each stage and record a failure.

**L6. The live-run script's "files handed back" counts `execution_result.outputs` on any turn without a failure, whether or not `verification_result.passed`.**
- This is pre-existing (main printed `len(outputs)` too), and it only affects the operator's printout.
- It is relevant because live-run reports quote "Delivered: yes" from it. The PR-2 live-run tooling should use `delivered_files`.

## Deferred

These are also added to `docs/backlog.md` (item 4's list).
- **M1 hardening:** set `failure` for Voice before re-raising, or make PR-2's discard-on-raise test cover it.
- **M2:** validate `Context.from_dict` input, or catch broadly at restore (PR-2).
- **L3:** an output-path containment check in the writing skills.
- **D-R1 (pre-existing, belongs with backlog item 2):** when Brain and Think fail, for example on a provider timeout, the reply is "None of the issues I check for were found. No changes were made to any file this turn." That's true, but it doesn't say the request wasn't understood or carried out. I reproduced it on `main`.
- The implementer's D1–D3 stand as written.

## Verdict

**MERGE.**
- I found no HIGH finding.
- The R6 contract holds for every stage, both raise timings and all three speakers.
- F5 prevents the overwrite that `main` has.
- `Context` round-trips.
- The timeout is applied on both providers.
- The suite passes on 3.11 and 3.13.
- Lint is unchanged.
- Both live runs show no false claims and unchanged originals.

M1 and M2 are obligations for PR-2, not regressions in PR-1. M1's one-line hardening is recommended but isn't a condition of this merge. Only the user merges to `main`.
