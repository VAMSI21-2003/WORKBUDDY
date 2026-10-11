# feat/follow-up-turns: reviewer's probes (round 1, head `8f70640`, base `f01de0a`)

Real app (`backend.main.create_app`) + real `build_engine` (real skills, Hands, Inspector, Voice), fake LLM only. No key, no network.
`fu.py` is the library (fake LLM routed by system prompt, a multi-turn `Sess`, independent openpyxl read of every delivered file,
upload SHA-256 before/after each turn). It imports `pr3.py` / `harness.py` from `feat-backend-api-confirmation/` and `feat-backend-api-core/` (not edited).
Run from this folder. `--tree DIR` (a `git worktree` of `origin/main`) runs a probe on the base; `--out FILE` moves the output (default `out/`, git-ignored).

    python probe_a.py     # 8 multi-turn scenarios x template/friend/focus: chain on copy, start over, nothing left, report, many follow-ups, 2nd upload / file_id, greeting + garbage between
    python probe_b.py     # adversarial: copy tampered / deleted, bad file_to_use values, declined plan + "original", model misreads, restart, two sessions, name collision (" (2)")
    python probe_c.py     # a stage raises before/after (all 6 stages), the turn's copy touched mid-turn
    python probe_d.py     # odd uploads: hostile file name, formulas, 90-char name, all-empty, hostile cell text, dotted names
    python probe_e.py     # mixed chains on a copy (one step has nothing left), unsupported step in a chain, report-only with "original"
    python probe_f.py     # turn detail of a nothing-left turn
    python view.py out/a-head.json s1/template     # print a probe's replies (substring of the key)
    python mutations.py --tree $WT                  # 34 one-place mutations; $WT = throwaway `git worktree add --detach $WT HEAD`, never the branch

Also run (shared, not edited): `scripts/review_harness/harness.py --out X.json` on head and base, then `feat-reply-quality/cmp_harness.py base.json head.json`
(532 turns: the only reply difference is the added sentence "I worked on your original upload." on 504 delivered replies).
