# Implementation: `feat/pipeline-failure-and-output-dir` (PR-1 of the backend API design)

**Design:** `docs/design/backend-api-design.md` on `design/backend-api`: Sections 3.2–3.5, 4, 5.3, 7.2 (the Engine bullets), 8.2, 8.3 (PR-1), and the "Decisions" sections that name PR-1.
**Rule-touching:** yes, both rules. It changes the Engine's error contract and the reply path (rule 1), and where skills write their output (rule 2). It needs an independent review by a separate session (`docs/reviews/pipeline-failure-and-output-dir-review.md`) before merging. That review hasn't been done. This document is the implementer's account, not a review.
**Live runs:** `docs/reviews/pipeline-failure-and-output-dir-live-runs.md`. 2 of 2 runs completed, no 429, no false claims, the original was unchanged in both.
**Not in this PR:** any API code, anything under `backend/`, and anything from PR-2 to PR-4.

## What changed, in one paragraph

When a stage raises, the Engine no longer re-raises. It logs the traceback, records `StageFailure(stage, error_type)` on `state.failure`, skips the remaining stages and still runs Voice. Voice sees the failure and records a fixed reply from `buddy/voice_catalog.py` without calling any speaker. The reply says something went wrong (with the stage as a fixed label), that it can't confirm any change was made, and that no file from the turn is being handed back. `_verified_delivery` is false for any turn with a failure, whatever the results hold. Separately, a turn can now name its own output folder (`BuddyState.turn_output_dir`). Hands copies it onto every `SkillTask.output_dir`, and the two writing skills write there instead of the shared `workspace/outputs/{workspace_id}/`. `Context` can be written to a dict and restored from one. Both LLM providers set an explicit request timeout, `build_engine` takes a confirmation provider, and the project's Python floor is 3.11.

## The seven items

### 1. R6 in the engine and Voice

- **`buddy/state.py`:** new frozen `StageFailure(stage, error_type)`. `BuddyState.failure: StageFailure | None = None`, cleared by `reset_transient()`. It has no message and no traceback (design 4.2), because exception text from pandas or an LLM call can contain cell values or request fragments.
- **`core/engine.py`:** `CognitiveCycle.named_stages()` returns `(slot name, stage)` pairs, so `StageFailure.stage` is always one of the eight slot names (a closed set). `Engine.run` behaves as follows:
  - A stage other than Voice raises: the Engine logs it with `logger.exception` (server side, as before), sets `state.failure`, skips the rest and runs Voice.
  - Voice itself raises: the Engine re-raises. With no stage left to produce a reply, this is the API's last-resort layer (design 4.3), which uses `FALLBACK_PIPELINE_ERROR`.
  - If a stage mutated the state in place before raising, those partial mutations stay on the state. Voice ignores them, because it only looks at `failure`.
- **`buddy/voice_catalog.py` (appended at the end):** `FAILURE_STAGE_LABELS` (a closed map with one label for each stage except Voice), `FAILURE_STAGE_LABEL_DEFAULT`, `FALLBACK_PIPELINE_ERROR`, `FALLBACK_INTEGRITY_FAILURE` and `failure_reply(stage)`. The texts:
  - `failure_reply("inspector")`: "Something went wrong while checking the result, so I can't confirm that any change was made. I haven't handed back any file from this turn."
  - `FALLBACK_PIPELINE_ERROR`: the same, with "while I was working on this".
  - `FALLBACK_INTEGRITY_FAILURE`: "I can't vouch for this result: your original file changed while I was working. I've held back every file from this turn." It is the design's text, unchanged. Nothing in the pipeline uses it; PR-2's integrity check will.
- **`buddy/voice.py`:**
  - `Voice.__call__` checks `state.failure` first and records `failure_reply(...)` without calling the speaker, so no model can be involved.
  - `SpeakingRequest` gains `failure` (last field, default `None`), and `_verified_delivery` returns false when it's set.
  - `TemplateSpeaker` returns the failure reply when it's called directly with a failed request, which covers `PersonalityVoice` too, since that falls through to `TemplateSpeaker` on any non-verified turn.

**Why the reply wording differs from the design's example.** The example ("…so I can't confirm that anything was done") contains "done", which `buddy.voice._COMPLETION_CLAIM` flags. That regex is the lint every other no-delivery reply is held to. "…that any change was made" says the same thing and passes it.

**What the failure reply deliberately doesn't say:** that the original is unchanged. Nothing on this path checks it. The stopped-chain reply does say it, because Hands' chain ran to a clean stop; a raising stage gives no such assurance. PR-2's runtime hash check (design 5.2) is what can make that claim, and it has its own fixed text.

**"I haven't handed back any file from this turn" is true only if the caller withholds the files.** Inside the pipeline, `execution_result.outputs` can still name a real file when Inspector raised after Hands. The design makes this true by construction at the API (`delivered_files` requires `state.failure is None`, Section 5.3). `scripts/run_dedupe_manually.py` now does the same: on a failed turn it prints the outputs as "withheld" and reports 0 files handed back. Any other caller of `build_engine` must check `state.failure` before handing back a file. I've listed this for the reviewer under "Things to look at" below.

### 2. F5: per-turn output folders

- **`skills/base.py`:** `SkillTask.output_dir: str | None = None`.
- **`skills/excel/dedupe.py`, `skills/excel/remove_empty_rows.py`:** `_build_output_path` writes to `Path(task.output_dir) / name` when it's set, and to the legacy `workspace/outputs/{workspace_id}/name` when it isn't. The name is unchanged. `analyze.py` writes no file, so it isn't changed.
- **`buddy/state.py`:** `BuddyState.turn_output_dir: str | None = None`, cleared by `reset_transient()`.
- **`buddy/hands.py`:** one line in `_build_task`, `output_dir=state.turn_output_dir`. It reaches every task, including the re-check of the final file and the chain-feed probe.

Rule 2: the output name is `{stem}_{op}_{task_id}.xlsx`, which is always longer than the input's `{stem}.{ext}`, so even when `output_dir` is the input's own folder, the output can't be the input file. What the skill can't guarantee on its own is that `output_dir` doesn't hold *other* user files. The API's turn folder (created with `exist_ok=False`, design 3.3) is what guarantees that. The skills create the folder with `mkdir(parents=True, exist_ok=True)`, as before.

**F5 with names alone, before this change.** In a real session, turn 1 runs proactive analysis first, so its dedupe is `task-1`. Later turns skip analysis because the file is already marked analyzed, so their dedupe is `task-0`. Turns 2 and 3 of the same kind therefore wrote the same path, and turn 3 overwrote turn 2's delivered file. `test_turns_of_the_same_kind_write_to_different_folders` runs three turns because two turns of the same kind don't collide.

### 3. `Context.to_dict()` / `Context.from_dict()`

`buddy/context.py`:
- `to_dict()` returns every field, including `analyzed_files` (sorted, so the output is stable) and both histories, with ISO 8601 timestamps and a `"version": 1` key.
- `from_dict(data, *, clock=_default_clock)` rebuilds the context through public methods only: `set_mode`, `add_uploaded_file`, `mark_analyzed`, `switch_active_file`, `switch_active_task`, `set_current_topic`, `set_metadata`, `append_conversation_turn` and `record_execution`. While it replays, the context's clock returns each entry's stored timestamp. It then sets `created_at` and `last_updated_at` to the stored values and switches to the injected clock.
- Invalid data raises `ValueError` (unknown version, mode or role, or a bad timestamp) or `KeyError` (a missing key).

`metadata` is deep-copied as is. It's JSON-ready only if its values are, and nothing in the pipeline writes to it today.

**Deviation:** the `"version"` key isn't in the design. I added it because PR-2 will persist this format to disk. Without a version, the first format change would have no way to tell old files from new. It's one key; drop it if the reviewer disagrees.

### 4. An explicit LLM request timeout

`buddy/llm.py`: `DEFAULT_REQUEST_TIMEOUT_S = 60.0`, passed to `anthropic.Anthropic(timeout=...)` and `groq.Groq(timeout=...)`, and overridable with a `timeout=` constructor argument on both providers.
- **[Certain]** The installed SDK defaults differ: `anthropic` 1.11.0 uses a 600 s read timeout and `groq` 1.7.0 uses 60 s. Both retry twice by default, so one `complete()` call can take up to about 3 × 60 s.
- **[Certain]** Brain, Think and `ClaudePlanner` already catch a failed call and degrade (no intent, a clarification, no plan), so a timeout there doesn't become an R6 failure. It becomes the existing honest no-delivery reply.
- **[Possible]** 60 s against the design's 180 s turn deadline: three LLM calls that each hit the timeout and both retries would exceed 180 s. That is PR-2's deadline to handle. I didn't lower `max_retries` because the design doesn't ask for it.

### 5. `build_engine(confirmation_provider=...)`

`buddy/engine_factory.py`: an optional keyword argument. The default is `NullConfirmationProvider`, so every existing caller behaves as before. The docstring is updated.

### 6. Python floor

- **`pyproject.toml`:** `requires-python = ">=3.11"`, `[tool.black] target-version = ["py311"]`, `[tool.ruff] target-version = "py311"`, `[tool.mypy] python_version = "3.11"`.
- **README:** "Python 3.13" became "Python 3.11 or newer". That README line is the stale comment I found. `requirements.txt`'s header comment ("Sprint 1 & 2…") is old but not wrong about the Python version, so I left it alone.

**Audit (the design's list):**

| Check | Method | Result |
|---|---|---|
| `type X =` aliases | grep over all 60 tracked `.py` files | none |
| PEP 695 generics (`def f[T]`, `class C[T]`) | grep | none |
| `typing.override`, `TypeIs`, `ReadOnly`, `warnings.deprecated` | grep | none |
| PEP 701 f-strings and any other 3.12+ syntax | `ast.parse(..., feature_version=(3, 11))` on all 60 files, run under Python 3.11.15 | 0 syntax errors |
| 3.12+ stdlib (`itertools.batched`, `Path.walk`, `copy.replace`, `calendar.Month`, and so on) | grep | none |
| ruff's `UP` rules at py311 vs py313 | `ruff check buddy tests core skills scripts` at both targets | byte-identical output |
| mypy at 3.11 vs 3.13 | `mypy buddy core skills` | the same 18 pre-existing errors |
| Full suite on 3.11 | `python -m pytest -q`, Python 3.11.15 | 781 passed |
| Full suite on 3.13 | a fresh `uv` venv on `/usr/bin/python3.13` (3.13.14), `-r requirements.txt` | 781 passed, after the fix below |

**Found and fixed: a fresh install on Python 3.12+ couldn't import `skills/excel/analyze.py`.**
- **[Certain]** `ydata-profiling` 4.18.4 does `import pkg_resources`. `pkg_resources` comes from setuptools, and venvs on Python 3.12+ don't include setuptools (nor does any `uv venv`). The latest setuptools, 84.0.0, no longer ships `pkg_resources`; 80.10.2 still does.
- On a fresh 3.13 venv, the import failed with `ModuleNotFoundError: No module named 'pkg_resources'`.
- I added `setuptools>=68,<81` to `requirements.txt` with a comment. A fresh 3.13 venv then imports `buddy.engine_factory` and `skills.excel.analyze`, and the suite passes.
- **[Likely]** The exact version that removed `pkg_resources` is somewhere between 80.10.2 and 84.0.0; I didn't bisect it. `<81` is conservative.
- This is a dependency change outside the PR-1 list, so it's called out here for the owner. The alternative is to drop it and say "3.11 and newer, with setuptools < 81 installed".

### 7. `xlrd`: not added (owner decision)

The design's "Decisions (2026-09-30)" item 4 and its "Consequences" ("PR-1 gains: … `xlrd`"), and Section 8.2's `requirements.txt` row, all say PR-1 adds `xlrd>=2.0.1`. **The owner decided `.xls` is out of scope for the demo, so this PR doesn't add it.** `requirements.txt` gains only the setuptools pin. Any API-side `.xls` acceptance (design 6.2, and the `.xls` fixture tests assigned to PR-2/PR-3) should follow the owner's decision rather than the design text, and the design's Decisions should record it. I haven't edited the design doc, because it lives on another branch.

## Other deviations from the design

1. **`fastapi`, `uvicorn` and `python-multipart` are not added.** Section 8.2 lists them in PR-1's `requirements.txt` row, but nothing in PR-1 imports them, the brief says "no API code", and part 2 of the Decisions puts the `fastapi` pin in PR-2. They belong with the code that uses them.
2. **`build_outcome_report` doesn't check `failure`.** Design 4.2 asks for this in `buddy/outcome.py`, which was off limits for this branch (another branch is editing it). It isn't reachable from Voice: the failure reply returns before any speaker runs, and `_verified_delivery` is false. Deferred as backlog D1.
3. **Voice raising re-raises.** The design says Layer 1 "doesn't help if Voice itself raises" and leaves that case to the API. I implemented exactly that rather than catching it in the Engine, because the Engine would then need reply text, and it contains no domain logic.
4. **The live-run script gained `--second-message`.** The design asks for "two turns in one session showing distinct folders" with the existing script. The script ran one turn, so it now has an option to run two, each with its own fresh folder (`mkdir(exist_ok=False)`, like design 3.3). It doesn't record the conversation between turns: wiring `record_user_message` and `record_buddy_reply` into turns is PR-2's job (design 3.3).

## Tests

`python -m pytest -q`: **781 passed** in 62 s on Python 3.11.15, up from 710 on `main`; the same 781 pass on 3.13.14. `ruff check buddy tests`: 22 errors, the same set as on `main` (compared per file and rule, ignoring line numbers). No new mypy errors.

| What the design asks for (7.2, 8.3, 5.3) | Test |
|---|---|
| A stage raising sets `failure` and still runs Voice (all 7 non-Voice stages) | `tests/core/test_engine.py::test_a_stage_that_raises_sets_failure_skips_the_rest_and_still_runs_voice` |
| No exception text on the state; the traceback is logged; the error type is recorded | `…::test_a_failure_never_puts_the_exception_message_on_the_state`, `…::test_the_failure_is_logged_with_its_traceback`, `…::test_a_stage_raising_a_non_runtime_error_records_its_type` |
| Voice raising propagates (the replaced re-raise test) | `…::test_voice_raising_still_propagates`, `…::test_voice_raising_after_an_earlier_failure_still_propagates` |
| The failure reply is produced and `_verified_delivery` is false for it | `tests/buddy/test_pipeline_failure_and_output_dir.py::test_verified_delivery_is_false_whenever_a_stage_failed`, `…::test_voice_gives_the_failure_reply_without_calling_its_speaker`, `…::test_a_speaker_called_directly_with_a_failure_gives_the_failure_reply` |
| Inspector raising after a real output gives a non-claiming reply (TemplateSpeaker, friend, focus) | `…::test_inspector_raising_after_a_real_output_yields_a_reply_that_claims_nothing`: a real, correct deduplicated file is on disk; the reply equals `failure_reply("inspector")`, matches no `_COMPLETION_CLAIM`, contains no exception text and no output name; the original's hash is unchanged |
| A raising skill, and a raising verifier (backlog R6's example) | `…::test_a_skill_raising_inside_hands_…`, `…::test_a_verifier_raising_inside_the_chain_…` |
| Failure texts: no claim, no digit, no placeholder; one label for each non-Voice slot; an unknown stage gets the generic text | `…::test_no_failure_reply_claims_any_outcome`, `…::test_every_stage_but_voice_has_a_failure_label`, `…::test_an_unknown_stage_gets_the_generic_failure_reply`, `…::test_the_failure_replies_say_nothing_is_confirmed_or_handed_back` |
| A stopped chain leaves `ExecutionResult.outputs` empty (the 5.3 assumption) | `tests/buddy/test_hands.py::test_a_chain_stopped_after_an_earlier_step_passed_hands_back_no_file`, over 5 stop reasons (not dispatchable, declined, skill failure, malformed result, failed verification), each with a real intermediate file on step 1. Already covered on `main`: an output that can't feed the chain, and the end-to-end verification stop. **The assumption holds**: `Hands` only fills `final_outputs` when `stopped_because is None`. |
| `turn_output_dir` reaches every task; it's unset by default | `tests/buddy/test_hands.py::test_hands_copies_the_turns_output_dir_onto_every_task`, `…::test_hands_leaves_output_dir_unset_when_the_turn_has_none` |
| Skills honour `output_dir` and keep the legacy path by default; same task id in two folders | `tests/skills/excel/test_dedupe.py` and `test_remove_empty_rows.py`: three tests each |
| F5 end to end: turns of the same kind; a chain turn then a single-step turn | `…::test_turns_of_the_same_kind_write_to_different_folders` (three turns, earlier files' hashes unchanged), `…::test_a_chain_turn_then_a_single_step_turn_keep_every_file_in_its_own_folder` |
| `failure` and `turn_output_dir` defaults and reset | `tests/buddy/test_state.py` |
| A deep copy of a fully populated `BuddyState` (design 3.3) | `tests/buddy/test_state.py::test_a_deep_copy_of_a_fully_populated_state_is_equal_and_independent`, plus a guard test that fails if a new `BuddyState` field is left out of the fixture |
| `Context` round trip, including `analyzed_files` and both histories | `tests/buddy/test_context.py`: a JSON round trip, the injected clock taking over only after the restore, independence from the source dict, an empty context, and rejection of a bad version, mode, role or missing key |
| LLM timeout on every provider | `tests/buddy/test_llm.py::test_every_provider_sets_an_explicit_request_timeout_by_default`, `…::test_a_provider_accepts_a_custom_request_timeout` |
| `build_engine`'s confirmation provider | `…::test_build_engine_uses_the_given_confirmation_provider`: a declining provider is asked once at the Write tier, nothing is written, `outputs == ()`, there's no completion claim, and the original is unchanged |

The only change to an existing test is the one the design names: `test_engine_propagates_stage_exceptions` is replaced by the R6 tests above.

## Things to look at, for the reviewer

1. **Callers of `build_engine` other than the API must now check `state.failure`.** Before this change, a raising stage ended the turn with an exception. Now it returns normally, and `execution_result.outputs` can name a real file the reply says wasn't handed back. In this repo the only other caller is the live-run script, which now checks. Is a returned failure state a hazard for future callers?
2. `Engine.run` treats a `failure` already set before the run as a failure (backlog D2).
3. Is the failure reply's stage label ("while checking the result") useful to the user, or noise? The design asked for it.
4. `from_dict` puts `analyzed_files` back without any check that the file still exists. That's correct for a restore, but PR-2 should decide what happens when an upload has been swept.
5. The setuptools pin (item 6) is a dependency change the brief didn't list.

## Deferred

Also in `docs/backlog.md`.
- **D1:** the `failure` guard in `build_outcome_report` (`buddy/outcome.py`, off limits for this branch).
- **D2:** a stale `state.failure` from a reused state.
- **D3:** in the second live run, "now remove the empty rows" ran on the original upload, not on turn 1's file. The reply was accurate about its own file. Whether "now … too" should build on the previous turn's file is a product question for PR-2's multi-turn live run.
- Not done in PR-1 by design: the API, `delivered_files`, the runtime integrity check, the deadline, conversation recording, and CI on both Python versions (there is no CI).
