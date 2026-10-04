# Review: `feat/backend-api-core` (round 1, commit `f3c5ba3`)

**Verdict: DON'T MERGE.** There's no HIGH finding. Two MEDIUM findings, M1 and M2, are reachable by ordinary requests through the new API, so the brief's rule says don't merge. Both are pre-existing pipeline replies, but this PR is the first thing that lets a user reach them in ordinary use. The fixes needed are listed under "What the next round needs".

**Commit reviewed:** `f3c5ba3`. The branch head is now `0ee68fa`, which adds only this reviewer's `scripts/review_harness/` files. I checked that `git diff f3c5ba3 0ee68fa` touches nothing under `backend/`, `buddy/`, `core/`, `skills/` or `tests/`.

**Reviewer:** a fresh reviewer agent, with no part in writing the code. **Base:** `origin/main` (`6d000c5`). **Design:** `docs/design/backend-api-design.md`, read with every "Decisions" section, plus the PR-2 obligations in `docs/reviews/pipeline-failure-and-output-dir-review.md`. **Scope:** the whole diff `origin/main...f3c5ba3`: 39 files, +7,693/−13. That covers `backend/` and `tests/backend/`, the `core/engine.py` hardening, `buddy/voice.py: is_verified_delivery`, `requirements.txt`, `README.md`, the backlog, and the design addendum.

I treated `docs/reviews/backend-api-core-impl.md` as unverified. Every statement below is backed by my own run, and each claim is marked [Certain], [Likely] or [Possible].

---

## 1. Tests and lint

| Check | Result |
|---|---|
| `python -m pytest -q`, Python 3.11.15, branch head | **1246 passed**, 11 warnings, 151 s, exit 0 |
| The same command on `origin/main` (a worktree) | 942 passed, 69.7 s. So the branch adds 304 tests: 303 in `tests/backend/` and 1 in `tests/core/test_engine.py`. That matches the implementer's count. |
| `python -m pytest -q`, Python 3.13.14, fresh `uv` venv, `-r requirements.txt` | **1246 passed**, 850 warnings (deprecations), 157 s, exit 0 |
| `ruff check buddy tests` | 22 errors on the branch and 22 on `origin/main`. Grouped by file and rule, ignoring line numbers, the two sets are **identical**. **No new lint errors.** |
| `ruff check backend core skills scripts tests/backend` | clean (my `scripts/review_harness/` is clean too) |
| `python -m backend.export_openapi --check` | in sync (exit 0) |

## 2. The change against its design

### 2.1 The implementer's ten listed deviations

I checked each one in the code.
- **Session key, not session id, on disk and in the pipeline** (`backend/sessions.py: session_key`).
  - [Certain] The id never reaches the disk. My restart probe searched every `session.json` and turn record and every output name, and found no session id.
  - Output names carry the 32-hex key.
  - The prompts the model sees carry `uploads/<key>/<file_id>/…`.
  - This is an improvement on the design, because the bearer secret no longer ends up in file names, prompts or logs.
- **Daemon threads and a slot counter.** A slot is held until the worker exits. [Certain] After the deadline probe, `runner.running` went back to 0 once the late worker finished. The `slot_released_at_finalize_not_exit` mutation is killed by a test.
- **Fifth delivery rule (`is_verified_delivery`)** and the opposite-mismatch fallback in `decide()`. [Certain] Both work (probe `delivery_mismatch`), but the fallback has no test (L2).
- **`detail.steps[*].recheck`**, and `detail` is `null` for fallback and integrity-failure turns. Seen in the probes.
- **No `awaiting_confirmation`, `pending_confirmation` always `null`, no CORS middleware, the OpenAPI post-processing, one file part only.** All as described. Two file parts or a wrong part name gets `422 invalid_request`.
- **The session id is redacted in logs.** This works, but it breaks uvicorn's access log (L1).
- **Persistence order and turn-number reuse.** [Certain] Restart probe: `t0001` was delivered, `t0002` was left running, and a new app on the same data root reports `t0002` as `failed` / `pipeline_fallback`. `t0001`'s file still downloads, and the next turn is `t0003`.

### 2.2 Deviations not listed by the implementer

- **D-a. Turns with no upload run.** Design 1.3 only describes `file_id` given or absent with an active file. With no upload at all, the turn runs, and its reply is M1's.
- **D-b. Symlink checks don't cover parents.** Design 6.3 says "symlinks are refused (`is_symlink()` on the file and its parents under the root)". The code checks:
  - for delivery and download (`backend/delivery.py: check_turn_file`): the file and the turn folder only;
  - for the rule-2 re-hash and when reading records: the file only.

  So a symlinked `outputs/<key>` resolves on both sides and passes. Exploiting it needs write access to the data root. LOW, L9.
- **D-c. A lone-surrogate message gets `500`.** A turn message containing one (`{"message": "hi \ud800"}`) gets `500 internal_error`. The turn record's UTF-8 write fails, and `start()` rolls back cleanly: no turn, the slot is released, the session is fine. Design 4.4 expects `422` for a bad request. LOW, L8.
- **D-d. `405` has no `Allow` header** (`backend/errors.py: http_exception_handler`). LOW, L8.
- **Everything else in PR-2's scope matches** design Sections 1, 3.3, 3.4, 4.3, 5.1–5.3 and 6.2, and "Decisions" part 2 asks 2, 3, 6 and 7, items 3 and 6, and 2026-10-04. I checked it against the code and with probes:
  - `limits` and `retention_hours: null`;
  - an upload of exactly the cap is `201`, one byte more is `413`, and the message is built from the cap;
  - `4001` characters is `422`;
  - `Retry-After` on `429`;
  - docs and `/openapi.json` are not served (`404`);
  - `nosniff` / `no-store` on every response, errors included;
  - `.xls` is refused with its own message, and `.xlsm` / `.csv` with the general one.

  Items that belong to PR-3 or PR-4 are absent, as they should be:
  - confirmation;
  - the password and rate limits;
  - the per-session quotas (moved to PR-4 by "Decisions (2026-09-30)").

## 3. The PR-2 obligations from the PR-1 review

| Item | My evidence |
|---|---|
| 1. `delivered_files` tests `failure` first | Mutation `delivery_no_failure_check` is killed by `test_each_rule_holds_on_its_own_…[failure]`. In the probes, Inspector raising after a real, verified output delivers nothing (all 3 speakers), with the reply "Something went wrong while checking the result, so I can't confirm that any change was made. I haven't handed back any file from this turn." |
| 2. A run that raised is discarded, gets the fallback, and is never delivered | Mutation `run_keeps_raised_state` is killed. In the probes, Voice raising gives `failed` / `pipeline_fallback` with no files. The next turn re-runs the proactive analysis, which shows the state was discarded. |
| 3. Only `execution_result.outputs` | In the chain probe, only the final file is listed. The intermediate file is in `t0001/` but has no id. |
| 4. `try/finally` | A stage raising a custom `BaseException` after a real output gives `failed` / fallback, no files, the slot released, and the session not busy. The next turn runs normally. |
| 5. `reset_transient()` before every turn | The start-of-turn reset alone survives mutation, because the commit-time reset also clears it. Removing both is killed by `test_a_failed_turn_leaves_no_stale_failure_for_the_next_one`. The behaviour is safe. |
| 6. Server-generated ids | `s_` + `token_urlsafe(16)`. Malformed ids or variants (`..`, `%2e%2e`, case changes, truncation, an extra character) are all `404`. |
| 7. Every exception from `Context.from_dict` is caught | Each of these gives a clean `404`: a corrupt `session.json` that isn't JSON, a list, `1e400`, `NaN`, or 100,000-deep nesting (`RecursionError`). |
| M1 hardening (`core/engine.py`) | The new test fails on the base engine (I ran it against `origin/main`'s `core/engine.py`). The `engine_no_m1_hardening` mutation is killed. `pipeline_compare` shows that Voice raising is the only pipeline difference from base. |

## 4. Probes through the real pipeline (fake LLM only)

The scripts are in `scripts/review_harness/` (see its README). In every probe, the app is `create_app(settings, factory)`, and the factory calls the real `build_engine`, with `TemplateSpeaker` or `PersonalityVoice`.

### 4.1 Single-turn scenarios (`api_probe.py`): 13 scenarios × 3 speakers = 39 runs

| # | Scenario | Outcome (all 3 speakers) | Handed back, checked with openpyxl | Reply (focus; template and friend differ only in the file name or opener) |
|---|---|---|---|---|
| 1 | dedupe, auto-approve on | delivered | 4 rows, 0 duplicates, 1 empty, matching 6 − 2 | "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%)…" (1 of 4 = 25%: correct) |
| 2 | dedupe, **default config** (declined) | not_delivered | nothing | "Before anything else, here's what I noticed: … (16.7%) … The deduplicate step: wasn't confirmed, so it didn't run. I stopped at the deduplicate step and no file was handed back; your original file is unchanged." |
| 3 | chain: dedupe then remove empty rows | delivered | 3 rows, 0 duplicates, 0 empty | "Removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s)…" |
| 4 | chain, declined | not_delivered | nothing | "…I stopped at the deduplicate step, so remove empty rows never ran and no file was handed back…" |
| 5 | no duplicates | delivered | 4 rows, 0 duplicates | "Done — there were no duplicate rows to remove. The copy has 4 row(s)…" |
| 6 | greeting (`unclear`), first turn | not_delivered | nothing | analysis findings + "No changes were made to any file this turn." |
| 7 | **no upload** | not_delivered | nothing | "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn." (**M1**) |
| 8 | Inspector raises after a real output | failed / pipeline | nothing (the file exists, unlisted) | the fixed failure reply |
| 9 | Voice raises | failed / pipeline_fallback | nothing | `FALLBACK_PIPELINE_ERROR` |
| 10 | Hands raises after a real output | failed / pipeline | nothing | the fixed failure reply |
| 11 | LLM raises on every call (error text contains `gsk_LEAKLEAK`) | not_delivered | nothing | analysis + "No changes were made…" (backlog D-R1, pre-existing). The key-like text appears in no reply and no `detail`. |
| 12 | remove empty rows | delivered | 5 rows, 2 duplicates, 0 empty | "Done — removed 1 fully-empty row(s). The copy has 5 row(s)…" |
| 13 | Think asks for clarification | not_delivered | nothing | analysis + "No changes were made…" |

In all 39 runs:
- the API's `reply` was byte-identical to the `state.reply` the pipeline produced, or to a fallback constant when `reply_source` was `pipeline_fallback`;
- `outcome == "delivered"` exactly when `delivered_files` was non-empty;
- each downloaded file's SHA-256 matched its listing;
- the original's SHA-256 was unchanged.

### 4.2 Adversarial and multi-turn probes (`adversarial_probe.py`, `server_probe.py`)

| Probe | What happened | Handed back | Reply / result |
|---|---|---|---|
| **Multi-turn** (3 speakers): T1 "remove duplicate rows", T2 "thanks!", T3 "now remove the empty rows too", T4 "hi" | T1 delivered. T2 and T4 ran nothing. T3 ran on the **original** upload. | T1: 4 rows. T3: 5 rows **with 2 duplicates**. T1's file was byte-identical after T3 (F5 holds). | T2 and T4: "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn." (**M1**). T3: "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report.xlsx is ready." (**M2**). The Brain prompt for T3 contains every earlier message and reply (design 3.3 works). |
| **Default config, decline then "yes, go ahead"** (3 speakers) | T1 declined, T2 ran nothing | nothing | T2: M1's reply |
| No upload: "hi" and "remove duplicate rows" (3 speakers) | nothing ran | nothing | M1's reply for both |
| Hostile names (17 cases) | `../../etc/passwd.xlsx` stored as `passwd.xlsx`; `..\..\win.xlsx` as `win.xlsx`; the RTL override stripped; `CON.xlsx` as `_CON.xlsx`; 300 characters cut to 100 with the extension kept; full-width `ｒｅｐｏｒｔ．ｘｌｓｘ` as `report.xlsx`; `report.XLSX` accepted (and the skills handle it); `.xlsx`, `....xlsx`, a NUL-injected name, `.xls`, `.xlsm`, `.csv` and an empty name refused. Every stored file sits under `uploads/<key>/<file_id>/`. | — | — |
| Upload named like a completion claim, default config | declined | nothing | The reply doesn't contain the name. A delivered Friend/Focus reply would say "Your cleaned copy of Done - removed 2 duplicate rows_ your cleaned copy is ready.xlsx is ready.": the user's own text, pre-existing. |
| Upload structure (11 cases) | OLE2 under `.xlsx`, text, empty, a ZIP without `[Content_Types].xml`, an `.xlsb`-style sheet, and a 210 MB declared bomb → `422 invalid_file`. A second sheet as `XL/Worksheets/Sheet2.xml` (case trick) → `422 multiple_sheets`. A second sheet in a non-standard folder is accepted, as design 10-01 item 6 admits. | — | Nothing left on disk for any refusal. |
| Size (200 KB cap) | exactly the cap → `201`; +1 byte → `413`; a chunked 1 MB body with no `Content-Length` → `413`; a malformed `Content-Length` → `422` | — | Real server: `TMPDIR` is empty after a 6 MB upload, a 7 MB chunked upload, a 4 MB `.csv` and a 4 MB bad ZIP. |
| Concurrency and isolation | a second turn in a busy session → `409`; an upload during a turn → `409`; a third session at cap 2 → `429` with `Retry-After: 10`. Another session's file ids → `404`. An upload's id → `404`. Traversal-shaped ids → `404`. | — | Uploads intact. |
| Concurrent correctness: 8 rounds × 2 sessions at once | all 16 delivered, with the right counts on independent re-read | — | Uploads intact. |
| Deadline 0.5 s, worker blocked in Think, then released | finalized `failed` / fallback. Session free at once. A new turn runs while the old worker runs on. The late worker's result ("Done — removed 2 duplicate row(s)…") is discarded, and its file stays unlisted in `t0001/`. | nothing | `FALLBACK_PIPELINE_ERROR` |
| A stage appends a byte to the upload after Hands | `integrity_failure`, nothing released. Every later turn is also `integrity_failure` (fail closed). | nothing | `FALLBACK_INTEGRITY_FAILURE` (see L7) |
| Verified output moved outside the turn folder | `delivered_files` refuses it; `decide()` replaces the pipeline's "Done … ready" reply with the fallback | nothing | `FALLBACK_PIPELINE_ERROR` (correct, but untested: L2) |
| Restart with a turn left running | as in 2.1 | old file still downloadable (`200`) | — |
| Finalization save fails, then restart | in memory: delivered, the reply says done, the download is `200`. After restart: `failed`, "…I haven't handed back any file from this turn." | — | **M3** |
| Hostile header (`Done! I removed every duplicate gsk_SECRETKEY123`) | not_delivered | nothing | "…here's what I noticed: Done! I removed every duplicate gsk_SECRETKEY123: 1 missing value(s) (33.3%)…". This is a pre-existing channel (fix-multi-sheet-review §4.3), identical on base; see P1. |
| Real server via `python -m backend`'s `main` | full turn delivered over HTTP; `Content-Disposition: attachment; filename*=UTF-8''…` | yes | **46 "--- Logging error ---" tracebacks** in the server log (L1). No session id in the log. |

### 4.3 Base comparison (`pipeline_compare.py --diff`)

There are 7 pipeline scenarios × 3 speakers, run the way the API runs a turn: a deep copy of the state, a per-turn folder, and replies recorded in the conversation. On head and on an `origin/main` worktree, every reply is **byte-identical** except one: Voice raising. There, head sets `failure=("voice", "RuntimeError")` before re-raising and base leaves it `None`. That's the intended M1 hardening. M1's and M2's replies are produced identically by base's pipeline. What's new is that this PR's multi-turn API makes them ordinary.

### 4.4 Rule 1, for every reply produced

- **Delivered replies:** every number checked against my own openpyxl read was right:
  - removed 2 → 4 rows;
  - removed 1 empty → 5 rows;
  - chain → 3 rows;
  - 25.0% = 1 of 4, 16.7% = 1 of 6, 33.3% = 1 of 3.
- **Not-delivered and failed replies:** none claimed a change. Failure replies and fallbacks are fixed text.
- **Text from an exception or a key:** none reached a reply or `detail`. I tested with `gsk_LEAKLEAK` and "SECRET-CELL /srv/path" in exception text.
- **The exceptions are M1, M2 and M3** below, plus the pre-existing P1 (header text).

### 4.5 Rule 2, every run

The original's SHA-256 was unchanged:
- in every run where I recorded it, before and after: all of 4.1, multi-turn, injection, hostile header, deadline, concurrency (both probes) and restart;
- in every run of the pipeline comparison.

The one exception is the integrity probe, which changes the upload on purpose. The API caught it each time.

## 5. Do the new tests catch what they guard?

- **Against the old code.**
  - `tests/core/test_engine.py::test_voice_raising_sets_failure_before_propagating` fails on `origin/main`'s engine, and passes on head.
  - The `tests/backend/` tests can't run on base, which has no `backend/` package.
- **Mutations** (`mutations.py`): one guard removed at a time, in a throwaway copy, against `tests/backend` and `tests/core/test_engine.py`. **23 of 29 were killed.** The 6 that survived:

| Mutation | Why it survived | Finding |
|---|---|---|
| `decide_no_mismatch_fallback`: the "reply says delivered, files refused → fallback" branch | No test covers it. If it regressed, the reply would say "Your cleaned copy … is ready" with no file: a HIGH. | L2 |
| `finalize_not_one_shot`: the second `if turn.finished` (the race guard) | No test drives the worker and the timer past the first check together | L3 |
| `upload_no_busy_check_at_register` | No test starts a turn while an upload is being written | L4 |
| `default_auto_approve_on`: `Settings.auto_approve = True` | Only `Settings.from_env` is tested; the dataclass default isn't pinned | L5 |
| `check_file_no_parent_check` | It's redundant with the `resolve()` comparison except for a symlinked sibling folder, which has no test | L6 |
| `start_no_reset_transient` | Redundant with the commit-time reset; removing both is killed | none |

---

## 6. Findings

### M1 (MEDIUM, ordinary): every turn that runs nothing says "I wasn't able to confirm that worked. No verifier has been configured."

- **Where.**
  - `buddy/voice.py:495` (`TemplateSpeaker.__call__`, the `_nothing_delivered` branch) → `_no_changes_message` (`:582`) → `_failure_message` (`:705`).
  - That path renders Inspector's fallback `NullVerifier` finding (`buddy/inspector.py:76-98`), which `CompositeVerifier` returns when there's no step to verify.
  - The API runs a turn with no upload (D-a), and keeps sessions open for follow-up turns. A file that was already analyzed gets no proactive step.
- **Scenario.** [Certain, all three speakers, through the API] Each of these gives the exact reply "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn.":
  - (a) any message before an upload ("hi", "remove duplicate rows");
  - (b) any follow-up that isn't one of the two actions, once the file has been analyzed: "thanks!" right after a delivered file, "hi";
  - (c) in the **default config**: "remove duplicate rows" (declined) followed by "yes, go ahead".

  [Likely, needs a live model] Brain's closed vocabulary maps "thanks", "yes" and any unsupported request ("can you sort it?") to `unclear`. The first reply of every analyzed turn ends with "Want me to tackle any of these?", so the user's natural "yes" lands here.
- **Why it matters.**
  - "No verifier has been configured" is false: verifiers are configured, and there was simply nothing to verify.
  - "I wasn't able to confirm that worked", sent in answer to "thanks!" after a verified delivery, reads as doubt about that delivery. That's the brief's "false can't-do about work that was done".
  - Nothing tells the user that nothing was attempted, or that a file is needed first.
- **Pre-existing?** The text is (backlog L3/N3, Low; N2, Medium), and `pipeline_compare` shows base's pipeline produces it identically. Earlier reviews rated it Low because it was hard to reach in one-shot runs. The API makes it reachable on every follow-up and before every upload.
- **Fix.**
  1. In `TemplateSpeaker.__call__`, first thing inside `if _nothing_delivered(request):`:

     ```python
     if execution_result is not None and not (
         execution_result.steps or execution_result.outputs or execution_result.errors
     ):
         return NOTHING_RAN_REPLY
     ```

     `NOTHING_RAN_REPLY` is a reviewed constant in `buddy/voice_catalog.py`, for example "I didn't run anything on a file this turn, so no file was changed."
  2. Add API tests, in all three speakers, for:
     - a turn before any upload;
     - "thanks!" after a delivered turn;
     - "yes" after a declined turn.

     Each asserts that the reply equals the constant, contains neither "confirm that worked" nor "verifier", and that the outcome is `not_delivered`. Add a second-turn scenario to `test_rule_1_holds_in_every_scenario`.
  3. **I prototyped this in a scratch copy.** The full suite passed (1246, unchanged), and the probes gave the constant for (a), (b) and (c). Under the review-loop rules, I mustn't be the reviewer of that fix.
  4. Optionally, also refuse a turn when the session has no upload, with a fixed `error.message`. That's an API-level error about a rejected request, which the design allows.

### M2 (MEDIUM, ordinary once file-changing steps run): "now remove the empty rows too" gives a copy without the first turn's change, and the reply doesn't say so

- **Where.**
  - `buddy/delegate.py` targets `context.active_file`, which is always the upload. The API never offers a delivered file as an input.
  - `buddy/voice.py` renders "Your cleaned copy of report.xlsx is ready."
  - This is the backlog's D3, explicitly a "product question for PR-2". The implementation notes don't mention it.
- **Scenario.** [Certain, through the API, all three speakers; the classification as `remove_empty_rows` is Likely for a real model]
  - T1 "remove duplicate rows" is delivered: 4 rows.
  - T3 "now remove the empty rows too" is delivered with the reply "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report.xlsx is ready."
  - The T3 file has **5 rows including the 2 duplicates** the user removed in T1. Nothing in the reply tells the user that this copy was made from the original.
  - This is the design's own live run 7 wording.
- **Reachability.** Every configuration in which file-changing steps run: `WORKBUDDY_AUTO_APPROVE=1` as the README instructs, and every approval once PR-3 lands. Not with the default config, which declines every write.
- **Fix.** This needs the owner's product decision between:
  - (a) follow-up turns read the session's last delivered file (cross-turn chaining); or
  - (b) keep reading the original, and say so.

  The minimal rule-1 fix, without a product change, is (b):
  - The API sets a transient, structured fact on the turn's state before the run: the number of files this session delivered in earlier turns (`len(record.delivered())`). Use a new `BuddyState` field cleared by `reset_transient()`, not text.
  - On a delivered turn where that number is above 0, Voice appends a reviewed catalog sentence, for example "I made this copy from your original report.xlsx, so it doesn't include changes from earlier files I gave you."
  - Test: dedupe, then "now remove the empty rows too". The second reply contains the sentence, and the first doesn't.

### M3 (MEDIUM, not ordinary): if the finished turn's record can't be saved, a restart later says no file was handed back, after it was

- **Where.** `backend/turns.py:337-367`, `_finalize`. The finished state is published in memory first. If `self._registry.saved(record, turn)` then raises, the error is only logged, and the disk keeps the `running` record written at start.
- **Scenario.** [Certain] I made `saved()` raise for finished turns.
  - The turn shows `delivered`, with the pipeline's "Done — removed 2 duplicate row(s)…", and the download returns `200`.
  - A new app on the same data root repairs it as `failed` / `pipeline_fallback`: "Something went wrong while I was working on this, so I can't confirm that any change was made. **I haven't handed back any file from this turn.**" That's false, because it was handed back and downloaded.
- **Reachability.** It needs a write failure under `sessions/` (disk full, I/O or permission error) between the output write and finalization, followed by a restart. It isn't ordinary, but disk full is a stated concern for the demo host before PR-4's retention.
- **Fix.** Make memory and disk agree.
  - Write the finished turn record **before** publishing it in memory.
  - If that write fails, finalize in memory as `failed` with `FALLBACK_PIPELINE_ERROR`, no files, the state discarded. That's what a restart would report anyway.
  - Test: `saved` raising for a finished turn gives `failed` / fallback / no files at once, and the same after a restart.

### LOW (deferred; not a merge condition; added to `docs/backlog.md`)

- **L1. `python -m backend`'s access log is broken.**
  - **Cause:** `backend/logging_filter.py:68-74`, `_redact_record`, sets `record.args = None`. uvicorn's `AccessFormatter` unpacks `record.args`.
  - **Effect:** [Certain] every request prints "--- Logging error --- … TypeError: cannot unpack non-iterable NoneType object": 46 in one short session on the real server. No secret is printed, because the message is redacted first.
  - **Why the tests miss it:** `test_the_session_id_is_masked_in_logs` uses caplog's formatter.
  - **Fix:** redact `record.msg` (if a `str`) and each `str` element of `record.args` in place, keeping its shape. Add a test that formats a redacted `uvicorn.access` record with `uvicorn.logging.AccessFormatter`.
- **L2. The rule-1 guard in `decide()` has no test.** That's `backend/turns.py:119-123`, which replaces a "delivered" reply when `delivered_files` refuses. [Certain] It works today (probe `delivery_mismatch`), but its mutation survives. Add a turn-level test, for example a Hands wrapper that moves the verified output outside the turn folder, asserting `failed` / fallback / no files.
- **L3. The one-shot race guard has no test.** That's `backend/turns.py:338`. Force the worker and the timer past the first check, for example with a barrier in a patched `decide`. Assert the turn finalizes once and never changes afterwards.
- **L4. The register-time `busy` check in uploads has no test.** That's `backend/routes.py:179`. If it regressed, an upload registered during a turn would be dropped from the context at commit, and the session couldn't be restored after a restart (`uploaded_files disagree`). Test: hold `store_upload` until a turn has started; assert `409` and nothing kept.
- **L5. `Settings.auto_approve`'s dataclass default isn't pinned.** Production uses `from_env`, which is tested. Add `assert Settings(data_dir=…).auto_approve is False`.
- **L6. `check_turn_file`'s name-based parent check has no test of its own.** Add one for an output path through a symlinked sibling folder that resolves into the turn folder.
- **L7. After an integrity failure, every later turn says "your original file changed while I was working".** The change happened in an earlier turn. It's only reachable after a rule-2 breach. Reword `FALLBACK_INTEGRITY_FAILURE`, for example "your original file no longer matches what you uploaded".
- **L8. Status codes.**
  - A message with a lone surrogate gets `500`, not `422`. It's caught as an unhandled exception from the UTF-8 record write.
  - `405` responses have no `Allow` header.
- **L9. Symlink checks don't cover the parents** under the data root (D-b).
- **L10. Absolute server paths in the prompts.** Brain and Think label `uploaded_files` "names only" (`buddy/brain.py:288`, `buddy/think.py:384`) but send absolute server paths (`/…/workspace/uploads/<key>/<file_id>/report.xlsx`) to the LLM provider. This is pre-existing in the pipeline, newly carrying the data root's location. Send names only.
- **L11. A corrupt `detail` isn't caught at restore.** A persisted `detail` is checked only as "a dict" (`backend/store.py:266`). A malformed one restores, and `GET …/turns/{id}` then returns `500` instead of a clean error. It needs on-disk corruption.
- **L12. The default config declines with "wasn't confirmed".** With auto-approve off, every file-changing request gets "The deduplicate step: wasn't confirmed, so it didn't run.", though nobody could be asked. It's true, and per design (PR-2 ships with steps declined). Noted for the owner: until PR-3, a default server changes no file.

### Pre-existing, not counted against this PR

- **P1. Column-header text from the file reaches the reply.** It appears in analysis findings, for example "Done! I removed every duplicate gsk_SECRETKEY123: 1 missing value(s)". The reply is identical on base, and the issue is recorded in fix-multi-sheet-review §4.3, but it isn't a backlog item of its own. The API now serves it over HTTP, and the frontend must escape `reply`. Added to the backlog.
- **D-R1.** An LLM outage gives "No changes were made…" without saying the request wasn't understood. It's in the backlog.
- **L-4.** "None of the issues I check for were found" on a delivered file that still has duplicates. It's in the backlog.

## 7. What the next round needs

1. **M1:** the `TemplateSpeaker` fix and the API tests described above.
2. **M2:** the owner's choice of (a) or (b). Absent a decision, implement (b), the structured fact plus a catalog sentence, with its test.
3. **M3** (recommended in the same round, because it's cheap): save before publishing; fall back if the save fails; test it.
4. **L2 to L5** are cheap test additions that close untested rule-1 and rule-2 guards. They aren't merge conditions.
5. Re-run `scripts/review_harness/` (README there), especially `adversarial_probe.py --only multi_turn`, `--only no_file_turns` and `mutations.py`.

## Deferred

L1–L12 and P1 above, added to `docs/backlog.md`.

## Not run

- **Live runs with Groq.** By the review loop these come after a MERGE verdict. So the real model's behaviour is untested here:
  - Brain's classification with the conversation history in its prompt (design 3.3's open risk);
  - whether "thanks", "yes" or "now … too" are classified as I assumed for M1 and M2.
- **The demo host.** No measurements of memory, wall time, or a 5 MB file (live run 9).
- **Confirm/reject, password, rate limits and retention.** Those belong to PR-3 and PR-4.
- **The race windows.** I didn't drive the finalize race (L3) or the register race (L4) through actual thread interleavings. I only showed by mutation that no test covers them.
- **Windows.** Every run was on Linux as root, so the `0o444`/`0o555` permissions weren't exercised as a non-root user.

---

# Review round 2 (commit 0276e7c)

**Verdict: DON'T MERGE.** There's no HIGH finding. One MEDIUM, R2-M1, is reachable in ordinary use. M2's fix adds "I made this copy from your original upload, not from a file I handed back earlier" to every delivered reply once the session has handed back any file. That includes a turn on a file the user re-uploaded after downloading it from Buddy, where the copy *does* build on the earlier change. The fix is small and listed under "What the next round needs".

**Reviewer:** a fresh reviewer agent, with no part in writing the code or the round-1 fixes. **Base:** `origin/main` (`6d000c5`). **Design:** `docs/design/backend-api-design.md`. **Scope:**
- the diff since the last reviewed commit, `f3c5ba3..0276e7c`. The code part is `ae734ae..0276e7c`: 18 files, +661/−18, in `backend/`, `buddy/state.py`, `buddy/voice.py`, `buddy/voice_catalog.py`, tests and docs. `0ee68fa` and `ae734ae` add only the round-1 reviewer's files.
- every finding round 1 left open;
- regression against the base.

I treated `docs/reviews/backend-api-core-fixes1.md` as unverified. Each claim below comes from my own run and is marked [Certain], [Likely] or [Possible].

## R2.1 Tests and lint

| Check | Result |
|---|---|
| `python -m pytest -q`, Python 3.11.15, head `0276e7c` | **1301 passed**, 11 warnings, 160 s, exit 0. Round 1 had 1246, so 55 tests were added; the fixes file's count matches. |
| `ruff check buddy tests` | 22 errors on head and 22 on `origin/main`. Grouped by file and rule, the two sets are **identical**. **No new lint errors.** |
| `ruff check backend core skills scripts tests/backend tests/buddy/test_nothing_ran_and_from_original.py` | clean, including my new `scripts/review_harness/` files |
| `python -m backend.export_openapi --check` | in sync (exit 0) |

## R2.2 Round-1 findings: are they fixed?

Every item below is checked with my own probes (`scripts/review_harness/`; see its README). Each probe was run on head and on a worktree of `ae734ae`, the code round 1 reviewed with nothing fixed.

| Finding | Status | My evidence |
|---|---|---|
| **M1** "No verifier has been configured" when nothing ran | **Fixed** [Certain] | `round2_probe.py --only m1_nothing_ran`, in all 3 speakers. Each of these now gets exactly "I didn't run anything on a file this turn, so no file was changed.":<br>• "hi" and "remove duplicate rows" before any upload;<br>• "thanks!" after a delivery;<br>• "yes, go ahead" after the default config declined;<br>• a second "hi";<br>• a later turn whose plan has only empty `source_phrase` steps;<br>• a later turn with the LLM down;<br>• a later turn where Think asks for clarification.<br>On `ae734ae` all of these gave M1's reply. Turns where something ran keep their own replies: a first-turn greeting with an upload (analysis findings), a second upload's greeting (its own analysis), a declined step, and an unknown capability ("couldn't be run because no available skill can do it"). None mentions a verifier. |
| **M2** "now … too" worked from the original without saying so | **Fixed for the same upload; open in a narrower form (R2-M1)** | `m2_from_original`, 3 speakers.<br>• **Same-upload follow-up:** dedupe, then "now remove the empty rows too". The second reply ends "I made this copy from your original upload, not from a file I handed back earlier." My openpyxl read of that file: 5 rows, 2 duplicates, 0 empty, so the sentence is true.<br>• **After a restart:** the sentence is still there (the count comes from the restored turn records).<br>• **Chain after a delivery:** the sentence is there, and the file is 3 rows, 0 duplicates, 0 empty.<br>• **A failed turn, then a delivery:** no sentence (correct).<br>• **A re-upload of the delivered file:** misleading, see R2-M1. |
| **M3** a finished record that couldn't be saved was published | **Fixed** [Certain] | `m3_save_failures`, three cases.<br>• **The finished save fails once, then the fallback save succeeds:** memory says `failed` / `FALLBACK_PIPELINE_ERROR` / no files, the file list has only the upload, and a restart reports the same.<br>• **Every finished save fails:** the same.<br>• **Only the session save fails at finalization:** memory and the restart both say `delivered`, and the download is `200` after the restart.<br>On `ae734ae` the first two cases gave `delivered` in memory and `failed` after the restart, so memory and disk disagreed. On head they agree in all three cases. |
| L1 uvicorn's access log broken | **Fixed for uvicorn** [Certain]; **the fix introduced R2-L1** | `server_probe.py` (the real `python -m backend` server): 0 "Logging error" lines, and the access log shows `/api/v1/sessions/[REDACTED]/…`. |
| L2 `decide()`'s mismatch fallback untested | **Fixed** | The mutation `decide_no_mismatch_fallback` is now killed by `test_a_verified_delivery_the_api_cant_hand_back_gets_the_fallback`. |
| L3 the one-shot race guard untested | **Fixed** | `finalize_not_one_shot` (retargeted to the new code) is killed by `test_the_worker_and_the_deadline_racing_finish_the_turn_once`. |
| L4 the register-time busy check untested | **Fixed** | `upload_no_busy_check_at_register` is killed. |
| L5 the `auto_approve` default not pinned | **Fixed** | `default_auto_approve_on` is killed. |
| L6 the name-based parent check untested | **Fixed** | `check_file_no_parent_check` is killed by the symlinked-sibling test. |
| L7 the wording of `FALLBACK_INTEGRITY_FAILURE` | **Fixed** [Certain] | The `integrity` probe now says "…your original file no longer matches the one you uploaded. I've held back every file from this turn." |
| L8 status codes | **Fixed** [Certain] | `l8_requests`:<br>• a lone high or low surrogate, including one that is all that's left after stripping, gets `422 invalid_request` (`500` on `ae734ae`);<br>• a valid surrogate pair (an emoji) is still accepted (`202`);<br>• `405` responses carry `Allow` (`POST` and `GET` where right). |
| L11 a corrupt `detail` restored | **Fixed** [Certain] | `l11_restore` and `l11_more`.<br>• Details stored by real turns restore unchanged after a restart: delivered, chain, clarification, declined (`stopped_at` set), no-intent and unknown-capability turns.<br>• `verified: "true"`, `verified: 1`, `steps: "abc"` and `detail: []` make the session a clean `404`. On `ae734ae` the first three restored, and `steps: "abc"` then gave `500` on the turn's `GET`.<br>• An extra key inside `detail` still restores (`TurnDetail` ignores extras); the API drops it when serving. That's harmless. |
| L9, L10, L12, P1 | **Open, deferred** as round 1 decided. Unchanged on head, and still in `docs/backlog.md`. | — |

## R2.3 The fixes against the design

- **D-e. A new pipeline input outside the API.** `BuddyState.earlier_deliveries` is set by `backend/turns.py` and read by `buddy/voice.py`. The design (Overview item 7, Section 3.3) lists the pipeline changes the API depends on, and this one isn't there. It is documented only in `backend-api-core-fixes1.md`. It respects invariant 1 (Section 5.1): the sentence is a reviewed constant in `buddy/voice_catalog.py`, and `reply` is still `state.reply` byte for byte. I confirmed this in all 39 `api_probe` runs and every multi-turn probe. LOW: add it to the design's decisions addendum. Its counting rule should change anyway (R2-M1).
- **M3's write order** matches design 3.1 ("after each finished turn the API writes `turns/{turn_id}.json` and rewrites `session.json`"). It also keeps invariant 7 (one finalization step under the session lock).
- **The new constants** (`NOTHING_RAN_REPLY`, `FROM_ORIGINAL_SENTENCE`) sit in `buddy/voice_catalog.py`, as design 3.6 requires for reply constants. Neither matches `_COMPLETION_CLAIM`. No opener or closer in the catalog contradicts them; I searched for "earlier", "again", "build" and "on top".
- Nothing else in the fixes departs from the design.

## R2.4 Probes through the real pipeline, and the base comparison

- **`api_probe.py`** (13 scenarios × 3 speakers) on head against `ae734ae`:
  - Every reply and outcome is identical except:
    - the random session key inside generated file names;
    - the Friend opener, which is seeded by the random session id;
    - scenario 7, no upload, which is the M1 change.
  - In all 39 runs:
    - the API's reply equals the pipeline's own reply, or the fallback when Voice raised;
    - `outcome == "delivered"` exactly when files were listed;
    - every download's SHA-256 matches its listing;
    - the original's SHA-256 is unchanged.
- **`adversarial_probe.py`** (all probes, 3 speakers) on head against `ae734ae`. The only differences are:
  - M1's reply in `multi_turn` and `no_file_turns`;
  - M2's sentence in `multi_turn` T3 and in `restart`'s T3;
  - L7's wording in `integrity`;
  - the ordering of file listings, and seeded openers.

  Hostile names, upload structure and size, concurrency, deadline, corrupt restore, concurrent correctness and the delivery mismatch behave as in round 1.
- **`pipeline_compare.py --diff`**, head against an `origin/main` worktree, 7 scenarios × 3 speakers. Every reply is byte-identical except:
  - `no_file` and the second turn of `two_turns_then_thanks`: the M1 constant replaces the verifier wording;
  - `voice_raises`: round 1's known hardening.

  The pipeline run on its own never sets `earlier_deliveries`, so it never adds the M2 sentence. Base and head agree there.

## R2.5 Adversarial cases aimed at the new code (`round2_probe.py`)

Every turn below is shown for all three speakers unless it says otherwise. Each delivered file was read independently with openpyxl. The original's SHA-256 was unchanged in every run except the deliberate integrity probe.

| Case | What happened | Handed back (my read) | Reply (focus unless noted) |
|---|---|---|---|
| Dedupe, download the copy, **upload it again**, "now remove the empty rows" | The turn runs on the re-upload: analyze → remove_empty_rows → analyze | 3 rows, 0 duplicates, 0 empty: the dedupe **is** in it | "Done — removed 1 fully-empty row(s). The copy has 3 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report_deduplicated_…-task-1.xlsx is ready. **I made this copy from your original upload, not from a file I handed back earlier.** None of the issues I check for were found." → **R2-M1** |
| Dedupe file A, then upload an unrelated `budget.xlsx` and dedupe it | delivered | 2 rows, 0 duplicates | "…Your cleaned copy of budget.xlsx is ready. I made this copy from your original upload, not from a file I handed back earlier. …" True, but beside the point (R2-M1 covers it) |
| The model misreads "thanks!" as a dedupe request after a delivery | A second dedupe of the original | 4 rows, 0 duplicates, 1 empty | "Done — removed 2 duplicate row(s). … I made this copy from your original upload, not from a file I handed back earlier." True. The extra file comes from the model's misreading, not the API. |
| A later turn: Think asks for clarification ("clean it") | nothing ran | nothing | "I didn't run anything on a file this turn, so no file was changed." True. Think's question never reaches the user, which was already so on base (R2-L4). |
| "remove duplicate rows" with no upload | nothing ran | nothing | The same constant. True, but it doesn't say a file is needed (R2-L4). |
| A later turn, LLM down (error text contains `gsk_LEAKLEAK`) | nothing ran | nothing | The same constant. No key-like text in the reply or `detail`. |
| An integrity failure whose finished save also fails | `failed` / `pipeline_fallback` (on `ae734ae`: `integrity_failure`) | nothing | `FALLBACK_PIPELINE_ERROR`. It claims nothing, and the next turn reports the integrity failure (fail closed). That's acceptable, because memory now agrees with what a restart reports. |
| A `BaseException` (not `Exception`) from the finished save | The turn stays `running` for good, and the next turn is `409` until a restart. On `ae734ae` the turn finished. | nothing | none (R2-L2) |
| Log calls `"Bearer %s"`, `"api_key=%s"`, `"GET /api/v1/sessions/%s/files"` with the redaction installed | The record no longer formats. Logging's error handler prints `Arguments: ('tok_plain_SECRET123',)` to stderr. On `ae734ae` all three printed redacted. | — | **R2-L1** |
| Upload name with odd bytes; surrogates in `file_id` and `mode` | `201 rep_ort.xlsx`; `404 file_not_found`; `422` | — | — |

**Rule 1, every reply produced in this round.**
- **Delivered replies:** every number matches my openpyxl read:
  - removed 2 → 4 rows;
  - removed 1 empty → 5 rows with 2 duplicates;
  - chain → 3 rows;
  - re-upload → 3 rows;
  - budget → 2 rows;
  - 25.0% = 1 of 4, 16.7% = 1 of 6.
- **Not-delivered and failed replies:** none claims a change.
- **No exception or key text** reached a reply or `detail`.
- **The one misleading reply** is R2-M1's.

**Rule 2:** the original's SHA-256 was unchanged in every run, before and after, in all the probes above. The exception is the integrity probes, which change the upload on purpose; the API caught it every time and released nothing.

## R2.6 Do the new tests catch what they guard?

- **Against the old code.** In a worktree of `ae734ae`, I copied in the new and changed tests and added only the new names, with no behaviour: the two constants, and `save_turn`/`save_session` as pass-throughs. **47 of them fail**, matching the fixes file. That covers:
  - every M1, M2 and M3 test;
  - L1, L8 and L11;
  - the L3 race test.
- **Mutations** (`mutations.py`, now 45, run against `TEST_PATHS`, which adds the buddy Voice and state tests): **42 killed, 3 survived.**

| Survivor | Effect | Assessment |
|---|---|---|
| `start_no_reset_transient` | redundant with the commit-time reset | none (as in round 1) |
| `m3_no_fallback_save`: the fallback record isn't written | the disk keeps the `running` record. A restart reports the same `failed` / fallback, and nothing reads the disk in between. | unobservable; no action |
| **`m2_counts_every_turn`**: `earlier_deliveries = len(record.turns)` instead of the deliveries | after any non-delivering turn (a greeting, a decline, a failure), the next delivery would say "not from a file I handed back earlier" though nothing was ever handed back | a test gap. It's closed by R2-M1's test item (b) below. |

Every new round-2 guard is killed:
- M1: the branch, ignoring errors, ignoring steps;
- M2: not set, always on, not passed to the speaker, not reset;
- M3: save failure ignored, save after publish;
- L1: always or never collapse;
- L8: the encodable check, `Allow`;
- L11: lax detail.

## R2.7 Findings

### R2-M1 (MEDIUM, ordinary): the from-original sentence is added after a delivery from *any* upload, including a re-upload of Buddy's own copy, where it misleads

- **Where.**
  - `backend/turns.py:240`: `work.earlier_deliveries = len(record.delivered())` counts every file the session handed back, whatever upload it came from.
  - `buddy/voice.py:419` appends `FROM_ORIGINAL_SENTENCE` (`buddy/voice_catalog.py:188`) whenever that count is above 0.
- **Scenario.** [Certain, through the API, all three speakers]
  1. Upload `report.xlsx`; "remove duplicate rows". Delivered: 4 rows.
  2. Download the copy and upload it again. It is stored as `report_deduplicated_<key>-task-1.xlsx` and becomes the active file.
  3. "now remove the empty rows". Delivered: **3 rows, 0 duplicates, 0 empty**, so the earlier dedupe *is* in this copy.
  4. The reply (focus; friend is the same after its opener): "Done — removed 1 fully-empty row(s). The copy has 3 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report_deduplicated_…-task-1.xlsx is ready. I made this copy from your original upload, not from a file I handed back earlier. None of the issues I check for were found." Template has the same sentence after "Saved as `…`".
- **Why it matters.**
  - In a session with two uploads, "your original upload" reads as the first one, `report.xlsx`.
  - "Not from a file I handed back earlier" says Buddy didn't use its own copy, but the bytes it worked on are exactly that copy.
  - [Likely] A user concludes the duplicates are back in this file. That's false (my read: 0 duplicates), and they may redo the dedupe or distrust the file.
  - In Friend and Focus, the same reply names `report_deduplicated_…` as the file it cleaned, which contradicts the sentence.
  - It's misleading rather than provably false, so MEDIUM.
  - With an unrelated second upload (`budget.xlsx`), the sentence is true but irrelevant (LOW on its own).
- **Reachability.** [Likely, ordinary] Download and re-upload are the API's own operations. Re-uploading Buddy's copy is how a user makes changes add up under option (b), and the sentence itself tells them each turn starts from the upload. It needs file-changing steps to run (`WORKBUDDY_AUTO_APPROVE=1`, as the README instructs, or any approval once PR-3 lands).
- **Fix.**
  1. Count only earlier deliveries made from **the same upload this turn works on**. In `TurnRunner.start`:

     ```python
     target_id = file_id if file_id is not None else record.active_file_id
     work.earlier_deliveries = sum(
         len(t.delivered_files)
         for t in record.turns
         if t.finished and t.outcome == "delivered" and t.target_file_id == target_id
     )
     ```

     `target_file_id` is already stored on every turn and restored after a restart. With this, the sentence appears only when this upload already produced a delivered copy, which is exactly the case it was written for. "Your original upload" then names an upload that already had a copy made from it.
  2. **I prototyped this in a scratch worktree.** With it, `m2_from_original` gives the sentence for the same-upload follow-up, after a restart and for the chain, and no sentence for the re-upload, for `budget.xlsx`, or after a failed turn. `tests/backend/test_turns.py` and `tests/buddy/test_nothing_ran_and_from_original.py` passed (129). Under the review-loop rules, I mustn't review that fix.
  3. Tests, through the API, in all three speakers:
     - (a) dedupe, download the copy, upload it again, "now remove the empty rows": delivered, **no** `FROM_ORIGINAL_SENTENCE`;
     - (b) a delivery after a non-delivering first turn (a greeting, or a failed turn): **no** sentence. This kills `m2_counts_every_turn`.
     - (c) a delivery on a second, unrelated upload after a delivery on the first: **no** sentence;
     - (d) keep the existing same-upload follow-up test (sentence present).

### LOW (deferred; not merge conditions; added to `docs/backlog.md`)

- **R2-L1. The L1 fix breaks log records whose format string has a secret prefix right before a placeholder, and logging then prints the raw argument.**
  - **Cause:** `backend/logging_filter.py:77-78` redacts `record.msg`, the format string, on its own. The patterns eat the placeholder:
    - `Bearer\s+[^\s'",;]+` matches `Bearer %s`;
    - `api[_-]?key\s*[=:]…` matches `api_key=%s`;
    - the `/sessions/` pattern's character class includes `%`, so it matches `/sessions/%s`.
  - **Effect:** `getMessage()` then raises "not all arguments converted". `_redact_record` returns early, the handler fails, and `logging.Handler.handleError` writes `Message: 'Authorization: [REDACTED]'` and `Arguments: ('tok_plain_SECRET123',)` to stderr.
  - **Evidence:** [Certain] `scripts/review_harness/l1_placeholder_probe.py` on head shows this for all three shapes. On `ae734ae` each printed redacted.
  - **Reachability:** [Likely none today]. No log call of that shape exists in `backend/`, `buddy/`, `core/`, `skills/`, `scripts/` or in the installed uvicorn, starlette, fastapi, httpx, groq and anthropic packages (searched). So it's latent, in a defence-in-depth layer whose job is to cover exactly such future calls.
  - **Fix (prototyped; the redaction and secrets tests passed, 39):**
    1. Don't redact `record.msg` separately.
    2. Compute `redacted = redact(record.getMessage())` first.
    3. Redact the `str` args in place, then re-format.
    4. If that differs from `redacted` (or fails), collapse to `msg = redacted, args = None`.

    That keeps uvicorn's access record whole and can never drop a placeholder. Test: the three calls above format without a "Logging error" and with no secret in captured stderr.
- **R2-L2. A `BaseException` from the finished save leaves the turn `running` for good.**
  - **Cause:** `backend/turns.py:402` catches only `Exception`, and `_run` cancels the deadline timer before `_finalize`.
  - **Effect:** [Certain] With an `Abort(BaseException)` raised by `save_turn` for finished records, the turn was still `running` after 4 s (deadline 2 s), and the next turn got `409` until a restart. On `ae734ae` the turn finished, because the save came after publishing.
  - **Reachability:** [Likely not reachable]. File I/O on a worker thread raises `Exception` subclasses (`OSError`, `MemoryError`).
  - **Fix:** catch `BaseException` in `_save_finished` and return `False`, so the turn finishes as `failed` with the fallback.
- **R2-L3. The `earlier_deliveries` pipeline input isn't in the design** (D-e above). Record it, with R2-M1's counting rule, in the design's decisions addendum.
- **R2-L4. `NOTHING_RAN_REPLY` is honest but unhelpful in two ordinary cases.**
  - Before any upload, "remove duplicate rows" doesn't say a file is needed. That's round 1's optional item 4: refuse the turn with a fixed `error.message`, or add a catalog sentence when `uploaded_files` is empty.
  - On a later turn where Think asked for clarification, the question never reaches the user. That was already so on base, where no reply path renders it.
- **Observation (pre-existing, not counted).** On later turns no `spreadsheet.analyze` runs, neither first nor the final-file recheck, so a later delivered reply carries no findings. `pipeline_compare` shows base's pipeline does the same. It's harmless to rule 1, but the owner may want to know, given "Decided behaviour" says analysis "is re-run on the final file".

## R2.8 What the next round needs

1. **R2-M1:** count `earlier_deliveries` per target upload (the code above). Add tests (a) to (d).
2. **Recommended in the same round, because it's cheap:** R2-L1's `_redact_record` change with its test, and R2-L2's `except BaseException`.
3. Re-run `scripts/review_harness/round2_probe.py --only m2_from_original` and `mutations.py --only m2`. `m2_counts_every_turn` must be killed. Also re-run `l1_placeholder_probe.py 2>&1` if R2-L1 is fixed.

## Deferred (round 2)

R2-L1 to R2-L4 above, added to `docs/backlog.md`. Round 1's L9, L10, L12 and P1 stay deferred.

## Not run (round 2)

- **Live runs with Groq.** By the review loop these come after a MERGE verdict. So these are still untested with a real model:
  - how it classifies "thanks", "yes" and "now … too" with the conversation in the prompt;
  - whether it reads a re-uploaded file name like `report_deduplicated_…-task-1.xlsx` as the right target.
- **The demo host, Windows, and a non-root user** (the `0o444` permissions), as in round 1.
- **Real thread interleavings for M3's lock-held I/O.** I didn't measure how long `GET` waits while a finished record is written under the session lock. It's one small atomic write, so I expect it to be short.
- **The full suite on my two prototypes** (R2-M1, R2-L1). I ran only the affected test files (129 and 39 passed). The prototypes were throwaway worktrees and aren't committed.
