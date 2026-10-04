# Review harness

Reviewer probes for `feat/backend-api-core` (round 1). They run the real app (`backend.main.create_app`) and the real pipeline (`build_engine`, real skills, verifiers and Voice) with only the LLM faked. No key or network is needed. Nothing here is part of the test suite.

Run from the repo root:

- `python scripts/review_harness/api_probe.py [--speaker template|friend|focus] [--only NAME]`: 13 single-turn scenarios per speaker (delivered, declined by the default config, chain, no duplicates, greeting, no file, a stage raising, Voice raising, LLM down, clarification). One JSON line each: outcome, reply, whether it equals the pipeline's own reply, an independent openpyxl read of each delivered file, and the original's SHA-256 before and after.
- `python scripts/review_harness/adversarial_probe.py [--only NAME] [--speaker S]`: multi-turn, no-file turns, hostile names, hostile headers, upload structure and size, concurrency and isolation, deadline with a late worker, an upload changed mid-turn, restart, corrupt records, concurrent-turn correctness, and a verified output outside the turn folder.
- `python scripts/review_harness/server_probe.py`: starts `backend.__main__.main` for real (uvicorn, production logging) with a fake LLM, checks the server log and `TMPDIR` after oversized uploads.
- `python scripts/review_harness/pipeline_compare.py --diff <head-root> <base-root>`: the same pipeline scenarios on two checkouts (for example a `git worktree` of `origin/main`); prints SAME/DIFFERENT per scenario and speaker.
- `python scripts/review_harness/mutations.py [--jobs 3] [--only NAME]`: removes one guard at a time in a throwaway copy and runs `tests/backend` and `tests/core/test_engine.py`; "SURVIVED" means no test noticed.

`harness.py` holds the shared fake LLM, fixtures and the independent file check.

Round 2 additions:

- `python scripts/review_harness/round2_probe.py [--only NAME] [--speaker S]`: probes aimed at the round-1 fixes. `m1_nothing_ran` (no upload, "thanks" after a delivery, "yes" after a decline, a second greeting, every planner step dropped, LLM down, clarification, a misread "thanks", a second upload, an unknown capability), `m2_from_original` (same-upload follow-up, **re-upload of a delivered file**, an unrelated second upload, a failed turn first, after a restart, a chain), `m3_save_failures` (the finished save fails once / always, the session save fails, an integrity failure whose save fails, a `BaseException` from the save), `l1_redaction` (`_redact_record` with uvicorn's formatters and odd records), `l8_requests` (surrogates, `Allow`), `l11_restore` and `l11_more` (valid details of every turn shape restore; corrupt ones are `404`). To compare with an older commit, copy it into a `git worktree` of that commit and run it there.
- `mutations.py` now also has the round-2 mutations (`m1_*`, `m2_*`, `m3_*`, `l1_*`, `l8_*`, `l11_*`) and runs `TEST_PATHS`, which adds the buddy Voice and state tests. `--only m2` runs one group.
- `python scripts/review_harness/l1_placeholder_probe.py [ROOT] 2>&1`: log calls whose format string puts a secret prefix right before a placeholder (`"Bearer %s"`, `"api_key=%s"`, `"/sessions/%s"`). Every line should come out redacted; a "Logging error" block means the raw argument went to stderr (round 2, R2-L1).
