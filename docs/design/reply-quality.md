# Reply quality

Branch `feat/reply-quality`. Backlog items 2 and 3, backlog R2-1 and N2, and the plural and download-name wording. Rule 1 is the centre of it: a reply never claims an outcome that didn't happen, and the Voice model never writes reply text (`CLAUDE.md`, "Decided behaviour"). Every new sentence is **fixed text chosen by code**, or **a number or refusal text that code already holds**.

## What was asked (the owner's five parts)

1. **"Nothing ran" says why.** When a turn runs no file-changing step, the reply states the real reason. It comes from data the code already has: the file can't be cleaned (the refusal's own fixed text), there is no file yet, or the request wasn't understood as something Buddy can do (a fixed line listing what Buddy can do). "No verifier has been configured" never reaches a user (R2-1, N2).
2. **Greetings and capability questions** get a short fixed reply in Friend or Focus style and no analysis dump. An action request misread as a greeting never gets a completion claim. When Think asks for clarification, the user sees a question: Think's reason is mapped to a fixed catalog question. Think's own words are never shown.
3. **A requested report is delivered or stated as not available.** Delivered means the analysis findings on the final file, labelled as the report. The analyzer reports exact duplicate rows with a count, so "It still has 2 duplicate rows" can't sit next to "None of the issues I check for were found."
4. **Wording.** Correct plurals everywhere (`1 row`, `2 rows`, never `row(s)`); every reply template read once for flow; numbers come only from verifier measurements. The phrase catalog and the UI copy snapshot are updated.
5. **Download names.** A person downloads `report_deduplicated.xlsx` or `report_cleaned.xlsx`, without the internal id. Files on disk keep their unique names. Two downloads in one session don't collide.

## Decisions

### D1. What decides the reply of a turn that changed nothing

`buddy.voice.TemplateSpeaker._no_change_reply`, for a turn with no file-changing step, no stage failure and no recorded error. The first that holds decides the main sentence:

| # | Condition (data) | Reply |
|---|---|---|
| 1 | `buddy.actions.is_conversation`: Brain's intent is `conversation`, and Brain listed no action (the report included) and the mention detector finds no file-changing action named in the message | greeting + what Buddy can do + what to do next (D3) |
| 2 | the session has no file | `NO_FILE_SENTENCE` + "No changes were made to any file this turn." |
| 3 | `BuddyState.file_refusal` (set by the Planner) | `I can't work on this file: <the skills' own refusal text>.` + no-changes sentence |
| 4 | Think asked for clarification | the fixed question for Think's reason code (D2); for `failed`, the "couldn't work out what you asked" sentence |
| 5 | Brain's call failed (`intent is None`) | the "couldn't work out what you asked" sentence + no-changes sentence |
| 6 | otherwise | each ledger action that didn't run, in the outcome renderer's own sentence for its reason code (`unsupported` quoted, "your message mentions X but I didn't run it", "I read it as part of your request but didn't run it"), then what Buddy can do; or, with an empty ledger, "I didn't read that as something I can do" and what Buddy can do |

After the main sentence: the requested report or why there isn't one (D4); else the analysis findings if the analysis ran; the "initial analysis couldn't run" sentence if it failed; and, unless the reply is a greeting or a question, "No changes were made to any file this turn."

A cleaning request on a refused file *does* run a step (the skill refuses): it keeps the stopped-chain reply, which already quotes the refusal text. Its opener says "I ran into a problem before I could change anything." and not "partway through" when no file-changing step had succeeded before the chain stopped.

### D2. Think's reason is a closed vocabulary

`Reasoning.clarification_reason` is one of `which_action`, `which_file`, `other` (the model picks from the list in Think's prompt) or `failed` (set by code when Think itself couldn't produce a reading). Anything else the model writes becomes `other`; no free text is kept. `voice_catalog.CLARIFICATION_QUESTIONS[reason][style]` holds the question. This is the same pattern as Brain's closed intent vocabulary: the model chooses a label, code writes the sentence.

### D3. Greetings are a Brain intent, guarded by code

Brain's vocabulary gains `conversation`. It lets a turn be answered with a greeting only when nothing contradicts it (`is_conversation`). The Planner also runs no proactive analysis for a conversation turn or a turn waiting for the user's answer (nobody asked for the file to be looked at, and a file is analysed once, so the findings would be used up before the real request). A message that asks for a report still gets its analysis.

If a step above READ tier ran or was planned, the turn is not conversational whatever the intent: the greeting path is only reached when no file-changing step ran, and a delivered turn is rendered by the outcome block. A misread action request whose wording gives it away (a mention of a file-changing action, a listed action, a listed report) gets the "your message mentions…" sentences; one that gives nothing away gets a greeting, which claims nothing and invites the user to say what to do.

### D4. A requested report: Brain's action, never a word list

**Decided by the owner after review round 3.** Three review rounds found the same false reply in `_no_skill_outcome`, each time after a fix that moved a word list (open filler list: false "can't do"; closed list of work verbs: a silent drop of an extra, so "Done", and a false "can't do" on everyday words such as a column called "email"; guards on "myself", "I can", "don't ...": genuine requests vetoed). So **no word list, pattern or phrase test anywhere decides whether a message asks for a report.**

- **`report_on_file` is a row of the action table** (`buddy/actions.py`, the single table) and a member of Brain's closed vocabulary, as an intent and as a `requested_actions` action. Its capability is `spreadsheet.analyze`; it changes no file (`changes_file=False`, so it is not a planner intent step) and has **no mention pattern** (`mention=None`), so the mention detector can never add or confirm a report. Brain's prompt says what it is ("a report, summary, analysis or overview of the file, whatever it is about") and no longer names a report as an example of `unsupported`.
- **Who reads it:** `asks_for_report(understanding)` is true only when Brain's reading (its `requested_actions`, or its intent) has the action. The Planner, the outcome renderer and Voice all use that and nothing from the message.
- **When unsure, the honest side.** If Brain did not read a report (it said `unsupported`, `unclear`, `conversation`, or nothing), nothing is labelled a report; an `unsupported` entry says "I can't do that yet" with the user's words, as for any action Brain says Buddy can't do. If Brain did read one and the analysis of the right file passed, the findings are labelled as the report. If it did and they can't be given, the reply says why. **An action in the vocabulary never gets "I can't do that yet".**
- **The ledger reports it like every other action** (`buddy.outcome._report_outcome`): `DONE` when the analysis of the right file passed (when a file-changing step passed, the analysis re-run on the delivered copy; when nothing was changed, the analysis of the original), else `NOT_DONE` with the reason from the verdicts (`report_recheck_failed`, `report_analysis_failed`, `report_not_run`; fixed sentences, no count). So "Done" means the report was given too, and a delivery whose report can't be given is "Partly done" with the reason. The findings themselves are put in the reply by `buddy.voice` (`Report on the copy: ...` / `Report on your original file: ...`), from the Inspector's independently derived findings.
- **Planner.** A file is analysed when Brain's reading has the report even if it was analysed earlier; a file the cleaning skills refuse is not analysed (the refusal sentence is the reply). A planner step with a capability no skill has, whose `source_phrase` is, ignoring case and spacing, exactly a phrase Brain gave to `report_on_file`, is planned as the analysis (`spreadsheet.analyze`); the planner prompt also names it as the capability for a report. A step that doesn't match stops the chain as a step no skill can run (decided behaviour), and the reply says so.
- **Greetings** are recognised from Brain's closed intent only: `is_conversation` is `conversation` with an empty ledger (the report included) and no file-changing action mentioned. No report word is read.
- **A mention inside the phrase Brain gave to the report** ("a report on the duplicate rows") is what the report is about and is not added to the ledger (`mentioned_actions(..., except_inside=...)`, compared as text). A mention outside it is still added, as before.
- **Delivered turn:** `Report on the copy: <findings>.` when the report was given; the reason is a not-done sentence in the outcome block when it wasn't. **Nothing changed:** `Report on your original file: <findings>.` or the reason; on a file that can't be analysed, the refusal sentence. **Chain stopped:** `REPORT_NOT_PRODUCED_SENTENCE`.
- A report has no "Want me to tackle any of these?" question: the user asked for a report, and Buddy can't act on most findings.
- **Cost, stated:** if Brain does not read a report that was asked for (it says `unsupported`, or nothing), the user gets "I can't do that yet" or "I didn't read that as something I can do" and, when no report was asked for, the plain analysis findings under "Here's what I noticed". That is true and flat. The ledger-recall measurement (backlog item 1) is how Brain's recall of this action is measured.

### D5. Exact duplicates

`ExactDuplicateRowAnalyzer` (skill) and `_independent_duplicate_row_findings` (Inspector, independently re-derived) report `N rows repeat an earlier row exactly`, using the same definition the dedupe verifier and the residual measurement use (`DataFrame.duplicated`, repeated blank rows included). A residual "still has 2 duplicate rows" and this finding therefore can't disagree.

### D6. Plurals

`buddy.text.count_noun(n, singular, plural=None)`. `Action.counted` became `Action.row_noun`. Every user-reachable `(s)` is gone from `buddy/`, `skills/` and `backend/`; a test scans the string literals.

### D7. Download names

`backend.names.download_name` parses the shape a skill writes, `<stem>(_<operation>_<session>-task-<n>)+.<ext>`, and rewrites only the generated parts: each loses its id and `no_empty_rows` is written `cleaned`. The stem (the person's own words, in the alphabet uploads are limited to) is kept, and cut if it must be so the operations and the extension always show (total at most 100 characters). Ids in any other shape are dropped. `display_names` numbers repeats ` (2)`, ` (3)` in delivery order, comparing names without regard to case. The stored `DeliveredFile.name` is unchanged (the name on disk), so nothing saved changes and no migration is needed: the friendly name is computed when a response is built (turn, state and file-list views, and the `Content-Disposition` header).

## Not built (needs model-written text)

None. Every reply path uses fixed catalog text or data. Two things were considered and left out because they would need the model to write text: a reply that quotes or paraphrases Think's own reason, and a free-text answer to "what can you do?" tailored to the user's file.

## Deferred

See the review file and `docs/backlog.md`.
