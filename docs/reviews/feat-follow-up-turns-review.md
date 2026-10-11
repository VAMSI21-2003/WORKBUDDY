# feat/follow-up-turns: independent review, round 1 (head `8f70640`, base `origin/main` `f01de0a`)

**Verdict: DON'T MERGE.** No HIGH. Two MEDIUMs that an ordinary request reaches (F1, F2); both are small fixes.

Probes: `scripts/review_harness/feat-follow-up-turns/` (README there; raw output in its git-ignored `out/`). Everything ran the real app and `build_engine` (real skills, Inspector, Voice) with only the LLM faked.

## What I ran

| Check | Result |
|---|---|
| `python -m pytest -q` (head) | 3143 passed, 6 skipped, 0 failed (585 s) |
| `ruff check buddy tests`, head vs base, by file and rule | Identical: same 17 file/rule groups, same counts. Nothing new |
| `harness.py` 532 turns, head vs base (`cmp_harness.py`) | 504 delivered replies differ only by the added sentence "I worked on your original upload."; 28 identical; no other change; original unchanged in all |
| `probe_a` 8 multi-turn API scenarios x template/friend/focus (24 sessions) | Chain on a copy, start over, nothing left, report on a copy, 2nd upload, `file_id`, greeting/garbage between: every file read independently with openpyxl; every count in every reply matched (e.g. 6 rows -> 4 -> 3, "removed 2", "removed 1"); voice model called 0 times on delivered and nothing-left turns |
| `probe_b` 13 adversarial scenarios x 3 speakers | Copy tampered or deleted -> upload used, reply says so; bad `file_to_use` values -> default `latest`; restart -> builds on copy; other session unaffected; findings F1-F3 below |
| `probe_c` stage raises before/after (6 stages) x 2 speakers; turn's copy touched mid-turn | Failure replies identical to base; failed turn records nothing; touched copy -> `integrity_failure`, nothing handed back, next turn fine |
| `probe_d` hostile file name, formulas, 90-char name, all-empty, hostile cell text, dotted names | No false claim. Names with claim-like text already reached replies on base ("Saved as ...", "Your cleaned copy of ...") |
| `probe_e` mixed chains on a copy | "there were no duplicate rows to remove. Removed 1 fully-empty row." etc.: correct; chain with an unsupported step still does not start |
| `probe_g` rule 2 | Original and every earlier delivered file byte-identical after 4 turns (SHA-256 of download vs listing); original identical before/after in every run of every probe |
| 34 mutations of the new code, run in a throwaway worktree | 29 killed, 5 survived (see T1-T3) |

Deviations from `docs/design/follow-up-turns.md`: none in behaviour. Three claims need the qualifications under F1, F2 and F4 (sections "How a turn picks its file" and "The reply").

## Findings

### F1 [MEDIUM, Certain; reachable by an ordinary request if Brain reads it as the design intends] "Use my original file" alone is answered "I didn't read that as something I can do", but the session did switch
- Place: `backend/sessions.py` `latest_copy` (start-over dropped even when the turn delivers nothing), `backend/turns.py` `_file_used_record`; reply from `buddy/voice.py`.
- Scenario (`probe_a` s3, `probe_b` b11; template, friend, focus): turn 1 "remove the duplicate rows" delivers; turn 2 "use my original file", Brain `intent: unclear, file_to_use: original` (the prompt's own example, and no action fits it). Reply: "I didn't read that as something I can do. I can remove duplicate rows, ... No changes were made to any file this turn." With intent `conversation` the reply is the greeting ("Hi. I can remove ..."). Meanwhile the session now offers the upload: `next_request_file` = `upload:report.xlsx`, and turn 3 builds on the upload, not the copy.
- Same silent state change on turns that did nothing at all: a plan the person **declined** while Brain read `original` (`probe_b` b5: reply says nothing ran and "your original file is unchanged", next file silently becomes the upload); a report-only request "give me a report on my original" (`probe_e` m5); a plain question that Brain mislabels `original` (b11).
- Consequence: a false "can't do" about something that was done, and the build-on is lost with no word in the reply (the panel line changes, the reply does not).
- Fix (simplest, cannot make the false claim): persist `start_over` only for a turn that **delivered** a file. In `latest_copy` honour `file_used.start_over` only when `turn.outcome == "delivered"`; in `_file_used_record`/`Finalization` store `start_over=False` for `not_delivered`. Then "use my original file" alone is an honest no-op and its existing reply is true, and "start over and remove duplicates" still works. (If the owner wants "use my original file" alone to work, the reply needs a fixed sentence "Your next request will start from your original upload." added by code when `file_used.start_over` and the turn is recorded; that also has to cover the declined and report cases. That is more machinery.) Add a test per speaker: delivered turn, then `unclear`+`original`, then a request: it must build on the copy.

### F2 [MEDIUM, Certain; reached by "start over and ..." followed by any follow-up] Friend and focus name a different file than the one worked on when two deliveries share a name
- Place: `buddy/voice.py` `_delivered_file_name` (takes `Path(step.input_reference).name`, the turn's private copy, named by `download_name`, which never has the " (2)"), against `FileUsed.copy_name` (the Files-panel name, which has it).
- Scenario (`probe_b` b14, friend/focus): turn 1 dedupe -> `report_deduplicated.xlsx`; turn 2 "start over, dedupe" -> `report_deduplicated (2).xlsx`; turn 3 "now remove the empty rows". Reply: "Your cleaned copy of **report_deduplicated.xlsx** is ready. ... I worked on `**report_deduplicated (2).xlsx**`, the latest copy I handed back". The first is a different file (the first delivery) that the turn did not read. Template is not affected (it names the generated output).
- Fix: in `_delivered_file_name`, when `request.file_used` is a copy with a `copy_name`, return `_name_in_reply(copy_name)`. Test: the b14 sequence in friend and focus; both names in the reply must be equal.

### F3 [LOW, Certain] Missing tests for three guards that matter (mutations that survive)
- T1 `backend/turns.py` `_offer`: the `expected={delivered file, turn's copy}` integrity entries. Remove them and nothing fails, so a skill that rewrites the delivered file or the turn's copy is not caught. The head does catch it (`probe_c` c-touch-input: `integrity_failure`, nothing handed back). Rule 2: add a test that touches the turn's copy and one that touches the earlier delivered file mid-turn.
- T2 `backend/turns.py` `_restore_upload_focus`, the `analyzed_files` filter. Remove it and the whole session is "not found" after the next restart (`probe_h`: `after restart: None []`) while 214 follow-up tests pass. Add: follow-up on a copy, restart, session opens and offers the copy.
- T3 `backend/turns.py` `_finalize`, the `result.outcome in (DELIVERED, NOT_DELIVERED)` guard on `file_used`. Remove it and a turn that **failed** after Brain read `original` drops the copy (`probe_h`: next file becomes `report.xlsx`). Add: failed turn with Brain `original`, then the next turn still builds on the copy.
- Also survived: `already_satisfied` without `_step_passed` and without `stopped_at`. I could not build a case where they matter (an earlier `verification_result.passed` check covers both), so I treat them as redundant guards [Likely].

### F4 [LOW, Certain] Smaller points
- Page line and `next_request_file` still name the delivered copy when it has become unreadable; the next reply then says the upload was used (true). A promise that is wrong, a reply that is right.
- Every turn on a copy re-analyses it (a new path each turn), so a leftover finding is repeated with "Want me to tackle any of these?" on every follow-up and on every unclear/failed-Brain turn after a delivery (base analysed once per upload). Findings are true for that file.
- `integrity_failure` caused by the delivered file or the turn's copy still says "your original file no longer matches the one you uploaded".
- Template speaker shows the generated name ("Saved as `report_deduplicated_<key>-task-1_no_empty_rows_<key>-task-2.xlsx`"); on base too.
- Going back to an older release makes every session saved by this version unreadable: `file_used` is written (as `null`) on every finished turn. Documented in the design; omitting the key when `null` would shrink it.

## For the owner (decisions, not mine to change)
- Nothing-left applies only when the turn worked on a copy; a first request on an upload with nothing to remove still delivers a copy, then the identical second request is "nothing to remove". Works as the writer flagged.
- API clients that send `file_id` on every turn never chain (pinned); each of their deliveries is made from the upload.
- Live runs with the real model are still required before merge (`docs/process/review-loop.md`): Brain's real reading of `file_to_use` (F1 depends on it) is untested here.

## Deferred (also in `docs/backlog.md`)
F3 (T1-T3), F4.

## Not run
- No live run with the real Groq model (fake LLM only); real Brain's `file_to_use` behaviour is unknown.
- No browser check of the page; the Files-panel line was checked only through the writer's tests and the API's `next_request_file`.
- The base's full test suite and mutations of the data-v3 fixture and `make_data_fixture.py 3` were not run; `tests/deploy/test_saved_data_survives.py` passes in the full suite.
- Multi-user (Google sign-in) ownership was not exercised beyond two sessions in one app.
