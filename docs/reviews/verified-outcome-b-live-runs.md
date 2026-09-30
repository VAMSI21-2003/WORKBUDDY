# Live runs: `feat/verified-outcome-b` (PR-B of the verified-outcome design)

**Date:** 2026-09-30. **Code under test:** commit `31bbb5f` (the PR-B code and tests; this doc and the impl doc were committed after it and change no code). **Provider:** Groq (`GroqProvider`, key attached by the cloud proxy). **Script:** `python scripts/run_dedupe_manually.py`, which runs `build_engine` with `PersonalityVoice` on a fresh fixture each run.

**Runs completed:** 6 of 6 planned: design Section 5.2 rows 4, 6, 7, 8 and 10, plus "remove the duplicate rows" in friend mode. No 429 or other provider error. (Row 4 was also run once on the uncommitted working tree before the commit. Its reply was byte-identical to the committed run's; only the committed runs are reported below.)

**Fixture (`default`):** `manual_report.xlsx`, 6 rows (`name`, `amount`): Alice, Bob, *blank*, Alice, Carol, Bob. It has 2 exact duplicates and 1 fully-empty row mid-file. The script rewrites it before every run, so its bytes (and hash) differ between runs; each run compares its own before and after.

**Independent check:** after each run the script re-reads the original and the delivered file with pandas. It reports rows, exact duplicates (`duplicated().sum()`, repeated blank rows included) and fully-empty rows for both, plus the original's SHA-256 before and after.

**What the script prints that PR-B added:** Brain's own `requested_actions` as returned, whether each phrase is in the message, the full ledger (Brain's entries, the intent entry if one was added, then mention-only entries), each `OutcomeReport` action with its status, reason and source, Brain's reading, and the residual counts.

## Summary

| Row | Mode | Message | Intent | Brain's `requested_actions` | Mention-only entries | Lead word | Delivered | Counts match pandas | False claim | Original unchanged |
|---|---|---|---|---|---|---|---|---|---|---|
| 4 | focus | "remove the duplicate rows and then remove the empty rows" | `multi_step_request` | dedupe, empty | none | Done | yes | yes | no | yes |
| 6 | focus | "remove the duplicate rows and then give me a report" | `multi_step_request` | dedupe, unsupported "give me a report" | none | Partly done | yes | yes | no | yes |
| 7 | focus | "remove the empty rows but keep the duplicates" | `remove_empty_rows` | empty | dedupe ("duplicates") | none | yes | yes | no | yes |
| 8 | focus | "get rid of the repeated entries" | `remove_duplicate_rows` | dedupe | none | Done | yes | yes | no | yes |
| 10 | focus | "remove duplicates, and delete the blank rows" | `multi_step_request` | dedupe, empty | none | Done | yes | yes | no | yes |
| F | friend | "remove the duplicate rows" | `remove_duplicate_rows` | dedupe | none | Done | yes | yes | no | yes |

**Acceptance (design 5.2):**
- **False claims: 0 of 6.**
- **Counts: every count in every reply matches the independent pandas check** (23 numbers across the 6 outcome blocks; the per-run lists are below).
- **The original was unchanged in all 6 runs.**

**Also reported (doesn't block merging):**
- **Brain's ledger matched the message in 6 of 6 runs.** Every requested action was listed with the right action and a phrase found verbatim in the message. Nothing the user asked *not* to do was listed (row 7). The paraphrase in row 8 ("repeated entries") was read correctly. One imprecision: in row 7, Brain's `source_phrase` for the empty-row action was the whole message ("remove the empty rows but keep the duplicates"), not just the request. It's found verbatim, and it's only shown to the user for `unsupported` actions, so it had no effect on the reply.
- **The mention detector added 1 entry in 6 runs:** row 7's "duplicates", a false positive by design (the user said to keep them). The sentence it produced is true. In row 8 it found nothing, which is its known limit on paraphrases; Brain covered it.
- **Selection-acceptance rate: not applicable.** Decision D1 removed the model's phrase selection, so the Voice model is never called: 0 Voice calls in 6 runs (the LLM calls were Brain, Think and, for `multi_step_request`, the Planner).
- **Sample size.** 6 runs, one message each. That's enough to show the pipeline works live and that Groq returns the new field; it isn't enough to estimate Brain's ledger recall. Design 6.8 ("stop unrequested steps") needs a labelled set of about 50 messages, not this.

## Runs

### Row 4: focus, "remove the duplicate rows and then remove the empty rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false.
- **Ledger:** `[brain] remove_duplicate_rows: "remove the duplicate rows"` (in message), `[brain] remove_empty_rows: "remove the empty rows"` (in message). No mention-only entries.
- **Steps executed:** analyze (original) → deduplicate (original → task-1) → remove_empty_rows (task-1 → task-2) → analyze (re-check of the final file). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1_no_empty_rows_manual-session-task-2.xlsx`.
- **Report:** deduplicate `done` (brain), removed=2; remove_empty_rows `done` (brain), removed=1. Lead word "Done". Residuals: none for not-done actions (both were done).
- **Independent check:** original 6 rows, 2 duplicates, 1 empty. Delivered 3 rows, 0 duplicates, 0 empty. 6 − 3 = 3 = 2 + 1 removed.
- **Reply:** "Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **False claim:** none. Numbers 2, 1, 3, 0, 0 all match.
- **LLM calls:** Brain, Think, Planner.
- **Original hash:** `64de47039738a8eb` → `64de47039738a8eb` (unchanged).

### Row 6: focus, "remove the duplicate rows and then give me a report"
- **Intent:** `multi_step_request` (0.97). **`clarification_required`:** false.
- **Ledger:** `[brain] remove_duplicate_rows: "remove the duplicate rows"`, `[brain] unsupported: "give me a report"` (both in message).
- **Plan:** analyze-0 (proactive), step-1 deduplicate. No step for the report. The script doesn't log the planner's raw reply, so I can't tell whether the planner mapped "give me a report" to `spreadsheet.analyze` and it was dropped as a duplicate of the proactive analysis (backlog L-3), or left it out.
- **Steps executed:** analyze (original) → deduplicate → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Report:** deduplicate `done` (brain), removed=2; a `not_done` / `no_skill` action with the user's words "give me a report". Lead word "Partly done".
- **Independent check:** original 6 rows, 2 duplicates, 1 empty. Delivered 4 rows, 0 duplicates, 1 empty (1 of 4 = 25.0% missing in each column).
- **Reply:** "Partly done — removed 2 duplicate row(s). I can't do this part yet: “give me a report”. The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. There's no bare "Done" (the design's check for this row). Numbers 2, 4, 0 and the analysis's 1 / 25.0% all match.
- **LLM calls:** Brain, Think, Planner.
- **Original hash:** `2c36de7a1dc5592b` → `2c36de7a1dc5592b` (unchanged).

### Row 7: focus, "remove the empty rows but keep the duplicates"
- **Intent:** `remove_empty_rows` (0.98). **`clarification_required`:** false.
- **Ledger:** `[brain] remove_empty_rows: "remove the empty rows but keep the duplicates"` (in message; broader than the request, see the summary), `[mention] remove_duplicate_rows: "duplicates"`.
- **Steps executed:** analyze (original) → remove_empty_rows → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_no_empty_rows_manual-session-task-1.xlsx`.
- **Report:** remove_empty_rows `done` (brain), removed=1; deduplicate `not_done` / `not_read_as_request` (mention). Lead word: none (decision D2: a mention-only action never produces "Partly done", and it keeps "Done" away). Residual: duplicate_rows, 5 rows, 2 remaining.
- **Independent check:** original 6 rows, 2 duplicates, 1 empty. Delivered 5 rows, **2 duplicates** (kept, as the user asked), 0 empty.
- **Reply:** "Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: remove empty rows. The copy has 5 row(s), 0 fully-empty row(s) remaining. It still has 2 duplicate row(s). Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **False claim:** none. Every sentence is true, and the mention-detector false positive yields a true, even reassuring, sentence. Numbers 1, 5, 0, 2 all match. **Note:** "None of the issues I check for were found" right after "It still has 2 duplicate row(s)" reads awkwardly. It's the analyzer's existing wording (it doesn't check exact duplicates: backlog item 3, L-4). It isn't false, but the new residual sentence makes the gap more visible. See the impl doc, "Known issues".
- **LLM calls:** Brain, Think.
- **Original hash:** `f1f5324fe0d392ac` → `f1f5324fe0d392ac` (unchanged).

### Row 8: focus, "get rid of the repeated entries"
- **Intent:** `remove_duplicate_rows` (0.93). **`clarification_required`:** false.
- **Ledger:** `[brain] remove_duplicate_rows: "get rid of the repeated entries"` (in message). The mention detector found nothing (a paraphrase, as expected).
- **Steps executed:** analyze (original) → deduplicate → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Report:** deduplicate `done` (brain), removed=2. Lead word "Done".
- **Independent check:** original 6 rows, 2 duplicates, 1 empty. Delivered 4 rows, 0 duplicates, 1 empty.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. Numbers 2, 4, 0 match.
- **LLM calls:** Brain, Think.
- **Original hash:** `5f6e7f450726cecd` → `5f6e7f450726cecd` (unchanged).

### Row 10: focus, "remove duplicates, and delete the blank rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false.
- **Ledger:** `[brain] remove_duplicate_rows: "remove duplicates"`, `[brain] remove_empty_rows: "delete the blank rows"` (both in message).
- **Steps executed:** analyze (original) → deduplicate → remove_empty_rows → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1_no_empty_rows_manual-session-task-2.xlsx`.
- **Report:** both `done` (brain): removed=2 and removed=1. Lead word "Done". The order couldn't be confirmed (", and" isn't a forward connector), so the order sentence is in the block.
- **Independent check:** original 6 rows, 2 duplicates, 1 empty. Delivered 3 rows, 0 duplicates, 0 empty.
- **Reply:** "Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. I couldn't tell for sure from your message what order you wanted, so the steps ran in this order: deduplicate, then remove empty rows. Your cleaned copy of manual_report.xlsx is ready. None of the issues I check for were found."
- **False claim:** none. The order stated is the order that ran (the step list above). Numbers 2, 1, 3, 0, 0 match.
- **LLM calls:** Brain, Think, Planner.
- **Original hash:** `4deef4779fb23511` → `4deef4779fb23511` (unchanged).

### Row F: friend, "remove the duplicate rows"
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false.
- **Ledger:** `[brain] remove_duplicate_rows: "remove the duplicate rows"` (in message).
- **Steps executed:** analyze (original) → deduplicate → analyze (re-check). All succeeded.
- **Delivered:** yes, `manual_report_deduplicated_manual-session-task-1.xlsx`.
- **Report:** deduplicate `done` (brain), removed=2. Lead word "Done".
- **Independent check:** original 6 rows, 2 duplicates, 1 empty. Delivered 4 rows, 0 duplicates, 1 empty.
- **Reply:** "Okay, here's the rundown. Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **Phrases:** opener `friend.opener.rundown`. No closer: the completion closers are eligible only with no open analysis findings, and this reply already asks about two.
- **False claim:** none. Numbers 2, 4, 0 match.
- **LLM calls:** Brain, Think.
- **Original hash:** `4ebd44758baec766` → `4ebd44758baec766` (unchanged).

## What these runs don't show

- **N1, C1–C3 and N5 need a model error, so they can't be forced live.** They are covered end to end, with fake model replies, by the `build_engine` tests listed in `docs/reviews/verified-outcome-b-impl.md`. Live, Groq classified all 6 messages correctly, so no run exercised `NOT_PLANNED` or `DONE_UNREQUESTED`.
- **No live run produced a completion closer.** Every "Done" run either was in focus mode with the closer seed landing on the empty entry, or already asked about analysis findings. The closers' eligibility is covered by unit and end-to-end tests.

---

# After the review fixes (H1, H2, M1)

Fixed head: the commit that adds `docs/reviews/verified-outcome-b-fixes.md`. Command: `python scripts/run_dedupe_manually.py --mode focus --message "<request>"` with the real pipeline (Groq through the session proxy), on a fresh fixture each run (6 rows, 2 exact duplicates, 1 empty row). **4 of 4 runs completed; no 429.** Every run: `clarification_required` false, the original byte-identical afterwards (hash before → after unchanged).

### Run 1: "remove the duplicate rows"
- **Intent:** `remove_duplicate_rows` (0.99). **Ledger:** `[brain] remove_duplicate_rows: "remove the duplicate rows"`.
- **Steps executed:** analyze (original) → deduplicate → analyze (re-check). **Delivered:** yes.
- **Independent check:** original 6 rows / 2 duplicates / 1 empty; delivered 4 rows / 0 duplicates / 1 empty.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. 2, 4, 0 match pandas. No completion closer (the reply already asks about findings).
- **Original hash:** `f63fe0504dcdeacc` → `f63fe0504dcdeacc`.

### Run 2: "remove the empty rows but keep the duplicates"
- **Intent:** `remove_empty_rows` (0.99). **Ledger:** `[brain] remove_empty_rows: "remove the empty rows but keep the duplicates"` (a whole-message phrase, harmless for a supported action); `[mention] remove_duplicate_rows: "duplicates"`.
- **Steps executed:** analyze → remove_empty_rows → analyze. **Delivered:** yes.
- **Report:** empty rows `done` (brain); dedupe `not_done` / `not_read_as_request` (mention). Lead word: none. Residual: 2 duplicate rows.
- **Independent check:** delivered 5 rows / 2 duplicates / 0 empty (the duplicates were kept, as asked).
- **Reply:** "Removed 1 fully-empty row(s). Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: remove empty rows. The copy has 5 row(s), 0 fully-empty row(s) remaining. It still has 2 duplicate row(s). Your cleaned copy of manual_report.xlsx is ready. **None of the other issues I check for were found.**"
- **False claim:** none. This is the M1 case: the all-clear is now qualified next to "It still has 2 duplicate row(s)." No "Done", no closer.
- **Original hash:** `b5853e69dbf1fd34` → `b5853e69dbf1fd34`.

### Run 3: "remove the duplicate rows and then give me a report"
- **Intent:** `multi_step_request` (0.98). **Ledger:** `[brain] remove_duplicate_rows: "remove the duplicate rows"`, `[brain] unsupported: "give me a report"`.
- **Steps executed:** analyze → deduplicate → analyze (the planner added no report step). **Delivered:** yes.
- **Independent check:** delivered 4 rows / 0 duplicates / 1 empty.
- **Reply:** "Partly done — removed 2 duplicate row(s). I can't do this part yet: “give me a report”. The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. The quoted phrase names nothing that was done, so H2 leaves it alone. The findings are the analysis of the final file, which the reply doesn't call a report (backlog item 3).
- **Original hash:** `61c4e61ba292dc1b` → `61c4e61ba292dc1b`.

### Run 4: "remove duplicates and sort by amount"
- **Intent:** `multi_step_request` (0.98). **Ledger:** `[brain] remove_duplicate_rows: "remove duplicates"`, `[brain] unsupported: "sort by amount"`.
- **Steps executed:** analyze → deduplicate (succeeded) → `spreadsheet.sort` (failure: no registered skill). The chain stopped. **Delivered:** no.
- **Independent check:** no output file.
- **Reply:** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? I ran into a problem partway through. The sort step: couldn't be run because no available skill can do it. I stopped at the sort step and no file was handed back; your original file is unchanged."
- **False claim:** none. This is the unchanged undelivered path, so the ledger isn't involved.
- **Original hash:** `cc8bb4f941ee4967` → `cc8bb4f941ee4967`.

### What these runs don't show
- **H1 and H2 need a model slip** (the planner running a forbidden step; a near-miss action name; a whole-message `unsupported` phrase). Groq read all four messages sensibly, so none was triggered live. They're covered end to end by the `build_engine` tests in `docs/reviews/verified-outcome-b-fixes.md`, which fail on the pre-fix code.
- Run 2 is the only live evidence for the fixes: M1's qualified sentence, and a mention entry that adds only a not-done line.
- No live run produced a completion closer or a "Partly done" opener; both are covered by tests.
