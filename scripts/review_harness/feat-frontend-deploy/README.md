# Review harness: `feat/frontend-deploy` (FE-2 and the FE-1 review's fixes)

Reviewer probes, not part of the suite. Run from the repo root. For the base comparison:
`git worktree add --detach ../base origin/main` and pass `--tree ../base` where shown.
Everything reuses `../feat-frontend-demo/` (`fe1.py`, `ui_probe.py`, `browser_probe.py`,
`rule_checks.py`, `mutations.py`) by importing it; nothing there is edited.

| Script | What | Output (`out/`) |
|---|---|---|
| `../feat-frontend-demo/ui_probe.py [--tree DIR]` | PR-3's 25 scenarios x 3 speakers through the page's routes (real pipeline, fake LLM) | `ui_probe_{head,base}.jsonl` |
| `compare_ui.py HEAD BASE` | the two UI probe outputs row by row (catalog lines and ids masked) | `ui_head_vs_base.txt` |
| `../feat-frontend-demo/rule_checks.py FILE...` | rule 1 and 2 checks over the JSONL | (printed: 150 rows, 0 problems) |
| `../feat-frontend-demo/browser_probe.py --speaker S [--tree DIR]` | Chromium: restart under a plan (b01), hostile input, two tabs, the 409 refetch (b06), double click, 360 px | `browser_probe_head_{template,friend,focus}.jsonl`, `browser_probe_base_template.jsonl` |
| `adversarial.py [--tree DIR] [--speaker S] [--only aNN]` | hostile `v`, restart under a plan / a lost answer / a finished turn, static caching edge cases, headers on every kind of response, restart with a password | `adversarial_{head,base}.jsonl` |
| `browser_adversarial.py [--speaker S]` | Chromium: restart during a lost answer (w01), restart then a stale click with no poll armed (w02), restart with a password (w03) | `browser_adversarial_head_template.jsonl` |
| `fixture_parity.py build TREE OUT` / `compare A B` | Part B: the new fixture helpers against the old ones, strict XML trees and the product's reads | `fixture_parity.txt` |
| `mutations.py [--jobs 3]` | 17 mutations of FE-2's guards in `git archive HEAD` copies (about 5 min) | `mutations.txt` |
| `docker_probe.py [--image workbuddy:review]` | the image: startup refusals, contents and permissions, a root-owned volume, a whole flow in production mode with the logs searched for the password and key, health and stop | `docker_probe.json` |

New tests against the base: copy the branch's six changed test files over a `git archive origin/main`
copy and run them (`out/new_tests_on_base.txt`).

Live runs with real Groq against the image (the implementer's script, container mode):

    docker build --secret id=pip_ca,src=/root/.ccr/ca-bundle.crt -t workbuddy:review .
    python scripts/live_frontend_runs.py --container workbuddy:review --runs 1,2,3,4 \
        --shots scripts/review_harness/feat-frontend-deploy/out/live_shots \
        --json scripts/review_harness/feat-frontend-deploy/out/live_runs_container.json

Suite and lint summary: `out/suite_and_lint.txt`.

## Round 2 (commit aa98844)

Everything above was rerun on head where named; outputs carry `_r2`. Images: build your own from
the head (`docker build ... -t workbuddy:r2rev .`), and pass it with `--image`.

| Script | What | Output (`out/`) |
|---|---|---|
| `mutations_r2.py [--jobs 3]` | 14 mutations of the round-1 fixes (`held`, static version, startup message, middleware) | `mutations_r2.txt` |
| `adversarial_r2.py [--speaker S]` | HTTP: hostile `held`; restart then the lookup / the 409 refetch with `held`; no-restart 409 refetch with the file read; `held` on others' sessions | `adversarial_r2_head.jsonl` |
| `browser_adversarial_r2.py --speaker S` | Chromium: w04 restart + lost answer, no poll; w05 answer lost on the way back (no restart); w06 409 with no poll; w07 restart + refetch lost | `browser_adversarial_r2_{S}.jsonl` |
| `browser_adversarial.py --speaker S` (round 1) rerun | w01-w03 on head (w02 was round 1's L3) | `browser_adversarial_r2run_{S}.jsonl` |
| `docker_probe_r2.py --image IMG` | k03 root-owned `/data`, k06 `:ro` mount, k07 `/data` a file (Docker refuses the mount; not the app), k08 root-owned subfolders | `docker_probe_r2.json` |
| `r2_extra.py s01 [--rev REV]` | L5 end to end: edit app.js under a running dev app; the page's `?v=` and both URLs' caching | `r2_extra_s01.jsonl` (HEAD and 0f0a09c) |
| `r2_extra.py w08 --speaker S` | production-like restart under a plan; the 401 notice's link, the password, then what the page shows | `r2_extra_w08.jsonl` |
| `r2_extra.py k03b --image IMG` | root-owned `/data`, waits for the exit | `r2_extra_k03b.jsonl` |
| `read_live_r2.py out/live_runs_container_r2.json` | the live runs' delivered files read with openpyxl | `live_runs_container_r2_openpyxl.txt` |

New tests on the round-1 code: the six changed test files copied over `git archive 0f0a09c`
(`out/new_tests_on_0f0a09c.txt`). Live runs: as above, with `--container workbuddy:r2rev` and
`_r2` output names. Suite and lint: `out/suite_and_lint_r2.txt`.
