# Live runs: `feat/backend-api-confirmation` (PR-3), through the real HTTP server

**When:** 2026-10-05, after review round 2's MERGE verdict (`docs/reviews/backend-api-confirmation-review.md`).
**Code:** the reviewed code at `b1059d3`. The runs were made at `4eefe88`, which adds only the round-2 review, its probe scripts and the backlog; `git diff b1059d3 4eefe88 -- backend buddy core skills tests scripts/live_api_runs.py` is empty.
**How** (design Section 7.5): `python scripts/live_api_runs.py --runs 3,4,T,C` starts `python -m backend` (uvicorn, 127.0.0.1, one worker) in its default configuration, with a fresh data root under `scratch/live-api/20261005T184039/`, and drives it over HTTP only.
- **Provider:** `GroqProvider` (`openai/gpt-oss-120b`), with the key attached by the cloud proxy. The key was never read or printed.
- **Python:** 3.11.15.
- **Confirmation:** the real flow. `WORKBUDDY_AUTO_APPROVE` isn't set (the driver removes it from the server's environment). Each turn with a file-changing step shows its plan and waits; the driver answers it with `POST …/confirmation` with the run's decision, or, in run T, doesn't answer. The confirmation timeout is the default, 120 s.
- **Pause:** 8 s between turns, to spare the rate limit.

**Rate limit:** no HTTP 429. The server made 10 calls to `api.groq.com`, all `200 OK`, and the server log has no line with `429`. All four planned runs completed.

**Independent check:** for every delivered file, the driver downloads it through the API and checks three things:
- the download's SHA-256 against the turn's record;
- the file read with pandas, against the original read with pandas (`dropna(how="all").drop_duplicates()` for both chains);
- every number the reply states, against those reads.

For every turn, it also lists the spreadsheets on disk in the turn's own folder, so a turn that ran no step can be seen to have written nothing. The original's SHA-256 is compared three ways: the local fixture, the hash recorded at upload, and the server's stored copy after the turn. The fixture is `manual_report.xlsx`: 6 rows, 2 exact duplicates and 1 fully empty row.

## Summary

| Run | What | Plan shown (once) | Answer | Outcome | Delivered file, my read | False claim? | Original |
|---|---|---|---|---|---|---|---|
| 3 | "Remove the empty rows and then the duplicates" | 2 steps: "Remove empty rows from manual_report.xlsx." / "Then remove duplicate rows from the result of step 1.", with `PLAN_NOTE_CHAIN` | approve (`200`) | delivered | 3 rows, 0 duplicates, 0 empty; equals the expected frame | none | unchanged |
| 4 | "Remove duplicate rows" | 1 step: "Remove duplicate rows from manual_report.xlsx.", no note | reject (`200`) | not_delivered | nothing written in the turn folder | none | unchanged |
| T | "remove duplicate rows", never answered | 1 step, as in run 4 | none: timed out after 120 s | not_delivered; `decision_source: "timeout"` | nothing written; a late approve got `409 no_pending_confirmation` | none | unchanged |
| C | "remove duplicate rows, then remove empty rows" | 2 steps: "Remove duplicate rows from manual_report.xlsx." / "Then remove empty rows from the result of step 1.", with `PLAN_NOTE_CHAIN` | approve (`200`) | delivered | 3 rows, 0 duplicates, 0 empty; equals the expected frame | none | unchanged |

In every run, the statuses polled were `running`, `awaiting_confirmation`, then `finished`. Each plan was shown exactly once, and listed the file-changing steps that then ran, in the order they ran. After each answer, the response was the turn `running` with `pending_confirmation: null`. In both chains, step 2 read step 1's output, as the plan's "the result of step 1" says.

Server log: 0 "Logging error" blocks, 0 tracebacks, 0 of the 4 session ids, 0 `429`s. Each plan's life is logged with the session's key, never its id: "waiting for its plan's answer (2 step(s))", "plan approve (user)", "finished: delivered (pipeline)"; for run T, "plan reject (timeout)" 120 s after the plan was shown.

## Run 3: "Remove the empty rows and then the duplicates" (focus), approved

- **Intent:** `multi_step_request`. **`clarification_required`:** false.
- **Plan shown** (6.2 s after the turn started, once): "Remove empty rows from manual_report.xlsx." then "Then remove duplicate rows from the result of step 1."; `plan_note`: "The steps run in this order, each on the previous step's result. Only the final file is handed back." Approved; the answer's response was `running`, with no plan.
- **Steps:**
  - analyze `manual_report.xlsx` [success, verified];
  - remove_empty_rows `manual_report.xlsx` → `manual_report_no_empty_rows_<key>-task-1.xlsx` [success, verified];
  - deduplicate that file → `…_deduplicated_<key>-task-2.xlsx` [success, verified];
  - analyze (re-check) of the final file [success, verified].
- **Delivered:** yes, the final file only (the intermediate stays in `t0001/`, unlisted). The download's SHA-256 matches the record.
- **Independent check:** the original has 6 rows, 2 duplicates and 1 empty row. The copy has 3 rows, 0 duplicates and 0 empty rows, and equals `original.dropna(how="all").drop_duplicates()`.
- **Reply:** "Done — removed 1 fully-empty row(s). Removed 2 duplicate row(s). The copy has 3 row(s), 0 fully-empty row(s) and 0 duplicate row(s) remaining. I couldn't tell for sure from your message what order you wanted, so the steps ran in this order: remove empty rows, then deduplicate. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. None of the issues I check for were found."
- **False claim:** none. 1 empty row removed, then 2 duplicates (of the 5 rows left), 3 rows left, with 0 duplicates and 0 empty rows: all match my read. The order sentence is true: the planner kept the model's order because the model's quote for the second step, "then the duplicates", contained order wording (server log). That order is the one the user stated, and the one the plan showed before it was approved. The sentence is the pipeline's existing behaviour, not PR-3's.
- **Original SHA-256:** `0684e91d…5239` → `0684e91d…5239` (unchanged).
- **Wall time:** 6.7 s.

## Run 4: "Remove duplicate rows" (focus), rejected

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** false.
- **Plan shown** (3.0 s, once): "Remove duplicate rows from manual_report.xlsx."; `plan_note`: null. Rejected; the answer's response was `running`, with no plan.
- **Steps:** analyze `manual_report.xlsx` [success, verified]; deduplicate [declined]. `stopped_at`: `spreadsheet.deduplicate`. `detail.confirmation`: reject by the user.
- **Delivered:** no. The turn folder holds no spreadsheet: no file-changing step ran.
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? The deduplicate step: wasn't confirmed, so it didn't run. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- **False claim:** none. Nothing was done to the file, and the reply says so. 1 of 6 is 16.7%. "Your original file is unchanged" matches the three hashes.
- **Original SHA-256:** `0684e91d…5239` → `0684e91d…5239` (unchanged).
- **Wall time:** 3.5 s.

## Run T: "remove duplicate rows" (friend), never answered

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** false.
- **Plan shown** (3.5 s, once): "Remove duplicate rows from manual_report.xlsx."; `plan_note`: null; `expires_at` 120 s later. The driver sent no answer.
- **Timeout:** the server logged "plan reject (timeout)" 120.0 s after the plan was published, and the driver saw the turn finished 123.6 s after starting it. `detail.confirmation`: reject, `decision_source: "timeout"`. (This run doesn't test the deadline's pause: 123.6 s is under the 180 s deadline either way. The pause is covered by the tests and review round 1's probes.)
- **Steps:** analyze [success, verified]; deduplicate [declined]. `stopped_at`: `spreadsheet.deduplicate`.
- **Delivered:** no. The turn folder holds no spreadsheet.
- **Late answer:** after the turn finished, the driver posted "approve" with the plan's id: `409 no_pending_confirmation`. Nothing changed.
- **Reply:** the same as run 4's.
- **False claim:** none. "Wasn't confirmed, so it didn't run" is true of a plan nobody answered.
- **Original SHA-256:** `0684e91d…5239` → `0684e91d…5239` (unchanged).

## Run C: "remove duplicate rows, then remove empty rows" (focus), approved once

- **Intent:** `multi_step_request`. **`clarification_required`:** false.
- **Plan shown** (5.1 s, once): "Remove duplicate rows from manual_report.xlsx." then "Then remove empty rows from the result of step 1.", with `PLAN_NOTE_CHAIN`. One approval; no second question.
- **Steps:**
  - analyze `manual_report.xlsx` [success, verified];
  - deduplicate `manual_report.xlsx` → `manual_report_deduplicated_<key>-task-1.xlsx` [success, verified];
  - remove_empty_rows that file → `…_no_empty_rows_<key>-task-2.xlsx` [success, verified];
  - analyze (re-check) of the final file [success, verified].
- **Delivered:** yes, the final file only. The download's SHA-256 matches the record.
- **Independent check:** 3 rows, 0 duplicates, 0 empty. It equals the expected frame.
- **Reply:** "Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. None of the issues I check for were found. Anything else?"
- **False claim:** none. 2 duplicates removed from 6 rows, then the 1 empty row, leaving 3 rows with 0 duplicates and 0 empty rows: all match my read. The steps ran in the stated order (no order sentence was needed).
- **Original SHA-256:** `0684e91d…5239` → `0684e91d…5239` (unchanged).
- **Wall time:** 5.6 s.

## Not run

- **The design's other runs** (1, 2, 5 to 9, R): not asked for this PR. They ran at PR-2 (`docs/reviews/backend-api-core-live-runs.md`).
- **A live chain that can't finish** (review round 1's M1 case, "remove duplicates and sort by amount"): not in this set. Review round 2 ran it live with Groq (`scripts/review_harness/feat-backend-api-confirmation/out/r2/live_*.json`): no plan shown, deduplicate skipped, nothing written.
- **Shutdown while a plan waits, through a real uvicorn process and signal:** not run live. It's tested through the app's lifespan (`TestClient`), and the server's clean shutdown was checked with no turn running.
- **Python 3.13, Windows, the demo host:** not available here.
