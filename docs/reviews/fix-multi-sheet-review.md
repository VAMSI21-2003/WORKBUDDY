# Review: `fix/multi-sheet` (backlog item 1a, review M3)

Review only. No file under `buddy/`, `skills/` or `tests/` was changed. This file and a short addition to `docs/backlog.md` item 1a (see "Deferred") are the only files written or committed.

> **Reviewer independence.** I have no memory of writing this code. I read `docs/reviews/fix-multi-sheet-impl.md` and `fix-multi-sheet-live-runs.md` only to get the list of claims to check. Every finding below comes from a scenario I ran myself.

## 0. Method

- **Trees.** The branch is at `aafc396`. `main` is at `310f660` (`origin/main`), checked out as a temporary git worktree in the session scratchpad (removed afterwards). Each harness run puts the tree under test first on `sys.path` and runs from that tree.
- **Harness.** My own scripts, in the session scratchpad, not in the repo. Every scenario goes through the real `build_engine`, with the real registered skills and verifiers. Only the LLM is faked, using the `_QueuedLLMProvider` and the canned Brain/Think/Planner replies from `tests/buddy/test_engine_factory.py`. Each run copies the fixture into its own temp directory as `uploads/report.<ext>`.
- **Grid.** 24 fixtures × {dedupe, remove empty rows, chain dedupe → empty rows} × {`TemplateSpeaker`, `PersonalityVoice` friend, `PersonalityVoice` focus} = 216 runs per tree. I ran the grid twice per tree: once with `xlrd` blocked (`sys.modules["xlrd"] = None`, the documented environment) and once with `xlrd` 2.0.2 installed.
- **Recorded per run:**
  - the reply, `stopped_at`, each step's status and `failure_reason`, and each verdict;
  - an independent read of the input's and each delivered file's sheet names (openpyxl for zip content, pandas otherwise) and row counts;
  - every file left under `workspace/`;
  - whether the reply matches `voice._COMPLETION_CLAIM`;
  - Voice-model calls;
  - the original's SHA-256 before and after.
- **Environment note.** To build fixtures I temporarily installed `xlwt`, `msoffcrypto-tool` and `xlsxwriter`. `xlsxwriter` changes pandas's default `.xlsx` writer and so changes the skills' error text, so I uninstalled it and **re-ran all four grids without it**. Only the second set of grids is reported. All four packages, and `xlrd`, were uninstalled before the final suite run.

**Run counts:** 864 grid runs, plus 54 probe runs (text injection, the `xlrd` sheet-type blind spot, a missing sheet part, column names on `main`) and 18 Inspector chain runs (§5). That's 936 harness runs in all. **The original's hash was unchanged in every one.** There were also 4 live Groq runs (§7).

### Fixtures

| Fixture | How it was built |
|---|---|
| `one_Sheet1`, `one_Data` | pandas, one sheet (6 rows: 2 exact duplicates, 1 empty row) named `Sheet1` / `Data` |
| `one_31char` | the same, sheet name `A`×31 |
| `one_40char` | `one_Sheet1` with `workbook.xml` edited to name the sheet `B`×40 |
| `one_forbidden_slash`, `one_forbidden_bracket` | `one_Sheet1` with `workbook.xml` edited to `Q1/Q2` / `[Data]` (characters Excel forbids) |
| `two_plain` | `Q1` (the 6 rows) and `Q2` (2 rows) |
| `two_hidden`, `two_veryHidden` | `two_plain` with `Q2` set to `hidden` / `veryHidden` (openpyxl) |
| `two_hidden_first` | `two_plain` with `Q1` hidden and `Q2` active |
| `chartsheet` | `Data` worksheet plus a real chart sheet `Chart1` (openpyxl `create_chartsheet`) |
| `one_macro.xlsm`, `two_macro.xlsm` | `one_Sheet1` / `two_plain` with the macro-enabled workbook content type |
| `one_renamed.xls`, `two_renamed.xls` | `.xlsx` content under an `.xls` name |
| `one_legacy.xls`, `two_legacy.xls` | real BIFF8/OLE2 files written by `xlwt` |
| `corrupt_trunc` | `two_plain` cut in half |
| `corrupt_notbook` | a zip holding only `hello.txt` |
| `corrupt_sheetxml` | `one_Sheet1` with `sheet1.xml` replaced by garbage |
| `empty_zero_rows` | `openpyxl.Workbook()` saved with no cells |
| `header_only` | header row, no data |
| `one_password`, `two_password` | `one_Sheet1` / `two_plain` encrypted with password `secret` (`msoffcrypto` `OOXMLFile.encrypt`, which gives the standard OLE2 `EncryptedPackage` container Excel writes) |

Probe fixtures are described where they're used.

## 1. Summary

| ID | Severity | Rule | Introduced here? | Finding |
|---|---|---|---|---|
| **H1** | **High (latent)** | 1 | **Yes** | With `xlrd` installed, the sheet count for a legacy `.xls` skips chart sheets, macro sheets and VB modules, and so does the Inspector's check. A two-sheet `.xls` whose second sheet is one of those is **delivered with one sheet**, and the reply says "Your cleaned copy of report.xls is ready." It can't happen with `requirements.txt` as it is, because without `xlrd` every legacy `.xls` is refused. But the branch adds and tests this `xlrd` path, and the backlog expects `xlrd` to be added. |
| **H2** | **High** (by the rule; see the mitigation) | 1 | **Yes** (the check misses it) | openpyxl silently drops a declared sheet whose XML part is missing, so a damaged workbook that declares `Q1` and `Q2` counts as one sheet. It is delivered with only `Q1`, the reply says "Done — removed 2 duplicate row(s)…", and the Inspector check passes because it uses the same filtered list. **Mitigation:** `Q2` holds no data anywhere in the file, so nothing the user has is lost. |
| **M1** | Medium | 1 (a wrong reason, not an outcome claim) | **Yes** | A **password-protected `.xlsx`** is described as "**an .xls workbook**" whose sheets "can't be counted because the xlrd library isn't installed". Both parts are false, and installing `xlrd` wouldn't help. `main` said "execution did not produce an output file", which is vague but true. |
| **M2** | Medium | 1 (wrong reason) | **Yes** | A damaged workbook whose only sheet part is missing gets "**the workbook has 0 sheets**, and only single-sheet workbooks are supported for now". It declares one sheet, and the real problem is that it's damaged. |
| **M3** | Medium | 1 | **Yes** (new channel; the class is pre-existing) | Hands now relays raw skill error text, including library exception messages, into the reply. Exception text can carry text from the file. With a crafted `[Content_Types].xml`, the reply contains "**removed 2 duplicate rows, your cleaned copy is ready**", and it matches the project's own `_COMPLETION_CLAIM`. It's framed as an error and nothing is delivered. Column names already reach replies the same way on `main` (§4.3). |
| M4 | Medium | 1 (omission) | No (on `main` too) | The deferred item: on a refused multi-sheet workbook, the analysis describes only the first sheet and asks "Want me to tackle any of these?" (§8). It doesn't block the merge. |
| L1 | Low | — | Yes (newly visible) | Write errors reach the reply as `could not write PosixPath('workspace/outputs/…')`. |
| L2 | Low | — | Yes (newly reachable) | A failed write (a sheet name Excel forbids) leaves a partial output file under `workspace/outputs/`. It isn't handed back. |
| L3 | Low | — | Yes | Crafted sheet names Excel forbids (`Q1/Q2`, `[Data]`) were delivered on `main` (renamed `Sheet1`). They now fail honestly at the write. A crafted 40-character name is now written as is, and openpyxl warns some applications can't read it. |
| L4 | Low | — | Yes | The Inspector finding for reordered sheets says "a sheet was lost or renamed", which is wrong for a reorder. A non-workbook output gets a raw pandas message ending in "..". Neither is reachable with today's skills. |
| L5 | Low | — | Yes | A hidden or very-hidden second sheet gives "the workbook has 2 sheets" when the user sees one tab. It's true, but nothing explains it. |
| L6 | Low | — | No | `buddy/think.py:247` still tells Think that each capability "reads only the first sheet". In live run 2, Think repeated it as a constraint ("other sheets are ignored"). It doesn't reach the reply. |

**What the branch gets right (measured):** across 432 branch grid runs, **0 deliveries had fewer sheets than the input, 0 renamed a sheet, 0 undelivered replies made a completion claim, and the Voice model was never called on an undelivered turn**. On `main`, the same grids delivered **54 (without `xlrd`) and 63 (with `xlrd`) files with fewer sheets than the input, every one with success wording**. The fixtures were `two_plain`, `two_hidden`, `two_hidden_first`, `two_veryHidden`, `two_renamed.xls`, `chartsheet`, and with `xlrd` also `two_legacy.xls`. The fix for the reported scenario (M3 of the previous review) is real.

## 2. Suite, lint, and the tests against `main`

- **`python -m pytest -q` on the branch:** **776 passed**, 11 warnings, in the documented environment (no `xlrd`). That matches the implementer's number.
- **`ruff check buddy tests`:** **22 errors on both trees**, identical by file and rule (17 file/rule pairs, same counts). None are new. `ruff check skills scripts`: clean.
- **The branch's tests on a `main` worktree:**
  - `tests/buddy/test_multi_sheet.py`: **31 failed, 6 passed**. The 6 that pass are `test_a_one_sheet_workbook_behaves_exactly_as_before[*-Sheet1]`, which are meant to be identical. That matches the implementer's claim.
  - `tests/buddy/test_inspector_sheets.py`: 5 failed, 4 passed. The 4 that pass are the "not compared" and "matching lists pass through" cases, which are no-ops on `main` by design.
  - `tests/skills/excel/test_workbook.py`: a collection error on `main`, because `skills.excel.workbook` doesn't exist there.
  - `tests/buddy/test_hands.py`: 1 of the 2 new tests fails on `main`. The other, "leaves the failure reason empty", is a negative case and passes on both trees.

### One-sheet workbooks: my own comparison

These are the grid's one-sheet fixtures (`one_*`, `header_only`, `empty_zero_rows`, `one_renamed.xls`, `one_macro.xlsm`, and with `xlrd` `one_legacy.xls`). I compared, per run, the reply, whether a file was delivered, the delivered row counts, `verification_result.passed` and `stopped_at`.

| Environment | Identical | Different | Which differ |
|---|---|---|---|
| no `xlrd` | 72 | 18 | `one_forbidden_slash`, `one_forbidden_bracket` (L3) |
| `xlrd` installed | 81 | 18 | the same two |

Apart from those, the only difference is the delivered sheet's name. The branch keeps it (`Data`, `A`×31, `B`×40, and `Sheet` for `empty_zero_rows`), while `main` always wrote `Sheet1`. **The implementer's "one-sheet workbooks are unchanged" holds for every workbook Excel itself can produce.**

## 3. Trying to break the sheet-count check

Replies are `TemplateSpeaker`'s for the single dedupe request. The friend and focus speakers gave the same text on every undelivered turn, and the chain variant adds "so remove empty rows never ran". "Analysis preface" means the reply starts "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these?".

| Fixture | Branch: delivered? | Branch reply (after any analysis preface) | `main` | Verdict |
|---|---|---|---|---|
| `two_hidden` | no | "…The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step and no file was handed back; your original file is unchanged." | delivered `['Sheet1']`, "Done — removed 2…" | Correct. L5 (wording) |
| `two_veryHidden` | no | same | delivered `['Sheet1']` | Correct. L5 |
| `two_hidden_first` | no | same, and the analysis preface describes the **hidden** `Q1` | delivered `['Sheet1']` | Correct. M4 is worse here (§8) |
| `chartsheet` (`Data` + chart sheet) | no | "the workbook has 2 sheets…" | delivered `['Sheet1']`, **chart sheet lost** | Correct |
| `one_macro.xlsm`, `two_macro.xlsm` | no | "The deduplicate step: couldn't be run because no available skill can do it…" | same | Unchanged. The skills don't accept `.xlsm` |
| `one_renamed.xls` (`.xlsx` content) | yes, `['Sheet1']`, 4 rows | "Done — removed 2 duplicate row(s). The copy has 4 row(s)…" | same | Correct |
| `two_renamed.xls` | no | "the workbook has 2 sheets…" | delivered `['Sheet1']` | Correct |
| `one_legacy.xls`, no `xlrd` | no | "the sheets in an .xls workbook can't be counted because the xlrd library isn't installed" | "execution did not produce an output file" | Correct, and clearer |
| `two_legacy.xls`, no `xlrd` | no | same | same as above | Correct |
| `one_legacy.xls`, `xlrd` | yes, `['Sheet1']`, 4 rows | "Done — removed 2…" | same | Correct |
| `two_legacy.xls`, `xlrd` | no | "the workbook has 2 sheets…" | delivered `['Sheet1']` | Correct, but see H1 |
| `corrupt_trunc` | no | "could not read 'uploads/report.xlsx': File is not a zip file." | "execution did not produce an output file" | True. Raw text (M3) |
| `corrupt_notbook` | no | "could not read 'uploads/report.xlsx': "There is no item named '[Content_Types].xml' in the archive"." | as above | True. Raw text (M3) |
| `corrupt_sheetxml` | no | "could not read 'uploads/report.xlsx': not well-formed (invalid token): line 1, column 1." | as above | True. Raw text (M3) |
| `empty_zero_rows` | yes, `['Sheet']`, 0 rows | "Done — there were no duplicate rows to remove. The copy has 0 row(s), 0 duplicate row(s) remaining. … None of the issues I check for were found." | same, sheet `Sheet1` | True |
| `header_only` | yes, 0 rows | same | same | True |
| `one_password.xlsx`, `two_password.xlsx`, no `xlrd` | no | "**the sheets in an .xls workbook can't be counted because the xlrd library isn't installed**" | "execution did not produce an output file" | **M1** |
| the same, `xlrd` installed | no | "could not read 'uploads/report.xlsx': Can't find workbook in OLE2 compound document." | as above | True. Raw text |
| `one_31char` | yes, sheet name kept | "Done — removed 2…" | delivered, renamed `Sheet1` | Correct |
| `one_40char` (crafted) | yes, the 40-character name kept | "Done — removed 2…" | delivered, renamed `Sheet1` | L3 |
| `one_forbidden_slash` (crafted `Q1/Q2`) | no | "could not write PosixPath('workspace/outputs/w/report_deduplicated_s-task-1.xlsx'): Invalid character / found in sheet title." Partial file left on disk. | delivered, renamed `Sheet1` | L1, L2, L3 |
| `one_forbidden_bracket` (crafted `[Data]`) | no | the same with "Invalid character [" | same as above | L1, L2, L3 |

### 3.1 H1: `xlrd` doesn't list chart sheets, macro sheets or VB modules

- **Where:** `skills/excel/workbook.py:63-76` counts a legacy `.xls` with `xlrd.open_workbook(...).sheet_names()`. `buddy/inspector.py:896-897` reads the input's names with `pd.ExcelFile`, which uses `xlrd` for OLE2.
- **Cause:** `xlrd.book.Book.handle_boundsheet` (`xlrd` 2.0.2) appends to `_sheet_names` only when `sheet_type == XL_BOUNDSHEET_WORKSHEET`. Macro sheets (type 1), chart sheets (2) and VB modules (6) are skipped with a note. So the skill's count and the "independent" check share the blind spot.
- **Probe:** I took `two_legacy.xls` (sheets `Q1` and `Q2`) and changed `Q2`'s BOUNDSHEET type byte to 2 (chart) in one copy and 1 (macro sheet) in another.
  - I couldn't produce a genuine `.xls` chart sheet here: LibreOffice in this container can't load spreadsheets.
  - `xlrd`'s decision depends only on that byte, read before the sheet's substream, so a real chart sheet takes the same path. **[Likely]**
  - With `xlrd` installed, `xlrd` reports `['Q1']` for both files.

| Request | Delivered | Reply (focus) |
|---|---|---|
| dedupe | `['Q1']`, 4 rows | "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. **Your cleaned copy of report.xls is ready.** I also noticed: …" |
| chain (template) | `['Q1']`, 3 rows | "Removed 2 duplicate row(s). Removed 1 fully-empty row(s). The copy has 3 row(s)… Saved as `report_deduplicated_s-task-1_no_empty_rows_s-task-2.xlsx`." |

- **Reachability:** none today. `xlrd` isn't in `requirements.txt`, and without it every OLE2 file is refused.
- **Why it's still HIGH and not deferred:**
  - The branch adds this `xlrd` counting path. `test_workbook.py` tests it with a fake `xlrd`.
  - `docs/backlog.md` item 1a lists "a real `.xls` can't be cleaned without `xlrd`" as open work.
  - So the most likely next change, adding `xlrd`, turns this on silently, and no test would catch it.

### 3.2 H2: a declared sheet with a missing part is dropped before counting

- **Probe:** `two_plain` with `xl/worksheets/sheet2.xml` deleted from the zip. `workbook.xml` still declares `<sheet name="Q1" …/>` and `<sheet name="Q2" …/>`.
- **Cause:** `openpyxl.reader.excel.ExcelReader.read_worksheets` skips any sheet whose target isn't in the archive (`if rel.target not in self.valid_files: continue`). So `sheetnames` is `['Q1']`.
- **Result:** in all 9 runs, the file was delivered with `['Q1']` and `passed=True`. Reply: "Done — removed 2 duplicate row(s). The copy has 4 row(s)… I also noticed: …". No reply mentions that the workbook is damaged or that `Q2` was dropped.
- **Severity:** by the stated rule (a file delivered with fewer sheets than the input) this is HIGH. In substance it's milder: `Q2` has no data anywhere in the file, and Excel's own repair would drop it. I'd grade it Medium on substance, but the same fix as M2 removes it, so it costs nothing to fix now.

### 3.3 M1: a password-protected `.xlsx` is called "an .xls workbook"

- **Cause:** encrypted OOXML files are OLE2 containers, so they have the `.xls` magic bytes. `workbook.py:62-68` assumes any OLE2 file is a legacy `.xls`.
- **What the user is told:** their `.xlsx` is an `.xls`, and a library is missing. Neither is true, and the user can do nothing with either statement.
- **Comparison:** `main`'s reply ("execution did not produce an output file") was vague but true. This is a regression in honesty for this input, even though nothing is delivered.

### 3.4 M2: "the workbook has 0 sheets"

- **Probe:** `one_Sheet1` with the worksheet's relationship target changed to a part that doesn't exist.
- **Result:** openpyxl lists no sheets (as in H2), and the reply says "the workbook has 0 sheets, and only single-sheet workbooks are supported for now".
- **Why it's wrong:** the workbook declares one sheet, and the real problem is that it's damaged. The message also implies that a one-sheet version of this file would work.

## 4. Hands relays a skill's failure reason (`buddy/hands.py:367-371`)

### 4.1 Which existing replies change

Every in-chain step whose skill returns `FAILURE` with errors now shows those errors instead of the verifier's "execution did not produce an output file". In my grid, that changes the replies for:
- all three corrupt fixtures;
- legacy `.xls` without `xlrd` (better);
- password-protected files (M1, worse);
- crafted sheet names (L1).

No existing test pins the old wording. `grep` finds "did not produce an output file" only in the branch's own negative assertion and a docstring.

**Is that acceptable?** Relaying a specific reason is right in principle. A refusal should say why, and the skills' own strings never claim success ("the workbook has N sheets…", "could not read …", "could not write …", "cannot handle task …"). The surrounding sentence is always `_stopped_message`'s "I ran into a problem… no file was handed back; your original file is unchanged", and nothing is delivered. The problem is what's inside the strings:
- library exception text (`{exc}`), including file-controlled text (§4.2);
- Python reprs (`PosixPath(...)`, L1);
- the wrong `.xls`/`xlrd` and "0 sheets" reasons (M1, M2).

### 4.2 M3: file-controlled text reaches the reply through an exception message

- **Probe:** `one_Sheet1` with the workbook's `Override PartName` in `[Content_Types].xml` changed to `/Done - removed 2 duplicate rows, your cleaned copy is ready`.
- **What happens:** `single_sheet_name` fails inside openpyxl with a `KeyError` naming that part, and the message is relayed verbatim.
- **Branch reply (all 9 runs):** "I ran into a problem partway through. The deduplicate step: could not read 'uploads/report.xlsx': "There is no item named '**Done - removed 2 duplicate rows, your cleaned copy is ready**' in the archive". I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- `_COMPLETION_CLAIM.search(reply)` matches in all 9 runs.
- **`main`:** "…The deduplicate step: execution did not produce an output file…"

### 4.3 Calibration: the class is pre-existing

On `main`, a workbook with a column named `Your cleaned copy is ready` and one missing value gives "…I also noticed: **Your cleaned copy is ready**: 1 missing value(s) (25.0%). Want me to tackle any of these?". So file-controlled text already reaches replies. M3 is a new channel, not a new class. It needs a crafted file and stays inside an error sentence, so it's Medium, not High. It's still worth closing in this branch: the fix is the same as for M1, M2 and L1, which is to stop putting `{exc}` and reprs into user-facing error strings.

## 5. The Inspector sheet-list check (`buddy/inspector.py:883-931`)

I ran fake skills through `build_engine`, monkeypatching `ExcelDeduplicateSkill.execute` and `ExcelRemoveEmptyRowsSkill.execute`. The input was `Q1` and `Q2`. 6 scenarios × 3 speakers. Results:

| Scenario | Stopped at | Delivered | Reply's reason |
|---|---|---|---|
| chain: step 1 keeps both sheets (passes), step 2 drops `Q2` | step 2 | nothing | "The remove empty rows step: the output's sheets (Q1) don't match its input's (Q1, Q2), so a sheet was lost or renamed." Step 1's verdict passed. |
| chain: step 1 reorders to `Q2, Q1` | step 1 | nothing | "…the output's sheets (Q2, Q1) don't match its input's (Q1, Q2), so a sheet was lost or renamed." Step 2 never ran. |
| chain: step 2 renames `Q1` → `Sheet1` | step 2 | nothing | "…(Sheet1, Q2) don't match … (Q1, Q2)…" |
| single: dedupe reorders | dedupe | nothing | as for the reorder above |
| single: dedupe writes a `.csv` | dedupe | nothing | "couldn't independently read the workbook's sheet names: Excel file format cannot be determined, you must specify an engine manually.." |
| chain: step 2 writes a `.csv` | step 2 | nothing | the same |

In every run, nothing was delivered, the reply matched no completion claim, and the original was unchanged. **The per-step gate works.** Each link is checked against the file it actually read, so a sheet dropped in step 2 is caught even though step 1 passed.

**Is the fail-safe default acceptable?** Yes. A step that reads a workbook and hands on something whose sheets can't be read fails closed, with an honest "nothing was handed back" reply. The cost is that a future workbook → CSV or report skill fails until it gets an explicit exemption. That's the right default for rule 1. Two wording problems, neither reachable today (L4):
- the reorder case says "a sheet was lost or renamed";
- the non-workbook case relays a raw pandas message with a double period.

**The check isn't fully independent.** For zip content it uses the same `openpyxl` `sheetnames` as the skill, and for OLE2 the same `xlrd` list. So it can't catch H1 or H2: it inherits the skill's blind spots. The fix below makes both use the sheet list declared in `workbook.xml`.

## 6. Original files

The original's SHA-256 was unchanged in every one of the 936 harness runs (864 grid, 54 probe, 18 Inspector).

Three of the 4 live runs printed an unchanged hash (§7). In the fourth, my output filter dropped the script's hash line, so I can't confirm it. It was a one-sheet dedupe run identical to live run 3.

No run wrote anything next to the upload. Every write went to `workspace/outputs/`.

## 7. Live runs (Groq, independent of the implementer's)

`python scripts/run_dedupe_manually.py --mode focus …`, with the key attached by the cloud proxy. 4 runs, no 429. These were run while `xlrd` was still installed in my environment. That doesn't affect `.xlsx` fixtures.

| # | Fixture | Message | Intent | `clarification_required` | Steps | Delivered | Independent check | Reply | False claim | Original hash |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | `two-sheets` | "remove the duplicate rows" | `remove_duplicate_rows` | false | analyze ✓, deduplicate ✗ (refused) | no (0 files, none written) | original sheets `['Q1','Q2']` | "Before anything else, here's what I noticed: name: 1 missing value(s) (16.7%); amount: 1 missing value(s) (16.7%). Want me to tackle any of these? I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step and no file was handed back; your original file is unchanged." | none (M4 applies) | `2d6d4670…a243` → same |
| 2 | `two-sheets` | "remove the duplicate rows and then remove the empty rows" | `multi_step_request` | false | analyze ✓, deduplicate ✗, remove empty rows skipped | no (0 files, none written) | `['Q1','Q2']` | the same, ending "…so remove empty rows never ran and no file was handed back; your original file is unchanged." | none (M4 applies) | `4a384361…cadb` → same |
| 3 | default (one sheet) | "remove the duplicate rows" | `remove_duplicate_rows` | false | analyze ✓, deduplicate ✓, re-check ✓ | yes, `['Sheet1']` | original 6 rows → output 4 rows | "Done — removed 2 duplicate row(s). The copy has 4 row(s), 0 duplicate row(s) remaining. Your cleaned copy of manual_report.xlsx is ready. I also noticed: name: 1 missing value(s) (25.0%); amount: 1 missing value(s) (25.0%). Want me to tackle any of these?" | none (6 → 4 matches 2 exact duplicates) | `6f720855…0599` → same |
| 4 | default | same as 3 | — | — | — | — | — | identical reply to run 3 | none | **not captured** (see §6) |

The implementer's live-run claims for the two-sheet fixture reproduce.

## 8. The deferred item: analysis of a refused multi-sheet workbook

**Severity: Medium (misleading by omission, not false). It does not block this merge.**

- The numbers are true for the first sheet. Nothing is claimed as done, and nothing is delivered.
- But "here's what I noticed" names no sheet, the percentage isn't the workbook's (1 of 8 rows, not 1 of 6), and "Want me to tackle any of these?" offers work the skills will refuse on this file.
- `two_hidden_first` makes it worse: the findings describe a **hidden** sheet the user can't see in Excel.
- It's pre-existing. `main` gives the same findings, and adds a delivered file that lost sheets.
- This branch doesn't make it worse in kind. The offer is now one the skills refuse, where on `main` they would have run and silently dropped the other sheets.

**Recommendation:** fix it as the next item in 1a. Skip `spreadsheet.analyze` when the workbook has more than one sheet, or prefix the findings with the sheet name and drop the "tackle" offer on a refused turn.

## 9. Verdict

**DON'T MERGE** until the fix below is in. It's small and contained to `skills/excel/workbook.py`, the two skills' error strings, and `buddy/inspector.py:_sheet_names`.

This is a close call, and the reason should be explicit. The branch does remove a real, measured rule-1 breach (54–63 deliveries with lost sheets per 216 runs on `main`, down to 0). But it also:
- adds a counting path (H1) that silently reintroduces sheet loss the moment `xlrd` is installed;
- has a check that inherits the skill's blind spots (H2);
- adds false reasons to replies (M1, M2);
- opens a new channel for file-controlled success wording (M3).

All of them come from two root causes: the sheet list comes from libraries that filter it, and raw exception text is relayed. One fix round covers both.

**Exact fix:**

1. **Count the declared sheets, in one shared helper used by both the skills and the Inspector.**
   - For zip content: read `[Content_Types].xml` to find the workbook part (content type `…spreadsheetml.sheet.main+xml` or `…ms-excel.sheet.macroEnabled.main+xml`).
   - Parse its `<sheets>/<sheet name=…>` with `xml.etree.ElementTree`, and use those names.
   - Also load openpyxl read-only. If its `sheetnames` differ from the declared list, raise `WorkbookRefused("the workbook looks damaged: it lists sheets that can't be read")`.
   - Use this helper in `skills/excel/workbook.py:_sheet_names` and in `buddy/inspector.py:_sheet_names`. This fixes H2 and M2.
2. **`single_sheet_name`:** if the declared list is empty, raise `WorkbookRefused("the workbook looks damaged: it has no readable sheet")`, never "has 0 sheets". This fixes M2.
3. **OLE2 content (`workbook.py:62-76`): don't count with `xlrd`.**
   - If the file contains the UTF-16-LE stream name `EncryptedPackage`, raise `WorkbookRefused("the workbook is password-protected, so it can't be opened")`.
   - Otherwise raise `WorkbookRefused("older .xls workbooks (Excel 97–2003) aren't supported yet; saving it as .xlsx will work")`.
   - Remove the `xlrd` branch until `xlrd` is a decided dependency with a count that includes non-worksheet sheets. This fixes H1 and M1.
   - It changes nothing in the supported environment, where `.xls` already can't be read.
4. **No raw exception text or reprs in user-facing errors.**
   - In `workbook.py`, use `"could not read the workbook; it may be damaged or not an Excel file"`.
   - In `dedupe.py` and `remove_empty_rows.py`, use `"could not read the workbook"` and `"could not write the cleaned copy"`.
   - Keep `exc` in `logger.exception` only. This fixes M3 and L1.
5. **Tests:**
   - an encrypted file: OLE2 magic plus `EncryptedPackage` bytes is enough, no new dependency;
   - a declared sheet with a missing part, for both the skill and the Inspector;
   - a workbook with no readable sheet;
   - a crafted `[Content_Types].xml` whose part name contains "cleaned copy is ready": assert the reply doesn't contain it and `_COMPLETION_CLAIM` doesn't match;
   - a legacy `.xls` refused even with a fake `xlrd` present.
6. **Per `CLAUDE.md`:**
   - a `docs/reviews/fix-multi-sheet-fixes.md`;
   - live runs on the fixed head (the two-sheet fixture plus one password-protected or damaged file);
   - a re-review by a separate session.

**Confidence in the verdict:** Medium-high. If the user prefers to take the rule-1 fix onto `main` now, merging as is would not deliver any lost sheet or false outcome in the documented environment: H1 needs `xlrd`, and H2 needs a damaged file whose lost sheet has no data. In that case, items 1–5 must be the very next change, and `xlrd` must not be added before them. I don't recommend that, because the fix is a few dozen lines.

## Deferred

These items don't block the merge after the fix above. They're added to `docs/backlog.md` item 1a.

- **M4.** The analysis covers the first sheet only, with no sheet named, and offers "tackle" work the skills will refuse (§8).
- **L2.** A failed write leaves a partial output file under `workspace/outputs/`. Delete `output_path` on a write failure.
- **L3.** Crafted sheet names Excel forbids now fail at the write, and names over 31 characters are written as is. Both are honest. Sanitising or truncating them would need a rename the reply must mention.
- **L4.** Inspector wording: "lost, renamed or reordered" for a reorder, and no raw pandas text for a non-workbook output.
- **L5.** Say "(including hidden sheets)" when hidden sheets push the count above one.
- **L6.** `buddy/think.py:247` still says the capabilities "read only the first sheet".
