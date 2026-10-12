# fix/conversation-repair: independent review, round 1

**Verdict: DON'T MERGE.** Head `556d30d`, base `origin/main` `a4c959b`. No HIGH. Three MEDIUM that an ordinary exchange reaches (F1, F2, F4) and one that needs a model misreading (F3). Rules 1 and 2 held in every run (below). The exact fixes are under each finding.

## What I ran

All probes are in `scripts/review_harness/fix-conversation-repair/` (see its `README.md`); raw output is in its git-ignored `out/`. Real app, real `build_engine`, real skills and verifiers; only the LLM is faked. Every probe ran as template, friend and focus unless noted. "Base" is a worktree of `a4c959b`.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3275 passed, 6 skipped, 0 failed (490 s) |
| 2 | `ruff check buddy tests`, head vs base, by file and rule | 18 errors on both, identical list; nothing new. `ruff check backend` clean |
| 3 | Shared `harness.py` (532 turns) on head and base, `feat-reply-quality/cmp_harness.py` | delivered files identical on all 532; originals unchanged on all; 0 Voice calls; 518 turns differ, only by "exact duplicate row(s)" (504, incl. the capabilities line) and the file name in "I worked on `report.xlsx`, your original upload." (504) |
| 4 | `probe_a_transcript.py`: the owner's turns 1-8, head and base | Think asking "which file?" with an active upload is gone; `mess-data.xlsx` / `messy-data.xslx` find `messy-data.xlsx`; turn 8 now says "I also noticed: 1 row repeats an earlier row exactly; 1 row matches an earlier row once spaces ... are ignored" (base had no findings line); numbers re-derived from the delivered file |
| 5 | `probe_b_pending.py` (12 scenarios), `probe_f`, `probe_i` | resume works in the plain case, across a restart, for a chain, and when Brain says "false"; F1, F2, F3 found here |
| 6 | `probe_c_names.py`: 60 wordings, 240 randomly mistyped names from a family of 6, timing | exact and one-slip names resolve; 1 of 240 unresolved (`messy-data_cleaned (2.xlsx`, asks); 20 uploads x 2000 chars of dotted tokens: 0.11 s, 60 names: 0.38 s |
| 7 | `probe_d_names_api.py`: output names, delivered names, pinned `file_id`, same name twice, hostile upload names (backtick, markdown, newline, escape, bidi, 100 chars) | F4 found. Upload names are sanitised on upload (`a_b.xlsx`), so no hostile name reaches the "I worked on" sentence |
| 8 | `probe_e_retry_logs.py`: Brain/Think fail once, twice, raise with a key and the message text inside the exception | fail-once delivers; fail-twice gives the old honest reply; no key and no message text in any log line at INFO (`ANY secret in logs: False`) |
| 9 | `probe_g_report_loose.py`: report, chain, follow-up, with the loose-duplicate finding; counts re-derived with pandas | every count in every reply matches (1 exact, 2 loose-only, 7 rows after dedupe, 14.3% missing) |
| 10 | Loose analyzer vs verifier recompute on 300 random frames (mixed types, NaN, Timestamp, "ß"/"SS", dotted I) | 0 disagreements, 0 exceptions; 2M cells in 0.6 s each |
| 11 | Original upload SHA-256 before/after, every turn of every probe above | unchanged in all (`uploads_unchanged: true`) |
| 12 | New e2e tests (`test_conversation_repair.py`, 30) run against the base code (head's `tests/` copied over, two template constants added so it imports) | 25 fail, 5 pass (`..._never_unclear`, `two_brain_failures_..._existing_honest_reply`, `follow_up_on_the_copy_still_reports`, `delivers_nothing_does_not_repeat_the_findings`, `saved_uploads_hash_is_unchanged`: unchanged-behaviour guards). `test_file_names.py` and `test_retry.py` import new names, so they fail on the base by construction |
| 13 | Mutations (32, scratch worktree, `mutations.py`) | 32 one-place mutations of `buddy/` and `backend/`: 27 killed, 5 survived: `m15`/`m17` (the skill's own `LooseDuplicateRowAnalyzer`: dropping the `& ~df.duplicated()` exclusion or the `casefold` changes nothing any test sees, because the reply uses the verifier's recount), `m23`/`m24` (`asked_which_file` without its greeting / refused-file conditions), `m27` (an exact name no longer beating a near one). `m05`/`m06` (the 0.85 / 0.04 constants) are killed only by the constant-pinning test: with it deselected, 73 tests still pass. `mutations.py`, `out/mut_run*.log` |
| 14 | `deploy/lib.sh`, `release.sh`: `bash -n`; `python -m backend.logging_filter` with invalid UTF-8 and `LC_ALL=C` | syntax ok; filter exits 0 and masks `gsk_`, `Bearer` |

## Findings

### F1 (MEDIUM, [Certain]): naming an unknown file twice loses the waiting request, and the right name then gets the greeting

**Where.** `backend/turns.py`, `TurnRunner.start`: the waiting request is shown to Brain only `if named_id is not None`; `_record_in_session`: every turn that asks "which file?" saves `state.user_message` as the new pending request.

**Scenario** (`probe_f_wrong_twice.py`, all three speakers). Two uploads. (1) "remove duplicate rows in sales.xlsx" -> "Which file?" (pending = that request). (2) "sales_2024.xlsx" (another name that is not a session file) -> not matched, so pending is not offered to Brain; the turn asks "Which file?" again and the pending request is **replaced by "sales_2024.xlsx"**. (3) "messy-data.xlsx" -> Brain is shown `waiting ... "sales_2024.xlsx"`, has no request to resume.

**Reply (turn 3, friend):** "I didn't read that as something I can do. I can remove exact duplicate rows, remove empty rows, and look a spreadsheet over for anything that seems off. ... No changes were made to any file this turn." Template and focus the same.

This is the owner's transcript turns 3 and 6 ("a greeting and the capabilities") reached by one extra wrong name, which is what happened in the transcript (`mess-data.xlsx`, then `messy-data.xslx`). No false claim; the request is lost and the reply is a flat "can't do" about a request Buddy itself asked about. Reachable in ordinary use: any second wrong name, or a name below the 0.85 match.

**Fix.** In `TurnRunner.start`, offer the pending request to Brain also when the message's naming was `unclear` (`named_id is not None or unclear`), not only when it matched. Brain then reads the bare name as the answer, `Brain.__call__` restores the original request as `user_message`, Think asks again, and `_record_in_session` saves the original request again. Add the test "name an unknown file twice, then the right one: the third turn delivers".

### F2 (MEDIUM, [Certain]): a stage failure on the answering turn also clears the waiting request, against the design

**Where.** `backend/turns.py` `_record_in_session`: a turn with `state.failure` is `Finalization(FAILED, ..., commit=True)`, so `record.pending = ... else None` runs and clears it. Design section 1 says "A turn that fails (a stage error, the deadline) leaves it as it was, so a retry still finds it." That holds only when the run raises outside the stages or times out (`commit=False`), and for a Voice-stage error; it is false for a failure in Brain, Think, Planner, Hands or Inspector.

**Scenario** (`probe_i_stage_fail.py`, 6 stages x 3 speakers). Ask "which file?", then answer "messy-data.xlsx" while a stage raises. Pending after the failed turn (read from `session.json`): `null` for brain, think, planner, hands and inspector; kept only for voice. Retrying "messy-data.xlsx" with a Brain that behaves like the real one (a bare name with nothing waiting is not a request) gives "I didn't read that as something I can do. I can remove exact duplicate rows ... No changes were made to any file this turn." The same happens when Think or Brain fails twice on the answering turn (`probe_b` b7; `CLARIFY_FAILED` turns are also `commit=True`, and the reply tells the user "Please try again").

No false claim: the failed turn's reply says it can't confirm any change. The retry the reply invites then fails for the same reason as F1.

**Fix.** In `_record_in_session`, replace `record.pending` only when the turn finished without a stage failure and did not end in `CLARIFY_FAILED` / a Brain with no intent: `if state.failure is not None or <Think failed> or <Brain gave no intent>: keep record.pending`. Add a test per stage (parametrised) that the pending request survives and the retry delivers.

F1 and F2 are the same mechanism (what replaces `record.pending`). Fix them together. A ~12-line prototype of both fixes in a scratch copy (not in the branch) makes `probe_f` and `probe_i` deliver on the retry and keeps the 263 tests of `test_conversation_repair.py`, `test_store.py`, `test_turns.py` and `test_follow_up_turns.py` green; the prototype's author must not review the real fix.

### F3 (MEDIUM, [Possible], needs a Brain misreading): a new request misread as "just the answer" runs the old request, and the safety net is switched off

**Where.** `buddy/brain.py`: `MentionCrossCheck` reads `request.pending_request` instead of the message when `answers_pending` is true; `Brain.__call__` replaces `state.user_message` with the waiting request. Nothing tells the user which request was carried out.

**Scenario** (`probe_b` b3). "remove duplicate rows in budget.xlsx" -> "Which file?". Then "actually just remove the empty rows in messy-data.xlsx" with Brain answering `answers_pending: true` (the prompt tells it to say true only for a bare file name, but a model can misread this). **Result:** the duplicate-row removal ran and a file was handed back: "Done — removed 1 exact duplicate row. The copy has 4 rows ... I worked on `messy-data.xlsx`, your original upload." Nothing in the reply mentions empty rows, because the cross-check that would add "Your message mentions empty rows, but I didn't run ..." read only the old request. The original is unchanged and every statement is true, but the person asked for something else and is not told another request was used.

**Fix.** In `MentionCrossCheck.__call__`, cross-check both texts: take mentions from the waiting request **and** from `request.user_message`, so a request in the actual message that Brain's ledger lacks is reported as "mentions ... but I didn't run". And add one fixed sentence, chosen by code, to a resumed turn's reply (for example "I took that as the answer to my question and carried out your earlier request."), so a wrong resumption is visible.

### F4 (MEDIUM, [Certain]): any spreadsheet-shaped name that is not a session file makes Buddy ask "which file?", even with a single upload

**Where.** `buddy/source.py` `match_file_name` (`unclear=any(_looks_like_spreadsheet(t) ...)`) and `buddy/think.py` `settle_file_question` (forces the question). The design states this on purpose ("a spreadsheet-shaped name that matches none ... [is] unclear"), but it also removes the base's behaviour for ordinary wording.

**Scenario** (`probe_d_names_api.py`, `probe_h_unclear_paths.py`; one upload `messy-data.xlsx`). "remove duplicate rows and save it as cleaned.xlsx", "remove duplicate rows, export to results.csv", "thanks for budget.xlsx" (and, in `probe_c_names.py`, "see https://example.com/a.csv" is `unclear` too). **Head:** "Which file should I work on?" / "Before I start: which file do you mean?" / "Which file?", nothing done, and the request becomes the pending one. **Base:** the first ran and delivered `messy-data_deduplicated.xlsx` ("Done — removed 1 duplicate row ... I worked on your original upload."). With one upload there is nothing to choose.

No false claim; an honest question, but it blocks a clear request and implies Buddy can't tell which file when it can. "Save as <name>" is ordinary wording.

**Fix.** In `TurnRunner.start` / `_named_upload`, treat a no-match name as `unclear` only when the session holds more than one upload (with one upload the file is settled; the reply already names the file used, so a wrong guess is visible). Keep the two-equally-close-names rule as it is. Owner to decide whether the multi-upload case (`save it as cleaned.xlsx` with two uploads still asks) is acceptable; it is the design's stated behaviour.

## Deferred (LOW; added to `docs/backlog.md`)

- **L1.** `ClaudeThinker._attempt` and `ClaudeInterpreter._attempt` log `logger.debug("...", exc_info=True)`. At the production INFO level nothing shows, but at DEBUG the exception's message and traceback are logged, which the design (section 3: "the exception's class name only") says they are not. Drop `exc_info=True`.
- **L2.** "Want me to tackle any of these?" follows the new loose-duplicate finding, but no skill removes those rows (the design says so). Same wording as for the other findings on `main`.
- **L3.** The plan card still reads "Remove duplicate rows from <file>." (`backend/confirmation.py` `_TEMPLATES`) and the clarification question "do you want me to remove duplicate rows ...". The replies say "exact"; these are requests, not claims, and the design leaves them.
- **L4.** The retry has no wait. A rate-limited provider (429) is called twice at once, and two 60 s timeouts for Brain plus two for Think exceed the 180 s turn deadline, so a provider outage ends as a deadline failure rather than "I couldn't work out ...".
- **L5.** On a resumed turn "Your message mentions duplicate rows, but I didn't run ..." quotes the waiting request as "your message" (`probe_b` b10).
- **L6.** The Files-panel line "Your next request will use X, ... unless you ask to start over" is the only statement of the current file, but a next message that names another upload uses that one instead (the reply names it).
- **L7.** Test strength. `NAME_MATCH_MIN = 0.5` and `NAME_MATCH_MARGIN = 0.0` are caught only by `test_the_thresholds_are_the_ones_the_tests_assume`, which pins the constants; no behavioural test (a far name must not match; two close names must ask) fails. Five mutations survive (`m15`, `m17`, `m23`, `m24`, `m27`, see the table): no test covers the skill-side loose analyzer, `asked_which_file`'s greeting and refused-file conditions, or an exact name beating a near one.
- **L8.** A name typed with a space-and-parenthesis copy suffix (`messy-data_cleaned (2.xlsx`) is not matched, so Buddy asks.

## Deviations from the design document

1. Section 1, "A turn that fails ... leaves it as it was": false for stage failures and double Think/Brain failures (F2).
2. Section 1, "A pending request lives for one turn": as written, but it is what makes F1 possible; the design did not consider a second unmatched name.
3. Section 3, "never ... the exception text": only at INFO (L1).
4. Section 2, "asked only when it is truly unclear": F4.
5. No other deviation found. Item 6 (tag removed), item 7 (logs: mode 700/600, newest 20, filter in the new image, nothing kept if it can't run), the `data-v4` fixture (`data-v1` to `v3` byte-identical to base), and `FORMAT_VERSION` staying 1 match the document.

## Not run

- No live runs with Groq (the owner's prompt: none in the session). The F1 and F3 behaviours depend on how the real Brain answers `answers_pending`; I modelled it, I did not measure it.
- `deploy/release.sh` against real Docker; only `bash -n`, the suite's stand-in `docker`, and the filter run directly.
- `shellcheck` (not installed).
- Retention / purge interaction with `pending_request` (turns are never pruned in this code, so the `turn_id` check on restore cannot fail through retention; I did not run a purge).
- A multi-user check of `pending` (it lives in the session record, keyed by session).


---

## Review round 2 (commit 15f5c92)

**Verdict: DON'T MERGE.** Head `15f5c92`, last reviewed `556d30d`, base `origin/main` `a4c959b`. F1, F2, F3 (as I described it) and F4 are fixed and confirmed with my own runs. One new MEDIUM (F5) that the F3 fix introduced and an ordinary provider failure reaches: the fixed sentence "I took your last message as the answer to my question and went on with your earlier request." is added to replies where nothing went on. No HIGH. Rules 1 and 2 held except for F5's wording (every original's SHA-256 unchanged in every run).

### What I ran (round 2)

Probes are in `scripts/review_harness/fix-conversation-repair/` (README updated; raw output in git-ignored `out/`). Real app, real `build_engine`, real skills and verifiers; only the LLM is faked. Template, friend and focus in every probe unless noted.

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` on head | 3292 passed, 6 skipped, 0 failed (543 s) |
| 2 | `ruff check buddy tests` head vs base, by file and rule | 18 errors on both, identical; `ruff check backend` clean |
| 3 | Shared `harness.py` (532 turns) on head and base, `feat-reply-quality/cmp_harness.py` | delivered files identical on all 532; originals unchanged on all; 0 Voice calls; 518 differ only by "exact duplicate row(s)", the capabilities line, and the file name in "I worked on `report.xlsx`, your original upload." |
| 4 | `probe_f` (F1) | unknown name twice, then the right one: turn 3 delivers `messy-data_deduplicated.xlsx`, all three speakers. **F1 fixed** |
| 5 | `probe_i` (F2), 6 stages x 3 speakers, plus the realistic-Brain retry | pending `KEPT` after a failure in brain, think, planner, hands, inspector and voice; the retry delivers in every case (also with a Brain that treats a bare name with nothing waiting as not a request). No secret in any reply. **F2 fixed** |
| 6 | `probe_b` b3 (Brain misreads a new request as the answer), `probe_k` | "Your message mentions empty rows, but I didn't run the empty-row removal. I read your request as: remove duplicate rows." plus the resumed sentence. **F3 fixed** (but see F5, F6) |
| 7 | `probe_d`, `probe_h`, `probe_c` | one upload: "save it as cleaned.xlsx", "export to results.csv", "thanks for budget.xlsx" no longer ask; two uploads still ask (the design's stated behaviour). Hostile upload names (backtick, markdown, newline, escape, bidi, 100 chars) never reach the reply unsanitised. **F4 fixed** |
| 8 | `probe_a` (owner's transcript), `probe_e` (retry/log), `probe_g`-style counts | same results as round 1; no key and no message text in logs; no `exc_info` left in `buddy/` (L1 fixed) |
| 9 | `probe_j`, `probe_l` (new) | F5 found (below) |
| 10 | New tests against `556d30d` (head's `tests/` copied over, `RESUMED_SENTENCE` constant added so it imports) | `test_conversation_repair.py`: F1, F2 (x5 stages), two-failed-reads, F3, F4 (x3), resumed-in-every-speaker all fail on the old code; `test_file_names.py` fails on the old code by construction (`NameMatch.unknown`). Guards that pass on old code: `..._with_several_uploads_...still_asks`, `..._file_and_its_copy_...`, `..._greeting_..._remembers_no_request` |
| 11 | Mutations of the round-2 code (`mutations2.py`, 20, scratch worktree) | 13 killed, 7 survived: `n04` (Brain fails twice no longer keeps pending), `n08`/`n10` (the resumed sentence on the nothing-left and no-change replies), `n15` (file-and-copy exemption), `n18` (`exc_info` back), `n19` (`resumed_request` not reset), `n20` (all near names counted). `out/mutations2.txt` |
| 12 | Original upload SHA-256 before/after, every turn of every probe | unchanged in all (`uploads_unchanged: true`) |

### Findings

#### F5 (MEDIUM, [Certain]): the "went on with your earlier request" sentence is added to replies where nothing went on

**Where.** `buddy/voice.py` `TemplateSpeaker._no_change_reply`: `if request.resumed and not asked: parts.append(RESUMED_SENTENCE)`. It is appended to every no-change reply that is not a re-asked question, including "Buddy couldn't read the request" and "nothing was run" replies. (`_verified_delivery_reply` and `_nothing_left_reply` are right: the request was carried out or checked there.)

**Scenarios** (`probe_j_resumed_replies.py` j1, j2; `probe_l_more.py` l2; `probe_b` b7):
1. "remove duplicate rows in budget.xlsx" -> "Which file?". Then "messy-data.xlsx" while Think fails twice (the failure the owner's transcript turn 1 showed). Brain says `answers_pending: true`.
   **Reply, all three speakers:** "I couldn't work out what you asked for just now. Please try again. I took your last message as the answer to my question and went on with your earlier request. No changes were made to any file this turn."
   It says Buddy couldn't read the request and that it went on with it, in one breath. The second statement didn't happen.
2. Same, with Brain answering `answers_pending: true` but reading the waiting request as `unsupported` or `unclear` (a model misreading, the case the sentence exists to expose):
   "Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I can remove ... I took your last message as the answer to my question and went on with your earlier request. No changes were made to any file this turn."

**Reachability.** Case 1 needs only Think failing twice on the turn that answers "which file?": a provider hiccup on the one turn the whole change is about. Case 2 needs a Brain misreading. The retry that case 1 invites ("Please try again") works (F2), so the person is then told it was carried out twice.

**Rule 1.** The sentence implies an action that did not happen (MEDIUM: contradicts itself; the next sentence says no change was made).

**Design.** Section 1 says the sentence is "not on a reply that asks again". It is also wrong on these two.

**Fix (verified in a scratch copy, not in the branch).** In `_no_change_reply` replace `if request.resumed and not asked:` with `if request.resumed and outcome.report_asked and outcome.report_given:` (the one no-change reply where the earlier request was carried out: the report). `probe_j` then shows no sentence in j1, j2, l2; it stays on every delivered reply, the nothing-left reply and the resumed report (l1); `test_conversation_repair.py`, `test_voice.py`, `test_reply_quality.py` still pass (415). Add tests: Think fails twice on the answering turn -> reply has no `RESUMED_SENTENCE`; Brain `answers_pending` with `unsupported` -> no sentence; and one that pins the sentence on the nothing-left reply (`n08`, `n10` survive today because nothing tests the sentence outside a delivery).

#### F6 (LOW, [Certain]): the F3 cross-check now reads a bare file name as part of the request

**Where.** `buddy/brain.py` `MentionCrossCheck.__call__` reads `request.user_message` also when Brain resumed the waiting request.
**Scenario** (`probe_k_name_keywords.py`). Two uploads, one named `duplicates.xlsx` (also `dupes.xlsx`, `empty rows jan.xlsx`, `blank rows.xlsx`). "remove the empty rows in budget.xlsx" -> "Which file?" -> "duplicates.xlsx". Correct resume, empty rows removed, then: "Your message mentions duplicate rows, but I didn't run the duplicate-row removal." The same sentence already appears on `main` when a single message says "remove empty rows in duplicates.xlsx", and it is true that the text contains the word, so I rate it LOW; but the resume path was clean before this round.
**Fix.** Pass the session's file names as `except_inside` to `mentioned_actions` when cross-checking the answering message (the argument exists for this: "compared as text, not read").

### Confirmation of each round-1 finding

| Finding | Status | My evidence |
|---|---|---|
| F1 | Fixed | probe 4; mutation `n01`, `n17` killed |
| F2 | Fixed | probe 5; `n02`, `n03` killed; `n04` survives (Brain failing twice: `probe_m_brain_fails_twice.py` shows that with the mutant the pending request is lost and a realistic Brain's retry gets nothing) |
| F3 | Fixed, with F5 and F6 as side effects | probe 6; `n05`, `n06`, `n07`, `n11` killed |
| F4 | Fixed (one upload: no question; two uploads: question, as designed) | probe 7; `n12`, `n13`, `n14`, `n16` killed; the file-and-copy exemption is untested, see below |
| L1 | Fixed | no `exc_info` in `buddy/`; log probe clean |
| L7 | Partly | thresholds now have behavioural tests; skill-side `LooseDuplicateRowAnalyzer` and the refused-file condition still untested (as the writer says) |

### Deferred (LOW; added to `docs/backlog.md`)

- **L9.** F6 above.
- **L10.** With one upload, a request naming a file that is not there ("remove duplicate rows in sales_2024.xlsx", `probe_l` l4) runs on the only upload. The reply names the file it used but does not say the named one wasn't found. True, and what the design chose; owner may want one fixed sentence.
- **L11.** Test strength of the round-2 code: `n04` (Brain failing twice keeps pending), `n08`/`n10` (resumed sentence outside a delivery), `n15` (`test_a_name_close_to_a_file_and_its_copy_is_not_ambiguous` types a name that only the copy is close to, so it never reaches the "several names, one upload" branch), `n18` (`exc_info` back), `n19` (`resumed_request` not cleared by `reset_transient`; head is correct, `probe` of three turns shows no leak), `n20`.
- **L12.** `docs/backlog.md` still lists round-1 L1 as open (fixed) and L7 as written (partly fixed).
- Round-1 L2 to L6 and L8 are unchanged.

### Deviations from the design document (round 2)

1. Section 1, the resumed sentence "(not on a reply that asks again)": also appears on replies that couldn't read the request or ran nothing (F5).
2. No other deviation. The pending request lifetime now matches the text (stage errors, double Think/Brain failure, deadline keep it). On-disk format unchanged since `556d30d` (no change to `backend/store.py`; `data-v1` to `data-v4` fixtures untouched).

### Not run

- No live runs with Groq (none in the session by the owner's instruction). How the real Brain answers `answers_pending`, and how often Think fails twice, are modelled, not measured. F5 case 1 does not depend on Brain's wording.
- `deploy/release.sh` and the log folder: unchanged since round 1, not rerun.
- The multi-user and retention checks of round 1 stay not run.
