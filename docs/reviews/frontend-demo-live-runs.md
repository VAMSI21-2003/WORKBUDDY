# feat/frontend-demo (FE-1): live runs with Groq, through the browser

Design `docs/design/frontend-design.md`, Section 10.4. Final head: the product code is the commit the independent reviewer approved in round 1 (`6522a21`, verdict MERGE); only documents, review files and scripts came after it. Driver: `scripts/live_frontend_runs.py`: it starts the real server (`python -m backend --port N`, the real pipeline on Groq, the key attached by the proxy and never read or printed), with a demo password, the production rate limits and a fresh data root, and drives Chromium (1100×820) the way a user would. Every run uses a fresh browser context, so its own gate, cookies and session. The fixture is the standard one (`manual_report.xlsx`: 6 rows, 2 exact duplicates, 1 empty row).

**Result: 4 of 4 runs completed. No Groq 429 anywhere in the server log (checked after every run). No false claim in any reply. The original's SHA-256 is unchanged in every run. In every run the reply the page shows is byte-identical to the API's `reply` for the same turn, the page showed no page notice, no HTTP status of 400 or more, and nothing in `localStorage` or `sessionStorage`.** The password never appears in the server log. Screenshots of each finished turn, and one of the gate and one of the plan card, are in `docs/reviews/frontend-demo-screens/`.

Run 2 was run twice. In the first pass the browser console showed two "Refused to apply inline style" messages from the page's Content-Security-Policy. I checked where they came from: they are from Playwright's own screenshot (its default options add inline styles; with `caret="initial", animations="allow"` the same page shows no `style` attribute and no message, and the page has none before a screenshot). The driver now uses those options, and run 2 was repeated with real Groq; the table gives the repeat. (The first pass of run 2 had the same steps, the same reply, the same file and the same checks; its only difference was those two console lines.) Run 1 used `http://localhost:<port>`, which also exercises the L5 fix.

## What each run did

| | Run 1 | Run 2 | Run 3 | Run 4 |
|---|---|---|---|---|
| Request | "remove duplicate rows" | "remove duplicate rows, then remove empty rows" | "remove duplicate rows" | "sort the rows by amount" |
| Answer to the plan | approve | approve | **reject** | (no plan shown) |
| Plan shown | 1 step: "Remove duplicate rows from manual_report.xlsx." | 2 steps in order, with the plan note | 1 step | none |
| Intent | `remove_duplicate_rows` | `multi_step_request` | `remove_duplicate_rows` | `unclear` |
| `clarification_required` | false | false | false | false |
| Outcome, reply source | `delivered`, `pipeline` | `delivered`, `pipeline` | `not_delivered`, `pipeline` | `not_delivered`, `pipeline` |
| Plan decision (server) | {"decision": "approve", "decision_source": "user"} | {"decision": "approve", "decision_source": "user"} | {"decision": "reject", "decision_source": "user"} | none |
| Steps executed | analyze [success, verified=True]<br>deduplicate [success, verified=True]<br>analyze (re-check) [success, verified=True] | analyze [success, verified=True]<br>deduplicate [success, verified=True]<br>remove_empty_rows [success, verified=True]<br>analyze (re-check) [success, verified=True] | analyze [success, verified=True]<br>deduplicate [declined, verified=False] | analyze [success, verified=True] |
| Stopped at | None | None | spreadsheet.deduplicate | None |
| File delivered? | yes, 1 | yes, 1 | no | no |
| Wall time (s) | 8.8 | 10.9 | 6.7 | 4.4 |

## Independent checks

| | Run 1 | Run 2 | Run 3 | Run 4 |
|---|---|---|---|---|
| Original, measured | 6 rows, 2 duplicates, 1 empty | same | same | same |
| Delivered file, read with pandas | 4 rows, 0 dup., 1 empty | 3 rows, 0 dup., 0 empty | no file | no file |
| Equals the expected result (pandas) | dedupe: true | dedupe then empty rows: true | no file | no file |
| Downloaded through the page's link, SHA-256 equals the API's record | true | true | no file | no file |
| Download links on the page (turn, sidebar) | 1, 1 | 1, 1 | 0, 0 | 0, 0 |
| Page reply == API `reply` | true | true | true | true |
| Original SHA-256 before (fixture, stored upload) and after | `ef431ecbf251…` = `ef431ecbf251…`; after `ef431ecbf251…` (unchanged) | `487a74e51f90…` = `487a74e51f90…`; after `487a74e51f90…` (unchanged) | `ef431ecbf251…` = `ef431ecbf251…`; after `ef431ecbf251…` (unchanged) | `ef431ecbf251…` = `ef431ecbf251…`; after `ef431ecbf251…` (unchanged) |
| Page notices; HTTP statuses ≥ 400; console errors | 0; 0; 0 | 0; 0; 0 | 0; 0; 0 | 0; 0; 0 |

(Runs 1, 3 and 4 uploaded the same fixture bytes; run 2 is the repeat, with a fixture built afresh, so its hash differs. Within a run, the local fixture, the stored upload before the turn and the stored upload after it are the same bytes.)

## The replies, as the page shows them, and whether any claims something that did not happen

**Run 1** (`remove duplicate rows`)

> Okay, here's the rundown. Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?

No false claim. Removed 2 duplicates and the copy has 4 rows: measured 6 rows, 2 duplicates, so 4 rows and 0 duplicates; the empty row is still there, and the reply doesn't say otherwise. "1 missing value(s) (25.0%)" in each column is the empty row among 4. The values-only sentence is the pipeline's standing caveat.

**Run 2** (`remove duplicate rows, then remove empty rows`)

> Okay, here's the rundown. Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. None of the issues I check for were found. Anything else you want me to look at?

No false claim. 2 duplicates and 1 empty row removed, 3 rows left, none duplicate or empty: all as measured. The reply says the delivered copy is "of manual_report.xlsx", as the pipeline always does.

**Run 3** (`remove duplicate rows`)

> Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? The deduplicate step: wasn't confirmed, so it didn't run. I stopped at the deduplicate step and no file was handed back; your original file is unchanged.

No false claim. The plan was rejected; the reply says the step "wasn't confirmed, so it didn't run", that no file was handed back and that the original is unchanged: all true (the page showed no file, the sidebar none, the API `delivered_files` empty, the hash unchanged). 1 missing value in 6 rows is 16.7%.

**Run 4** (`sort the rows by amount`)

> Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn.

No false claim. No plan was shown and no step changed a file, and the reply says "No changes were made to any file this turn." It does not say that sorting isn't supported, only what the analysis found and that nothing was changed; that is the pipeline's own wording for an `unclear` intent (the backlog already lists the pattern), not the page's.

## What was not run

- Runs on a phone-sized screen or in other browsers (the browser tests cover 360×640 in Chromium with a fake LLM).
- More than one speaker: the page was used in its default mode (friend); the review's browser probes covered all three with a fake LLM.
- A reverse proxy and HTTPS.

Raw results: `scratch/` is git-ignored; the numbers above are copied from the driver's output (`scripts/live_frontend_runs.py --json`).
