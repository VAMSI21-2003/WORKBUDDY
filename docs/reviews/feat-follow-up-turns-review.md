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

---

# Review round 2 (commit 5be03f1)

**Verdict: MERGE** on the code, as far as a fake-LLM review can say. No HIGH, no MEDIUM, F1 and F2 are fixed. Still required before merge (`docs/process/review-loop.md`): live runs with the real Groq model. F1's fix removes the dependency on how real Brain reads `file_to_use`, so the risk is lower than in round 1, but the reading itself is still untested.

Scope: diff `8f70640..5be03f1` (`backend/sessions.py`, `backend/store.py`, `backend/turns.py`, `buddy/voice.py`, docs, tests), every round-1 finding, regression against base `origin/main` (`f01de0a`). Probes: `scripts/review_harness/feat-follow-up-turns/` (README there; raw output in the git-ignored `out/`). Real app and `build_engine`, only the LLM faked.

## What I ran

| Check | Result |
|---|---|
| `python -m pytest -q` (head) | 3162 passed, 6 skipped, 0 failed (604 s) |
| `ruff check buddy tests`, head vs base, by file and rule | Identical (same 13 file/rule groups, same counts). Nothing new |
| Round-1 probes a-e re-run (24 + 42 + 28 + 21 + 15 sessions; 535 turns, with probe i) | Every row count in a reply ("The copy has N rows") matches my own openpyxl read (338 checks, 0 mismatches). Original upload SHA-256 unchanged after every turn of every run. Voice model called 0 times on any delivered turn. No claim of work on a turn that delivered nothing |
| Round-1 vs round-2 replies, template and focus (friend differs only by random openers) | Only the intended changes: `next_request_file` after a no-delivery turn (s3, b5, b11, m5) and the follow-up turn that now builds on the copy; b14 now names `report_deduplicated (2).xlsx` in both places |
| `probe_i` (new): 8 scenarios x template/friend/focus, 24 sessions | See F1/F2 below. All counts and names right; no false claim |
| `probe_j` (new): rollback claim, written by head, opened by the base's `SessionStore` | Session whose only turn failed: readable by base (record has no `file_used`). Sessions with an unclear or delivered turn: base reads them as not found (documented). Head reads all three |
| `harness.py` 532 turns, head vs base (`cmp_harness.py`) | Same as round 1: 504 delivered replies differ only by "I worked on your original upload."; 28 identical |
| Round-2 tests run against the round-1 code (`8f70640`) | 14 of the new tests fail there (F1: 12, F2: 2); they pass on head |
| Mutations of the head, throwaway worktree (38) | 31 killed. 7 survived, all judged inert or already known; see T-list below |

## Round-1 findings

- **F1 Fixed** [Certain]. Turn 1 "remove the duplicate rows" (delivers), turn 2 "use my original file" (Brain `unclear` + `original`, or `conversation` + `original`): reply is "I didn't read that as something I can do ... No changes were made to any file this turn." (true), `next_request_file` still names `report_deduplicated.xlsx`, and turn 3 "remove the empty rows" builds on the copy and says so ("I worked on `report_deduplicated.xlsx`, the latest copy I handed back, not on your original upload", 3 rows, matches the file). Same for a declined plan read as `original` (b5), a report-only request on "my original" (m5), a failed start-over turn (i2: hands raises, then the next turn builds on the first copy). "Start over and ..." that delivers still works (i1, i4, i5, i6, i8), also after a restart (i1: the turn after the restart builds on the start-over file, 3 rows). A second upload's start-over does not touch the first upload's latest copy (i7). Hand-edited records in round-1's format (`file_used: null`; `start_over: true` on a not_delivered turn) open and change nothing (i3).
- **F2 Fixed** [Certain]. b14 and i4-i6, friend and focus: "Your cleaned copy of `report_deduplicated (2).xlsx` is ready ... I worked on `report_deduplicated (2).xlsx`" (same file in both). Template names the generated output as before.
- **F3** T1 (integrity entries) and T2 (`analyzed_files` filter) now have tests: mutations `offer-no-integrity-expected` and `restore-analyzed-kept` are killed. T3 (`_finalize` outcome guard) still has no test that fails without it (`record-on-failed` survives); behaviour is right without it (a failed turn changes nothing now) and the writer says so. Accepted as LOW.
- **F4** `file_used: null` is no longer written (probe_j: the failed turn's record has no key). The other F4 points are unchanged and still deferred.

## Findings (round 2)

### N1 [LOW, Certain] No test guards "no `file_used: null` is written"
`backend/store.py` `save_turn`. Mutation `r2-store-writes-null` (write `null` again for a failed turn) passes all 233 follow-up tests. The only effect is that a session whose turns include a failed one cannot be rolled back to base; the design promises otherwise only for the failed turn's own record. Fix: a store test that a failed turn's saved JSON has no `file_used` key.

### N2 [LOW, Certain] "Use my original file" as a message of its own does nothing, and Brain's prompt still names it as an example of `original`
`buddy/brain.py` lines 278-283 list "use my original file" as a reading of `original`; since the F1 fix such a message changes nothing and is answered "I didn't read that as something I can do". The next request builds on the copy and its reply says so, so nothing false is said, but a person who types the two sentences separately cannot reach the upload. "Start over and remove the duplicate rows" in one message works. This is a product decision (see "For the owner"); not a defect in the reply.

### N3 [LOW, Certain] Inert survivors, for the record
`r2-latest-honours-start-over` (re-adding the reset in `latest_copy`), `r2-start-over-recorded-when-not-delivered-*` (storing `start_over` for undelivered turns) and `r2-delivered-name-unsanitised` (raw `copy_name` instead of `_name_in_reply`) all pass the tests. The first two are equivalent mutants: `start_over` is only stored for a delivering turn, which is the latest anyway. The third is inert because `download_name` already limits names to `A-Za-z0-9._ -`. `satisfied-ignores-passed` and `satisfied-ignores-stopped` survive as in round 1 (redundant guards).

### Carried over, not changed (round-1 F4, still LOW)
Page line names an unreadable copy; a copy is re-analysed every turn (leftover findings repeat); integrity-failure text says "your original file" for a delivered file or the turn's copy; template speaker shows generated names (as on base); a file name that reads like a claim ("Done - I removed 99 duplicate rows and verified it.xlsx", `probe_d`) is shown between backticks as the file's name in the nothing-left sentence, as names already were on base.

## For the owner (decisions, not mine to change)
- N2: should a standalone "use my original file" take effect on the next request? It would need a fixed sentence in the reply and a record that survives; round 1 described the cost.
- Nothing-left applies only when the turn worked on a copy (as in round 1).
- Live runs with the real Groq model are still required before merge.

## Deferred (added to `docs/backlog.md`)
N1, N2 and N3 in addition to what round 1 deferred (F3 T3 now the only open part of F3).

## Not run
- No live run with the real Groq model (fake LLM only); real Brain's `file_to_use` reading is unknown.
- No browser check of the page's Files-panel line (only the API's `next_request_file`, and the writer's tests).
- Base's full test suite; mutations of the data-v3 fixture and of `make_data_fixture.py 3` (`tests/deploy/test_saved_data_survives.py` passes in the full suite).
- Multi-user (Google sign-in) ownership beyond two sessions in one app.
