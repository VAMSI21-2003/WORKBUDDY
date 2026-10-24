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

## Round 2 (head `e645c84`)

    python p6_round2.py --out out/r2-p6.json [--speakers friend] [CASE ...]  # 36 new drafts x 3 speakers; FALSE drafts SHOWN
    python why.py s01-dedupe "msg one || msg two" ...                         # the checker's problems for a draft on a scenario's real sheet
    python p7_writer_budget.py       # real build_providers_from_env vs a local hanging server: how long the writer holds a turn
    python p8_overrefusal.py         # true drafts the round-1 fixes refuse
    python mutations_r2.py [NAME ...]            # 30 mutations of the round-1 fixes vs the 4 new test files
    python mutations_r2.py --full NAME ...       # the whole suite, for survivors

Rerun `p2_drafts.py` and `p5_checker_unit.py` too: they hold round 1's cases.

## Round 3 (head `358a88a`: slots + social vocabulary + `question_about_file`)

`nb.py` now reads both Fact shapes (rounds 1-2 `file_text`, round 3 `from_file`/`variants`) and both prompt formats.

    python p9_round3.py --out out/r3-p9.json [--speakers friend] [CASE ...]  # 30 slot drafts + social words x 3 speakers; FALSE drafts SHOWN
    python p10_api_questions.py --out out/r3-p10.json [CASE ...]             # question_about_file through the HTTP API (reads the latest copy)
    python p10_api_questions.py --tree /some/dir/base --question-intent conversation   # base: the same questions read as chat / unclear
    python mutations_r3.py [NAME ...]          # 23 mutations of the replacement vs the 4 natural-buddy test files
    python mutations_r3.py --full NAME ...     # the whole suite, for survivors

Rerun `p1_ordinary.py` (echo / raise / base), `cmp1.py`, `check_numbers.py`, `p2_drafts.py` and `p6_round2.py` too: earlier rounds' drafts are free text, so on a file turn they must all be refused now.
