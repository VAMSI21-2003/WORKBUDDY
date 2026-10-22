# feat/urgency-tone: reviewer's probes (round 1, head `433d035`)

Real `build_engine` (real skills, planner, Inspector, Voice) with a fake LLM only. No key, no network.
Imports `../feat-reply-quality/rq.py` and `../feat-backend-api-confirmation/pr3.py` (not edited). Every script
takes `--tree DIR` (a `git worktree` of `origin/main` for the base; there `user_state` is ignored, so
base == head-calm is the regression check). Raw output goes to `out/` (git-ignored). Run from this folder.

    git worktree add /some/dir/base origin/main
    python p1_matrix.py --out out/p1-head.json                    # 22 scenarios x calm/urgent/frustrated/absent x 3 speakers = 264 turns
    python p1_matrix.py --tree /some/dir/base --out out/p1-base.json
    python cmp1.py out/p1-head.json out/p1-base.json              # head-calm == base; urgent/frustrated == calm focus (template: calm template)
    python check_numbers.py out/p1-head.json                      # counts in delivered replies vs an independent openpyxl read
    python p2_odd_values.py                                       # 24 odd `user_state` values + non-dict Brain replies
    python p3_api.py > out/p3-head.jsonl                          # HTTP API: the plan card, approve/reject, per state and speaker; HEAD vs GET /healthz
    python p4_faults.py                                           # a stage fails (verifier, hands, planner, read) with each state
    python p5_multiturn.py                                        # state is per turn; question then urgent answer
    python p6_healthz.py                                          # HEAD /healthz: bucket shared with GET, 429, other methods
    python mutations.py /some/dir/head-worktree [NAME ...]        # 29 one-place mutations, 4 at a time, ~15 min

`p3_api.py` polls fast, so its statuses show `HTTP 429` between states (the poll bucket); that is the probe, not the product.
