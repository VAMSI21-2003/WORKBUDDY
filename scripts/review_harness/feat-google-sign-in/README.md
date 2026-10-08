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
