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
