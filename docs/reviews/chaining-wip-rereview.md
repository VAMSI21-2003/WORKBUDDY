# Re-review: `chaining-wip` after the review fixes

Review only. No code was changed and nothing was committed.

> **Reviewer independence: not met.** You asked for a reviewer with no memory of this code. I do have memory of it: I wrote the fixes being reviewed (commit `71ec26d` and `docs/reviews/chaining-wip-fixes.md`) earlier in the same conversation.
>
> To offset that, I didn't rely on the fixes doc or on anything I said earlier. Every result below comes from code run during this re-review. The probe scripts ran the real `build_engine` pipeline, with real skills and real `.xlsx` files; only the LLM was faked.
>
> A reviewer who didn't write the fixes may still catch what I miss. For a truly independent check, rerun this review in a fresh session.

## 1. Repository state

```
$ git status
On branch chaining-wip
nothing to commit, working tree clean

$ git log --oneline -6
71ec26d fix: address chaining-wip review (B1, M1, L2, M2/M3 guards, L5, one "passed" rule)
ed08226 fix: no false completion claims, produced file always named, findings describe the final file
53db0b3 wip: step chaining + duplicate-analyze filter (not ready: false completion claim in Voice)
9bb03bc fix: Think now knows Buddy executes skills itself and their real limits
a5552a4 fix: force UTF-8 console output in run_dedupe_manually.py so Windows cp1252 consoles don't crash on LLM Unicode
12d4489 fix: PersonalityVoice no longer fabricates outcomes or leaks internal capability labels
```

**Everything is committed.** Line numbers below refer to `71ec26d`.

## Summary of findings

| ID | Severity | Finding |
|---|---|---|
| R1 | **High** | When nothing was delivered, `PersonalityVoice` still passes on completion claims phrased in words its regex doesn't list |
| R2 | Medium | The M2 order check still silently reverses a correct plan when the order is stated as "last", "second" or "ahead of that" |
| R3 | Medium (not reachable today) | When a file was delivered but failed its final-file check, `PersonalityVoice` passes on "All done" and says the cleaned copy is ready |
| R4 | Medium (existed before the fixes) | When analysis of the original fails, a delivered, verified file is reported as "wasn't able to confirm", and the file isn't named |
| R5 | Low | `_plain_words` changes legitimate text: other file types whose name contains "spreadsheet.", and a missing space after "spreadsheet." |
| R6 | Low | When a verifier raises an exception, the turn crashes with no reply; if it happens inside the chain, a leftover intermediate file stays on disk |
| R7 | Low | The reason given for a malformed step is misleading: "execution did not produce an output file" |
| R8 | Low (not reachable) | The strict "passed" rule compares path strings exactly, so the same file written differently counts as a mismatch |

**Test suite:** 449 passed (the expected count), run during this re-review.

---

## 2. Each fix, checked

### B1: "Done" when nothing was delivered

**Original scenario:** "remove the duplicate rows and then give me a report", run through `build_engine`.
- **Steps:** `[(analyze, success), (deduplicate, success), (report, failure)]`. `stopped_at` is `spreadsheet.report`, `outputs == ()`, and the original file is unchanged.
- **Inspector:** `passed=False`, and the report step's own verdict is `False`.
- **`TemplateSpeaker` reply:** "…I ran into a problem partway through. The report step: couldn't be run because no available skill can do it. I stopped at the report step and no file was handed back; your original file is unchanged." It contains no "Done", no completion wording and no capability id.
- **`PersonalityVoice`, model reply "All done — duplicates removed.":** the reply is rejected and the factual reply above is used instead.

**New scenario: a three-step chain that stops at the middle step.** The message is "remove the duplicate rows, then give me a report, then remove the empty rows".
- Dedupe passes, report fails, and remove-empty-rows is skipped. Inspector fails the turn.
- `TemplateSpeaker` names the step that stopped the chain, and says that remove-empty-rows never ran.

**Verdict:** fixed on the `TemplateSpeaker` path. **Broken on the `PersonalityVoice` path (R1).**

**New scenario: Inspector itself raises an exception.** A patched verifier raises inside Inspector.
- `Engine.run` re-raises the exception (`core/engine.py:122`).
- `state.reply` is `None`. The original file is unchanged.
- There's no false claim, but there's no reply at all. See R6.

### M1: a failed re-check of the final file

**Original scenario:** analysis fails only on its second call.
- Inspector `passed=True`. The re-check verdict is recorded as `passed=False, recheck=True` and doesn't count against the turn (`buddy/inspector.py:588`).
- The reply starts "Done —", includes "Saved as `report_no_empty_rows_s-task-1_deduplicated_s-task-2.xlsx`", and ends "I couldn't re-check the final file for anything else worth flagging after the changes."
- The reply contains no `spreadsheet.` and no `[`.

**Verdict:** fixed.

**New scenario: the reverse case.** Analysis of the *original* fails, and the re-check passes. That exposed R4, which already existed before the fixes.

### L2: "I did the work" with no verification

- **Original scenario:** only a read-only step ran, and there's no verification. The reply is "I wasn't able to confirm any result. No changes were made to any file this turn." **Fixed.**
- **New scenario: no verification, and the chain stopped.** The reply is "I ran into a problem partway through. deduplicate failed verification: 2 row(s) missing. I stopped at the deduplicate step…". This is correct.
- **New scenario: no verification, and a file was delivered.**
  - `TemplateSpeaker`: "I made the changes, but wasn't able to confirm the result." (`buddy/voice.py:405`). That's honest.
  - `PersonalityVoice` with the model reply "All done!": "All done! Your cleaned copy of u.xlsx is ready.". The claim goes through because something was delivered.
  - This is the same gap as R3, and only reachable when Inspector doesn't run. It always runs in `build_engine`.

### One definition of "passed"

**Checked end to end:**
- On a real two-step chain, every step's `input_reference` matches its Inspector verdict's `input_reference` exactly: the relative Windows path, and each intermediate output.
- Every step that ran counts as passed.
- On a real single-intent turn ("remove duplicate rows", with the active file given as an **absolute** path), the prompt doesn't say "NOT performed", and the absolute path is marked as analyzed.

**Verdict:** fixed. No step that genuinely passed is reported as not performed. The one theoretical weakness is R8.

### M2 guard: steps are reordered only when every quoted phrase appears exactly once

- **Original scenario:** "for the sales rows, remove duplicates, then drop the empty rows", with the model quoting "rows" for the empty-rows step. `_order_stated_by_user` returns `None`, so the model's correct order is kept. **Fixed.**
- **New scenarios:** the guard confidently reverses a correct plan. See R2.

### M3 guard: an output that can't feed the chain

**Scenario:** the real dedupe skill is patched to return two `.xlsx` files, and its own `verify()` check is bypassed.
- **Dedupe first:** the chain stops, dedupe is recorded as `failure`, and remove-empty-rows is skipped. The reply is "The deduplicate step: produced 2 file(s) instead of one .xlsx, so its result couldn't be carried forward. I stopped at the deduplicate step, so remove empty rows never ran…".
- **Dedupe last:** the chain stops, and nothing is handed back. The previous step's file is *not* handed back, which is correct.

**Verdict:** fixed.

**New scenario: the same patch, with the skill's own `verify()` left in place.** The skill rejects its two-file result before the M3 guard is reached. That gives R7.

### L5: marking a file analyzed

- **New scenario:** analysis of the original fails, and the re-check of the final file passes. `analyzed_files` is empty, so the original isn't marked.
- When the original's analysis passes, it is marked, including when the active file is given as an absolute path.

**Verdict:** fixed.

---

## 3. Regression checks

### Can `_plain_words` change a legitimate file name or sentence?

**Yes (R5).** It's applied to the model's reply (`buddy/voice.py:907`), to the whole `TemplateSpeaker` reply, and to prompt facts. The pattern (`buddy/voice.py:53`) is `\bspreadsheet\.(?!xlsx|xlsm|xls|csv)[a-z_]+\b`.

| Input | Output |
|---|---|
| `Saved your budget-spreadsheet.ods copy.` | `Saved your budget-ods copy.` |
| `Your file spreadsheet.tsv is ready.` | `Your file tsv is ready.` |
| `I opened sales spreadsheet.json for you.` | `I opened sales json for you.` |
| `Cleaned team-spreadsheet.xlsb; …` | `Cleaned team-xlsb; …` |
| `the spreadsheet.and then` (missing space) | `the and then` |
| `Q3 spreadsheet.numbers export` | `Q3 numbers export` |

These are left unchanged: `spreadsheet.xlsx`, `spreadsheet.XLSX`, `my_spreadsheet.ods`, and `spreadsheet.Then`.

### Can a step that genuinely passed be reported as NOT performed, because of path formats?

**Not in the real pipeline.** `CompositeVerifier` copies each step's own `input_reference` string into its verdict, so the two can't differ. That was confirmed on a relative Windows path, on intermediate outputs, and on an absolute active file.

The comparison itself is exact-string, though (`buddy/voice.py:145`):
- `uploads/report.xlsx` vs `uploads\report.xlsx`: passed = **False**
- relative vs absolute path: passed = **False**
- an empty verdict path: passed = True, because a missing path matches anything

So any future verifier that records its own path, rather than copying the step's, would turn genuine passes into "NOT performed" (R8).

### Does any reply still say "Done" or imply completion when `outputs == ()`?

- **`TemplateSpeaker`:** no. Every probe with `outputs == ()` went to the stopped or no-changes replies, and none matched the completion-claim pattern.
- **`PersonalityVoice`:** yes (R1).

---

## 4. Findings, most severe first

### R1 (High): nothing delivered, and `PersonalityVoice` passes on a completion claim

**Where:** `buddy/voice.py:158` (`_COMPLETION_CLAIM`) and `buddy/voice.py:900`, where a reply is rejected only if it matches that pattern.

**Scenario** (real `build_engine` pipeline, three-step chain stopped at the middle step, `outputs == ()`, original unchanged). These model replies went to the user **unchanged**:
- "Duplicates are gone and your sheet is down to 3 rows."
- "All set! I took care of the duplicate rows."
- "Your spreadsheet now has 3 rows with the duplicates stripped out."

**Other phrasings the pattern misses:**
- "Dropped the empty rows."
- "Deduplication applied."
- "Wrapped up — 3 rows left."
- "The empty rows have been stripped out."
- "I took care of it."

**Why it matters:**
- This breaks the core rule: no file came back, and the reply says the work happened.
- The deterministic safeguard is a word list, and it lets through the wording a model actually uses.
- None of the 6 valid live runs had a stopped chain, so this path has never been observed live.
- The default `build_engine` speaker, `TemplateSpeaker`, is safe. The live path, `run_dedupe_manually.py` with `PersonalityVoice`, isn't.

**Suggested direction:** when nothing was delivered, don't rely on detecting a claim. Always use `TemplateSpeaker`'s factual reply for the outcome, or at minimum make the stop sentence and the no-changes sentence a fixed part of every reply.

### R2 (Medium): the M2 order check reverses a correct plan, with no warning

**Where:** `buddy/planner.py:321` (`_ORDER_INVERTING_WORDS`), `:326` (`_LATE_FIRST`), `:377` (sorts by where each phrase appears).

**Scenario:** `_order_stated_by_user` called directly, with the model's (correct) plan being empty rows, then duplicates.

| Message | Result |
|---|---|
| "remove the duplicate rows last, and remove the empty rows" | reordered to duplicates, then empty rows: **wrong** |
| "remove the duplicate rows second and remove the empty rows" | **wrong** |
| "remove the duplicate rows, but do the empty rows ahead of that" | **wrong** |
| "…; before that, remove the empty rows" | the model's order is kept, which is correct |

**Why:**
- The inverting-word list doesn't include "last", ordinals like "second" or "then", "ahead of", "beforehand" and similar.
- Every phrase here appears exactly once, so the new guard doesn't back off.

**Why it matters:** this breaks the user's decided rule that execution follows the order they stated. It happens silently, and it overrides a model that got the order right. This is a different problem from the deferred "fallback enforcement" item: here the check actively makes the order wrong.

### R3 (Medium, not reachable today): a delivered file that failed its final-file check

**Where:** `buddy/voice.py:900`, where the claim check runs only when nothing was delivered, and `:909`, where the file sentence is appended.

**Scenario** (made-up results): every step passed, but the `final_output` check failed ("1 duplicate row(s) remaining").
- `TemplateSpeaker` is honest: "I wasn't able to confirm that worked. Final file: output has 4 row(s), 1 duplicate row(s) remaining."
- `PersonalityVoice` with the model reply "All done — duplicates and empty rows removed." gives "All done — duplicates and empty rows removed. **Your cleaned copy of report.xlsx is ready.**"

**Reachability:** not reachable with the current skills. Removing empty rows can't create duplicates, and deduplicating can't create empty rows. It becomes reachable as soon as a skill is added whose change can undo another's.

**Related:** the L2 case "no verification, file delivered" (above) behaves the same way.

### R4 (Medium, already present at `ed08226`): analysis of the original fails and hides a delivered file

**Where:**
- `buddy/inspector.py:461`: a failed analysis step fails its verdict.
- `buddy/inspector.py:588`: only re-checks are left out of the overall result, so a failed analysis of the *original* still fails the whole turn.

**Scenario** (real pipeline): analysis fails on its first call, the original; the chain completes and is verified, including `final_output`.
- Inspector: `passed=False`.
- Reply: "I wasn't able to confirm that worked. Output has 3 row(s)…; final file: …; nothing notable found."
- The delivered file is **never named**, and the reply suggests the requested work didn't succeed.

**Before the fixes:** the same scenario at `ed08226` gives the same reply, so this isn't a regression. It's the unfixed mirror image of M1: a read-only step outside the chain can still fail the turn.

### R5 (Low): `_plain_words` changes legitimate text

**Where:** `buddy/voice.py:53` and `:907`.

**What it does:** see the table in section 3. Other file types whose name contains "spreadsheet." (`.ods`, `.tsv`, `.json`, `.xlsb`, `.numbers`) lose their name, and "spreadsheet.and" (a missing space) loses a word.

**Reachability:** the skills only read `.xlsx`, but a model reply can mention another file. It changes wording, not facts.

### R6 (Low): a verifier exception means no reply at all

**Where:** `core/engine.py:122` (re-raise), `buddy/hands.py:504` (the in-loop verifier call, with no exception handling).

**Scenario:**
- **Inspector's verifier raises:** `Engine.run` raises and `state.reply` is `None`.
- **The same verifier raises inside the chain:** Hands crashes mid-chain. The intermediate file `report_no_empty_rows_s-task-1.xlsx` is left on disk.

In both cases the original file is unchanged and there's no false claim. The caller gets an exception instead of an honest "something went wrong".

### R7 (Low): misleading reason for a malformed step

**Where:** `buddy/inspector.py:699`: only steps with a `failure_reason` get Hands' reason. Hands' malformed-result path (`buddy/hands.py`, where it discards the result) sets none.

**Scenario:** the dedupe skill returns two files, and its own `verify()` rejects them. The reply says "The deduplicate step: **execution did not produce an output file**." In fact it produced two, and they were discarded as malformed. The stop still happens correctly; only the stated reason is wrong.

### R8 (Low, not reachable): the "passed" rule compares path strings exactly

**Where:** `buddy/voice.py:145`.

**Scenario:** the same file written differently (a `/` vs `\` separator, or a relative vs absolute path) counts as a mismatch, so a genuine pass would be reported as "NOT performed". It isn't reachable, because `CompositeVerifier` always copies the step's own string.

---

## 5. Full suite

`python -m pytest tests`: **449 passed**, 16 warnings, run during this re-review. This matches the expected 449.

## Verdict: DON'T MERGE

- **What works:** the fixes do what they set out to do. In every scenario I ran, including the new ones (a chain stopped at the middle step, a two-file step placed first or last, analysis failing on the original with a passing re-check), `TemplateSpeaker` never claims completion when nothing was delivered. The original file was never modified, a failed re-check no longer fails the turn, and no capability id reaches a reply.
- **Why not merge — R1:** on the `PersonalityVoice` path, a stopped chain with nothing delivered can still produce "Duplicates are gone and your sheet is down to 3 rows." That's exactly the false completion claim the review's blocker was about. It's the path the live runs use, and it's guarded only by a word list the model can easily sidestep.
- **Also — R2:** the order check can still silently reverse a correctly planned order, which breaks the user's own stated-order rule.

Fix R1 (make the outcome deterministic whenever nothing was delivered) before merging, and preferably R2 as well. R3 to R8 can be deferred with notes.

---

## Deferred

The follow-up (`docs/reviews/chaining-wip-fixes-2.md`) fixed R1, R2 and R4. R3 is covered by the R1 fix. These remain open:

- **R5 (Low).** `_plain_words` still rewrites names of other file types that contain "spreadsheet." (`budget-spreadsheet.ods` becomes `budget-ods`, and the same happens with `.tsv`, `.json`, `.xlsb` and `.numbers`), and drops "spreadsheet." before a word with no space ("the spreadsheet.and then"). It changes wording, not facts. It now reaches model replies less often, since the model only phrases verified-delivery and plain conversation turns.
- **R6 (Low).** A verifier that raises still crashes the turn with no reply (`core/engine.py` re-raises). If it happens inside Hands' in-loop check, an intermediate file is left on disk. The original is untouched and no false claim is made.
- **R7 (Low).** A step discarded by its own well-formedness check is still reported as "execution did not produce an output file", when in fact it produced output that was discarded as malformed. Hands' malformed-result path sets no `failure_reason`.
- **R8 (Low, not reachable).** The strict "passed" rule still compares `input_reference` strings exactly (`/` vs `\`, relative vs absolute). `CompositeVerifier` always copies the step's own string, so it can't trigger today.
