# fix/security-pass: fixes after review round 3, and the owner's decision on R1-2

Round 3 (`docs/reviews/fix-security-pass-review.md`, reviewed head `c766fa6`) ended DON'T MERGE with five blocking MEDIUMs. I did not edit the review file. The owner then decided R1-2 (option b) and asked for review round 3 to be run again over the current head. I fixed R3-1 to R3-5 first, because the same reviewer would have found the same five again; the new review is numbered round 4 in `fix-security-pass-review.md` and is written by a new Opus reviewer.

**Not a third patch of one mechanism.** R3-1 and R3-2 are regressions of the reader-based walk (`_reader_verdict`) and R3-3 to R3-5 are the same kind of gap in what the walk doesn't cover (parts openpyxl parses whole, the text cap, the analysis). The walk itself is unchanged in design. If the new review finds another way past the walk, the mechanism, not the walk's counters, needs replacing.

## R1-2 (owner's option b)

`buddy/outcome.py`: an `unsupported` entry whose words overlap a done action's phrase now has the outcome `NO_SKILL_UNSURE`. Overlap means: Brain's phrase for a done action is inside the entry's words and does not name that action (z31: "delete the file" filed under duplicate removal; e2/S11, S15: a paraphrase like "get rid of the repeated entries" that the mention pattern doesn't match), or that phrase spans two requests and the entry holds all of it (a whole-message flag). The reply has no lead word ("Done" would be false when more was asked, "Partly done" false when the flag was a double-listing) and says: "Some of your wording overlaps what I did, so I can't tell if you asked for something more; if you did, I haven't done it, so check the copy is what you wanted." What was done is still stated first. "Partly done … can't do yet" stays for an entry that overlaps no done phrase ("remove duplicates and sort by amount"). `request_complete` is false, so no "Done" and no closer that implies the request is finished.

Tests: `tests/buddy/test_residual_done_paths.py` (z31 ×5 messages, e2 and S15, a whole-message flag, and the control that "Partly done" stays for "sort by amount", each in template, friend and focus), `tests/buddy/test_outcome.py` (the outcome, the lead word, the sentence), `tests/buddy/test_engine_factory.py` (s22). The whole `tests/buddy` suite passes (2,115).

Side effect to know: the whole-message flags "remove and delete the duplicates" and "find the duplicates and remove them" used to say "Partly done"; they now get the neutral sentence (their done phrase spans two requests, so they overlap it). "Don't leave any duplicates" and "delete the duplicates of my file" still say "Partly done" (leftover words, no overlap with a two-request phrase).

## R3-1 to R3-8

| Finding | What I did |
|---|---|
| **R3-1** one part declared as thousands of sheets | Rows are counted across all sheets together (`all_rows`). `TOO_MANY_ROWS` now says "the workbook has more than 200,000 rows (all its sheets counted together)", true for one sheet or many; `TOO_MANY_CELLS` likewise. Tests: 2,000 sheets on one part refused in seconds (the mutation takes 93 s), rows across sheets add up. |
| **R3-2** `workbook.xml` parsed before it is size-capped | `_scan` refuses a `workbook.xml` over 1 MiB and a `styles.xml` over 8 MiB by declared size, before any openpyxl load (`TOO_BIG_PART`). Tests assert `openpyxl.load_workbook` is never called. |
| **R3-3** parts openpyxl reads whole | `keep_links=False` in all five loads; styles capped (above); more than 1,000,000 shared strings refused, counted by the expat pass that already streams every part (`_part_verdict`). A file at that cap (1,000,000 strings, 1,000,000 cells, 5 MB): check 26 s, process peak 398 MiB including building the file. A turn loads the workbook about 8 times, so about 3.5 minutes of reads (backlog, with R3-8). |
| **R3-4** `MAX_TEXT_CHARS` 50 M | 10 M. The reviewer measured about 30 bytes per character over a turn, so a file at the cap is about 300 MiB. Not re-measured as a whole turn here. |
| **R3-5** near-duplicate pairs | Splink gets at most 100 characters of each field, and the check is skipped (no findings) when its two blocking rules would pair more than 250,000 rows, counted with `value_counts` before Splink starts. Tests: the frame Splink is given, a table over the pair limit never reaches Splink, one under it does. A skipped check says nothing, the same as the existing row limit. |
| R3-6 | Closed by `keep_links=False` in the walk; M2 (a grid bomb with a missing external-link part) is a test. |
| R3-7 | Tests: an understating `<dimension>`; the date guard by counting `strptime` calls (the old test was fast without the guard); all 17 quote marks, compared with the module's own set so a mark added there fails the test until added here. The reviewer's three surviving mutations are killed (checked). The "17 kinds" claim in `fixes2` was wrong when written (the test had 8) and is true now. |
| R3-8 | Owner's call, unchanged (backlog). |

Mutations I checked: `all_rows` back to per-sheet, `reset_dimensions()` removed, `keep_links` dropped from the walk, the styles cap and the shared-strings cap removed, the 100-character slice removed, the pair limit removed. Each is killed by a test added here.

## Run

Lint: nothing beyond the baseline. Full suite on the committed head is in the report. No live runs (instruction).
