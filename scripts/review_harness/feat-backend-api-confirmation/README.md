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
