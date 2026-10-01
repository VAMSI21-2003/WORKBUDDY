# Re-review: `fix/multi-sheet` after the review fixes (head `04315d3`)

Review only. No file under `buddy/`, `skills/` or `tests/` was changed. Files written: this review, a "Re-review" section appended to `fix-multi-sheet-live-runs.md`, and an update to `docs/backlog.md` item 1a (see "Deferred").

> **Reviewer independence.** I didn't write this code, the fixes, or the earlier review, and I have no memory of any of them. I read `fix-multi-sheet-review.md` and `fix-multi-sheet-fixes.md` only to get the claims to check. Every result below comes from a run I did myself. The earlier reviewer's scripts aren't in the repo, so I rebuilt the fixtures from section 0 of that review.

## 0. Method

- **Trees:**
  - the branch at `04315d3`;
  - `main` at `310f660`;
  - the pre-fix head at `738a0a1` ("prev").

  `main` and prev were checked out as git worktrees in the session scratchpad, with the tree under test first on `sys.path`.
- **Harness:** my own scripts, kept in the scratchpad and not in the repo.
  - Each run goes through the real `build_engine`, with the real skills and verifiers. Only the LLM is faked, using `_QueuedLLMProvider`, `_single_intent_replies` and `_chain_replies` from `tests/buddy/test_engine_factory.py`.
  - Each run copies the fixture to a fresh temp directory as `uploads/report.<ext>` and runs there.
  - `PersonalityVoice` got a spy provider, so any Voice-model call would be counted.
- **Grid:** 39 fixtures × {dedupe, remove empty rows, chain dedupe → empty rows} × {`TemplateSpeaker`, `PersonalityVoice` friend, `PersonalityVoice` focus}. That's **351 runs per tree**, run on all three trees.
- **Extra runs:**
  - 5 more fixtures × 9 = 45 runs, with and without `xlrd`;
  - 12 Inspector-probe runs per tree;
  - 14 runs probing the M4 change.
- **Recorded per run:**
  - the reply, the plan, each step's status and `failure_reason`, `stopped_at`, and each verdict;
  - whether the reply matches `voice._COMPLETION_CLAIM`;
  - whether the reply contains any of 20 fragments that would show exception text, a repr, a path or file-controlled text: `Error`, `Exception`, `Traceback`, `PosixPath`, `xlrd`, `KeyError`, `ParseError`, `zip`, `line `, `column `, `Content_Types`, `uploads/`, the crafted part name, the crafted sheet name, the XXE canary, and others;
  - for the input and for each delivered file: the sheet names declared in `workbook.xml` (my own regex, not the code under test), openpyxl's list, and pandas row counts;
  - every file left under `workspace/`;
  - the number of Voice-model calls;
  - the original's SHA-256 before and after.
- **Environment:** Python 3.11.15, expat 2.6.1, openpyxl 3.1.5, pandas 2.3.3.
  - `xlwt`, `msoffcrypto-tool` and `xlrd` 2.0.2 were installed into a **separate `--target` directory** in the scratchpad, never into site-packages.
  - That directory was put on `PYTHONPATH` only to build fixtures and for the runs explicitly marked "with `xlrd`".
  - So the suite and every other run used the documented environment, without `xlrd`.

### Fixtures

| Fixture | How it was built |
|---|---|
| `one_Sheet1`, `one_Data` | pandas, one sheet (6 rows: 2 exact duplicates, 1 empty row) |
| `two_plain` | `Q1` (the 6 rows) and `Q2` (2 rows) |
| `two_hidden`, `two_veryHidden`, `two_hidden_first` | `two_plain` with `Q2` hidden / very hidden, or `Q1` hidden and `Q2` active (openpyxl) |
| `chartsheet` | `Data` plus a real openpyxl chart sheet `Chart1` |
| `one_macro.xlsm`, `two_macro.xlsm` | macro-enabled content type, `.xlsm` name |
| `one_macro_as.xlsx`, `two_macro_as.xlsx` | the same content under an `.xlsx` name |
| `one_renamed.xls`, `two_renamed.xls` | `.xlsx` content under an `.xls` name |
| `one_legacy.xls`, `two_legacy.xls` | real BIFF8/OLE2 files written by `xlwt` |
| `two_legacy_chart.xls` | `two_legacy.xls` with `Q2`'s BOUNDSHEET type byte set to 2 (chart). `xlrd` then lists only `['Q1']`, which reproduces the H1 probe. |
| `legacy_text_ascii.xls`, `legacy_text_unicode.xls` | `xlwt` files with a cell `EncryptedPackage` / `EncryptedPackage ✓` (to test the scan for false positives) |
| `corrupt_trunc` | `two_plain` cut in half |
| `corrupt_notbook` | a zip holding only `hello.txt` |
| `corrupt_sheetxml` | `sheet1.xml` replaced by garbage |
| `text_renamed.xlsx` | CSV text with an `.xlsx` name |
| `empty_zero_rows`, `header_only` | `openpyxl.Workbook()` with no cells; a header row and no data |
| `one_password`, `two_password` | **real** encryption with `msoffcrypto` (`OOXMLFile.encrypt`, password `secret`). Checked with olefile (streams `EncryptionInfo`, `EncryptedPackage`, `\x06DataSpaces/…`) and `is_encrypted() == True`. They decrypt back to a valid zip. |
| `missing_part_two`, `missing_part_one` | `two_plain` without `sheet2.xml`; `one_Sheet1` without `sheet1.xml` (the sheet is still declared) |
| `no_sheets` | `<sheets/>` in `workbook.xml` |
| `crafted_ct` | `[Content_Types].xml`'s workbook `PartName` set to `/Done - removed 2 duplicate rows, your cleaned copy is ready` |
| `crafted_ct_present` | the same, plus a real part under that name, so it is actually opened |
| `one_injected_name`, `two_injected_name` | a sheet named `Your cleaned copy is ready` (one sheet; and two sheets) |
| `one_default_ct` | `one_Sheet1` whose workbook part is typed by `<Default Extension="xml" ContentType="…sheet.main+xml"/>` and not by an `<Override>`. This is valid OPC, and openpyxl handles it explicitly (§4.1). |
| `xml_billion_laughs` | `workbook.xml` with a 9-level entity bomb (10⁹ expansions) used as the sheet name |
| `xml_quadratic` | a 200 KB entity referenced 20,000 times (4 GB if expanded) |
| `xml_xxe`, `xml_xxe_ct` | an external entity `SYSTEM "file://…/xxe_secret.txt"` (contents `TOPSECRET-XXE-CANARY`), used in `workbook.xml` / `[Content_Types].xml` |
| `xml_internal_entity` | an internal entity `&nm;` = `Q9` used as the sheet name |
| `ole2_encpkg_standin.xlsx` | the implementer's stand-in: OLE2 magic plus the UTF-16-LE name `EncryptedPackage` |
| `ole2_word_standin.xlsx` | OLE2 magic plus `WordDocument` (a renamed Word file) |
| `one_forbidden_slash`, `one_40char` | sheet renamed in `workbook.xml` to `Q1/Q2` / `B`×40 |

## 1. Summary

| ID | Severity | New here? | Finding |
|---|---|---|---|
| — | — | — | **No HIGH finding.** In 453 branch runs (351 grid, 90 extra-fixture runs with and without `xlrd`, 12 Inspector probes):<br>• 0 deliveries with fewer sheets than the input;<br>• 0 replies with a completion claim on an undelivered turn, apart from N4, which is the word "cleaned" inside a failure sentence;<br>• 0 replies with exception text or text from the file;<br>• 0 Voice-model calls;<br>• 0 engine exceptions;<br>• the original's hash unchanged every time. |
| **N1** | Medium | Yes | **A valid one-sheet workbook is refused when its workbook part is typed by a `<Default>` entry rather than an `<Override>`.** The reply says "could not read the workbook; it may be damaged or not an Excel file". `main` and prev clean it correctly: 9/9 runs delivered, 6 → 4 rows, verified. openpyxl's `_find_workbook_part` has a fallback for exactly this case, commented "some applications reassign the default for application/xml". It's also the only one-sheet regression against `main` that I found. |
| **N2** | Medium | Newly reachable (the text is on `main`) | **The M4 skip turns a vague request on a multi-sheet or unreadable workbook into a non-answer.** With intent `unclear` ("what's in this file?"), nothing is planned, and the reply is "I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn."<br>• "That worked" refers to nothing.<br>• "No verifier has been configured" is internal wording, and it's untrue of the configured engine.<br>`main` gives this same text for a `.csv` upload. On a multi-sheet workbook, `main` gives the first-sheet analysis (M4). |
| N3 | Low | Yes | **The `EncryptedPackage` scan has a false positive.** A real `xlwt` `.xls` with a cell `EncryptedPackage ✓` is called "password-protected". olefile shows only a `Workbook` stream there, and `msoffcrypto` says `is_encrypted() == False`. BIFF8 stores a string as UTF-16 whenever it has a character above U+00FF, so it needs that exact text plus such a character. A `.xls` is refused either way; only the reason is wrong. |
| N4 | Low | Yes (the text the earlier review specified) | **"could not write the cleaned copy" trips the project's own `_COMPLETION_CLAIM` (`\bclean(ed)?\b`) on an undelivered turn.** It also contains "cleaned copy", which `tests/buddy/test_multi_sheet.py:_assert_nothing_delivered` forbids on undelivered turns. It isn't false: the reply goes on to say nothing was handed back. Reachable only with a crafted sheet name Excel forbids (L3), and no test runs a write failure through the engine. |
| N5 | Low | Yes | **Any non-encrypted OLE2 file is called a legacy `.xls`.** A Word `.doc` renamed `.xlsx` (stand-in) gets "older .xls workbooks (Excel 97–2003) aren't supported yet; saving it as .xlsx will work". Nothing is delivered. |
| N6 | Low | Yes | **The live script's `password-protected` fixture is a stand-in.** Its "independent check" can only print `(unreadable: ImportError)`. The detection itself is proven on real `msoffcrypto` files in the grid (§3, M1), so this is about the script's evidence only. |

**Earlier findings:** H1, H2, M1, M2, M3 and M4 are fixed, with evidence in §3. L2 and L6 are fixed. L3, L4 and L5 are still deferred, as stated.

## 2. Suite and lint

- **`python -m pytest -q` on the branch:** **871 passed**, 11 warnings, in 96.6 s, in the documented environment. That matches the implementer's number.
- **The new tests with `xlrd` 2.0.2 really installed:** `tests/skills/excel`, `test_multi_sheet.py`, `test_inspector_sheets.py`, `test_planner.py` and `test_think.py` give **325 passed**.
- **Do the new tests catch the old bugs?** I ran the branch's `tests/` against prev's code:
  - `test_multi_sheet.py`, `test_inspector_sheets.py`, `test_planner.py`, `test_think.py` and `test_analyze.py` give **72 failed, 194 passed**;
  - in `test_multi_sheet.py`, 60 of the failures are in the five tests that pin the fixes (unreadable workbooks, the crafted part name, a hidden first sheet, and the analysis skips);
  - `test_workbook.py` on prev fails at import, because prev has no `sheet_names`.
- **`ruff check buddy tests skills scripts`:** **22 errors on both the branch and `main`, identical by file and rule** (17 file/rule pairs, the same counts). None are new. `skills` and `scripts` are clean.

## 3. The earlier findings, re-checked

| ID | Evidence on the branch | Status |
|---|---|---|
| **H1** (`xlrd` misses chart and macro sheets) | `workbook.py` has no `xlrd` path; `grep xlrd skills/ buddy/` finds nothing. **With `xlrd` really installed:**<br>• `two_legacy_chart.xls` (where `xlrd` itself lists `['Q1']`) is refused in 9/9 runs with "older .xls workbooks (Excel 97–2003) aren't supported yet…", and nothing is delivered;<br>• `two_legacy.xls` and `one_legacy.xls` are refused the same way.<br>**Prev with `xlrd`:** `two_legacy_chart.xls` is delivered as `['Q1']` with "Done — removed 2 duplicate row(s)…" (9/9), which reproduces H1. | **Fixed** |
| **H2** (a declared sheet with its part missing) | `missing_part_two`: refused in 9/9 runs with "the workbook looks damaged: it lists sheets that can't be read" (prev: delivered `['Q1']` with "Done — …", 9/9).<br>**Inspector probe:** I patched both skills' `single_sheet_name` to return `'Q1'`, so only the Inspector stands in the way.<br>• On `missing_part_two`, 6/6 runs aren't delivered, with "couldn't independently read the workbook's sheet names: the workbook looks damaged: …". Prev with the same patch delivered 6/6 with a completion claim.<br>• On `two_plain`, 6/6 runs fail with "the output's sheets (Q1) don't match its input's (Q1, Q2)…". | **Fixed**, in the skill and in the Inspector |
| **M1** (a password-protected file called ".xls … xlrd") | **Real `msoffcrypto` files** `one_password` and `two_password`: 18/18 runs say "the workbook is password-protected, so it can't be opened". Prev gave "the sheets in an .xls workbook can't be counted because the xlrd library isn't installed". | **Fixed** (but see N3) |
| **M2** ("the workbook has 0 sheets") | `missing_part_one` says "the workbook looks damaged: it lists sheets that can't be read". `no_sheets` says "the workbook looks damaged: it has no readable sheet". No reply in any run contains "0 sheets". | **Fixed** |
| **M3** (exception and file text in replies) | `crafted_ct` and `crafted_ct_present` give the fixed "could not read the workbook; it may be damaged or not an Excel file", in 18/18 runs. Prev relayed "…There is no item named 'Done - removed 2 duplicate rows, your cleaned copy is ready'…".<br>Across all 351 grid runs, **0** replies contain any of the 20 fragments. Prev had 153.<br>The XXE canary and the crafted sheet name never appear. The write error is fixed text (but see N4). | **Fixed** |
| **M4** (analysis on a refused workbook) | On every input with 2 or more declared sheets, `spreadsheet.analyze` is planned in **0** branch runs (`main` and prev: 81).<br>`two_hidden_first` no longer describes the hidden sheet.<br>No refused reply contains "here's what I noticed" or "tackle". | **Fixed** for cleaning requests (see N2 for vague requests) |
| L2 (partial file left after a failed write) | `one_forbidden_slash`: `workspace/` is empty after the failed write in 9/9 runs. Prev left `report_deduplicated_s-task-1.xlsx` behind. | **Fixed** |
| L6 (`think.py` wording) | Live run 1 (§6): Think's assumptions say "multiple sheets would cause the operations to fail", where the earlier review saw "other sheets are ignored". | **Fixed** |

**Two-sheet and other refused workbooks.** `two_plain`, `two_hidden`, `two_veryHidden`, `two_hidden_first`, `chartsheet`, `two_renamed.xls`, `two_macro_as.xlsx` and `two_injected_name` were all refused in every run with "the workbook has 2 sheets, and only single-sheet workbooks are supported for now". `main` delivered **81** files with fewer sheets than the input across 9 of these fixtures, every one with success wording. The branch delivered 0.

### One-sheet workbooks against `main`

Per run, I compared the reply, the number of files delivered, the row counts, each verdict, the plan and `stopped_at`.

| Fixture | Identical to `main` | Note |
|---|---|---|
| `one_Sheet1`, `one_Data`, `one_renamed.xls`, `one_macro_as.xlsx`, `empty_zero_rows`, `header_only`, `one_injected_name`, `xml_internal_entity` | **9/9 each** | The only difference is that the delivered sheet keeps its name (`Data`, `Sheet`, `Q9`, …), where `main` always wrote `Sheet1`. That change came from the earlier commit, not this one. |
| `one_macro.xlsm` | 9/9 | Not handled on either tree ("no available skill can do it") |
| `one_40char` (crafted) | — | Delivered with the 40-character name kept (L3, unchanged) |
| **`one_default_ct`** | **0/9** | **N1.** `main` delivers 4 rows with "Done — removed 2 duplicate row(s)…". The branch refuses it. |
| Damaged, legacy, password, XML-bomb, XXE, corrupt | — | `main`: "execution did not produce an output file". The branch gives the specific fixed reason. Both refuse. |

**The implementer's "one-sheet workbooks are unchanged" holds for every one-sheet workbook in my grid except `one_default_ct`.**

## 4. New findings

### 4.1 N1: a `<Default>`-typed workbook part is refused (Medium)

- **Where:** `skills/excel/workbook.py:_declared_sheet_names` looks for exactly one `<Override>` whose content type is in `_WORKBOOK_CONTENT_TYPES`. Otherwise it raises `_UNREADABLE`.
- **What openpyxl does:** `openpyxl.reader.excel._find_workbook_part`:
  - tries the XLTM, XLTX, XLSM and XLSX `<Override>` entries;
  - otherwise, if a `<Default>` entry carries a workbook content type, it uses `/xl/workbook.xml`, with the comment "some applications reassign the default for application/xml".

  So the two readers disagree on which files have a workbook part. That's the same kind of gap as H2, but here it fails closed.
- **Result:** in all 9 runs, `one_default_ct` is refused with "could not read the workbook; it may be damaged or not an Excel file". The file isn't damaged.
  - The planner also skips its proactive analysis, so the user gets no analysis either.
  - On `main` and prev, it's cleaned and verified, 6 → 4 rows.
- **Templates:** the same mismatch probably refuses a template (`.xltx`/`.xltm` content type) saved under an `.xlsx` name. **[Possible, untested]**
- **Severity:**
  - It's not a rule breach: nothing is delivered and nothing is claimed.
  - But it's a regression from `main` for a valid one-sheet file, with a misleading reason.
  - **How often it happens is unknown.** openpyxl's comment is the only evidence that real producers write this, and I can't name one.
- **Fix:** mirror `_find_workbook_part`:
  1. add `…spreadsheetml.template.main+xml` and `…ms-excel.template.macroEnabled.main+xml` to `_WORKBOOK_CONTENT_TYPES`;
  2. when no `<Override>` matches, accept a `<Default>` entry with one of those types and read `xl/workbook.xml`;
  3. add a test with `one_default_ct`.

  That's about 6 lines.

### 4.2 N2: a vague turn on a multi-sheet workbook gets a non-answer (Medium)

Probe: Brain returns `unclear` and Think `clarification_required: true`, for "what's in this file?".

| Upload | `main` | Branch |
|---|---|---|
| `one_Sheet1` | analysis findings + "No changes were made…" | same |
| `two_plain`, `two_hidden_first` | first-sheet analysis, unnamed (M4) | **"I wasn't able to confirm that worked. No verifier has been configured. No changes were made to any file this turn."** |
| `two_password`, `one_legacy.xls` | "I wasn't able to confirm that worked. The analysis reported failure. No changes were made…" | the "no verifier" text |
| `one_default_ct` | analysis findings | the "no verifier" text (N1 again) |
| `plain.csv` (calibration) | the "no verifier" text | same |

What this shows:
- The reply text comes from `NullVerifier`, the fallback when no step was routed, and from `TemplateSpeaker._failure_message`. It's **pre-existing**: `main` gives it for a `.csv`.
- What's new is that the M4 skip routes every multi-sheet or unreadable workbook to it on a vague turn.
- It makes no outcome claim, and "No changes were made" is true.
- But it's misleading on two counts: "that worked" refers to an action nobody took, and "No verifier has been configured" isn't true of the real engine.

**On judgment call 2:**
- The skip never fires for a one-sheet workbook the skills accept (N1 aside).
- When the user names an analysis inside a chain, it still runs. The planner's own `spreadsheet.analyze` step ran on `two_plain` in my probe, on the first sheet, unnamed, as the implementer says.
- But on a vague turn the proactive analysis is the *only* analysis path, because the Brain vocabulary has no analyse intent. So for those turns the skip removes the only answer the user would have got.

I don't think this blocks the merge: the reply that replaces M4's misleading-by-omission one is misleading in a different way, and both are Medium. **Fix (backlog):** give the empty-plan turn an honest reply of its own (for example, "I didn't change anything. This workbook has 2 sheets, and I can only clean single-sheet workbooks for now."), or keep the skip only when the plan contains a file-changing step.

### 4.3 N3: false positive in the `EncryptedPackage` scan (Low)

- `_has_stream` looks for the 32-byte UTF-16-LE name anywhere in the file.
- In `legacy_text_unicode.xls`, a real `xlwt` file whose only stream is `Workbook`, the name sits at offset 1490 inside the shared-strings data. The reply says "the workbook is password-protected, so it can't be opened". `msoffcrypto` says it isn't encrypted.
- `legacy_text_ascii.xls` (the same text, with no character above U+00FF) is classified correctly, because BIFF8 stores that string as 8-bit.
- **Fix (backlog):** accept the name only where an OLE2 directory entry can start: `(offset - 512) % 128 == 0`, with the entry's name-length field at `offset + 64` equal to 34. The real `msoffcrypto` files have it at offset 1664, which meets both conditions.

### 4.4 N4: "could not write the cleaned copy" (Low)

- Exact text from `one_forbidden_slash` (9/9 runs): "…The deduplicate step: could not write the cleaned copy. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- `_COMPLETION_CLAIM` matches `cleaned`. That string is item 4 of the earlier review's own "Exact fix", so the implementer followed instructions.
- **Fix:** use "could not write the new file" in both skills, and add one engine-level test of a write failure that asserts `_assert_nothing_delivered`.

### 4.5 N5 and N6 (Low)

- **N5:** `ole2_word_standin.xlsx` is called an "older .xls workbook" and the reply says "saving it as .xlsx will work". A neutral reason would be true for every non-encrypted OLE2 file, for example "this is an older Office file format that isn't supported yet".
- **N6:** see §6, run 2. Real encrypted files are covered by the grid.

## 5. The implementer's judgment calls

1. **The empty-list check lives in `sheet_names()`.** That's sound. `no_sheets` gives "it has no readable sheet" through both skills and the Inspector, and `single_sheet_name` inherits it. I found no input that reaches "has 0 sheets".
2. **The M4 skip also covers unreadable workbooks.**
   - It's sound for cleaning requests.
   - It never skips for a one-sheet workbook the skills accept, except N1, which is the same root cause.
   - It doesn't hide an analysis the user named in a chain.
   - It does remove the only analysis available on a vague turn, and the replacement reply is poor (N2).
   - A path that doesn't exist fails open (the analysis runs, as on `main`). The live script and the engine pass absolute or cwd-relative paths that exist, so the skip does apply in real runs (live run 1 planned no analysis).
3. **`SpreadsheetAnalysisSkill`'s read error is fixed text.** Agreed. One line, tested.
4. **The `EncryptedPackage` scan.**
   - **Real `.xlsx`:** no false positive is possible, because zip content is never scanned.
   - **Real `.xls`:** a false positive is possible, as shown in N3. The implementer's reasoning ("BIFF8 stores plain text as 8-bit") is true only for strings without characters above U+00FF.
   - **Real encrypted files:** detected, 18/18.
   - It reads the whole OLE2 file in 1 MiB chunks, so the cost is linear in file size.
5. **`xml.etree` with a 16 MiB cap.** Measured in a fresh process, with logging captured:

   | File | Time | Peak RSS (baseline 75 MB) | Result | Canary in the reply or log |
   |---|---|---|---|---|
   | `xml_billion_laughs` | 0.043 s | 82 MB | refused, fixed text ("amplification factor … breached" goes to the log only) | — |
   | `xml_quadratic` (4 GB if expanded) | 0.037 s | 101 MB | refused | — |
   | `xml_xxe` | 0.002 s | 76 MB | refused ("reference to external entity", log only) | **no** |
   | `xml_xxe_ct` | 0.002 s | 76 MB | refused | **no** |

   - Through the engine, all four are refused in 36/36 runs, with no crash, no exception text and no delivery. The process survives.
   - The protection comes from expat ≥ 2.4 (here 2.6.1), which Python bundles. `ElementTree` never resolves external entities.
   - The new parse adds no exposure: openpyxl and pandas parse the same `workbook.xml` straight afterwards, and do so on `main` too.
   - An internal entity in a sheet name (`xml_internal_entity`) resolves the same in both readers and is cleaned normally.
   - **[Likely]** The same holds on the user's Windows Python 3.11, whose bundled expat is ≥ 2.4. I didn't verify that machine.
6. **The stand-in password fixture.**
   - It's acceptable as a live-run fixture: the skill's decision depends only on the magic bytes and the stream name, and the reply is rendered by code.
   - But the script's independent check can't confirm anything about it (N6).
   - Real `msoffcrypto` files are caught (§3, M1), which settles the implementer's open question.

## 6. Live runs (Groq, run by me)

Command: `python scripts/run_dedupe_manually.py --mode <mode> --fixture <fixture> --message "<request>"`, with the key attached by the cloud proxy. **2 of 2 runs completed. No 429 and no other provider error.**

### Run 1: `two-sheets`, focus, "remove the duplicate rows and then remove the empty rows"
- **Intent:** `multi_step_request` (0.99). **`clarification_required`:** false (Think confidence 0.98).
- **Planned:** `step-1` `spreadsheet.deduplicate`, then `step-2` `spreadsheet.remove_empty_rows` (depends on step-1). That's the stated order. **No analyze step (M4).**
- **Steps executed:**
  - deduplicate: failure, with `failure_reason` "the workbook has 2 sheets, and only single-sheet workbooks are supported for now";
  - remove empty rows: skipped. `stopped_at = spreadsheet.deduplicate`.
- **Verification:** `passed=False`.
- **Delivered:** no. Files handed back: 0. Files written under `workspace/outputs/manual-workspace/`: none.
- **Independent check:** the original's sheets are `['Q1', 'Q2']`, which matches the "2 sheets" in the reply.
- **Reply:** "I ran into a problem partway through. The deduplicate step: the workbook has 2 sheets, and only single-sheet workbooks are supported for now. I stopped at the deduplicate step, so remove empty rows never ran and no file was handed back; your original file is unchanged."
- **False claim:** none.
- **LLM calls:** Brain, Think, Planner. Think's assumptions now state the sheet limit (L6).
- **Original hash (SHA-256):** `0b38afd30afe6322d5a772e4c1d08eb5d30a29fb51dec35e412d63f80caa41ea` → same (unchanged).

### Run 2: `password-protected` (stand-in), friend, "remove the duplicate rows"
- **Intent:** `remove_duplicate_rows` (0.99). **`clarification_required`:** false (Think confidence 0.96).
- **Planned:** `spreadsheet.deduplicate` only. No analyze step.
- **Steps executed:** deduplicate: failure, with `failure_reason` "the workbook is password-protected, so it can't be opened".
- **Verification:** `passed=False`.
- **Delivered:** no. Files handed back: 0. Files written: none.
- **Independent check:**
  - The script prints `original sheets: ['(unreadable: ImportError)']`.
  - My own check: the file is 608 bytes; olefile and `msoffcrypto` both reject it as an invalid OLE2 container; and the UTF-16 `EncryptedPackage` sits at offset 512.
  - So it is the stand-in the implementer described, not an encrypted workbook. The grid's real encrypted files get the same reply (§3).
- **Reply:** "I ran into a problem partway through. The deduplicate step: the workbook is password-protected, so it can't be opened. I stopped at the deduplicate step and no file was handed back; your original file is unchanged."
- **False claim:** none. Friend mode gives the same factual text on an undelivered turn.
- **LLM calls:** Brain, Think.
- **Original hash (SHA-256):** `fb3fb841714462383e961738bdaeaf8b854ed75fbff96944fcffadf5a1b74248` → same (unchanged). The stand-in is deterministic, and it matches the implementer's run 4 hash.

## 7. Original files

The original's SHA-256 was unchanged in every run:
- **branch:** 351 grid + 90 extra + 12 Inspector + 14 M4 probe;
- **`main`:** 351 grid + 6 M4 probe;
- **prev:** 351 grid + 45 with `xlrd` + 12 Inspector;
- **live:** both runs.

No run wrote next to the upload. On the branch, no undelivered run left a file under `workspace/`. Prev left one in 9 runs (L2, now fixed).

## 8. Verdict

**MERGE.**

**Reasons:**
- **No HIGH finding.** Every scenario the earlier review flagged as HIGH or MEDIUM is fixed, and I confirmed each one myself, including with `xlrd` installed and with real encrypted files.
- **The rule-1 gain is large:** `main` delivers 81 files with lost sheets and success wording per 351 runs on this grid; the branch delivers 0.
- **Everything new fails closed.** N1–N6 refuse or describe; none delivers, claims an outcome, or leaks text from the file or an exception.

**This is close for one reason, N1.** It's a regression from `main` for a valid one-sheet file. If the user knows their workbooks come from a tool that types the workbook part through `<Default>` (openpyxl says some do), fix N1 before merging. It's about 6 lines plus a test, and the re-review can be limited to `_declared_sheet_names`.

**Confidence:** Medium-high.

**What would change this verdict:** any one of these would make it DON'T MERGE:
- a mainstream producer (Excel, LibreOffice, Google Sheets, Numbers, a common library) that writes `<Default>`-typed workbook parts;
- any input that gets a sheet dropped from a delivered file.

**Next change after the merge, in order:**
1. N1: workbook-part lookup as in openpyxl, plus a test.
2. N4: the write error becomes "could not write the new file", plus an engine-level test of a write failure.
3. N2: an honest reply for an empty plan.
4. N3 and N5.

## Deferred

These don't block the merge. They're added to `docs/backlog.md` item 1a.
- **N1 (Medium):** a `<Default>`-typed workbook part, and probably template content types, are refused; mirror `openpyxl.reader.excel._find_workbook_part`.
- **N2 (Medium):** a vague turn on a multi-sheet or unreadable workbook gets "I wasn't able to confirm that worked. No verifier has been configured."
- **N3 (Low):** `EncryptedPackage` found in `.xls` cell text calls the file password-protected; match only a directory-entry position.
- **N4 (Low):** "could not write the cleaned copy" matches `_COMPLETION_CLAIM`.
- **N5 (Low):** a non-Excel OLE2 file is called an "older .xls workbook".
- **N6 (Low):** the live script's password fixture is a stand-in, and its independent check is uninformative.
- Still open from the earlier review: L3, L4, L5; an explicit analysis of a multi-sheet workbook names no sheet; per-sheet cleaning; `xlrd`.
