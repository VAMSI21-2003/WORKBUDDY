# Round 5 probes: what each one covers and the replies worth knowing

Head `073be8a` (source of the mechanism: `0eb950c`), base `origin/main` `c9592ca`. Raw outputs are in `out/` (git-ignored); every script
regenerates its own. `README.md` has the commands. "template / friend / focus" = the three speakers; "alone" = nothing but the report.

## Scripts

| Script | Scenarios x speakers | What it asks |
|---|---|---|
| `r5_shapes.py` | 54 x 3 = 162 turns | A: every shape in which Brain can hand over a report (intent only, action only, both, names in odd case, phrase not in the message, report beside an `unsupported` extra, intent contradicting the actions, a greeting that asks for one, Brain failing). B: report + dedupe with the planner naming the report step `spreadsheet.analyze` / `spreadsheet.report` with Brain's words / with other words / not at all, report first, three steps, a sort in the chain, planner garbage. C: Brain does NOT read the report (`unsupported`, nothing, unknown action names, `conversation`, the report ignored). D: Brain's report phrase swallows more than the report. |
| `r5_files_faults.py` | 76 x 3 = 228 | The report on 21 file kinds and no file, alone and after a dedupe; every stage failing (analysis skill, both verifiers on the 1st/2nd call, pandas read on the 1st/2nd/3rd call, planner, Hands, Think failing, Think asking). |
| `r5_sessions.py` | 10 sessions x 3 = 60 turns | Report after a delivery, twice, after a greeting, after a question; a dedupe+report followed by another. |
| `r5_hostile.py` | 24 x 3 = 72 | Empty/whitespace message, regex and backslash phrases, a 1-character phrase, 1000 report entries, wrong JSON types, Brain's own words in an action name, planner steps with an empty/missing phrase or quoting the report phrase. |
| `r5_api.py` | 2 speakers x 4 turns | The HTTP app: report alone, dedupe+report, report after a delivery; download names and the original's hash. |
| `r5_fuzz.py` | seed 11: 250 x 3; seed 12: 250 x 2; seed 21 (`--bias delivered`): 200 x 3 | Seeded random Brain/Think/planner/file/fault combinations; 8 invariants (header of the file). |
| `r5_mutations.py` | 53 mutations | One-place mutations of `actions`, `brain`, `planner`, `outcome`, `voice` and the three prompts. |
| `r5_fold.py` | 53 x 3 = 159 | The round-3 H1 class: a report plus an extra (11 wordings: in pdf, in French, to my boss, in a new sheet, print it ...). Brain FOLDS the extra into the report phrase, SPLITS it into an `unsupported` entry, or files the whole as `unsupported` (what the base's prompt asked for). |
| `r5_fold2.py` | 4 x 3 | Brain folds a file-changing clause ("remove the empty rows") into the report phrase. |
| `r5_wordings.py` | 60 x 3 = 180 | 30 wordings that broke rounds 1-3, as `report_on_file`, alone and after a dedupe: one distinct reply per speaker and shape. |
| `r5_l11.py` | 6 | The pre-existing exception-text leak, head vs base. |
| `r5_cmp.py`, `r5_harness_check.py` | | Head-vs-base comparison of any probe / shared-harness output. |

## Replies worth knowing (template wording; friend and focus add their opener and file line)

Brain reads the report (`report_on_file`):

- alone, plain file: `Report on your original file: 2 rows repeat an earlier row exactly; name: 1 missing value (16.7%); amount: 1 missing value (16.7%). No changes were made to any file this turn.`
- dedupe + report: `Done — removed 2 duplicate rows. The copy has 4 rows, with 0 duplicate rows remaining. Saved as ... The copy has the values only; ... Report on the copy: name: 1 missing value (25.0%); amount: 1 missing value (25.0%).`
- the re-check fails: `Partly done — removed 2 duplicate rows. I couldn't produce the report you asked for: I couldn't re-check the final file. The copy has 4 rows, ...`
- the analysis fails (alone): `I couldn't produce the report you asked for: the analysis of your file failed. No changes were made to any file this turn.`
- multi-sheet / merged cells / no header: `I can't work on this file: the workbook has 2 sheets, ... No changes were made to any file this turn.`
- chain stopped: `... I didn't produce the report you asked for, since no file was handed back. I stopped at the deduplicate step ...`

Brain does not read it (the stated cost, `r5_shapes.py` group C):

- `unsupported`, alone: `I can't do that yet: “give me a report”. I can remove ... Here's what I noticed when I looked it over: ... Want me to tackle any of these? No changes were made ...`
- `unsupported`, after a dedupe: `Partly done — removed 2 duplicate rows. I can't do this part yet: “give me a report”. ... I also noticed: ...` (the base says the same)

The finding that blocks (H1, `r5_fold.py`, `r5_fold2.py`): when Brain puts an extra or another action INSIDE the report's `source_phrase`, nothing in the code sees it:

- `remove the duplicate rows and give me a report in pdf`, Brain `report_on_file` = `give me a report in pdf`: `Done — removed 2 duplicate rows. ... Report on the copy: ...` (11 of 11 wordings, 3 speakers). Split into its own `unsupported` entry the reply says `Partly done ... I can't do this part yet: “in pdf”` (10 of 10); the base said the same for all 11.
- `remove the duplicate rows, remove the empty rows and give me a report`, Brain `report_on_file` = `remove the empty rows and give me a report`, planner plans only the dedupe: `Done — removed 2 duplicate rows. ... Report on the copy: ...` while the copy still has 1 fully-empty row. The mention cross-check would have said so; `except_inside` makes it blind inside Brain's report phrase.

Findings of this round (details in `docs/reviews/feat-reply-quality-review.md`, "Review round 5"):

- D04/D06: Brain's phrase `give me a report`, message `give me a report on the duplicate rows`: `Your message mentions duplicate rows, but I didn't run the duplicate-row removal. I read your request as: report on the file. ... Report on your original file: ...`
- D01/D03/D09, H22-H24: Brain's report phrase is the whole message, so the removal / sort in it is not mentioned: `Report on your original file: ... No changes were made to any file this turn.`
- B03/H19: the planner names the report step `spreadsheet.report` with words other than Brain's: `The report step: couldn't be run because no available skill can do it. I didn't produce the report you asked for, since no file was handed back.` (decided behaviour for a step no skill can run; the step is the report)
- X-read-excel-raises-all/1st-alone: the analysis skill fails: `I wasn't able to confirm that worked. The analysis reported failure.` (older sentence, L9)
- S5: `give me a report on the cleaned file` after a delivery: `Report on your original file: 2 rows repeat ...` (labelled; the copy is not re-read in a later turn)

Pre-existing, identical on the base (`r5_l11.py`, `probe_f_header_text.py`): a column header and a verifier's exception text reach a reply.
