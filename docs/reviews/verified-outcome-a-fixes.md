# Fixes for `docs/reviews/verified-outcome-a-review.md`: H1 and M1

Branch `feat/verified-outcome-a` (PR #3). Not merged. The review said the re-review needs only H1 and M1 fixed. M2 is PR-B's ledger; M3 and L1–L3 are tracked in `docs/backlog.md`.

This is the fixer's own record. It is not the re-review: per `CLAUDE.md`, a separate session with no memory of writing this should review these fixes.

## H1: "removed" now counts what the step removed, blank repeats included

**The flaw.** Dedupe (`drop_duplicates`) treats identical blank rows as duplicates and keeps one. The verifier's `removed` was measured on the non-empty rows only, so:
- BT (Alice, blank, Bob, blank, Carol): the reply said "there were no duplicate rows to remove" while the file went from 5 rows to 4.
- X-two-empty (6 rows: 1 duplicate data row, 2 identical blank rows): the reply said "removed 1" while 2 rows were removed.

**The fix** (`buddy/inspector.py`, `buddy/models.py`, `buddy/outcome.py`):
- Dedupe's `removed` is `int(original.duplicated().sum())` on the input as read, blank rows included. `StepMeasurement` gains `removed_blank`, the number of those that were repeated blank rows, plus a `removed_data` property.
- `removed` is kept only if the files confirm it:
  - `input_rows − rows in the output == removed`. The `.xlsx` round trip drops a trailing blank row, so a kept blank row that ends up last vanishes and makes the row delta larger than what dedupe removed. That case used to be masked, because the existing check leaves blank rows out on both sides. It now gives `removed=None`.
  - For dedupe, `removed_blank == max(blank rows in the input − 1, 0)`: exactly one of the identical blank rows is kept. A step that dropped every blank row also gives `removed=None`.
- When `removed` is `None`, the renderer says "The duplicate-row removal ran and passed its check." with no number. The final-state sentence ("The copy has N row(s)…") still comes from the verifier's read of the output.
- Rendering: "Removed 2 duplicate row(s), including 1 repeated blank row." (plural: "including 2 repeated blank rows"). The suffix appears only when `removed_blank > 0`.
- The empty-rows step is unchanged in meaning (`removed_blank` is 0), and it gets the same row-delta check.
- `friend.opener.seeing_double` ("That sheet was seeing double.") now needs `removed_data >= 1`. If dedupe removed only repeated blank rows, the user's data had no twins, and the opener would overstate.
- `docs/design/verified-outcome-design.md`: the `removed` field comment, the dedupe/empty-rows verifier bullets (previously "measured on the non-empty rows", the line the review cited), the rendering table, and the measurement test list are corrected.
- `scripts/run_dedupe_manually.py` prints `removed_blank` alongside `removed`.

**Tests.**
- `tests/buddy/test_engine_factory.py`, through `build_engine` (15 cases): scenarios BT and X-two-empty × single step and chain (dedupe, then empty rows) × `TemplateSpeaker`, friend and focus `PersonalityVoice`. Each case checks against fresh pandas reads:
  - the "Removed N" sentences add up to rows in minus rows out;
  - dedupe's N equals `original.duplicated().sum()`, and "including N repeated blank row" equals blank rows in minus one;
  - "The copy has N row(s)", the duplicates remaining and the empty rows remaining match the delivered file;
  - every number in the outcome block is in the set pandas confirms, and so are the analysis finding's numbers;
  - the original is byte-identical afterwards (rule 2).
  - A further test (3 speakers) covers the round-trip case (Alice, blank, Alice): rows 3 → 1, `removed` unconfirmable, so the reply has the no-count sentence and no "removed"/"Removed" and no "no duplicate rows to remove".
  - **All 15 fail on the pre-fix `buddy/`**, and they pass with the fix.
- `tests/buddy/test_inspector.py` (4 new): BT gives `removed=1, removed_blank=1`; X-two-empty gives `removed=2, removed_blank=1`; a round trip that dropped a blank gives `removed=None`; a step that dropped every blank gives `removed=None`.
- `tests/buddy/test_outcome.py`: two renderer rows for the "including … repeated blank row(s)" wording; the number-provenance property test now includes `removed_blank`, and its random reports include blank repeats.
- `tests/buddy/test_voice_catalog.py`: "seeing double" does not fire when only blank repeats were removed.

**Rendered replies now** (from the harness, `TemplateSpeaker` and friend/focus `PersonalityVoice`; file names elided):

| Scenario | Reply outcome block |
|---|---|
| BT, single dedupe | "Done — removed 1 duplicate row(s), including 1 repeated blank row. The copy has 4 row(s), 0 duplicate row(s) remaining." |
| BT, chain | "Removed 1 duplicate row(s), including 1 repeated blank row. Removed 1 fully-empty row(s). The copy has 3 row(s), 0 duplicate row(s) and 0 fully-empty row(s) remaining." (5 → 3) |
| X-two-empty, single dedupe | "Done — removed 2 duplicate row(s), including 1 repeated blank row. The copy has 4 row(s), 0 duplicate row(s) remaining." |
| X-two-empty, chain | "Removed 2 duplicate row(s), including 1 repeated blank row. Removed 1 fully-empty row(s). The copy has 3 row(s), … remaining." (6 → 3) |
| Alice, blank, Alice | "Done — the duplicate-row removal ran and passed its check. The copy has 1 row(s), 0 duplicate row(s) remaining." |

**Not fixed, on purpose.** In the round-trip case the user gets no count. A count of "removed 2 duplicate rows" would be a claim about the step that the files don't support, and the user asked for the no-count sentence in that case.

## M1: three completion-implying closers removed

`buddy/voice_catalog.py`: removed `friend.closer.anything_else`, `friend.closer.whats_next` and `focus.closer.anything_else`, and their predicate `_asks_nothing_else`. Every mode's closer slot now has only its empty entry, so no closer is ever added to a reply. (The friend and focus openers keep their reviewed entries.)

**Tests:** `test_no_closer_implies_the_request_is_finished` (the three ids are gone, every closer is empty, for every report and mode); the H1 end-to-end tests assert no "Anything else" or "What's next" in any reply; the selection-variety test now checks openers only.

**Backlog** (`docs/backlog.md`): under PR-B, "reintroduce completion-implying closers only when the ledger confirms every requested action is DONE".

## Backlog additions

- **M3** (multi-sheet workbooks lose every sheet after the first while the reply says "cleaned copy"): item **1a**, right after PR-B, marked high priority, with acceptance criteria. (Inserted as "1a" so the existing cross-references to items 2, 3 and 4 stay valid.)
- **L1, L2, L3** from the review: under item 5, marked Low, and prefixed "verified-outcome-a" because item 5 already has different L1 and L3 entries from `chaining-wip-review.md`.

## Verification

- `python -m pytest -q`: **710 passed** (about 51 seconds), no failures or skips.
- `ruff check buddy tests`: 22 errors, the same count as before the change (known, pre-existing). None of the changed lines add one.
- Live runs with Groq were not repeated for these fixes. The delivered reply is code-rendered with no model call on delivered turns, and the fake-LLM end-to-end tests exercise the whole real pipeline, but `CLAUDE.md` asks for live runs before merging, so they're still owed to `docs/reviews/verified-outcome-a-live-runs.md`.

## Deferred (unchanged from the review)

M2 (PR-B), M3 (backlog 1a), L1, L2, L3 (backlog item 5).
