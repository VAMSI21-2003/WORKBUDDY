# Review harness: `feat/backend-api-confirmation` (PR-3)

Reviewer probes for the plan confirmation. They run the real app (`backend.main.create_app`) and the real pipeline (`build_engine`: real skills, Hands, Inspector, verifiers, Voice) with only the LLM faked, except `live_plan_check.py`. Not part of the test suite. The fake LLM, fixtures and independent openpyxl check come from `../feat-backend-api-core/harness.py` (imported, not edited).

Run from the repo root. `--tree DIR` runs a probe against another checkout (for example `git worktree add --detach ../base origin/main`); on a tree without PR-3, "approve" maps to `auto_approve=True` and "reject"/"timeout" to `auto_approve=False`.

- `pr3.py`: shared pieces (an app on a temp data root, a factory that records every run, `drive()` that polls a turn and answers each plan, `run_turn()` that reports plans, answers, outcome, reply, `detail.confirmation`, an independent read of each delivered file, every spreadsheet written in the turn folder, and the original's SHA-256 before and after).
- `api_probe.py [--tree DIR] [--speaker S] [--only NAME]`: 25 ordinary turns x 3 speakers (approve, reject, timeout, chains, unsupported steps, misread intent, hostile file name, a wait longer than the deadline). One JSON line each. Scenarios 19, 21 and 25 show review finding M1.
- `adversarial_probe.py [--speaker S] [--only NAME] [--trials N]`: races (answer vs timeout, approve vs reject), stale ids across turns, hostile bodies, shutdown while waiting and before the plan is asked, the deadline after an approval, a step that resolves to another tier after approval, a stage raising after the answer, an 8-step chain, the session view while waiting, `file_id`, settings from the environment. PR-3 trees only.
- `decline_reply_probe.py [--tree DIR]`: the Voice change at the pipeline level (no API), with a provider that answers per step; compare this branch with `origin/main`.
- `live_plan_check.py [--decision approve|reject] [--message TEXT]`: one turn with the real model from the environment (never printed), in-process; prints the plan shown and what ran. Three LLM calls per turn (Brain, Think, the planner).
- `mutations.py [--jobs 4] [--only NAME] [--root DIR]`: 63 one-line mutations of the new guards, each in a throwaway copy, running `tests/backend` and the Hands, Voice and engine tests. "SURVIVED" means no test noticed. With 4 jobs a run takes about an hour.
- `new_tests_on_base.py BASE_TREE`: this branch's new and changed test files run against the base code.

Round 1 outputs are in `out/`:
- `api_probe_head.jsonl`, `api_probe_base.jsonl`, `api_probe_head_vs_base.txt`
- `adversarial_{template,friend,focus}.jsonl`; `adversarial_template_hostile.jsonl` is the full hostile-bodies run (the first template run stopped at the first accepted body)
- `decline_reply_head.jsonl`, `decline_reply_base.jsonl`, `decline_reply_head_vs_base.txt`
- `live_plan_check_reject.json`, `live_plan_check_approve.json`
- `mutations.txt` (`mutations_rerun.txt` names the failing test for six kills whose first line was a log record), `new_tests_on_base.txt`
- `older_harnesses.txt` (earlier reviews' pipeline harnesses, head vs base), `rule_checks.txt` (every run's original hash, and every delivered file's counts against its reply)

## Round 2 (head `b1059d3`)

Run from the repo root. The round-1 probes above were re-run unchanged on the round-2 head (`api_probe.py`, `adversarial_probe.py`, `live_plan_check.py`); the new scripts are prefixed `r2_`. Make worktrees of the round-1 head and the base first, for example `git worktree add --detach ../r1head 3ef871c` and `git worktree add --detach ../base origin/main`.

- `r2_probe.py [--tree DIR] [--part api|engine] [--speaker S] [--only NAME]`: 16 cases aimed at the M1 fix (a chain the Dispatcher can tell won't finish), through the API (`--part api`) or through `build_engine` alone with its default `NullConfirmationProvider` (`--part engine`, the CLI path): an unsupported step last, in the middle, first (control), with analysis planned, repeated steps, misspelled or hostile capability ids, a read-like unsupported step, an unconfirmed order, a header-only and a no-duplicates file, a second turn after a delivery, and a supported chain (control).
- `r2_l2_probe.py [--only NAME] [--trials N] [--speaker S]`: what the L2 fix leaves and changes: the provider closed between the answer and the worker's wake-up (deterministic), an answer with no working time left (trials), and a server shutdown after the approval while step 1 runs.
- `r2_mutations.py [--jobs 4] [--only NAME]`: 17 mutations of the round-1 fixes' guards, plus round 1's 14 surviving mutations again. Hands mutations also run `tests/buddy/test_engine_factory.py`. About 40 minutes with 4 jobs.
- `r2_new_tests_on_old.py OLD_TREE`: the tests added or changed since round 1, run on the round-1 code (one line per test case).
- `r2_compare.py A.jsonl B.jsonl [--key scenario|case] [--strip-catalog] [--ignore statuses,...]`: compares two runs of the same probe; `--strip-catalog` drops the seeded openers and closers.
- `r2_rule_checks.py [FILE ...]`: rule 2 (the original's hash) and rule 1 (delivered files against the reply's numbers; claim words in replies that delivered nothing) over every round-2 output.

Round 2 outputs are in `out/r2/` (each file's name says which probe and tree; `*_vs_*.txt` are comparisons). The earlier harnesses' raw outputs are gzipped (`prb_harness_*.json.gz` from `scripts/review_harness/harness.py --out …`, `round5_*.json.gz` from `scripts/review_harness/round5.py --out …`, each on head and with `--tree` on base); `older_harnesses_head_vs_base.txt` compares them (`r2_older_compare.py`), and `pipeline_compare_head_vs_base.txt` is `feat-backend-api-core/pipeline_compare.py --diff HEAD_TREE BASE_TREE`.
