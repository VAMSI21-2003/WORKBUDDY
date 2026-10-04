# Fixes for the review of `feat/verified-outcome-b`

**Review:** `docs/reviews/verified-outcome-b-review.md` (verdict: DON'T MERGE, H1 and H2).
**Fixed here:** H1, H2 and M1, plus L2 (which the H2 fix needed anyway). L1 and L3 go to `docs/backlog.md` (item 1, "Deferred").
**Not merged.** A re-review by a separate session is still needed (`CLAUDE.md`: the session that wrote the code doesn't review it).
**Suite:** `python -m pytest -q` gives **1008 passed** (review: 839). `ruff check buddy tests` finds 22 errors, the same set as `main` (line numbers stripped); none are new.
**Live runs:** `docs/reviews/verified-outcome-b-live-runs.md`, "After the review fixes": 4 of 4 completed, 0 false claims, the original unchanged in every run.

## H1: a mention entry never confirms a request

**Cause.** `request_complete`, "Partly done"'s `requested_done` and the `DONE` derivation accepted a ledger entry from any source, so the mention detector (which also matches "don't remove duplicates") could turn work the user forbade into `DONE`, and then into "Done —" and a completion closer.

**Change** (`buddy/outcome.py`):
- `request_complete` needs every action to be `DONE` with a **Brain** source (`brain` or `intent`).
- `lead_word`: "Partly done" needs a `DONE` action **from Brain's reading** and a `NOT_DONE` one.
- Derivation of a passing step:

| Ledger entry for its capability | Brain gave a reading | Status | Source |
|---|---|---|---|
| Brain or intent | (yes by definition) | `DONE` | that source |
| mention only | yes | `DONE_UNREQUESTED` | `""` |
| mention only | no | `DONE` | `"mention"` (blocks "Done —", closers) |
| none | yes | `DONE_UNREQUESTED` | `""` |
| none | no | `DONE` | `""` |

A mention entry can still only add lines to the not-done side, or (with no Brain reading) leave the reply as plain as PR-A's.

**Docs corrected.** The design's 2.2 table ("yes (any source)") and the impl doc's table and lead-word rules now say the above.

**Tests.**
- `build_engine`, each across template/friend/focus and six session ids (so the seeded openers and closers are exercised):
  - `c2`: "don't remove duplicates, just remove the empty rows". Dedupe is stated as "I also removed 2 duplicate row(s), though I didn't read that as part of your request."; no "Done", "Partly done", closer or "seeing double" opener.
  - `c3` and `c7`: "remove the empty rows but keep the duplicates" / "keep the duplicates, remove the empty rows", with no `requested_actions`. Both removals are stated; no "Done" or closer.
  - `c6`: Brain reads only `remove_empty_rows`, the planner ran only dedupe. Dedupe is unrequested, the empty-row removal is "read as part of your request" but not run, and the residual "It still has 1 fully-empty row(s)." is stated; no "Partly done", no "only part of this went through".
- Unit tests: the derivation table above, `test_lead_word` and the catalog fixtures (a mention-only `DONE` is not `request_complete`).
- All new `build_engine` cases (159, across H1, H2, L2 and M1) fail on the pre-fix code (`buddy/` stashed); the 3 other cases my `-k` filter matched are the existing C1/C2 tests, which pass either way.

**Existing tests that changed, and why (all of them).** These encoded the old rule:

| Test | Was | Now |
|---|---|---|
| `test_full_slice_reports_a_failed_re_check_without_failing_the_delivered_file`, `test_full_slice_names_the_delivered_file_when_the_initial_analysis_fails` | `startswith("Done — removed 1 fully-empty row(s). Removed 1 duplicate row(s).")` | `startswith("Removed 1 fully-empty row(s). Removed 1 duplicate row(s).")` and no "Done". Their Brain reply has no `requested_actions`, so both actions come from the detector alone. |
| `test_h1_removed_counts_repeated_blank_rows_and_every_number_matches_pandas` (chain cases) | `reply.count("Done — removed") == 1` | no "Done" in the reply. The duplicate-sentence regex accepts an optional "Done — ". The single-step cases (Brain's intent) still lead "Done". |
| `test_outcome.py::test_lead_word` | a mention-only `DONE` next to a `DONE` gave "Done"; mention-only `DONE` + `NO_SKILL` gave "Partly done" | both give `None`; a mention-only `DONE` alone gives `None` |
| `test_voice_catalog.py` `_COMPLETE["chain"]` | second action from `mention` | second action from `intent`; two mention-only reports added to `_INCOMPLETE` |
| `test_brain.py::test_an_action_outside_the_vocabulary…` | parametrised with `Remove_Duplicate_Rows` | `remove_duplicates` (see H2); the casing case has its own test |

No test was skipped, marked `xfail` or deleted.

## H2: `NO_SKILL` never quotes or describes something that was done

**Cause.** Any action string not exactly in the vocabulary became `unsupported`, and every `unsupported` entry was rendered "I can't do this part yet: “<phrase>”", whatever the phrase named.

**Change.**
- `buddy/brain.py`: the action name is stripped and casefolded before the vocabulary check ("Remove_Duplicate_Rows", "remove_duplicate_rows " are that action).
- `buddy/outcome.py`, `_no_skill_outcome`: for each `unsupported` entry, take its phrase and remove
  1. every match of an action's mention pattern, and
  2. the words Brain quoted for an action whose step passed (a paraphrase like "get rid of the repeated entries").

  If something was removed (the phrase names an action) and only filler is left (`_FILLER_WORDS`: articles, "please", "remove", "keep", "rows", …), the entry is **dropped**. If other words are left ("sort by amount"), the entry renders the **unquoted generic** sentence, since a quote would include the named action. If the phrase names no action, it's unchanged: quoted when it's in the message, generic otherwise.
- Identical `NO_SKILL` sentences are collapsed (L2), comparing the quoted phrase casefolded; all generic ones count as one.

**One deviation from the review's wording.** The review asks for this only when the named action is `DONE`. I apply it to any action the phrase names, done or not, because `b5` ("keep the duplicates", dedupe not run) needs it: "I can't do this part yet: “keep the duplicates”" is false when keeping them is what happened, and the mention detector already gives the true sentence for that action (`NOT_READ_AS_REQUEST`). Dropping more can only make the reply say less, never claim more.

**Known limits.**
- The filler list is a heuristic. An unrecognised extra word in a phrase that names an action gives the generic "something I can't do yet" sentence, which is a true statement about an unclear part of the request, but it can be noisy. The false-positive direction (dropping a real unsupported request) needs a phrase made only of filler and an action's words, so nothing is asked beyond a done or mentioned action.
- An `unsupported` phrase with no action words and nothing but filler ("please", `b6`) is still quoted. Left as is; it can't name anything done.

**Tests** (`build_engine`, three speakers):
- `k-remove_duplicates`: "remove_duplicates", "Remove_Duplicate_Rows", "remove_duplicate_rows ", "deduplicate" (six session ids). Reply is "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining."; no "can't do", no "Partly done", no "only part of this went through".
- `a1`: whole-message unsupported phrase gives "Partly done — removed 2 duplicate row(s). I read part of your request as something I can't do yet. …", never the quote.
- `a4`: unsupported "remove duplicates" is dropped: no "can't do", no "Partly done".
- `b5`: "keep the duplicates" is dropped; the reply keeps the N1-style lines and "It still has 2 duplicate row(s)."; no "Partly done".
- `l2`: repeated `unsupported` entries produce one quoted and one generic sentence.
- Unit: the drop/generic/quote table, the paraphrase case, and a dropped entry that names an action that didn't run.
- Brain: a casing or whitespace slip is still that action; `remove_duplicates` is `unsupported`.

## M1: the analyzer's all-clear next to a residual

`buddy/voice.py`: when any residual `remaining` is non-zero, the analyzer's note "none of the issues I check for were found" is replaced by "none of the other issues I check for were found". With no residual it's unchanged (existing tests). Test: the N1 shape through `build_engine`, three speakers ("It still has 2 duplicate row(s)." then "None of the other issues I check for were found.").

## Deferred (also in `docs/backlog.md`)

- **L1:** `DONE_UNREQUESTED` when Brain's only reading is an `unsupported` entry.
- **L3:** "Partly done" when Brain lists something the user said to keep; mention noise on "deduplicated" and "blank lines".
- Carried over unchanged: M3 and the PR-A review's L1–L3.
