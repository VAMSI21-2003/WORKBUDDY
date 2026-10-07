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
