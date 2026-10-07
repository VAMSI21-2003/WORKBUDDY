# Review harness: `feat/release-safety`

Reviewer probes, not part of the suite. Run from the repository root. Outputs from round 1 are in
`out/`.

| Script | What | Output (`out/`) |
|---|---|---|
| `deploy_probes.sh SCRATCH [REV]` | The deploy stack on this machine's Docker, run from a clone in `SCRATCH` (never this checkout), with the LLM faked by `fake_groq.py` through `review-override.yml` (copied to the clone's git-ignored `deploy/docker-compose.override.yml`). d01 first release, d02 same commit again, d03 turns through Caddy (`stack_turn.py`, `make_files.py`), d04 `check-proxy.sh` with hops 1, 0, 2, d05/d06 backup and restore (d06 restores the oldest archive at the retention limit: H1), d07 releases whose build, start or smoke fails, then a good one, d08 first release fails then rerun (M2), d09 a `$` in the password (M3), d10 session ids in Caddy's log (M4). Save the `workbuddy-data` volume first: the names are fixed | `d*.txt`, `stack_turns.jsonl` |
| `stack_turn.py --stack DIR --mode M --file F [--decision reject]` | One turn through Caddy: sign in, upload, message, plan, download, read the file with openpyxl, and the upload's SHA-256 inside the volume before and after | one JSON line |
| `mutations.py [--rev REV] [--empty-dirs] [--only Mnn]` | 15 mutations of the branch's guards (saved-data shape and open tests, lock tests, ruff baseline), each in a `git archive` copy. `M00` is the unmutated copy: without `--empty-dirs` it fails (finding M1); with it, it passes and every real mutation must fail. `M15` is an equivalent mutant (only `session.json` holds paths) | `mutations.txt` |
| `../feat-frontend-demo/ui_probe.py [--tree DIR]` and `../feat-frontend-deploy/compare_ui.py`, `../feat-frontend-demo/rule_checks.py` | The 25 scenarios x 3 speakers in-process (real pipeline, fake LLM), head and base | `ui_probe_{head,base}.jsonl`, `ui_head_vs_base.txt` |

Other outputs: `suite_and_lint.txt` (suite in this checkout and in a fresh GitHub clone; ruff per file
and rule, head = base), `new_tests_on_base.txt` (the branch's `tests/deploy` over a `git archive
origin/main` copy), `image_nocache_build.txt` and `image_freeze.txt` (a `--no-cache` build of the head
from the lock; its `pip freeze` equals `docs/reviews/frontend-deploy-image-freeze.txt`),
`venv311_lock_install.txt` (the dev lock into a clean Python 3.11 venv, `--require-hashes --no-deps`,
then `pip check`), `d06b_restore_oldest_with_fix.txt` (H1 with a prototype fix, in the scratch clone).

Building in this sandbox needs `WORKBUDDY_PIP_CA=/root/.ccr/ca-bundle.crt` (release.sh passes it as a
build secret).
