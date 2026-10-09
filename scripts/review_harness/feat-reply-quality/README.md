# feat/reply-quality: reviewer's probes (round 1)

Real `build_engine` (real skills, planner, Inspector, Voice) with a fake LLM only. No key, no network.
Probes find the repo from their own location (HERE.parents[2]); run them from this folder. `--tree DIR` runs a probe against another checkout (a `git worktree`
of `origin/main` for the base). The writer's own probes are in `writer/` (not edited).

    BASE=/some/dir/rv-base; HEADWT=/some/dir/rv-head              # two throwaway worktrees
    git worktree add $BASE origin/main                           # the base, for comparison
    git worktree add $HEADWT HEAD                                # the branch head, for mutations (edited and restored)
    cd scripts/review_harness/feat-reply-quality

    python probe_a.py --out out/a-head.json                      # 489 turns: the five parts + an adversarial block, 3 speakers
    python probe_a.py --tree $BASE --out out/a-base.json
    python view.py out/a-head.json p3- --speakers template --base out/a-base.json   # replies, head vs base
    python probe_b_report_phrases.py --out out/b-head.json       # 34 report wordings as Brain's `unsupported`, alone and after "remove the duplicate rows and ..."
    python probe_c_faults.py --out out/c-head.json               # a stage fails (analysis, verifier, planner, hands, Think), with and without a report request
    python probe_d_api.py --out out/d-head.json                  # HTTP API: download names x6 deliveries, awkward upload names, greetings, reports, no file
    python probe_d2_page.py                                      # the web page's transcript shows the friendly names
    python probe_e_more.py --out out/e-head.json                 # declined plans, near-duplicates, odd messages, Brain/Planner garbage
    python probe_f_header_text.py [--tree DIR]                   # a column header that reads like a claim (pre-existing P1), head vs base
    python probe_g_formulas.py [--tree DIR]                      # the formula-count sentences, singular and plural
    python probe_h_thanks.py                                     # "thanks for the summary!" after a delivery
    python probe_i_hallucinated_ledger.py                        # Brain lists an action the message never asked for
    python mutations.py --tree $HEADWT [--full] [NAMES]       # 53 one-place mutations; needs a throwaway worktree of the branch head
    python check_numbers.py out/a-head.json out/b-head.json ...  # every count a reply states vs the independent read
    python cmp_harness.py base.json head.json                    # independent head-vs-base comparison of harness.py / round5.py output

`rq.py` is the library (fixtures, fake LLM, independent openpyxl read of the files, automatic flags). Each record has the reply, the steps,
whether a file was handed back, the original's SHA-256 before/after and the flags (`(s)`, "verifier", a raw capability id, a secret or file text,
a wrong plural, a completion word in a reply that delivered nothing). `view.py` prints a probe's JSON; `out/` holds the results of round 1:
`a-*.json` ... `e-*.json`, `d-head.json`, `mutations-round1*.txt` (kill/survive per mutation).

`probe_d_api.py` imports `pr3.py` and `harness.py` from `feat-backend-api-confirmation/` and `feat-backend-api-core/` (not edited).
Those branches' own scripts import `NOTHING_RAN_REPLY`, which this branch removes (see the review, Deferred).
