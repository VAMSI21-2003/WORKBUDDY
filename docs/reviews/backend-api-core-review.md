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
