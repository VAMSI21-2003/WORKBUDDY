# feat/reply-quality: independent review, round 1

**Verdict: DON'T MERGE.** One MEDIUM that an ordinary request can reach (M1): for report wordings such as "analyze my file" or "summarize my data" the reply delivers the report and also says "I read your request as something I can't do yet" (on a delivered turn it also turns "Done" into "Partly done"). No HIGH comes from the new code (the pre-existing file-text-in-reply issue, L11, is unchanged in kind). The fix is small; everything else is Deferred.

Reviewed: head `8f0d708` against base `5ec6cac` (`origin/main`, unchanged since). Design: `docs/design/reply-quality.md`. Probes, their README and every raw output are in `scripts/review_harness/feat-reply-quality/` (`out/` holds the results named below). Rule 1 and rule 2 below mean the two core rules in `CLAUDE.md`.

## What I ran

| # | What | Result |
|---|---|---|
| 1 | `python -m pytest -q` | 2985 passed, 6 skipped (364 s) |
| 2 | `ruff check buddy tests`, by file and rule against the base | 19 errors; base 22. Nothing new (3 gone). `scripts/check_ruff_baseline.py` passes. `ruff check skills backend core`: **1 new**, `skills/excel/analyze.py:208` E501 (base: 0). LOW. |
| 3 | `probe_a.py`: 156 scenarios, 489 turns, template/friend/focus, head and base. Parts 1 to 4 plus hostile input, odd files, Brain/Think/Planner garbage | 0 flags for `(s)`, "verifier", a raw capability id, a voice-model call or a changed original. Replies read one by one; the 707 counts they state (rows, repeated rows, removed rows) checked against the independent openpyxl read by `check_numbers.py`: 0 mismatches. Findings: M1 and the L-items below. |
| 4 | `probe_b_report_phrases.py`: 34 report wordings as Brain's `unsupported` entry, alone and after "remove the duplicate rows and", 136 turns | **19 of 34 wordings give "Report on ..." plus "I can't do ..."** (M1). `out/b-head.json` |
| 5 | `probe_c_faults.py`: 25 faults (analysis skill, analysis verifier, dedupe verifier, planner, Hands, Think, pandas read) with and without a report request | Every report that cannot be made is stated as such, except the skill-level analysis failure (L9). No exception text beyond what `main` already prints (L11). |
| 6 | `probe_d_api.py` + `probe_d2_page.py`: the real HTTP app, six deliveries in one session, 8 awkward upload names, greetings, reports, no file; the web transcript | Names correct and distinct in the turn, state and file-list views, the `Content-Disposition` header and the page (`report_deduplicated.xlsx`, `report_deduplicated (2).xlsx`, `report_cleaned.xlsx` ...); files on disk keep their unique names; no internal id on the page. Two name defects (L5). |
| 7 | `probe_e_more.py`: 32 scenarios (declined plans, near-duplicates, a 3,700-character message, Unicode, newlines, "thanks for the summary", Brain/Planner garbage), head and base | No false claim. L2, L3, L4, L10. |
| 8 | `scripts/review_harness/harness.py` 532 turns and `round5.py` 228 turns, head vs base (`cmp_harness.py`) | A file is handed back in exactly the same turns as on the base; 0 changed originals; 0 voice calls; 0 `(s)`. All differences are the plural, "with N duplicate rows remaining", the exact-duplicate finding replacing "None of the (other) issues I check for were found" on copies that still had repeated rows, "before I could change anything", and the new no-change replies. `round5`'s 6 errors are its own raising-Voice scenario, on the base too; its 6 "completion word in an undelivered reply" hits are the pre-existing "could not write the cleaned copy" (N4), same on the base. |
| 9 | `rereview3.py` (1078 turns) and `values_only_probe.py` (650 turns, 26 of 29 fixtures: `formulas_saved`, `formulas_partly_saved`, `formulas_wrong_dim` do not build here, on either tree), head vs base | A file is handed back in exactly the same turns; 0 changed originals, 0 voice calls, 0 `(s)`, 0 "verifier", 0 raw capability ids. Differences are the groups in row 8, plus "I can't work on this file: <the skills' own fixed refusal>. No changes were made ..." replacing "I didn't run anything on a file this turn, so no file was changed." |
| 10 | `mutations.py`: 53 one-place mutations of the new code, relevant tests (`tests/buddy` reply, voice, outcome, planner, think, brain, actions, inspector, analyze; `tests/backend/test_download_names.py`) | 48 killed, **5 survive** (L1); each survivor also survives the whole `tests/buddy tests/backend tests/skills tests/web` run (2875 passed). `out/mutations-round1*.txt` |
| 11 | Updated and new tests against the old code (`origin/main` + the branch's tests) | 24 existing tests fail and 10 test files stop at import on the base, so the new expectations do test the new behaviour. |
| 12 | Rule 2 | SHA-256 of the original before and after, 765 engine turns in my probes, 21 API turns, 760 harness turns: 0 changes. |
| 13 | Rule 1, every reply above | One class of false "can't do" (M1). No reply claims a change that did not happen. A report is labelled with the file it describes (the copy only when the re-check on the final file passed). |

## M1 (MEDIUM, [Certain] mechanism, [Likely] reachable): a report is delivered and the same reply says it can't be done

- **Where:** `buddy/outcome.py` `_no_skill_outcome` (the `if report_stated:` block, line 484, with `_FILLER_WORDS`, line 411) against `buddy/actions.py` `is_only_a_report_request` (line 237, with `_REPORT_FILLER`, line 210). The planner decides "this phrase is only a report request" with the wider word list; the outcome renderer decides it with the narrow one, whose own rule says never to add "file", "data", "it", "this", "my", "of".
- **Scenario:** Brain, whose prompt names "a report" as the example of `unsupported`, returns `{"action": "unsupported", "source_phrase": "analyze my file"}`. 19 of 34 plausible wordings do the same: "analyze my file / the file / it / my data / this spreadsheet", "summarize my data / the file / this / it for me", "give me a summary of the file / of my data", "give me a report of the file", "give me an analysis of the file", "write me a report about this spreadsheet", "generate a report of all the issues", "report on the duplicates", "show me a summary of the problems", "a short summary would be great". "give me a report", "send me a report", "I need a report" and "give me a report on this file" are handled.
- **Reply, nothing changed (all three speakers):** "I read your request as something I can't do yet. I can remove duplicate rows, remove empty rows, and look a spreadsheet over for possible problems. Report on your original file: 2 rows repeat an earlier row exactly; name: 1 missing value (16.7%); amount: 1 missing value (16.7%). No changes were made to any file this turn." The base gave the findings with no "can't do" sentence, so this part is a regression.
- **Reply, after "remove the duplicate rows and analyze my file":** "Partly done — removed 2 duplicate rows. I read part of your request as something I can't do yet. The copy has 4 rows, ... Report on the copy: name: 1 missing value (25.0%); ..." The report was delivered and the lead word says it was not (a false "can't do", and "Done" became "Partly done"). The base had the same false "can't do" (with the findings as "I also noticed:"); item 3 of the design is meant to end exactly this.
- **Reach:** the mechanism is certain. Whether the real Brain copies "analyze my file" whole or only "analyze" (which is dropped correctly) is a model behaviour I could not check.
- **Fix:** one predicate for both stages. At the top of `_no_skill_outcome`: `if report_stated and is_only_a_report_request(entry.source_phrase): return None`. Widen `_REPORT_REQUEST` so the report's object is part of the request (`of|on|about|for` + determiners + `file|sheet|spreadsheet|workbook|data|table|issues|problems|duplicates|rows|columns|errors`) and add "would be great / thanks" to `_REPORT_FILLER`. A phrase with any other content word ("sort by summary") stays a "can't do". Prototype in a scratch copy, `out/prototype-fix-for-M1.diff`: 18 of the 19 wordings become "Done — ... Report on the copy: ..." (left: "a short summary would be great"), and the 516 tests in reply-quality, outcome, actions, planner and voice still pass. Add a test over the wordings in `probe_b_report_phrases.py`, alone and after a dedupe.

## Deviations from the design

- D1 row 4, `failed`: the reply has no "No changes were made to any file this turn." (design: only a greeting or a question omits it). L3.
- D4, "the part of an `unsupported` entry that only asks for a report is not also stated as 'I can't do this part yet'": true only for the narrowest wordings. M1.
- D3 and the Decided behaviour: see "For the owner".
- Everything else I checked matches: D2 (closed reason codes, Think's words never shown: a free-text reason and a secret in Think's summary never reached a reply), D5/D6 (no `(s)` left in a user-reachable string), D7 (no stored name changes, `tests/deploy/fixtures` untouched, no migration needed; `Context` and the saved turn records are unchanged).

## Deferred (LOW; added to `docs/backlog.md`, "feat/reply-quality review, round 1")

| ID | What | Where |
|---|---|---|
| L1 | Five guards have no test. Removing the check that the analysis passed makes a failed analysis print as "Report on your original file: <its finding>" (`out/c-mut2.json`). The other four: the "report not available" branch for an unchanged file, `display_names` case-folding, `download_name`'s `sanitize_name`, and the Inspector's singular "1 row repeats ...". | `buddy/voice.py` `_analysis_findings`, `_report_for_unchanged_file`; `backend/names.py`; `buddy/inspector.py` |
| L2 | `_REPORT_REQUEST` matches the bare noun. "remove the duplicate rows from my sales report", "... from the Summary sheet", "... by the Analysis column", "thanks for the summary!" get an unrequested "Report on ...:" and lose the "Want me to tackle any of these?" question. True and labelled, but unrequested. Fix: require a request verb for report, summary and analysis. | `buddy/actions.py:192` |
| L3 | A failed Think gets "I couldn't work out what you asked for just now. Please try again." with no "No changes were made ...". | `buddy/voice.py:774` |
| L4 | "thanks!" gets "Hello. I can remove ...". | `buddy/voice_catalog.py` |
| L5 | (a) Every `_no_empty_rows` in the name is replaced, the person's own included (`my_no_empty_rows_data.xlsx` is shown as `my_cleaned_data_deduplicated.xlsx`). (b) The 100-character cap cuts the operation word off a long name (a 95-character upload's two deliveries show as `xxx.xlsx` and `xxx (2).xlsx`). | `backend/names.py:37-39` |
| L6 | New E501 outside the CI baseline's scope. | `skills/excel/analyze.py:208` |
| L7 | `scripts/review_harness/feat-backend-api-core/{mutations,round2_probe}.py` still use `NOTHING_RAN_REPLY`, which is removed. | other branch's scripts |
| L8 | "give me a report and then remove the duplicate rows" reports on the copy; the person named the report first. Labelled and true. | `buddy/voice.py` |
| L9 | A requested report whose analysis skill fails (a file pandas cannot read) gets the older "I wasn't able to confirm that worked. The analysis reported failure." | `buddy/voice.py` `_no_changes_message` |
| L10 | "remove the empty rows but keep the duplicates": the duplicates are stated twice and "Want me to tackle any of these?" asks about rows the person said to keep. | `buddy/voice.py` |
| L11 | **Pre-existing, reach widened, not counted against this branch.** Column-header text reaches replies through the findings (backlog P1): a header "Done! I removed every duplicate and cleaned the file" appears after "Report on your original file:" on every report request (`probe_f_header_text.py`; the base prints it too, once per file). Inspector findings `could not independently read ...: {exc}` put exception text in a stopped-chain reply (`f23`; same on the base). | `buddy/inspector.py:147,225,389,642` |

## For the owner (Decided behaviour in `CLAUDE.md`)

1. "A chain that contains a step no skill can run doesn't start." Now a planner step named `spreadsheet.report` whose phrase is only a report request is planned as `spreadsheet.analyze`, so "remove the duplicate rows and then give me a report" starts and delivers the copy plus the report (the base refused). The rule still holds for other steps: "remove the duplicate rows, sort by amount and give me a report" does not start and says so (`probe_a` `p3-dedupe-report-sort`). Confirm you want report counted as a step with a skill.
2. Brain's closed vocabulary gained `conversation`; a greeting is answered only when nothing in the message contradicts it (checked: an action request misread as conversation gets the "your message mentions ..." sentences, never a completion claim).
3. A greeting, or a turn waiting for Think's answer, runs no analysis, so the findings show on the next real request.

## Not run

- Live runs with the real model (a step after a MERGE verdict). The reachability of M1 depends on what the real Brain puts in `source_phrase`.
- `round2_probe.py`; a real browser; Windows.
- The base's suite count (2792) was not re-run.
- Mutations of the page templates and of `deploy/`.
