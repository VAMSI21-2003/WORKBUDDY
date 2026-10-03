# PR-B review harness

Runs the real `build_engine` (real skills and verifiers) with a fake LLM. No API key, no network.
The fake routes by system prompt: Brain and Think get fixed JSON, the Planner gets the scenario's
plan, anything else is "Voice" and is counted. Each scenario runs as template, friend (12 session
ids, so seeded openers/closers show) and focus = 14 turns; all scenarios = 532 turns.

    pip install -r requirements.txt
    python scripts/review_harness/harness.py --summary                  # this checkout
    python scripts/review_harness/harness.py --tree ../other --out o.json   # another tree
    python scripts/review_harness/harness.py --summary x2 a3 c6         # some scenarios only

Fixture: 6 rows (Alice, Bob, blank, Alice, Carol, Bob): 2 duplicates, 1 empty row. Ids match by prefix
(`x2` runs x2-broad-delete-file). Diff two trees' JSON; voice_calls must be 0, orig_unchanged true.
Scenario ids (user message; what Brain said; what the planner ran):
- c2 c3 c6 c7 c8: H1. A negation or "keep" names an action. c2 the forbidden dedupe ran; c3/c7 Brain
  gave no reading; c6 only the forbidden step ran; c8 "do not dedupe".
- k-*: H2. Brain misspelled the action name (`remove_duplicates`, casing, trailing space).
- a1-a5: H2. "remove duplicates and sort by amount" with a whole-message or partial `unsupported`.
- b2 b4 b5 b6: repeated `unsupported`; a kept action listed (L3); "keep the duplicates"; "please".
- e1 e2: paraphrase ("get rid of the repeated entries"); e1 = L1. x1: Brain's only entry is `unsupported`.
- x2-x6: broad `unsupported` phrases ("...and delete the file", "clean the data", "drop the sheet",
  "email me the report", bare "delete the file"). x2-x4 expose review finding N1 (false "Done —").
- x7 Brain missed one of two requests; x8 both listed; x9 plain request; x10 nothing to dedupe.
- m1-*: the analyzer's all-clear next to a residual. r-*: undelivered paths (must equal `main`).

## Re-review 3 scenarios (`rereview3.py`)

`rereview3.py` imports this harness and runs 77 more scenarios (14 turns each) with the same fake LLM:
`rr2-*` are the 32 adversarial scenarios from Re-review 2 §4, rebuilt from its table, and `z01`–`z45`
target the `688f62b` code (removal-verb strip, location strip, joiner rule, negation/keep). A scenario
with `sheets=2` adds a second sheet `Q2` to the fixture and records the delivered file's sheet names.

    python scripts/review_harness/rereview3.py --summary                 # this checkout
    python scripts/review_harness/rereview3.py --tree ../other --out o.json z09 rr2-s22

## Round 5 probes (head `d0b32c4`: R1 fix and the merge of `origin/main`)

Run each from the repo root; `--tree DIR` (a `git worktree` of another commit) works for `round5.py`.

    python scripts/review_harness/round5.py --out r5.json          # 228 turns, 38 scenarios
    python scripts/review_harness/round5.py a0 c-fail d0           # some scenarios only (prefix match)
    python scripts/review_harness/scope_diff.py [TREE]             # 41,412 generated `unsupported` phrases through _no_skill_outcome
    python scripts/review_harness/sheetname_probe.py               # crafted sheet names: does output's sheet list differ from input's?
    TREE=../main python scripts/review_harness/formulas_probe.py   # formulas, formatting and merged cells through dedupe

`round5.py` scenarios: `a*` multi-sheet and R1 scope phrases; `b*` odd workbooks (hostile/long/XML sheet
names, corrupt, not Excel, OLE2, encrypted, empty); `c-fail-*` a stage raises (all stages, with a secret in the
exception text); `d*` per-turn output folder and confirmation provider (decline, raise).
