# Review round 2 probes: `feat/release-safety`

Run from the repository root. Outputs are in `out/`. Round 1's scripts are one folder up and are
reused as they are (`../deploy_probes.sh`, `../stack_turn.py`).

| Script | What | Output (`out/`) |
|---|---|---|
| `../deploy_probes.sh SCRATCH 7cda772` | Round 1's real-stack probes (d01 to d10) on the head. Save the `workbuddy-data` volume first, and re-tag any `workbuddy:<12 hex>` image you want kept (a good release prunes them) | `d*.txt`, `stack_turns.jsonl` |
| `stack_round2.sh SCRATCH` | After the above, on the same scratch stack: e01 restore the oldest archive at the retention limit, then smoke and turns (H1); e02 a commit whose `deploy/` differs (M5); e03 the same commit with the app stopped (M2); e04 a `$` in the password (M3); e05 session ids in the logs (M4) | `e*.txt` |
| `check_stack_turns.py FILE.jsonl...` | Rule 1 and rule 2 over `stack_turn.py` lines: the reply's numbers against an openpyxl read, no completion words on an undelivered turn, the upload's hash in the volume unchanged | `stack_turns_check.txt` |
| `restore_probes.py` | `restore.sh` on real Docker, isolated (the suite's `Site`/`Volume`, imported): odd path, `../` member, a top-level name with a space (replace fails halfway, then the printed undo), a full disk during unpack (needs root to mount a 4 MB tmpfs), a left-over staging folder, KEEP=1 | `restore_probes.txt` |
| `release_probes.py` | `release.sh` with the suite's stand-in docker: same commit while unhealthy (P1, P2), a local edit under `deploy/` (P3), an ignored override (P4), an older commit with the same `deploy/` (P5) | `release_probes.txt` |
| `env_parse_probe.py` | What `docker compose` reads from `.env` against what the scripts read, and whether `check_env_syntax` refuses: 23 values and 6 line forms | `env_parse_probe.jsonl` |
| `mutations2.py --rev 7cda772` | 18 mutations that undo one round-1 fix each, in a `git archive` copy; N00 is the unmutated copy (a clone-like tree with no empty folders) | `mutations2.txt` |

Also in `out/`: `suite_and_lint.txt`, `new_tests_on_0ea40c4.txt` (this head's `tests/deploy` over
the round-1 code), `ui_probe_head.jsonl`, `ui_head_vs_base.txt` and `rule_checks.txt` (round 1's
in-process probes, compared with round 1's base output; nothing under `buddy/`, `core/`, `skills/`
or `backend/` changed since).
