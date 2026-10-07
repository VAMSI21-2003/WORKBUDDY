# Live runs through the browser, against the Docker image: `feat/frontend-deploy`

Real Groq, the four runs of `docs/design/frontend-design.md` 10.4, driven in Chromium by `scripts/live_frontend_runs.py --container workbuddy:fe2`. The page under test is the **image**, not a source checkout: `WORKBUDDY_ENV=production`, an https public origin of `https://localhost:<port>`, the design's real rate limits, one worker, a bind-mounted data folder, run as uid 10001. A TLS relay (`scripts/tls_relay.py`, a throw-away certificate, no HTTP parsing) stands in front as a host's proxy would, and the browser ignores the certificate error. The container ran on the host's network, because this sandbox reaches Groq only through its egress proxy. The key reached the container as an environment variable passed through from this environment; I never read or printed it.

**Round 1 of the review loop, commit `0c132fa`** (the image was built from it). The post-MERGE repeat on the final head replaces this file, as the task asks. Raw results: `frontend-deploy-live-runs.json`. Screenshots: `frontend-deploy-screens/` (gate, plan card, each finished turn).

**Result: 4 of 4 runs completed. No Groq 429** (the check is any HTTP 429 from the provider in the container's log, even one the SDK retried). The access password is not in the container's log. No page notice, no console error and no HTTP status of 400 or more in any run; nothing in `localStorage` or `sessionStorage` in any run. In every run the reply the page shows equals the API's `reply` for the same turn, byte for byte. In every run the original's SHA-256 is the same before and after (`a5c0bace…1379`, the local fixture, the stored upload before, and the stored upload after).

The fixture is `manual_report.xlsx`: 6 rows, 2 exact duplicates, 1 fully empty row (measured by pandas: 6 / 2 / 1).

## Run 1: "remove duplicate rows"

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** False.
- **Plan card:** Remove duplicate rows from manual_report.xlsx.. **Answer sent:** approve.
- **Steps executed:**
  - spreadsheet.analyze [success, verified=True] manual_report.xlsx -> -
  - spreadsheet.deduplicate [success, verified=True] manual_report.xlsx -> manual_report_deduplicated_c3e26c3b15e8d81c4b45d8449b846171-task-1.xlsx
  - spreadsheet.analyze (re-check) [success, verified=True] manual_report_deduplicated_c3e26c3b15e8d81c4b45d8449b846171-task-1.xlsx -> -
  - stopped at: nothing.
- **Outcome:** `delivered` (pipeline). **Wall time:** 8.8 s.
- **File delivered:** yes: `manual_report_deduplicated_c3e26c3b15e8d81c4b45d8449b846171-task-1.xlsx`.
- **Independent check of the file:** downloaded through the page's link, SHA-256 equals the recorded one (True); read with pandas: 4 rows, 0 duplicate rows, 1 empty rows, equal to what pandas computes from the original (equals_expected_dedupe = True); links on the turn 1, in the Files panel 1.
- **Reply (the page's text equals the API's: True):** "That sheet was seeing double. Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** No. "removed 2 duplicate row(s)" (6 rows, 2 duplicates, so 4 left, as pandas measures in the delivered file); "4 row(s), 0 duplicate row(s) remaining" (measured 4 rows, 0 duplicates); the empty row is still in the copy and the reply doesn't say otherwise; "the copy has the values only…" is true of the file; "name: 1 missing value(s) (25.0%)" is 1 of the copy's 4 rows.
- **Original's SHA-256, before and after:** `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` and `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` (unchanged).

## Run 2: "remove duplicate rows, then remove empty rows"

- **Intent:** `multi_step_request`. **`clarification_required`:** False.
- **Plan card:** Remove duplicate rows from manual_report.xlsx.; Then remove empty rows from the result of step 1. (note: "The steps run in this order, each on the previous step's result. Only the final file is handed back."). **Answer sent:** approve.
- **Steps executed:**
  - spreadsheet.analyze [success, verified=True] manual_report.xlsx -> -
  - spreadsheet.deduplicate [success, verified=True] manual_report.xlsx -> manual_report_deduplicated_84804f26840681cc19bb7a78af5878ee-task-1.xlsx
  - spreadsheet.remove_empty_rows [success, verified=True] manual_report_deduplicated_84804f26840681cc19bb7a78af5878ee-task-1.xlsx -> manual_report_deduplicated_84804f26840681cc19bb7a78af5878ee-task-1_no_empty_rows_84804f26840681cc19bb7a78af5878ee-task-2.xlsx
  - spreadsheet.analyze (re-check) [success, verified=True] manual_report_deduplicated_84804f26840681cc19bb7a78af5878ee-task-1_no_empty_rows_84804f26840681cc19bb7a78af5878ee-task-2.xlsx -> -
  - stopped at: nothing.
- **Outcome:** `delivered` (pipeline). **Wall time:** 6.8 s.
- **File delivered:** yes: `manual_report_deduplicated_84804f26840681cc19bb7a78af5878ee-task-1_no_empty_rows_84804f26840681cc19bb7a78af5878ee-task-2.xlsx`.
- **Independent check of the file:** downloaded through the page's link, SHA-256 equals the recorded one (True); read with pandas: 3 rows, 0 duplicate rows, 0 empty rows, equal to what pandas computes from the original (equals_expected_chain = True); links on the turn 1, in the Files panel 1.
- **Reply (the page's text equals the API's: True):** "Those blank rows were just taking up space. Done — removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. The copy has the values only; formatting, column widths and other sheet settings aren't carried over. None of the issues I check for were found."
- **False claim:** No. "removed 2 duplicate row(s). Removed 1 fully-empty row(s)" and "3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining" match the delivered file as pandas measures it (3 / 0 / 0). The plan card listed the two steps in order with the chain note, and step 2 read step 1's output (the file names show it).
- **Original's SHA-256, before and after:** `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` and `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` (unchanged).

## Run 3: "remove duplicate rows" (rejected)

- **Intent:** `remove_duplicate_rows`. **`clarification_required`:** False.
- **Plan card:** Remove duplicate rows from manual_report.xlsx.. **Answer sent:** reject.
- **Steps executed:**
  - spreadsheet.analyze [success, verified=True] manual_report.xlsx -> -
  - spreadsheet.deduplicate [declined, verified=False] manual_report.xlsx -> -
  - stopped at: spreadsheet.deduplicate.
- **Outcome:** `not_delivered` (pipeline). **Wall time:** 6.7 s.
- **File delivered:** no.
- **Independent check of the file:** no file delivered, and the page shows no download link (links: 0 on the turn, 0 in the Files panel).
- **Reply (the page's text equals the API's: True):** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? The deduplicate step: wasn't confirmed, so it didn't run. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- **False claim:** No. "wasn't confirmed, so it didn't run", "no file was handed back" and "your original file is unchanged" are all true (no download link on the page, nothing delivered, hash unchanged). The 16.7% is 1 missing value in the original's 6 rows.
- **Original's SHA-256, before and after:** `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` and `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` (unchanged).

## Run 4: "sort the rows by amount"

- **Intent:** `unclear`. **`clarification_required`:** False.
- **Plan card:** none shown. **Answer sent:** none (no card).
- **Steps executed:**
  - spreadsheet.analyze [success, verified=True] manual_report.xlsx -> -
  - stopped at: nothing.
- **Outcome:** `not_delivered` (pipeline). **Wall time:** 4.4 s.
- **File delivered:** no.
- **Independent check of the file:** no file delivered, and the page shows no download link (links: 0 on the turn, 0 in the Files panel).
- **Reply (the page's text equals the API's: True):** "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? No changes were made to any file this turn."
- **False claim:** No. "No changes were made to any file this turn." is true (hash unchanged, nothing delivered), and the reply doesn't claim to have sorted. The pipeline read it as `unclear`, with no plan shown.
- **Original's SHA-256, before and after:** `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` and `a5c0bace32b6f0d6d31ab73ae8394647f82311a5d4a908cfaf05a7e5df7c1379` (unchanged).

## The values-only probes, with and without lxml

Not browser runs; fake LLM, the real pipeline. 725 turns of `scripts/review_harness/values_only_probe.py` and 400 of `round2_probe.py`, run with lxml uninstalled and, through `scripts/probe_lxml_parity.py`, with lxml installed and used: 0 fields differ in 1,125 of 1,125 runs; the original changed in 0 runs. Details, and why a wrapper was needed: `docs/reviews/frontend-deploy-impl.md` (Part B) and `docs/reviews/frontend-deploy-lxml-parity/`.

## The image

Packages the image resolved at build time: `docs/reviews/frontend-deploy-image-freeze.txt` (pandas 2.3.3, ydata-profiling 4.18.4, splink 4.0.17; no lxml). Memory and time of the same flow under 512 MB, 1 GB and 2 GB limits: `docs/deploy.md`, Section 6.

## Not run

Browsers other than Chromium; a real host, so `WORKBUDDY_TRUSTED_PROXY_HOPS` behind a real proxy and a real certificate; run 1's `localhost` against `127.0.0.1` split of FE-1 (the container runs behind one https origin); pandas 3.x.
