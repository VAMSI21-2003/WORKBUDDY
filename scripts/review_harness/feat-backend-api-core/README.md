# Review harness

Reviewer probes for `feat/backend-api-core` (round 1). They run the real app (`backend.main.create_app`) and the real pipeline (`build_engine`, real skills, verifiers and Voice) with only the LLM faked. No key or network is needed. Nothing here is part of the test suite.

Run from the repo root:

- `python scripts/review_harness/feat-backend-api-core/api_probe.py [--speaker template|friend|focus] [--only NAME]`: 13 single-turn scenarios per speaker (delivered, declined by the default config, chain, no duplicates, greeting, no file, a stage raising, Voice raising, LLM down, clarification). One JSON line each: outcome, reply, whether it equals the pipeline's own reply, an independent openpyxl read of each delivered file, and the original's SHA-256 before and after.
- `python scripts/review_harness/feat-backend-api-core/adversarial_probe.py [--only NAME] [--speaker S]`: multi-turn, no-file turns, hostile names, hostile headers, upload structure and size, concurrency and isolation, deadline with a late worker, an upload changed mid-turn, restart, corrupt records, concurrent-turn correctness, and a verified output outside the turn folder.
- `python scripts/review_harness/feat-backend-api-core/server_probe.py`: starts `backend.__main__.main` for real (uvicorn, production logging) with a fake LLM, checks the server log and `TMPDIR` after oversized uploads.
- `python scripts/review_harness/feat-backend-api-core/pipeline_compare.py --diff <head-root> <base-root>`: the same pipeline scenarios on two checkouts (for example a `git worktree` of `origin/main`); prints SAME/DIFFERENT per scenario and speaker.
- `python scripts/review_harness/feat-backend-api-core/mutations.py [--jobs 3] [--only NAME]`: removes one guard at a time in a throwaway copy and runs `tests/backend` and `tests/core/test_engine.py`; "SURVIVED" means no test noticed.

`harness.py` holds the shared fake LLM, fixtures and the independent file check.

Round 2 additions:

- `python scripts/review_harness/feat-backend-api-core/round2_probe.py [--only NAME] [--speaker S]`: probes aimed at the round-1 fixes. `m1_nothing_ran` (no upload, "thanks" after a delivery, "yes" after a decline, a second greeting, every planner step dropped, LLM down, clarification, a misread "thanks", a second upload, an unknown capability), `m2_from_original` (same-upload follow-up, **re-upload of a delivered file**, an unrelated second upload, a failed turn first, after a restart, a chain), `m3_save_failures` (the finished save fails once / always, the session save fails, an integrity failure whose save fails, a `BaseException` from the save), `l1_redaction` (`_redact_record` with uvicorn's formatters and odd records), `l8_requests` (surrogates, `Allow`), `l11_restore` and `l11_more` (valid details of every turn shape restore; corrupt ones are `404`). To compare with an older commit, copy it into a `git worktree` of that commit and run it there.
- `mutations.py` now also has the round-2 mutations (`m1_*`, `m2_*`, `m3_*`, `l1_*`, `l8_*`, `l11_*`) and runs `TEST_PATHS`, which adds the buddy Voice and state tests. `--only m2` runs one group.
- `python scripts/review_harness/feat-backend-api-core/l1_placeholder_probe.py [ROOT] 2>&1`: log calls whose format string puts a secret prefix right before a placeholder (`"Bearer %s"`, `"api_key=%s"`, `"/sessions/%s"`). Every line should come out redacted; a "Logging error" block means the raw argument went to stderr (round 2, R2-L1).

Round 3 additions:

- `python scripts/review_harness/feat-backend-api-core/round3_probe.py [--only NAME] [--speaker S] [--cases A,F]`: probes aimed at the round-2 fixes.
  - `m2_targets` (cases A to J): the per-upload count of `earlier_deliveries`. Switching back to an earlier upload with `file_id`, re-uploading the original's exact bytes, re-uploading Buddy's copy and then two follow-ups, an explicit `file_id`, a restart, a deadline with a late worker, Voice raising, two uploads with the same name, a chain's copy re-uploaded, a delivery on another upload only. For each delivered file it lists which upload or earlier delivered file it was made from, by applying its own model of the skill to each one (`made_from_by_my_model`), so the from-original sentence can be checked against what happened.
  - `l1_logging`: odd records through the installed redaction: uvicorn access records (under the real `uvicorn.access` logger name) with query strings any client can send, records that can't be formatted, secrets in bytes, exceptions, dicts, `%%`, tracebacks. Secrets are built at run time, because logging's error handler prints the calling source line.
  - `l2_base_exception`: a `BaseException` from the finished-record save (once, always, `KeyboardInterrupt`, `SystemExit`), from the session save, and (not new code) `uploads_unchanged` or `make_read_only` raising inside `_finalize`.
- `mutations.py` has the round-3 mutations (`r3_*`); `--only r3` runs them. Round 2's `m2_not_set_at_start`, `m2_counts_every_turn`, `l1_always_collapse` and `l1_never_collapse` no longer apply to the code ("NOT APPLIED"); `r3_m2_not_set`, `r3_m2_counts_every_turn`, `r3_l1_always_collapse` and `r3_l1_never_collapse` replace them.
- `server_probe.py` also sends `GET /healthz?api_key=` and two more such query strings, and counts the "Logging error" blocks they cause.
- `adversarial_probe.py --only restart` now waits for the old app's worker before leaving, so its temporary folder no longer fails to clean up under load, and `download_same_bytes` compares against the turn's listed SHA-256.

Round 4 additions (after the merge of `origin/main` and the 2 MiB cap):

- `python scripts/review_harness/feat-backend-api-core/round4_probe.py [--only NAME] [--speaker S]`: the merged pipeline through the API.
  - `ledger_followups`: later turns where Brain's reply carries the PR-B `requested_actions` (earlier fakes never sent it): the model re-lists turn 1's action, the mention detector, an unsupported request, a partly unsupported one, a re-planned chain, a misread "unclear", "thanks!".
  - `formulas_multi_turn`: an original with formulas (saved and unsaved results); a follow-up on it; Buddy's values-only copy re-uploaded.
  - `plain_table_api` and `nothing_ran_paths`: uploads that aren't plain tables (merged cells, header on row 3).
  - `two_sheets_hidden_path`: a real second sheet (declared in `workbook.xml`) stored outside `xl/worksheets/`, which the upload check can't see; "hi" and "remove duplicate rows".
  - `cap_default` (template only): `Settings.from_env` defaults; exactly 2 MiB, one byte more, the 5 MiB override.
  - `analytics_via_api` (template only): an analysis turn through the API with `requests.get` patched; round 3's P2.
- `python scripts/review_harness/feat-backend-api-core/timing_probe.py [--root CHECKOUT] [--cap BYTES]`: wall time of a dedupe and a two-step chain on the live-run fixture shape, built just under the cap (default 2 MiB), through the API with a fake LLM and the deadline raised to 900 s. `--root` times another checkout (a `git worktree`).
- `mutations.py` has the round-4 mutations (`r4_*`); `--only r4` runs them.
- Outputs of the round-4 runs are in `round4_out/`.
