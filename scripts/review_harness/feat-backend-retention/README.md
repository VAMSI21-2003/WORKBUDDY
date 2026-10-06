# Review harness: `feat/backend-retention` (PR-4)

Reviewer probes for access, rate limits, retention and quotas. They run the real app (`backend.main.create_app`) and the real pipeline (`build_engine`: real skills, Hands, Inspector, verifiers, Voice) with only the LLM faked. The fake LLM, fixtures and openpyxl check come from `../feat-backend-api-core/harness.py`, and the app helpers from `../feat-backend-api-confirmation/pr3.py`. Both are imported, not edited. Not part of the test suite.

Run from the repo root. Make a base worktree first: `git worktree add --detach ../base origin/main`.

- `old_probe.py SCRIPT [--tree DIR] [args]` runs a PR-3 probe (`../feat-backend-api-confirmation/SCRIPT`) unchanged. On a PR-4 tree it first relaxes the default buckets, because those probes poll every 10 ms. Used for `api_probe.py`: 25 turns x 3 speakers, on head and on base. Also used for `r2_l2_probe.py --only unit_close` (R2-L1).
- `pr4_probe.py [--only NAME] [--speaker S]` covers 15 cases, with production-like settings, real buckets and injected clocks. Each poll advances the monotonic clock by 2 s. The cases are: the cookie flow, a Bearer chain, a cookie reject, a cookie answer with no or a foreign Origin, retention after a delivery, retention while a plan waits, retention across restarts, a partial sweep followed by a turn (finding M2), quotas, the storage cap with a turn, guessing and `Retry-After`, hostile credentials and paths, the gate failing closed, the sweep-interval overflow (L1), and the cost of unknown routes. Cases c01 to c10 run once per speaker.
- `windows_readonly_sim.py [--fixed]` simulates finding M1. It applies Windows' rule that a file with the read-only attribute can't be unlinked to `backend.retention`'s `os.unlink`. With `--fixed`, it clears the bit before unlinking.
- `startup_probe.py` runs `python -m backend` in a subprocess under 15 environments: refused, or the address it binds.
- `mutations.py [--jobs 4] [--only NAME]` runs 46 one-line mutations of the new guards. Each runs in a `git archive HEAD` copy with `pytest -x tests/backend`. A run takes about 40 minutes with 4 jobs.

Round 1 outputs are in `out/`:
- `api_probe_head.jsonl`, `api_probe_base.jsonl`, `api_probe_head_vs_base.txt` (from `../feat-backend-api-confirmation/r2_compare.py --key scenario --strip-catalog`)
- `pr4_probe.jsonl`
- `windows_readonly_sim.json`, `windows_readonly_sim_fixed.json`
- `startup_probe.jsonl`
- `r2_l1_unit_head.jsonl`, `r2_l1_unit_base.jsonl`
- `mutations.txt`

## Round 2 (commit `6959db2`)

Also make a round-1 worktree: `git worktree add --detach ../r1 4352d1c`.

- `r2_probe.py [--tree DIR] [--only NAME]`: 4 cases on the M1 fix, through the real app and pipeline, reusing `pr4_probe.py`. w1 (Windows rule; two expired sessions, a plan waiting across the sweep, a session used at +23 h, then turns after the sweep), w2 (Windows rule; swept from disk by a second process), pf (Windows rule plus one upload whose unlink fails, as for a file still open on Windows), hl (a read-only file outside the data root hard-linked into outputs). w1, w2 and pf run per speaker. Run with no `--tree`, with `--tree ../r1`, and with `--tree` on a copy that has `out/r2_proto_fix.diff` applied.
- `r2_foreign_owner.py [--tree DIR]`: run as root. It calls that tree's `remove_tree` as user `nobody` on a folder holding a `0o444` file owned by root (needs `setpriv`, and the tree must be readable by `nobody`).
- `r2_mutations.py`: 5 one-line mutations of the M1 fix, each against `tests/backend/test_retention.py` in a `git archive HEAD` copy (about 1 minute).
- Also rerun: `pr4_probe.py`, `windows_readonly_sim.py` (now without `--fixed`), `startup_probe.py`, and `old_probe.py api_probe.py` on head and on `--tree ../base`, compared with `../feat-backend-api-confirmation/r2_compare.py --key scenario --strip-catalog`. Rule checks: `../feat-backend-api-confirmation/r2_rule_checks.py FILE...`.

Round 2 outputs are the `out/r2_*` files.
