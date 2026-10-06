# Review harness: `feat/frontend-demo` (FE-1, the web page)

Reviewer probes for the page (`backend/web`). They run the real app (`backend.main.create_app`) and the real pipeline (`build_engine`: real skills, Hands, Inspector, verifiers, Voice) with only the LLM faked. The fake LLM, fixtures and openpyxl check come from `../feat-backend-api-core/harness.py`, the recording factory and the 25 ordinary scenarios from `../feat-backend-api-confirmation/` (`pr3.py`, `api_probe.py`). All imported, not edited. Not part of the test suite.

Run from the repo root. For the base comparison make a worktree first: `git worktree add --detach ../base origin/main`. `--tree DIR` (handled by `pr3.py`) runs a probe against another checkout, for example a copy with `out/proto_fix.diff` applied.

- `fe1.py`: shared pieces. `PageApp` (the app over `TestClient` on https, relaxed buckets unless given), `drive_page()` (composer POST, polls with the version the page holds, answers the plan card's form), a stdlib HTML tree, `visible_text()`, `check_downloads()` (fetches each link, sha256 against the API's `delivered_files`, openpyxl read).
- `ui_probe.py [--speaker S] [--only NAME]`: PR-3's 25 scenarios x 3 speakers through the page's routes. Per row: the reply slot vs the JSON `reply` vs `GET /` vs the live region; links only when delivered; every link fetched and read; the text outside the reply slot; the plan card; the original's SHA-256.
- `compare_page_vs_base.py PAGE.jsonl API.jsonl`: the page's replies, outcomes, plan text and delivered names against the JSON API's on another tree (catalog openers and closers stripped). `API.jsonl` is `../feat-backend-retention/old_probe.py api_probe.py [--tree ../base]`.
- `adversarial.py [--speaker S] [--only NAME]`: 16 HTTP cases: hostile file name, message and column names; odd paths and parameters; the pointer cookie; cross-session fragments; answer races and `unconfirmed=`; a busy session; a model that raises halfway; a misread request; the active file after a delivery; a server restart under a page that holds a version (c12); production cookie and Origin; the session quota message; a plan nobody answers; the "new session" notice when the new session can't be made (c16).
- `browser_probe.py [--speaker S] [--only NAME]`: Chromium (`/opt/pw-browsers`) against uvicorn: b01 restart while a plan waits (dev), b02 hostile name and message, b03 two tabs answer the same plan, b06 the refetch after a `409` on the plan card (poll in flight / idle), b04 double-click Approve, b05 a long name at 360 px.
- `rule_checks.py FILE...`: rule 2 and rule 1 over the JSONL outputs (reply equals API, links only when delivered, the reply's counts against the openpyxl read, no completion words when nothing was delivered or outside the reply).
- `mutations.py [--jobs 3] [--only NAME]`: 26 one-line mutations of the page's guards in `git archive HEAD` copies; Python ones run `tests/web` and the two new backend test files, `app.js` ones run the named browser tests. About 25 minutes with 3 jobs.

Round 1 outputs (`out/`):
- `ui_probe_head.jsonl`; `api_probe_head.jsonl`, `api_probe_base.jsonl`, `api_probe_head_vs_base.txt`; `page_head_vs_api_base.txt`
- `adversarial_head.jsonl`
- `browser_probe_{template,friend,focus}.jsonl`, `browser_probe_protofix_template.jsonl` (with `proto_fix.diff` applied)
- `mutations.txt`, `proto_fix.diff`, `rule_checks.txt`, `suite_and_lint.txt`
