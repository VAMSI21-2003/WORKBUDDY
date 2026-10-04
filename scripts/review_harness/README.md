# Review harness

Reviewer probes for `feat/backend-api-core` (round 1). They run the real app (`backend.main.create_app`) and the real pipeline (`build_engine`, real skills, verifiers and Voice) with only the LLM faked. No key or network is needed. Nothing here is part of the test suite.

Run from the repo root:

- `python scripts/review_harness/api_probe.py [--speaker template|friend|focus] [--only NAME]`: 13 single-turn scenarios per speaker (delivered, declined by the default config, chain, no duplicates, greeting, no file, a stage raising, Voice raising, LLM down, clarification). One JSON line each: outcome, reply, whether it equals the pipeline's own reply, an independent openpyxl read of each delivered file, and the original's SHA-256 before and after.
- `python scripts/review_harness/adversarial_probe.py [--only NAME] [--speaker S]`: multi-turn, no-file turns, hostile names, hostile headers, upload structure and size, concurrency and isolation, deadline with a late worker, an upload changed mid-turn, restart, corrupt records, concurrent-turn correctness, and a verified output outside the turn folder.
- `python scripts/review_harness/server_probe.py`: starts `backend.__main__.main` for real (uvicorn, production logging) with a fake LLM, checks the server log and `TMPDIR` after oversized uploads.
- `python scripts/review_harness/pipeline_compare.py --diff <head-root> <base-root>`: the same pipeline scenarios on two checkouts (for example a `git worktree` of `origin/main`); prints SAME/DIFFERENT per scenario and speaker.
- `python scripts/review_harness/mutations.py [--jobs 3] [--only NAME]`: removes one guard at a time in a throwaway copy and runs `tests/backend` and `tests/core/test_engine.py`; "SURVIVED" means no test noticed.

`harness.py` holds the shared fake LLM, fixtures and the independent file check.
