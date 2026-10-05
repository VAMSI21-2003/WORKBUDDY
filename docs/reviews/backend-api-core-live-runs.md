# Live runs: `feat/backend-api-core` (PR-2), through the real HTTP server

**When:** 2026-10-04, after review round 3's MERGE verdict (`docs/reviews/backend-api-core-review.md`).
**Code:** the reviewed code at `7be9949`. The runs were made at `3d6d9dc` and `edb31f9`, which add only the review file and the live-run driver; `git diff 7be9949 HEAD -- backend buddy core skills tests` is empty.
**How** (design Section 7.5): `scripts/live_api_runs.py` starts `python -m backend` (uvicorn, 127.0.0.1, one worker) with a fresh data root under `scratch/live-api/` and drives it over HTTP only.
- **Provider:** `GroqProvider` (`openai/gpt-oss-120b`), with the key attached by the cloud proxy. The key was never read or printed.
- **Python:** 3.11.15.
- **`WORKBUDDY_AUTO_APPROVE=1`:** there is no confirm endpoint until PR-3, so "approve" in design 7.5 means the stand-in approves every Write-tier step.
- **Pause:** 8 s between turns, to spare the rate limit.

**Rate limit:** no HTTP 429. Every Groq call returned `200`: 0 lines with `429` in any server log. All planned runs completed.

**Independent check:** for every delivered file, the driver downloads it through the API and checks three things:
- the download's SHA-256 against the turn's record;
- the file read with pandas against the original read with pandas (`drop_duplicates()`, `dropna(how="all")` or both);
- every number the reply states, against those reads.

The original's SHA-256 is compared three ways: the local fixture, the hash the upload recorded, and the server's stored copy after the turn.

**Not run:** runs 3 and 4 (per-step confirmation, reject) need PR-3. Python 3.13, Windows and the demo host weren't available.

## Summary

| Run | What | Outcome | Delivered file, my read | False claim? | Original |
|---|---|---|---|---|---|
| 1 | "remove duplicate rows" | delivered | 4 rows, 0 duplicates, 1 empty; equals `drop_duplicates()` | none | unchanged |
| 2 | same session, "remove the empty rows" | delivered | 5 rows, 2 duplicates, 0 empty; equals `dropna(how="all")`. Run 1's file byte-identical after it | none | unchanged |
| 5 | "hi, what can you do?" | not_delivered | — | none | unchanged |
| 6 | no-duplicates fixture, "remove duplicate rows" | delivered | 4 rows, 0 duplicates | none (no "removed N") | unchanged |
| 7 | run 1's session, "now remove the empty rows too" | delivered | 5 rows, 2 duplicates, 0 empty | none | unchanged |
| R | dedupe, then re-upload the copy, "now remove the empty rows too" | delivered ×2 | 4 rows; then 3 rows, 0 duplicates, 0 empty | none | both uploads unchanged |
| 8 | server killed mid-turn, restarted | failed (repaired) | — | none | unchanged |
| 9 | 5 MB fixture, default 180 s deadline | failed ×2 (deadline) | — | none | unchanged |
| 9 (measured) | 5 MB fixture, deadline raised to 900 s | delivered ×2 | 58,212 rows; 58,211 rows; both equal the expected frames | none | unchanged |

Server logs (all runs): 0 "Logging error" blocks, 0 tracebacks, 0 of the 9 session ids, 0 `429`s.

## Run 1: "remove duplicate rows" (focus)

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** false.
- **Steps:**
  - analyze `manual_report.xlsx` [success, verified];
  - deduplicate `manual_report.xlsx` → `manual_report_deduplicated_<key>-task-1.xlsx` [success, verified];
  - analyze (re-check) of the copy [success, verified].
- **Delivered:** yes, `manual_report_deduplicated_1ff5…63b3-task-1.xlsx`, in `t0001/`. The download's SHA-256 matches the record.
- **Independent check:** the original has 6 rows, 2 duplicates and 1 empty row. The copy has 4 rows, 0 duplicates and 1 empty row, and equals `original.drop_duplicates()`.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. Removed 2, 4 rows and 0 duplicates match my read; 1 of 4 is 25.0%.
- **Original SHA-256:** `44c3766e…3ec36` → `44c3766e…3ec36` (unchanged).
- **Wall time:** 14.9 s. That includes the first `ydata_profiling` import in the server process.

## Run 2: same session, "remove the empty rows"

- **Intent:** `remove_empty_rows`. **`clarification_required`:** false.
- **Steps:** remove_empty_rows `manual_report.xlsx` → `manual_report_no_empty_rows_<key>-task-0.xlsx` [success, verified]. No analysis, because the file was analyzed in run 1.
- **Delivered:** yes, in `t0002/`. The download's SHA-256 matches the record.
- **F5:** run 1's file is in `t0001/` and run 2's in `t0002/`. Run 1's file, downloaded again after run 2, is byte-identical to its recorded hash.
- **Independent check:** 5 rows, 2 duplicates, 0 empty. It equals `original.dropna(how="all")`.
- **Reply:** "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I made this copy from your original upload, not from a file I handed back earlier."
- **False claim:** none. The from-original sentence is true: the copy has run 1's 2 duplicates back in it.
- **Original SHA-256:** unchanged (`44c3766e…3ec36`).
- **Multi-turn context:** the model classified the request correctly, with run 1's message and reply in the prompt.

## Run 5: "hi, what can you do?" (friend)

- **Intent:** `unclear`. **`clarification_required`:** false.
- **Steps:** analyze `manual_report.xlsx` [success, verified].
- **Delivered:** no (`not_delivered`).
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn."
- **False claim:** none. 1 of 6 is 16.7%, and no file was changed or handed back. It doesn't answer the question asked; that's backlog item 2.
- **Original SHA-256:** unchanged.

## Run 6: no-duplicates fixture, "remove duplicate rows" (focus)

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** false.
- **Steps:** analyze → deduplicate → analyze (re-check), all success and verified.
- **Delivered:** yes. **Independent check:** the original and the copy both have 4 rows and 0 duplicates; the copy equals `drop_duplicates()`.
- **Reply:** "Done — there were no duplicate rows to remove. The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **False claim:** none. N2 holds: no "removed N".
- **Original SHA-256:** `1edc778a…5a5d` → unchanged.

## Run 7: run 1's session, "now remove the empty rows too" (the third turn)

- **Intent:** `remove_empty_rows`. **`clarification_required`:** false. So design 3.3's risk didn't show: two earlier turns in the prompt didn't push the model to a wrong intent.
- **Steps:** remove_empty_rows `manual_report.xlsx` [success, verified].
- **Delivered:** yes, in `t0003/`. **Independent check:** 5 rows, 2 duplicates, 0 empty. It equals `dropna(how="all")` of the original, so it does **not** build on run 1's file. That's the expected behaviour under the current option (b).
- **Reply:** "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I made this copy from your original upload, not from a file I handed back earlier."
- **False claim:** none. The sentence tells the user that the copy is made from the original, so the "too" request didn't add up the changes. Whether it *should* add up is still the owner's decision (review round 1, M2 (a) vs (b)).
- **Original SHA-256:** unchanged.

## Run R: the delivered copy, uploaded again (review round 3's suggestion)

- **R.1:** "remove duplicate rows" on `manual_report.xlsx`. Delivered: 4 rows, 0 duplicates, 1 empty. Equals `drop_duplicates()`. The reply is the same as run 1's. Original unchanged.
- **R.2:** the copy was downloaded through the API and uploaded again; the server stored it under its own name, `manual_report_deduplicated_<key>-task-1.xlsx`. Then "now remove the empty rows too".
  - **Intent:** `remove_empty_rows`. **`clarification_required`:** false.
  - **Steps:** analyze (the new upload) → remove_empty_rows → analyze (re-check), all success and verified.
  - **Independent check:** 3 rows, 0 duplicates, 0 empty. It equals the re-upload with its empty row dropped, so this time the changes do add up.
  - **Reply:** "Done — removed 1 fully-empty row(s). The copy has 3 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report_deduplicated_6f05…2af8-task-1.xlsx is ready. None of the issues I check for were found."
  - **False claim:** none. There's no from-original sentence, which is right (R2-M1). The file name it mentions is long because of the session key; see observation 4.
  - **SHA-256:** the re-upload is unchanged (`4114d13b…5ee4`), and so is the first upload (`44c3766e…3ec36`).

## Run 8: the server killed mid-turn, then restarted

- A turn "remove duplicate rows" was started. Half a second later its status was `running`, and the server was killed with `SIGKILL`.
- A new server process on the same data root then reported the turn as follows:
  - `status: finished`, `outcome: failed`, `reply_source: pipeline_fallback`;
  - reply: "Something went wrong while I was working on this, so I can't confirm that any change was made. I haven't handed back any file from this turn.";
  - no files.
- The session was not busy, and its turn list was `[t0001]`. The interrupted turn was reported as failed, not missing.
- **False claim:** none. **Original SHA-256:** unchanged.

## Run 9: the 5 MB fixture

The fixture is the design's measured shape: 14 columns (6 floats, 3 ints, 4 short strings, 1 date), with about 5% duplicate rows and 3 fully empty rows. It's 5,138,565 bytes (98% of the 5 MiB cap): 61,277 rows, 3,063 duplicates and 3 empty rows by my read. Each run used a fresh server process.

### With the default 180 s deadline

| Turn | Wall time | Outcome | Reply |
|---|---|---|---|
| "remove duplicate rows" | 180.4 s | failed, `pipeline_fallback` | "Something went wrong while I was working on this, so I can't confirm that any change was made. I haven't handed back any file from this turn." |
| "remove duplicate rows, then remove empty rows" (new session) | 180.5 s | failed, `pipeline_fallback` | the same |

- **Where the time went, from the server log, for the first turn:**
  - Brain and Think: about 3 s;
  - the analysis of the original: 55 s;
  - the dedupe with its step check: 34 s;
  - the re-check analysis was still running when the deadline fired.
- **The second turn** started while the first turn's worker was still running. Threads can't be killed (design 9.2), so the two competed for the CPU, and its first analysis alone ran past 180 s.
- **False claim:** none. Nothing was handed back. **Original:** unchanged (`49977eda…c0d1`).
- **Peak server RSS:** 694 MB.

### Measured with the deadline raised (`WORKBUDDY_TURN_DEADLINE_S=900`), turns one after the other

| Turn | Wall time | Peak server RSS | Outcome |
|---|---|---|---|
| "remove duplicate rows" | **255.6 s** | 708 MB | delivered |
| "remove duplicate rows, then remove empty rows" | **308.1 s** | 770 MB | delivered |

- **Turn 1 (dedupe).**
  - **Intent:** `remove_duplicate_rows`, no clarification.
  - **Steps:** analyze (55 s) → deduplicate (34 s) → analyze re-check and Inspector (163 s), all verified.
  - **Independent check:** the original has 61,277 rows, with 3,063 duplicate data rows and 3 empty rows. The copy has 58,212 rows, 0 duplicates and 1 empty row. It equals `drop_duplicates()`, so 3,065 rows were removed: 3,063 duplicates plus 2 of the 3 identical blank rows.
  - **Reply:** "Done — removed 3065 duplicate row(s), including 2 repeated blank rows. The copy has 58212 row(s), 0 duplicate row(s) remaining. Your cleaned copy of big_report.xlsx is ready. I also noticed: f0: 1 missing value(s) (0.0%); … f5: 408 unusually high/low value(s) (e.g. …). Want me to tackle any of these?"
  - **False claim:** none. Every count matches my read.
  - The "1 missing value(s) (0.0%)" is 1 of 58,212 rounded (observation 5).
  - The outlier counts are the analyzer's own findings. I didn't recompute them; that's the pre-existing verified-outcome-a L1.
- **Turn 2 (chain).**
  - **Intent:** `multi_step_request`.
  - **Steps:** analyze → deduplicate → remove_empty_rows (on the dedupe's output) → analyze re-check, all verified.
  - **Independent check:** 58,211 rows, 0 duplicates, 0 empty. It equals `dropna(how="all").drop_duplicates()`.
  - **Reply:** "Removed 3065 duplicate row(s), including 2 repeated blank rows. Removed 1 fully-empty row(s). The copy has 58211 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of big_report.xlsx is ready. I also noticed: f0: 419 unusually high/low value(s) … Want me to tackle any of these?"
  - **False claim:** none.
- **Original SHA-256:** `e6a0c337…8bf2` → unchanged for both turns.

**What this means for the 5 MiB default.** The design's evidence rule ("Decisions (2026-09-30, part 2)", item 3) says: "If a two-step chain on a 5 MB fixture takes over 120 s through the API (two-thirds of the deadline), lower the default to 2 MB before deployment."
- Here the chain took **308 s**, and even a single step took **256 s**. That's well past the 180 s deadline, so at the default settings every 5 MB turn ends as an honest failure.
- This container's CPU isn't the demo host's. PR-4's checklist already requires repeating run 9 there.
- On this evidence, the 5 MiB default is too large. The owner's call is whether to lower it (2 MB per the design's rule), raise the deadline, or make the analysis cheaper. In the single-step turn, the analysis of the original (55 s) and the re-check analysis with Inspector's verification (163 s) take 218 of the 256 s.
- Recorded in `docs/backlog.md`.

## Observations (none is a false claim or a changed original)

1. **First attempt: a reset keep-alive connection.**
   - The first launch of the driver (`scratch/live-api/20261004T133112/`) stopped during run 1. The server reset the driver's keep-alive connection while the turn ran its first analysis, which includes the CPU-heavy first import of `ydata_profiling`.
   - The server stayed up, and the turn carried on. Every Groq call returned `200`.
   - **Likely cause:** the event loop was starved for a few seconds. uvicorn's 5 s keep-alive timer then closed a connection that already held an unread poll, which sends a reset.
   - **Fix to the driver:** it now retries idempotent GETs, as a polling client would, and counts the retries. The completed runs needed **0** retries.
   - **For the frontend:** a poll can fail with a reset during a busy turn and must be retried. The frontend design's "5 consecutive failures" rule assumes retries already.
   - **A server-side mitigation** is to import the analysis libraries at startup. That would be a code change, so it needs its own review.
2. **A deadline doesn't stop the work.** After run 9's first turn timed out, its worker kept the CPU and slowed the next turn, which then timed out too. This is design 9.2's known risk, now seen in practice. The global cap (2) bounds it.
3. **The same download name from two turns.** Runs 2 and 7 each delivered `manual_report_no_empty_rows_<key>-task-0.xlsx`, from different folders (`t0002/`, `t0003/`). Nothing was overwritten (F5 holds), but a browser saves the second download under a renamed name.
4. **Long generated names.** Output names carry the 32-hex session key (`…_deduplicated_<key>-task-1.xlsx`), and a re-uploaded copy's output name carries it twice. That's true but unfriendly.
5. **"1 missing value(s) (0.0%)"** on the 58,212-row file. It's 1 of 58,212 rounded to one decimal. The count is right, but "0.0%" next to 1 reads oddly. This is the analysis wording, which predates this branch.

## Verdict for the live runs

No false claim and no changed original in any run, so nothing returns to the review loop. Observations 1 to 5 and run 9's timing are added to `docs/backlog.md` for the owner.

---

# Live runs after review round 4 (head `18ae9bb`, reviewed code `c136b93`)

**When:** 2026-10-04, after round 4's MERGE verdict. This head has `origin/main` (`1462ce4`: values-only copy, the PR-B reply ledger) merged in and the 2 MiB default cap ("Decisions (2026-10-04)", item 3). `git diff c136b93 18ae9bb -- backend buddy core skills tests` is empty; `18ae9bb` changes only `scripts/live_api_runs.py`.
**How:** as above: `scripts/live_api_runs.py --runs 1,2,7,9b`, a real `python -m backend` server, Groq (`openai/gpt-oss-120b`) through the proxy, `WORKBUDDY_AUTO_APPROVE=1`, Python 3.11.15, default settings otherwise (2 MiB cap, 180 s deadline).
**Rate limit:** no 429; all 4 runs completed. Server logs: 0 "Logging error" blocks, 0 tracebacks, 0 of the 3 session ids, 0 `429`s.

| Run | What | Outcome | Wall time | Delivered file, my read | False claim? | Original |
|---|---|---|---|---|---|---|
| 1 | "remove duplicate rows" | delivered | 5.7 s | 4 rows, 0 duplicates, 1 empty; equals `drop_duplicates()` | none | unchanged |
| 2 | same session, "remove the empty rows" | delivered | 3.5 s | 5 rows, 2 duplicates, 0 empty; equals `dropna(how="all")`. Run 1's file byte-identical after it, in `t0001/` vs `t0002/` | none | unchanged |
| 7 | same session, "now remove the empty rows too" | delivered | 3.5 s | 5 rows, 2 duplicates, 0 empty | none | unchanged |
| 9b | 2 MB fixture, "remove duplicate rows" | delivered | **123.3 s** | 22,798 rows, 0 duplicates, 1 empty; equals `drop_duplicates()` | none | unchanged |
| 9b | 2 MB fixture, "remove duplicate rows, then remove empty rows" | delivered | **153.6 s** | 22,797 rows, 0 duplicates, 0 empty; equals `dropna(how="all").drop_duplicates()` | none | unchanged |

## Run 1: "remove duplicate rows" (focus)

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** false.
- **Steps:** analyze `manual_report.xlsx` → deduplicate → analyze (re-check), all success and verified.
- **Delivered:** yes, in `t0001/`. The download's SHA-256 matches the record.
- **Independent check:** the original has 6 rows, 2 duplicates and 1 empty row. The copy has 4 rows, 0 duplicates and 1 empty row.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. The numbers match my read (1 of 4 is 25.0%). The values-only sentence comes from main's PR #11, and it's true: the copy is written values-only.
- **Original SHA-256:** `f7168da7…65cb` → unchanged.

## Run 2: same session, "remove the empty rows"

- **Intent:** `remove_empty_rows`. **`clarification_required`:** false.
- **Steps:** remove_empty_rows `manual_report.xlsx` [success, verified].
- **Delivered:** yes, in `t0002/`. F5 holds: run 1's file, downloaded again afterwards, is byte-identical to its recorded hash.
- **Independent check:** 5 rows, 2 duplicates, 0 empty.
- **Reply:** "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I made this copy from your original upload, not from a file I handed back earlier. Anything else?"
- **False claim:** none.
  - The from-original sentence is true: the copy has the 2 duplicates back.
  - "Anything else?" is main's PR-B closer. The ledger shows the one requested action done, which is true.
- **Original SHA-256:** unchanged.

## Run 7: same session, "now remove the empty rows too" (the third turn)

- **Intent:** `remove_empty_rows`. **`clarification_required`:** false. With two earlier turns in the prompt, the model still classified it correctly.
- **Steps:** remove_empty_rows `manual_report.xlsx` [success, verified].
- **Delivered:** yes, in `t0003/`. **Independent check:** 5 rows, 2 duplicates, 0 empty. It was made from the original, as option (b) does.
- **Reply:** "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I made this copy from your original upload, not from a file I handed back earlier."
- **False claim:** none. The sentence tells the user that the "too" didn't build on run 1's file.
- **Original SHA-256:** unchanged.

## Run 9b: the 2 MB fixture, at the default 180 s deadline

- **The fixture:** run 9's shape at the new default cap: 2,013,624 bytes (96% of 2 MiB), 24,000 rows × 14 columns. By my read it has 1,200 duplicate data rows and 3 empty rows.
- **Server:** a fresh process, with the default settings.

**One step: "remove duplicate rows" (123.3 s, peak server RSS 479 MB).**
- **Intent:** `remove_duplicate_rows`.
- **Steps:** analyze → deduplicate → analyze (re-check), all verified.
- **Independent check:** 22,798 rows, 0 duplicates, 1 empty. It equals `drop_duplicates()`, so 1,202 rows were removed: 1,200 duplicates plus 2 of the 3 identical blank rows.
- **Reply:** "Done — removed 1202 duplicate row(s), including 2 repeated blank rows. The copy has 22798 row(s), 0 duplicate row(s) remaining. Your cleaned copy of big_report.xlsx is ready. The copy has the values only; … I also noticed: f0: 1 missing value(s) (0.0%); … f5: 169 unusually high/low value(s) (e.g. …). Want me to tackle any of these?"
- **False claim:** none. The counts match. The "(0.0%)" is 1 of 22,798 rounded (observation 5 above). The outlier counts are the analyzer's own, which I didn't recompute.

**Chain: "remove duplicate rows, then remove empty rows" (153.6 s, peak server RSS 510 MB).**
- **Intent:** `multi_step_request`.
- **Steps:** analyze → deduplicate → remove_empty_rows (on the dedupe's output) → analyze (re-check), all verified.
- **Independent check:** 22,797 rows, 0 duplicates, 0 empty. It equals `dropna(how="all").drop_duplicates()`.
- **Reply:** "Done — removed 1202 duplicate row(s), including 2 repeated blank rows. Removed 1 fully-empty row(s). The copy has 22797 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of big_report.xlsx is ready. The copy has the values only; … I also noticed: f0: 163 unusually high/low value(s) … Want me to tackle any of these?"
- **False claim:** none.

**Original SHA-256** (both turns): `93a5deff…1789` → unchanged.

**What the timing means.**
- At 2 MiB, both turns finish inside the 180 s deadline on this host, where at 5 MB neither did.
- The chain took 153.6 s, though. That's 85% of the deadline, above the design's 120 s comfort line (two-thirds of the deadline). It agrees with review round 4's R4-L2, which measured 152–160 s with a fake model.
- So on this host 2 MiB works, with little margin. The demo host's numbers (PR-4 checklist) decide whether to go lower, lengthen the deadline or cut the number of reads.

## Verdict for these runs

No false claim and no changed original, so nothing returns to the review loop.
