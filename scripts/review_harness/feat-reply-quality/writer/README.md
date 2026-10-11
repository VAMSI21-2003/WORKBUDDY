# feat/reply-quality: probes

Real `build_engine` (real skills, planner, Inspector, Voice), fake LLM. No key, no network. Run from the repo root.

    python scripts/review_harness/feat-reply-quality/writer/writer_matrix.py --out new.json            # this checkout
    python scripts/review_harness/feat-reply-quality/writer/writer_matrix.py --tree ../main --out old.json   # a worktree of origin/main
    python scripts/review_harness/feat-reply-quality/writer/compare.py old.json new.json --show 3

`writer_matrix.py` (the writer's): 40 scenarios x template/friend/focus, one record per turn (reply, steps, whether a file was
handed back, the original's hash before/after). Groups: `n*` nothing ran (no file, unclear, refused files of every kind),
`g*` greetings (and action requests misread as greetings), `c*` Think asked for clarification (known, unknown and secret-bearing
reasons), `r*` reports, `d*` delivered baselines.

`compare.py` takes the JSON of any harness in `scripts/review_harness/` run on two trees and classifies how each reply changed:
`plural`, `plural + exact-duplicate finding / flow` (both removed from both replies leaves them equal), or a named new feature
(`refusal`, `no-file`, `greeting`, `report`, `not-understood`...), else `other`. It prints, for the new run, any reply with `(s)`,
"verifier" or a raw capability id, any voice-model call, any changed original, and any difference in whether a file was handed back.

The other harnesses, against a worktree of the base: `harness.py`, `rereview3.py`, `round5.py`, `values_only_probe.py`,
`round2_probe.py` all accept `--tree` and `--out`. In the writer's environment three `values_only_probe` fixtures
(`formulas_saved`, `formulas_partly_saved`, `formulas_wrong_dim`) and two `round2_probe` ones (`r2_formula_text_na`,
`r2_formula_error_unsaved_mix`) fail to build on the base too (an XML patch assertion in `patch_sheet`), so they were left out;
name the fixtures on the command line to choose.

`results/` holds the writer's comparison output for the head named in `docs/reviews/feat-reply-quality-impl.md`.
