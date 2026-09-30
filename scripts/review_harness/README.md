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
