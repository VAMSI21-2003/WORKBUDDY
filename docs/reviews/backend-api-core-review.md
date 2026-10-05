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

---

# Review round 3 (commit 7be9949)

**Verdict: MERGE.** There's no HIGH finding and no MEDIUM finding. R2-M1 is fixed, and so are R2-L1, R2-L2 and R2-L3. The LOW findings below are deferred to `docs/backlog.md`.

**Reviewer:** a fresh reviewer agent, with no part in writing the code, the earlier fixes, or the earlier reviews. **Base:** `origin/main` (`6d000c5`). **Design:** `docs/design/backend-api-design.md`. **Scope:**
- the diff since the last reviewed commit, `0276e7c..7be9949`. The code part is `9e4866a..7be9949`: `backend/logging_filter.py`, `backend/turns.py`, two test files, the design addendum, the backlog and the fixes file (7 files, +238/−20). `9e4866a` adds only the round-2 review, its probe scripts and its backlog items.
- every finding round 2 left open;
- regression against the base, and against `0276e7c`.

I treated `docs/reviews/backend-api-core-fixes2.md` as unverified. Each claim below comes from my own run and is marked [Certain], [Likely] or [Possible]. My probes ran on head, on a worktree of `0276e7c` (the code round 2 reviewed), and, for the pipeline, on a worktree of `origin/main`.

## R3.1 Tests and lint

| Check | Result |
|---|---|
| `python -m pytest -q`, Python 3.11.15, head `7be9949` | **1317 passed**, 11 warnings, 174 s, exit 0. Round 2 had 1301, so 16 tests were added; the fixes file's count matches. |
| `ruff check buddy tests` | 22 errors on head and 22 on `origin/main`. Grouped by file and rule, the two sets are **identical**. **No new lint errors.** |
| `ruff check backend core skills scripts tests/backend` | clean, including my new and changed `scripts/review_harness/` files |
| `python -m backend.export_openapi --check` | in sync (exit 0) |

## R3.2 Round-2 findings: are they fixed?

| Finding | Status | My evidence |
|---|---|---|
| **R2-M1** the from-original sentence followed a delivery from any upload | **Fixed** [Certain] | `round3_probe.py --only m2_targets` (10 cases) and `round2_probe.py --only m2_from_original`, all 3 speakers, on head and on `0276e7c`. On head the sentence appears **only** when the same upload already had a copy handed back: the same-upload follow-up, a follow-up after a restart, a chain after a delivery, going back to an earlier upload with `file_id` (also after a restart), and an explicit `file_id` on both turns. It's absent for a re-upload of Buddy's copy (round 2's scenario), an unrelated second upload, a delivery on another upload only, two uploads with the same name, a chain's copy re-uploaded, after a failed turn, after Voice raised, and after a turn that passed its deadline while its late worker wrote a file. Where it appears, my own model of the skill, applied to every upload and every earlier delivered file, shows the copy was made from that upload and from no earlier delivered file, so the sentence is true. On `0276e7c` the sentence also appeared on the second upload's first delivery (cases A, H, J), on the re-uploaded copy (C, I), and on the re-uploaded original (B). The only case where head's change removed a true sentence is B; see R3-L5. |
| **R2-L1** redacting the format string on its own dropped placeholders | **Fixed** [Certain], **with a narrower gap (R3-L1)** | `l1_placeholder_probe.py` prints all three shapes redacted, with no "Logging error". `round3_probe.py --only l1_logging` (20 records): `"Authorization: Bearer %s"` and `"100%% at /sessions/%s"` leak the raw argument to stderr on `0276e7c` and are masked on head. The real server (`server_probe.py`) formats every ordinary access line as `/api/v1/sessions/[REDACTED]/…`, and the session id appears nowhere in its log. |
| **R2-L2** a `BaseException` from the finished save left the turn running | **Fixed** [Certain] | `round3_probe.py --only l2_base_exception`: a custom `BaseException` once or always, `KeyboardInterrupt` and `SystemExit` from `save_turn` for the finished record. On head every case finishes `failed` with `FALLBACK_PIPELINE_ERROR`, no files, the session not busy, the next turn `202`, and a restart reports the same. On `0276e7c` all four stayed `running` and the next turn got `409`. |
| **R2-L3** `earlier_deliveries` missing from the design | **Fixed** [Certain] | "Decisions (2026-10-04)", item 2, describes the field, where it's set, why, the per-upload rule, and that option (a) is still the owner's to decide. Each statement matches `backend/turns.py:240-252`, `buddy/state.py:157,249` and `buddy/voice.py:419`. |
| R2-L4 | **Open, deferred** as round 2 decided. Unchanged on head and still in `docs/backlog.md`. | `m1_nothing_ran` gives `NOTHING_RAN_REPLY` for "remove duplicate rows" before any upload, and for a later turn where Think asks for clarification. |
| L9, L10, L12, P1 (round 1) | **Open, deferred**. Unchanged. | — |

Everything round 1 fixed still holds on head: `round2_probe.py` (M1, M2, M3, L1, L8, L11 probes) gives the same results round 2 recorded, apart from R2-M1's intended change.

## R3.3 The fixes against the design

- The per-upload counting rule matches the design's new implementation note, word for word in effect. The note is correctly labelled "not an owner decision", and leaves option (a) to the owner.
- `_save_finished` catching `BaseException` keeps invariant 7 (one finalization step under the session lock), and memory and disk still agree (my restart checks).
- The new `_redact_record` is still a pattern filter that never reads a key's value (design 6.4).
- No other departure from the design in this diff.

## R3.4 Probes through the real pipeline, and the base comparison

- **`api_probe.py`** (13 scenarios × 3 speakers) on head. In all 39 runs:
  - the API's reply equals the pipeline's own reply, or the fallback when Voice raised;
  - `outcome == "delivered"` exactly when files were listed;
  - every download's SHA-256 matches its listing;
  - the original's SHA-256 is unchanged.

  The replies are the ones round 2 recorded. This diff can't change a single-turn reply: the only `turns.py` change outside the save is the count, which is 0 on a session's first delivery.
- **`adversarial_probe.py`** (all probes, 3 speakers) on head. Hostile names, upload structure and size, concurrency and isolation (another session's files `404`, the cap `429` with `Retry-After`), the deadline, the integrity failure (fail closed), corrupt restore (`404`), 8 rounds of concurrent correctness, the delivery mismatch, and the restart (the interrupted turn reads `failed` / fallback, the earlier file still downloads byte for byte, no session id on disk) all behave as in round 2. `multi_turn` T3 carries the sentence (same upload).
- **`round2_probe.py`** (all probes, 3 speakers) on head: as in round 2, except the re-upload case, which no longer has the sentence (R2-M1).
- **`pipeline_compare.py --diff`**, head against an `origin/main` worktree, 7 scenarios × 3 speakers. Every reply is byte-identical except the three differences round 2 recorded: `no_file` and the "thanks" turn of `two_turns_then_thanks` (`NOTHING_RAN_REPLY` instead of the verifier wording), and `voice_raises` (round 1's hardening sets `failure`). The pipeline run on its own never sets `earlier_deliveries`.

## R3.5 Adversarial cases aimed at the new code

Every case below ran in all three speakers unless it says otherwise. Each delivered file was read independently with openpyxl. "Made from" is my own model of the skill (exact-duplicate removal, fully-empty-row removal) applied to every upload and every earlier delivered file of the session; it lists the ones that give exactly the delivered rows.

| Case (`round3_probe.py`) | What happened (head) | Handed back (my read) | Reply (focus; friend adds an opener, template says "Saved as `…`" instead of naming the input) |
|---|---|---|---|
| A. Dedupe `report.xlsx`; upload `budget.xlsx` and dedupe it; `file_id` = report, "now remove the empty rows from report.xlsx too" | T2: no sentence (on `0276e7c`: sentence). T3: sentence. | T3: 5 rows, 2 duplicates, 0 empty. Made from: the report upload only. | T3: "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report.xlsx is ready. I made this copy from your original upload, not from a file I handed back earlier." True. |
| B. Dedupe; upload **the same bytes** again (`report.xlsx`, same SHA-256); "now remove the empty rows too" | No sentence (on `0276e7c`: sentence). | 5 rows, **2 duplicates**, 0 empty. | "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report.xlsx is ready. None of the issues I check for were found." → **R3-L5**. "None of the issues…" with 2 duplicates is backlog L-4 (pre-existing). |
| C. Dedupe; re-upload Buddy's copy; "now remove the empty rows"; then "and remove duplicate rows again" | T2: no sentence (fixed). T3: sentence. | T2: 3 rows, 0 duplicates, 0 empty. T3: 4 rows, 0 duplicates, 1 empty, made from the re-upload (the steps show `deduplicate:report_deduplicated_…`). | T3: "Done — there were no duplicate rows to remove. The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of report_deduplicated_…-task-1.xlsx is ready. I made this copy from your original upload, not from a file I handed back earlier." True in the API's terms (T2's change isn't in it); see R3-L7 on the word "original". |
| D. Same upload, `file_id` sent explicitly on both turns | T2: sentence | 5 rows, 2 duplicates | as A's T3. True. |
| E. Dedupe report; upload budget; **restart**; `file_id` = report, "now … too" | sentence | 5 rows, 2 duplicates, made from the report upload | as A's T3. True. |
| F. T1 passes its 4 s deadline while Think is blocked; the late worker then writes its file; T2 dedupe | T1 `failed` / fallback; T2 delivered, **no sentence** | T2: 4 rows. The late file is never listed. | T1: `FALLBACK_PIPELINE_ERROR`. T2: the ordinary dedupe reply. True: nothing was handed back before. |
| G. Voice raises on T1; T2 dedupe | T1 `failed`; T2 no sentence | T2: 4 rows | True. |
| H. Two uploads both named `report.xlsx`, different content; dedupe the second; `file_id` = the first, "now … too" | T2 no sentence; T3 sentence | T3: 5 rows, 2 duplicates, made from the first upload | True. The two files share a name, which was so before this diff. |
| I. Chain on the original; re-upload the chain's copy as `report_clean.xlsx`; dedupe | no sentence | 3 rows, 0 duplicates, 0 empty | "Done — there were no duplicate rows to remove. … Your cleaned copy of report_clean.xlsx is ready…" True. |
| J. Two uploads; dedupe the second; then `file_id` = the first | no sentence | 4 rows, made from the first upload | True: the first upload had no copy yet. |
| BaseException from the finished save (4 kinds), from the session save | see R3.2; a session-save `BaseException` leaves the turn `delivered` in memory and on disk (the turn record was saved first), and the next turn runs | — | — |
| `uploads_unchanged` or `make_read_only` raising inside `_finalize` (not new code) | the turn stays `running`, the next turn is `409` until a restart (identical on `0276e7c`) | nothing | none → **R3-L4** |
| 20 odd log records (`l1_logging`), on head and `0276e7c` | see R3-L1 to R3-L3 | — | — |
| The real server, `GET /healthz?api_key=`, `GET /healthz?x=Bearer`, `GET /api/v1/sessions/<id>?api_key=` | each answered `200`; each printed a "--- Logging error ---" block instead of its access line (identical on `0276e7c`) | — | no secret and no session id in the log → **R3-L3** |

**Rule 1, every reply produced in this round.**
- **Delivered replies:** every number matches my openpyxl read:
  - removed 2 → 4 rows, 0 duplicates;
  - removed 1 empty → 5 rows;
  - the re-uploaded copy → 3 rows;
  - "no duplicate rows to remove" → 4 rows (C) and 3 rows (I), 0 duplicates;
  - budget → 2 rows;
  - chain → 3 rows;
  - 25.0% = 1 of 4, 16.7% = 1 of 6.
- **The from-original sentence** appears only where the copy was made from an upload that already had a copy handed back, and from no earlier delivered file (my model, every case above). It never appears on a first delivery.
- **Not-delivered and failed replies** claim no change. No exception or key text reached a reply or `detail`.
- **Misleading by omission:** case B (R3-L5). "None of the issues I check for were found" on a file with duplicates is backlog L-4, pre-existing.

**Rule 2:** the original's SHA-256 was unchanged, before and after, in every run: 69 turn checks in `m2_targets`, 6 in the deadline case, 7 in the `BaseException` probe, 39 in `api_probe`, 75 in `round2_probe` and 31 in `adversarial_probe`. The four exceptions are the integrity probes, which change the upload on purpose; the API caught each one and released nothing.

## R3.6 Do the new tests catch what they guard?

- **Against the old code.** I copied head's `tests/backend/test_turns.py` and `tests/backend/test_app_basics.py` (the only changed test files) into the `0276e7c` worktree and ran the 16 new tests. **10 fail:**
  - the re-upload test (a) and the other-upload test (c), in all 3 speakers;
  - the `BaseException` test;
  - all 3 split-secret logging tests.

  The 6 that pass are test (b), "a first delivery after a turn that delivered nothing". Round 2's count, `len(record.delivered())`, already gave 0 there; (b) exists to kill the "count every turn" mutation, which it does (below).
- **Mutations** (`mutations.py`, now 56, run against `TEST_PATHS`): **47 killed, 5 survived, 4 not applied.** The 4 not applied are round 2's `m2_not_set_at_start`, `m2_counts_every_turn`, `l1_always_collapse` and `l1_never_collapse`, whose target lines changed. I retargeted each as an `r3_` mutation, and all four are killed.

Every new round-3 guard is killed:
- `r3_m2_not_set`, `r3_m2_target_only_file_id`: by the follow-up tests;
- `r3_m2_counts_every_turn`, and `r3_m2_whole_session` (round 2's rule): by the re-upload test;
- `r3_l1_redact_msg_first` (round 2's code shape): by the split-secret tests;
- `r3_l1_never_collapse`, `r3_l1_always_collapse`: by the non-string-argument test and the uvicorn formatter test;
- `r3_l2_exception_only`: by the `BaseException` test.

| Survivor | Effect | Assessment |
|---|---|---|
| `start_no_reset_transient` | redundant with the commit-time reset | none (as in rounds 1 and 2) |
| `m3_no_fallback_save` | unobservable (round 2) | none |
| `r3_m2_no_outcome_filter` | equivalent: only delivered turns list files | none |
| **`r3_m2_target_ignores_file_id`**: the count uses the active upload even when `file_id` names another | [Certain] On a mutated copy, case E (dedupe report, upload budget, restart, `file_id` = report, "now … too") loses the sentence: 5 rows with 2 duplicates and round 1's M2 reply. Case J (a delivery on budget only, then `file_id` = report) gains it on report's first delivery. | test gap: **R3-L6** |
| **`r3_l1_unformattable_unredacted`**: a malformed log call's arguments aren't redacted at all | logging's error handler would print the raw string arguments | test gap: part of **R3-L2** |

## R3.7 Findings

There's no HIGH and no MEDIUM finding.

### LOW (deferred; not merge conditions; added to `docs/backlog.md`)

- **R3-L1. The R2-L1 fix decides whether a record holds a secret from the whole formatted line only, so a secret argument glued to a word character goes unmasked.**
  - **Where:** `backend/logging_filter.py:87-88` (`redacted = redact(original)`, `if redacted != original:`).
  - **Scenario** [Certain]: `log.warning("auth=x%s", "Bearer " + token)` and `log.warning("cfg:my%s", "api_key=" + token)`. The `Bearer` and `api_key` patterns start with `\b`, which a word character just before the placeholder defeats. So the whole line "has no secret", and the record is left alone. Head writes `auth=xBearer LEAK77` and `cfg:myapi_key=LEAK77`. `0276e7c`, which also redacted each string argument on its own, wrote `auth=x[REDACTED]` and `cfg:my[REDACTED]`. This narrowing was introduced by this round's fix.
  - **Reachability:** [Likely none today]. I searched for string literals that put a word character right before `%s` or `%r`. There are none in `backend/`, `buddy/`, `core/` or `skills/`, and none on a logging line in the installed uvicorn, starlette, fastapi, httpx, httpcore, h11, anyio, groq and anthropic packages. It's latent, in a defence-in-depth layer.
  - **Fix:**
    1. Treat a record as holding a secret when the formatted line **or any string argument on its own** changes under `redact`.
    2. Redact the arguments in place.
    3. Keep the shape only if the re-formatted line equals its own redaction; otherwise collapse to `redact(reformatted)` (or `redact(original)` if it can't be re-formatted).

    Test: the two calls above write no secret. Prototyped together with R3-L2 and R3-L3; see "Not run".
- **R3-L2. In a log record that can't be formatted, a secret inside a non-string argument reaches stderr.**
  - **Where:** `backend/logging_filter.py:82-85` and `_redacted_args` (`:103-108`). The `except` branch redacts only `str` arguments, and `logging.Handler.handleError` prints `Arguments: %s` of the tuple, which shows each argument's `repr`.
  - **Scenario** [Certain]: `log.warning("x %s %s", b"gsk_…")` and `log.warning("x %s %s", ValueError("gsk_…"))` (one argument short) print the raw key to stderr. It's the same on `0276e7c`, and round 1's code had the same branch, so it isn't a regression.
  - **Test gap:** the mutation `r3_l1_unformattable_unredacted`, which removes this branch's redaction altogether, survives. Even string arguments in a malformed call aren't covered by any test.
  - **Reachability:** [Likely none]. It needs a malformed log call, which is a bug in itself, with a secret inside a non-string argument.
  - **Fix:** in that branch, map each argument that isn't a `str` or a number to `redact(repr(arg))`. Test: those two calls, and one with a `str` argument, write no secret to stderr.
- **R3-L3. Any client can turn its access-log line into a "--- Logging error ---" block.**
  - **Where:** `backend/logging_filter.py:94-96` collapses a `uvicorn.access` record to `args = None`, and uvicorn's `AccessFormatter` unpacks `record.args`.
  - **Scenario** [Certain, the real server via `server_probe.py`]: `GET /healthz?api_key=`, `GET /healthz?x=Bearer` and `GET /api/v1/sessions/<id>?api_key=` each answer `200`. Each logs "TypeError: cannot unpack non-iterable NoneType object" and `Message: '127.0.0.1:… - "GET /api/v1/sessions/[REDACTED]?[REDACTED]" 200'` instead of its access line.
  - **Cause:** on the whole line, the patterns run past the path into ` HTTP/1.1`, because `api_key\s*[=:]\s*` eats the space. The path argument alone has nothing to mask, so the two redactions disagree and the record collapses.
  - **Impact:** no secret and no session id are printed. Log noise that any unauthenticated request can cause, and that request's access line is lost. It's the same on `0276e7c` (round 1's L1 fix introduced it), so it isn't a regression.
  - **Fix:** never collapse a `uvicorn.access` record. Its format string is fixed and holds no secret, and its only free text, the path, is already redacted as an argument. Test: format `GET /healthz?api_key= HTTP/1.1` through `AccessFormatter`: no "Logging error", and the line is written.
- **R3-L4. The steps of `_finalize` before the finishing block aren't guarded, so they can leave a turn `running` for good.**
  - **Where:** `backend/turns.py:329` (`uploads_unchanged`), `:340` (`except Exception` around `decide`, which lets a `BaseException` through), and `:349` (`make_read_only`).
  - **Scenario** [Certain, by fault injection]: if either helper raises `RuntimeError`, the turn stays `running`, the session stays busy, and the next turn gets `409` until a restart, which then reports `failed` / fallback. It's the same on `0276e7c`. This is R2-L2's failure through a different door.
  - **Reachability:** [Likely none]. Both helpers catch `OSError`, and uploads are capped at 5 MB, so a `MemoryError` while hashing is implausible.
  - **Fix:**
    - wrap `uploads_unchanged` and `decide` in one `try/except BaseException`;
    - wrap `make_read_only` in another;
    - both fall back to `Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)`, which delivers nothing (fail closed).

    Test: monkeypatch each helper to raise; the turn is `failed` / fallback, no files, the session not busy. Prototyped: `l2_base_exception` then finishes all 7 cases.
- **R3-L5. The per-upload count keys on the file id, so re-uploading the same original bytes loses the sentence.**
  - **Where:** `backend/turns.py:240-252`.
  - **Scenario** [Certain, all 3 speakers]:
    1. Dedupe `report.xlsx`; it's delivered.
    2. Upload the same file again (the same SHA-256, which `GET /sessions/{id}` shows).
    3. "now remove the empty rows too".

    The copy is delivered with **5 rows including the 2 duplicates**, so the dedupe isn't in it. The reply (focus): "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of report.xlsx is ready. None of the issues I check for were found." There's no from-original sentence. On `0276e7c` it had one. ("None of the issues…" with duplicates in the file is backlog L-4, pre-existing.)
  - **Why LOW, not MEDIUM:**
    - The user supplied the original bytes again themselves.
    - The reply is accurate about the file it names.
    - The designed flow keeps the upload in the session, and a follow-up needs no re-upload.

    [Possible] It becomes ordinary if the frontend has users attach a file with each message. The frontend PR should check this.
  - **Fix:** count earlier deliveries from any upload with the same bytes as this turn's upload:

    ```python
    target = record.files.get(target_id) if target_id is not None else None
    same_bytes = {
        f.file_id for f in record.files.values()
        if target is not None and f.sha256 == target.sha256
    }
    work.earlier_deliveries = sum(
        len(t.delivered_files)
        for t in record.turns
        if t.finished and t.outcome == DELIVERED and t.target_file_id in same_bytes
    )
    ```

    A re-uploaded Buddy copy has a different SHA-256 from the original, so R2-M1 stays fixed. Prototyped: case B gets the sentence in all 3 speakers, and cases A, C, I and J are unchanged. Test: case B through the API in all 3 speakers, keeping tests (a) to (d). Change the design note's "from the same upload" to "from the same upload, or an upload with the same bytes".
- **R3-L6. No test turns on a non-active upload by `file_id` after a delivery.**
  - The mutation `r3_m2_target_ignores_file_id` survives.
  - **Effect** [Certain, on a mutated copy]: case E loses the sentence (round 1's M2 reply returns), and case J gains it on a first delivery (R2-M1's class).
  - **Fix:** two API tests, in all 3 speakers:
    - (e) dedupe `report.xlsx`, upload `budget.xlsx`, then `file_id` = report, "now remove the empty rows too": the sentence is there;
    - (f) upload both, dedupe budget, then `file_id` = report, "remove duplicate rows": no sentence.
- **R3-L7. "Your original upload" is ambiguous when the upload is itself a re-upload of Buddy's copy.**
  - **Scenario:** case C's third turn: dedupe, re-upload the copy, remove empty rows, then "remove duplicate rows again".
  - The sentence is true in the API's terms: the copy is made from the re-upload and doesn't include the second turn's change.
  - But in a session with two uploads, "original" can read as `report.xlsx`, especially in Template, which names only the output.
  - **Fix** (optional, the owner's wording call): a catalog sentence that doesn't say "original", for example "I made this copy from your upload, not from a copy I handed back in an earlier turn."
- **R3-L8. The backlog's D3 status is stale.**
  - **Where:** `docs/backlog.md:132`.
  - It still says the sentence appears "whenever the session handed back a file before", and "(round-1 fixes, pending review)".
  - **Fix:** describe the per-upload rule and point to the design's "Decisions (2026-10-04)", item 2.

### Pre-existing, not counted against this PR

- **P2. ydata-profiling sends telemetry on every analysis.** [Certain]
  - `skills/excel/analyze.py:72` uses `ydata_profiling`, whose `analytics_features` (`ydata_profiling/utils/common.py`) calls `requests.get("https://packages.ydata.ai/ydata-profiling?…")` unless `YDATA_PROFILING_NO_ANALYTICS` is set.
  - The request carries the package and Python versions, the OS, whether a GPU is present, and the analyzed file's **row and column counts**. It's synchronous, inside the turn, with no timeout, and it runs `nvidia-smi` in a subprocess first.
  - This sandbox's proxy logged and refused hundreds of these connections during my probe runs. The same happens on `main` (PR-1's analyze skill).
  - Through the API, a public server sends the shape of every user's file to a third party, and a network that drops (rather than refuses) outbound traffic could hold a turn until its deadline.
  - **Fix:** set `os.environ.setdefault("YDATA_PROFILING_NO_ANALYTICS", "1")` before `ydata_profiling` is imported (in `skills/excel/analyze.py`, or in `backend/__main__.py` and the CLI), with a test that patches `requests.get` and asserts an analysis never calls it. Added to the backlog.

## R3.8 What the next round needs

Nothing blocks the merge. The next step, by `docs/process/review-loop.md`, is the live runs with Groq on this head.

Recommended, not merge conditions:
1. R3-L5's per-content count, because it is cheap and restores the one true sentence round 2 had.
2. R3-L1 to R3-L4 together, with their tests. They're all latent or noise, but R3-L1 is a narrowing the R2-L1 fix introduced.
3. The live runs should include a two-turn session ("remove duplicate rows", then "now remove the empty rows too") and a re-upload of the delivered copy, to check how the real model classifies them with the conversation in the prompt.

## Deferred (round 3)

R3-L1 to R3-L8 and P2 above, added to `docs/backlog.md`. R2-L4, and round 1's L9, L10, L12 and P1, stay deferred.

## Not run (round 3)

- **Live runs with Groq.** By the review loop these come after a MERGE verdict, on the final head. So the real model's classification of follow-ups, and of a re-uploaded file name, is still untested.
- **`api_probe.py` and `adversarial_probe.py` on `0276e7c`.** I ran them on head only. Round 2 recorded their results on `0276e7c`, and the diff can't change a single-turn reply; my multi-turn comparison with `0276e7c` is `m2_targets` (all 10 cases, 3 speakers).
- **Python 3.13.** Round 1 ran the suite there; I ran it on 3.11.15 only.
- **The demo host, Windows, and a non-root user** (the `0o444` permissions), as in earlier rounds.
- **The full suite on my prototype.** In a throwaway copy I prototyped the fixes for R3-L1 to R3-L5 and ran `tests/backend`, `tests/core/test_engine.py` and `tests/buddy/test_nothing_ran_and_from_original.py` (391 passed), `l1_logging`, `m2_targets` (A, B, C, I, J), `l2_base_exception` and `server_probe.py` on it. The prototype isn't committed. Under the review-loop rules, I mustn't review those fixes.

---

# Review round 4 (commit c136b93)

**Verdict: MERGE.** There's no HIGH and no MEDIUM finding. The merge of `origin/main` (`510ed06`) keeps every round 1–3 fix working next to main's PR-B reply ledger and values-only copy, and the 2 MiB default cap (`c136b93`) works as the design's new item 3 says. Four LOW findings are deferred.

**Reviewer:** a fresh reviewer agent, with no part in writing the code, the fixes, or the earlier reviews. **Base:** `origin/main` (`1462ce4`; `git fetch` shows it hasn't moved). **Design:** `docs/design/backend-api-design.md`. **Scope:** `7be9949..c136b93`. The branch's own code change is `backend/settings.py` (the cap) and three tests; the rest is the merge of main (`buddy/`, `skills/`, tests, docs), the probes moved into `scripts/review_harness/feat-backend-api-core/`, the live-run driver, and docs. `git diff 1462ce4 HEAD -- buddy core skills` holds only this branch's own additions (`state.py`, `voice.py`, `voice_catalog.py`, `core/engine.py`); every file directly in `scripts/review_harness/` equals main's. I treated `backend-api-core-fixes3.md` as unverified.

Probes, their README and every output are in `scripts/review_harness/feat-backend-api-core/` (round-4 outputs in `round4_out/`).

## R4.1 What I ran

| Check | Result |
|---|---|
| `python -m pytest -q`, Python 3.11.15, head | **2142 passed**, 11 warnings, 204 s, exit 0 (matches the fixes file) |
| `ruff check buddy tests` | 22 errors on head and 22 on `origin/main`; identical by file and rule. **No new lint errors.** `ruff check backend core skills scripts/live_api_runs.py scripts/review_harness/feat-backend-api-core tests/backend`: clean. |
| `python -m backend.export_openapi --check` | in sync |
| Design | Item 3: [Certain] `Settings.from_env` gives 2,097,152; `limits.max_upload_bytes` says the same; exactly 2 MiB is `201`, one byte more is `413` "That file is larger than 2 MB."; `WORKBUDDY_MAX_UPLOAD_BYTES=5 MiB` accepts 5 MiB and refuses one byte more with "…5 MB." (`round4_probe.py --only cap_default`). Item 2: the from-original sentence still follows the outcome block, which now ends with main's values-only sentences. No other deviation; see R4-L1 for run 9's fixture. |
| New tests on old code | The 3 changed tests (`test_the_413_message_is_built_from_the_cap`, `test_settings_defaults_match_the_design`, `test_the_413_message_names_the_configured_cap`) all fail on `7be9949`. |
| Pipeline vs base (`pipeline_compare.py --diff`, head vs `origin/main`, 7 scenarios × 3 speakers) | Byte-identical except the three known differences: `NOTHING_RAN_REPLY` for `no_file` and the "thanks" turn, and `failure` set when Voice raises. `round4_out/pipeline_compare_head_vs_main.txt` |
| API vs pre-merge (`api_probe.py`, 13 scenarios × 3 speakers, head and `7be9949`) | Same outcomes, same `reply_source`, same delivered files by my openpyxl read. Replies are identical except main's values-only sentence and the seeded opener or closer. On head: the reply equals the pipeline's (or the fallback when Voice raised), `delivered` ⇔ files listed, every download matches its SHA-256. `round4_out/api_probe_head_vs_7be9949.txt` |
| `adversarial_probe.py`, `round2_probe.py`, `round3_probe.py`, `server_probe.py`, `l1_placeholder_probe.py` on head | The same results round 3 recorded, plus the values-only sentence. M1, M2, R2-M1, M3, L1, L8, L11, R2-L1 and R2-L2 still hold. Open items behave as before: R3-L3's 3 "Logging error" blocks, R3-L4, R3-L5. No session id in the server log. |
| `round4_probe.py` (new, 3 speakers) | Later turns whose Brain reply carries PR-B's `requested_actions`; an original with formulas; non-plain tables; a second sheet hidden from the upload check. See R4.2. |
| `timing_probe.py` (new) | A 2,055,233-byte fixture (24,497 rows × 14 columns), fake LLM. Head: one step 121 s and 125 s, chain 160 s and 152 s. `7be9949`: 109 s and 128 s. All delivered; replies match my read. See R4-L2. |
| Round 3's P2 (ydata telemetry) | **Fixed by main** [Certain]. Through the API with the variable unset and `requests.get` patched: 0 calls (`analytics_via_api`). Main's `analytics_probe.py` on `7be9949`: 1 call to `packages.ydata.ai`. |
| Mutations (`mutations.py`, 61) | **51 killed, 6 survived, 4 not applied** (round 2's patterns that round 3 replaced). Round 3's 5 survivors are unchanged. New: `r4_cap_back_to_5mib`, `r4_from_original_not_appended`, `r4_nothing_ran_removed` and `r4_is_verified_delivery_always_false` are killed; `r4_from_original_before_outcome` survives (R4-L3). `round4_out/mutations_head.txt` |
| Rule 2 | The original's SHA-256 was unchanged before and after in 417 turn checks across all the probes. The 4 exceptions are the integrity probes, which change the upload on purpose; the API caught each one and handed back nothing. |

## R4.2 Adversarial cases aimed at the merge (`round4_probe.py`)

These ran in all 3 speakers. In the first five rows, turn 1 is "remove duplicate rows", delivered with 4 rows, and the case is turn 2; the other rows say their own turns. Each handed-back file was read with openpyxl. "Made from" is my own model of the skills (see round 3). Replies are quoted from focus mode; friend adds an opener, and template says "Saved as `…`".

| Case | Handed back (my read) | Reply | Rule 1 |
|---|---|---|---|
| The model re-lists turn 1's action on "now remove the empty rows too" (that phrase isn't in the message) | 5 rows, 2 duplicates, 0 empty; made from the upload | "Partly done — removed 1 fully-empty row(s). I didn't run the duplicate-row removal, though I read it as part of your request. The copy has 5 row(s), 0 fully-empty row(s) remaining. It still has 2 duplicate row(s). Your cleaned copy of report.xlsx is ready. The copy has the values only; … I made this copy from your original upload, not from a file I handed back earlier." | True. It also tells a "now … too" user that the duplicates are back. |
| "now the empty rows too, the duplicates are already gone" (mention detector) | the same | "Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: remove empty rows. … It still has 2 duplicate row(s). … I made this copy from your original upload…" | True |
| "remove empty rows and sort by amount" | 5 rows | "Partly done — removed 1 fully-empty row(s). I can't do this part yet: “sort by amount”. … from-original sentence" | True |
| The model plans turn 1's dedupe again as a chain step | 3 rows, 0 duplicates, 0 empty; steps read the upload | "Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). … the steps ran in this order: deduplicate, then remove empty rows. … I made this copy from your original upload…" | True |
| "sort it by amount", "get rid of the blank lines please" read as `unclear`, "thanks!" | nothing | "I didn't run anything on a file this turn, so no file was changed." | True; it doesn't say sorting is unsupported (the R2-L4 family) |
| An original with 4 formulas, results saved / unsaved: dedupe, then "now remove the empty rows too", then the values-only copy re-uploaded and "now remove the empty rows" | T1: 4 rows. T2: 4 rows, 1 duplicate, 0 empty. T3: 3 rows. My count: 4 formulas, 0 or 4 unsaved; the copy has 0. | T1: "Your file has 4 formula cell(s). The copy has their last saved values, not the formulas." (unsaved: "4 of them had no saved value, …"). T2: the same, plus the from-original sentence. T3: no formula sentence and no from-original sentence. | True |
| Merged cells; header on row 3; a declared second sheet stored at `xl/other/sheet2.xml` | Upload `201` for all three (the hidden sheet passes the upload check, as design 10-01 item 6 admits). "hi": nothing. Dedupe: nothing. | "hi": `NOTHING_RAN_REPLY`. Dedupe: "I ran into a problem partway through. The deduplicate step: the sheet has merged cells, …" (or "a column in the sheet has no header", or "the workbook has 2 sheets, …") "… no file was handed back; your original file is unchanged." | True; no text from the file reaches the reply |
| 2 MiB dedupe and chain (`timing_probe.py`) | My model: 1,225 duplicates (2 of them repeated blank rows), 3 empty rows | "removed 1225 duplicate row(s), including 2 repeated blank rows. The copy has 23272 row(s)…"; chain "…23271 row(s), 0 duplicate row(s) and 0 fully-empty row(s)" | True |

No reply in any probe claims something that didn't happen. No exception text, key-like text or session id reached a reply or `detail`. The hostile-header case (P1) is unchanged and pre-existing.

## R4.3 Findings

There's no HIGH and no MEDIUM finding.

### LOW (deferred; not merge conditions; added to `docs/backlog.md`)

- **R4-L1. The live-run driver's run 9 can't run at the new default cap.**
  - **Where:** `scripts/live_api_runs.py:48` (`MAX_UPLOAD = 5 * 1024 * 1024`) and `five_mb_fixture()` build a 4.75–5 MiB fixture. `Server` (`:136-139`) starts `python -m backend` without `WORKBUDDY_MAX_UPLOAD_BYTES`, and `Driver.upload` calls `raise_for_status()`.
  - **Effect:** [Certain] at the default, any upload over 2 MiB is `413` (`cap_default`). So run 9 stops with an HTTP error, and the "2 MB live run" that the backlog's live-run status says is pending can't be produced as written. Design 7.5 and item 3 don't restate run 9's fixture size either.
  - **Reachability:** the next live-run session, which the review loop needs on this head.
  - **Fix:** build the fixture under the server's own `limits.max_upload_bytes`, read from the session response, and label it by its size. Keep an option that sets `WORKBUDDY_MAX_UPLOAD_BYTES`, so 5 MB can still be measured.
- **R4-L2. At 2 MiB, a two-step chain uses 84–89% of the 180 s deadline after the merge.**
  - **Evidence** [Certain, this sandbox, fake LLM, so no model latency]: the chain took 160 s and 152 s on head and 128 s on `7be9949`; one step took 121 s and 125 s on head and 109 s before. So the design's own threshold, two-thirds of the deadline (120 s), is passed at 2 MiB too.
  - **Cause:** [Likely, not profiled] main's added full reads of the workbook: the plain-table check in the planner, the formula count, and the residual measurement.
  - **Effect:** with Groq's latency (three calls for a chain) or a slower host, chains near the cap will reach the deadline. They then end as honest `failed` turns with the fallback reply: no false claim, nothing handed back. The backlog already notes that a timed-out turn keeps its CPU and slowed the next one.
  - **Fix (the owner's call):** measure run 9 at 2 MB on the final head (after R4-L1). If the chain is over 120 s, choose between a 1 MiB default, a longer deadline, or fewer reads per turn.
- **R4-L3. No test pins where the from-original sentence goes.**
  - **Where:** `buddy/voice.py:428-430`.
  - **Effect:** the mutation `r4_from_original_before_outcome`, which puts the sentence before the outcome block, survives. That block's lead word ("Done —", "Partly done —") would no longer start the reply, against design item 2 ("after the outcome block") and main's D3 order.
  - **Fix:** in the existing follow-up test, assert that the sentence comes after the outcome's file sentence (`reply.index(FROM_ORIGINAL_SENTENCE) > reply.index("Saved as")`, or "is ready." for Friend and Focus).
- **R4-L4. Backlog statuses are stale after the merge.**
  - **Where:** `docs/backlog.md`, section 4's status says the review "ended in MERGE at round 3"; the live-runs entry says the cap is "pending review round 4"; the P2 entry is still open, though main fixed it (R4.1).
  - **Fix:** update these lines when this round is committed. I added a reviewer status line to P2.

### Still open from earlier rounds (unchanged; `backend/` is unchanged since `7be9949` apart from the cap)

R3-L1 to R3-L8, R2-L4, and round 1's L9, L10, L12 and P1 stay deferred. My probes reproduce R3-L3 (the server log), R3-L4 (`uploads_unchanged` or `make_read_only` raising), R3-L5 (case B) and R3-L6 (the mutation survives) unchanged.

## R4.4 What the next step needs

Nothing blocks the merge. By `docs/process/review-loop.md` step 5, the live runs must be redone on this head: `backend-api-core-live-runs.md` was made at `7be9949`, before main's reply changes. They should include:
- run 9 at 2 MB (R4-L1);
- a two-turn session ("remove duplicate rows", then "now remove the empty rows too"), to see whether Groq, with the conversation in its prompt, re-lists turn 1's action in `requested_actions` (the first case in R4.2).

## Deferred (round 4)

R4-L1 to R4-L4, added to `docs/backlog.md`. Everything listed under "Still open" above stays deferred. P2 is closed by main.

## Not run (round 4)

- **Live runs with Groq on the merged head.** They're the writer's step after MERGE. So the real model's `requested_actions` on follow-up turns is untested.
- **Mutations of main's own new code** (the PR-B ledger, the values-only copy). It was reviewed in main's PRs; my mutations cover this branch's guards and its additions inside the merged `buddy/voice.py`.
- **Timing on the demo host, or with real model latency.** I timed this sandbox only, twice on head and once on `7be9949`.
- **Python 3.13, Windows, and a non-root user,** as in earlier rounds.
- **Prototypes of fixes.** I prototyped none this round.
