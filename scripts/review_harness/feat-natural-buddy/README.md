# feat/natural-buddy: reviewer's probes (round 1, head `da2c927`)

Real `build_engine` (real skills, planner, Inspector, Voice, and on the branch the real `NaturalVoice`,
`build_fact_sheet` and `check_reply`). Only the models are faked: Brain/Think/planner get fixed JSON
(`../feat-reply-quality/rq.py`, imported, not edited) and the writer model is a Python callable in `nb.py`.
No key, no network. Run from this folder; raw output goes to `out/` (git-ignored).

    git worktree add /some/dir/base origin/main
    python p1_ordinary.py --writer echo  --out out/p1-head-echo.json     # 20 scenarios x template/friend/focus, careful writer
    python p1_ordinary.py --writer raise --out out/p1-head-raise.json    # writer unreachable -> template reply
    python p1_ordinary.py --tree /some/dir/base --out out/p1-base.json   # base (PersonalityVoice / TemplateSpeaker)
    python cmp1.py out/p1-head-raise.json out/p1-base.json               # friend/focus must be identical
    python check_numbers.py out/p1-head-echo.json                        # "Removed N" vs the files (openpyxl)
    python p2_drafts.py --out out/p2.json [CASE ...]                     # 37 adversarial drafts x 3 speakers; FALSE drafts that are SHOWN
    python p3_rollback.py /some/dir/base ../../..                        # can base (a rollback) open data-v5?
    python p4_api.py                                                     # HTTP API: slow writer vs the turn deadline; markup in a message
    python p5_checker_unit.py                                            # checker timing (ReDoS) and one-line drafts
    python mutations.py [NAME ...]                                       # 33 one-place mutations vs the 3 new test files
    python mutations.py --full NAME ...                                  # same against the whole suite (slow, ~15 min each)

`nb.run(spec, speaker, writer=...)`: `speaker` "template" = no mode (head: NaturalVoice; base: TemplateSpeaker),
"friend"/"focus" = that mode. A writer is `nb.echo` (the tests' careful `echo_answer`), or
`nb.const(answer, ...)` (one answer per attempt; a callable gets the prompt). Each record has the
messages, `writer_shown` (the draft was shown) vs `fallback` (template), the sheet's turn kind, the
delivered file's facts read with openpyxl, and `orig_unchanged` (SHA-256 before/after).
