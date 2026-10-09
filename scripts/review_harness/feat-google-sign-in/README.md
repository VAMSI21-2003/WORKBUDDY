# Review harness: `feat/google-sign-in`

Reviewer probes, not part of the suite. Run from the repository root with the venv active
(`source /root/wbvenv/bin/activate`). Real app and real pipeline; only the LLM is faked, and Google
is the suite's fake identity provider (`tests/idp`, real RS256 tokens). The suite's helpers
(`tests/backend/helpers.py`, `tests/backend/signin_helpers.py`) and the other branches' harnesses
(`../feat-frontend-demo/fe1.py`, `ui_probe.py`, `rule_checks.py`, `../feat-frontend-deploy/compare_ui.py`)
are imported, never edited. Round-1 outputs are in `out/`.

| Script | What | Output (`out/`) |
|---|---|---|
| `../feat-frontend-demo/ui_probe.py [--tree /root/base]` | PR-3's 25 scenarios x 3 speakers through the page (dev mode), head and base; compare with `../feat-frontend-deploy/compare_ui.py HEAD BASE`; rule 1/2 with `../feat-frontend-demo/rule_checks.py` | `ui_probe_{head,base}.jsonl`, `ui_head_vs_base.txt` |
| `signed_in_probe.py [--speaker S] [--only NAME]` | The same 75 runs as Alice signed in with Google on production settings; after each, Bob (signed in) hits all 12 session routes of Alice's session (each must equal the unknown-id answer byte for byte), and `GET /` with Alice's pointer planted | `signed_in_head.jsonl`, `signed_in_vs_base.txt` |
| `adversarial.py [--only cNN]` | 23 cases: c01 token variants into `verify_id_token` (alg confusion, aud lists, nonce, nbf, kid...), c02 login CSRF, c03 replay and two tabs, c04 callback junk, c05 allowed list + restart, c06 restart paths, c07 damaged owner files, c08 v1 fixture, c09 rollback to base and forward (base runs in a subprocess from `/root/base`), c10 operator and demo password in production, c11 per-person limit across addresses, c12 log redaction, c13 hostile email, c14 sign-out CSRF, c15 Bob answers Alice's plan, c16 file ids across sessions, c17 eviction, c18 demo-password mode landing (H1), c19 path tricks, c20 switching person in one browser, c21 recycled email, c22 expiry, c23 landing after a sweep | `adversarial_head.jsonl` |
| `mutations.py [--jobs 4] [--only M01,M02] [--wide]` | 34 one-line mutations in `git archive HEAD` copies (M00 = unmutated, must pass; M35 = the H1 prototype fix, must pass). Default runs the 13 sign-in/ownership test files; `--wide` runs `tests/backend`, `tests/web` and the saved-data test (the root-only retention test deselected) | `mutations.txt`, `mutations_wide.txt` |

Other outputs: `suite_and_lint.txt` (suite, ruff per file and rule head vs base, the CI baseline
check), `new_tests_on_base.txt` (the branch's new test files over a `git archive origin/main` copy),
`proto_fix_h1.diff` and `proto_fix_h1_adversarial.jsonl` (c18, c06, c20, c23 with the prototype fix,
run from a scratch copy that has `adversarial.py` copied into this same path).

## Round 2 (commit 75eb95b)

| Script | What | Output (`out/`) |
|---|---|---|
| `adversarial.py` (round 1's 23 cases, unchanged) | rerun on the head | `r2_adversarial_r1cases_head.jsonl` |
| `adversarial_r2.py [--only rNN]` | 8 cases on the round-1 fixes: r01 demo password, three holders and a restart (with a delivered turn); r02 dev, two browsers; r03 Google + demo password outside production; r04 the operator on `GET /`; r05 stale and hostile callbacks vs the binder; r06 the issuer warning; r07 `latest_for` owner edge strings; r08 a Google person then the demo password in one browser. Imports `adversarial.py` | `r2_adversarial_r2.jsonl` |
| `mutations_r2.py [--jobs 3] [--only N01] [--wide]` | Round 1's survivors (M08, M12, M19, M26, M30) and N01-N09 on the fixes; multi-patch mutations; imports `mutations.py`. `--wide` adds `tests/e2e` | `r2_mutations.txt` |
| `../feat-frontend-demo/ui_probe.py [--tree /root/base]`, `signed_in_probe.py`, then `../feat-frontend-deploy/compare_ui.py` and `../feat-frontend-demo/rule_checks.py` | as in round 1 | `r2_ui_probe_{head,base}.jsonl`, `r2_signed_in_head.jsonl`, `r2_probes_vs_base.txt` |

Also: `r2_suite_and_lint.txt`, and `r2_new_tests_on_round1_code.txt` (the round-2 tests over a `git archive 948ec6c` copy with the head's three test files).
