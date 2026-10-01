# Live runs: `feat/pipeline-failure-and-output-dir` (PR-1 of the backend API design)

**Date:** 2026-10-01. **Provider:** Groq (`GroqProvider`, default model `openai/gpt-oss-120b`, key attached by the cloud proxy). **Python:** 3.11.15. **Script:** `python scripts/run_dedupe_manually.py`, which runs `build_engine` with `PersonalityVoice`. Its new `--second-message` option runs a second turn in the same session, each turn in a fresh folder of its own.

**Runs completed:** 2 of 2 planned (design Section 8.3: "a normal turn, plus two turns in one session showing distinct folders"). There were 4 Groq calls in total, with no 429 and no other provider error.

**Fixture:** `default`, rewritten fresh at the start of each run: `scratch/uploads/manual_report.xlsx` has 6 rows (`name`/`amount`), 2 exact duplicates (Alice, Bob) and 1 fully-empty row mid-file.

**Independent check:** after each turn, the script re-reads the original and the delivered file with pandas and counts rows, duplicates and fully-empty rows. It hashes the original (the first 16 hex characters of its SHA-256) before and after each turn and across the whole run. In run 2 it also hashes turn 1's delivered file before and after turn 2, and checks that each delivered file is inside its turn's folder.

**What these runs can't show:** R6. A real provider can't make a stage raise without a test hook in production code, which the design deliberately doesn't add (Section 7.5). R6 is covered by the fake-LLM tests listed in `pipeline-failure-and-output-dir-impl.md`. Both live runs had `state.failure` = none.

## Summary

| # | Turn | Message | Intent | `clarification_required` | Delivered | Independent check matches the reply | False claim | Original unchanged |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | "remove duplicate rows from this file" | `remove_duplicate_rows` (0.99) | false | yes | yes | no | yes |
| 2 | 1 | "remove duplicate rows from this file" | `remove_duplicate_rows` (0.99) | false | yes | yes | no | yes |
| 2 | 2 | "now remove the empty rows" | `remove_empty_rows` (0.99) | false | yes | yes | no | yes |

**F5 (run 2):** the two turns' files are in different folders (`…/t0001/` and `…/t0002/`), and turn 1's delivered file is byte-identical after turn 2 (`b22a72101a55711d` → `b22a72101a55711d`).

## Run 1: a normal dedupe turn (focus mode, legacy output path)

`python scripts/run_dedupe_manually.py --mode focus --message "remove duplicate rows from this file"`

This run sets no `turn_output_dir`, so it checks that the default path is unchanged for existing callers.

- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false. **Failure:** none, every stage ran.
- **Steps executed:** `spreadsheet.analyze` (original) → `spreadsheet.deduplicate` → `spreadsheet.analyze` (re-check of the final file). All succeeded. Inspector passed.
- **Delivered:** yes, `workspace/outputs/manual-workspace/manual_report_deduplicated_manual-session-task-1.xlsx` (the legacy path, as before this branch).
- **Measurements:** deduplicate: input_rows=6, removed=2, output_rows=4, remaining=0.
- **Independent check:** the original has 6 rows, 2 duplicates and 1 empty row. The delivered file has 4 rows, 0 duplicates and 1 empty row. The dedupe skill doesn't remove empty rows.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none. 2 removed, 4 rows and 0 duplicates all match pandas. 25.0% is correct for the delivered file, where 1 of 4 rows is empty.
- **LLM calls:** Brain, Think.
- **Original hash:** `86856e2874481745` → `86856e2874481745` (unchanged).

## Run 2: two turns in one session, each in its own folder (focus mode)

`python scripts/run_dedupe_manually.py --mode focus --message "remove duplicate rows from this file" --second-message "now remove the empty rows"`

Run folder: `workspace/outputs/manual-workspace/manual-session/run-20261001T205209262743/`. Each turn's folder was created with `mkdir(exist_ok=False)` and set as `state.turn_output_dir` after `reset_transient()`.

### Turn 1: "remove duplicate rows from this file"
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false. **Failure:** none.
- **Steps executed:** analyze (original) → deduplicate → analyze (re-check). All succeeded. Inspector passed.
- **Delivered:** yes, `…/t0001/manual_report_deduplicated_manual-session-task-1.xlsx`. Inside turn 1's folder: true.
- **Measurements:** deduplicate: input_rows=6, removed=2, output_rows=4, remaining=0.
- **Independent check:** the original has 6 rows, 2 duplicates and 1 empty row. The delivered file has 4 rows, 0 duplicates and 1 empty row.
- **Reply:** "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?"
- **False claim:** none.
- **LLM calls:** Brain, Think.
- **Original hash:** `e545a5e911a6a0a0` → `e545a5e911a6a0a0` (unchanged).

### Turn 2: "now remove the empty rows"
- **Intent:** `remove_empty_rows` (0.99). **`clarification_required`:** false. **Failure:** none.
- **Steps executed:** `spreadsheet.remove_empty_rows` only. Proactive analysis didn't run again, because turn 1 marked the file analyzed in `Context`. Inspector passed.
- **Delivered:** yes, `…/t0002/manual_report_no_empty_rows_manual-session-task-0.xlsx`. Inside turn 2's folder: true.
- **Measurements:** remove_empty_rows: input_rows=6, removed=1, output_rows=5, remaining=0.
- **Independent check:** the step read the **original** upload (6 rows), not turn 1's file. The delivered file has 5 rows, 0 empty rows and still 2 duplicates.
- **Reply:** "Done — removed 1 fully-empty row(s). The copy has 5 row(s), 0 fully-empty row(s) remaining. Your cleaned copy of manual_report.xlsx is ready."
- **False claim:** none. Every number matches pandas, and the reply says nothing about duplicates.
- **Observation, not a defect of this branch:** a user who says "now … too" might expect turn 2 to build on turn 1's file. The pipeline applies each turn to the upload. That's backlog D3, for PR-2's multi-turn live run 7.
- **LLM calls:** Brain, Think.
- **Original hash:** `e545a5e911a6a0a0` → `e545a5e911a6a0a0` (unchanged).

### F5 check
- Different paths: true. Different folders: true (`t0001` vs `t0002`).
- Turn 1's delivered file: `b22a72101a55711d` → `b22a72101a55711d` after turn 2 (unchanged).
- Original across the whole run: `e545a5e911a6a0a0` → `e545a5e911a6a0a0` (unchanged).

**Limit of this run:** the two turns' file names already differ (`_deduplicated_…task-1` vs `_no_empty_rows_…task-0`), so this run shows separate folders but not a name collision that the folders prevent. A real collision needs two later turns of the same kind (both `task-0`). That case is covered by the fake-LLM test `test_turns_of_the_same_kind_write_to_different_folders` (three turns), not live.
