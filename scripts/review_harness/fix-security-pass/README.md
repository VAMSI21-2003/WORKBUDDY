# fix/security-pass: reviewer's probes (round 1, head `f1af923`; base `origin/main` `89625e7`)

Real `build_engine` (real skills, planner, Hands, Inspector, Voice), fake LLM only, through
`../feat-reply-quality/rq.py` and `r5lib.py` (imported, not edited). No key, no network. Run from the
repo root. `--tree DIR` (a `git worktree` of `origin/main`) runs a probe on the base. Output goes to
`out/` (git-ignored). Add `2>/dev/null` to hide Splink's training noise.

    python scripts/review_harness/fix-security-pass/probe_a_pipeline.py [--tree B] [--speakers template,friend,focus] [--only S01,S09]
        # 19 scenarios: hostile header/cell text in a report and a dedupe, a SQL-looking header, a 300-char header,
        # an int header, text that looks like numbers, z31/z18/paraphrase/keep-clause flags, a step no skill runs,
        # the Inspector failing to read output/input/analysed file (exception text with a secret)
    PYTHONPATH=. python scripts/review_harness/fix-security-pass/probe_limit_bypass.py   # files that pass check_workbook_limits but read far bigger
    PYTHONPATH=. python scripts/review_harness/fix-security-pass/measure_read.py FILE.xlsx  # peak RSS of one pandas read
    python scripts/review_harness/fix-security-pass/probe_e_bomb_turn.py ROWS COLS [--tree B]  # whole turn on a 1.5 KB sparse file; peak RSS, time
    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_f_odd_zips.py [--tree B] [--turns]  # sheet outside xl/worksheets/, corrupt deflate, method 99
    python scripts/review_harness/fix-security-pass/probe_g_report_odd.py [--tree B]   # report and dedupe turns on probe_f's files (run probe_f first)
    python scripts/review_harness/fix-security-pass/probe_b_neardup.py [TREE]        # NearDuplicateAnalyzer on odd headers (small frame)
    python scripts/review_harness/fix-security-pass/probe_c_sql_header.py [TREE]     # crafted headers: marker file written? findings still found?
    python scripts/review_harness/fix-security-pass/probe_d_retry.py [--tree B]      # Brain/Think meet 429, 503, 400, timeout, connection drop, status as text
    python scripts/review_harness/fix-security-pass/probe_h_dispatch_text.py [--tree B]  # does '<session key>-task-N' reach a reply?
    python scripts/review_harness/fix-security-pass/probe_i_text_cells.py [TREE]     # "007", 7, big ints, TRUE, 0.10 through both cleaning skills
    python scripts/review_harness/fix-security-pass/probe_j_bool_cells.py [--tree B] # TRUE/FALSE in a number column through a dedupe turn (pre-existing)
    python scripts/review_harness/fix-security-pass/mutations.py [NAME ...]          # 39 one-place mutations, each in a fresh `git archive HEAD` export
    python scripts/review_harness/fix-security-pass/prototype_fix.py DEST            # exports HEAD to DEST with the reviewer's R1-1 prototype applied

`make_sparse_bomb.py` builds the small far-corner workbooks the bomb probes use. The shared harnesses were
also run unchanged on head and base: `scripts/review_harness/harness.py --out X.json` and
`scripts/review_harness/rereview3.py --out Y.json`, compared with `feat-reply-quality/cmp_harness.py`.

Round 2 (head `9d17990`), same conventions:

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_k_round2.py [--tree DIR] [--read] [--only K1,K7]
        # 13 workbooks aimed at the round 1 fix (row r vs cell reference, a junk/long-digit/damaged part first,
        # a sheet not named .xml, calcChain, one long shared string): refused or not; --read measures one pandas read
    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_l_turns.py K1_row_r_mismatch_20k [...] [--tree DIR] [--speakers ...]
        # whole dedupe and report turns on out/k_<name>.xlsx (run probe_k first): reply, delivery, hash, peak RSS, time
    python scripts/review_harness/fix-security-pass/mutations_r2.py [NAME ...]        # 18 mutations of the round 1 fixes + 3 re-runs
    python scripts/review_harness/fix-security-pass/prototype_fix_r2.py DEST          # HEAD exported to DEST with the R2-1/R2-3 prototype

Round 3 (head `c766fa6`), same conventions. Run probe_m first; it writes `out/m_*.xlsx`:

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_m_round3.py [--tree DIR] [--only M1,M5] [--read] [--timeout 600]
        # 16 workbooks aimed at the reader-based check: many sheets on one part (M1, M1e), a broken external link
        # beside a grid bomb (M2), many shared strings (M3), styles (M4), a big external link (M5), long or
        # same-prefix names for the near-duplicate check (M6), text at the caps (M7), defined names (M8),
        # 1,000,000 cells (M9). Per file: upload route verdict, check verdict, time and peak RSS in a capped child
    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_n_turns.py M5_ext_link_2_3m_cells [...] [--tree DIR] [--kinds dedupe,report,empty] [--no-facts] [--timeout 900]
        # whole turns, one capped child per turn; --no-facts keeps the harness's own openpyxl reads out of the peak
    OPENPYXL_LXML=False sh scripts/review_harness/fix-security-pass/rss_watch.sh python scripts/review_harness/fix-security-pass/probe_o_long_names.py out/m_M6_short_names_10000.xlsx [--tree DIR]
        # the near-duplicate check alone; the watcher samples RSS and kills it past LIMIT_MIB (default 6144)
    python scripts/review_harness/fix-security-pass/mutations_r3.py [NAME ...]        # 15 mutations of the round 2 fixes
    python scripts/review_harness/fix-security-pass/prototype_fix_r3.py DEST          # HEAD exported to DEST with the R3-1/2/3/5 prototype

DuckDB spills to `.tmp/` in the working directory when the near-duplicate check runs out of memory; delete it afterwards.
