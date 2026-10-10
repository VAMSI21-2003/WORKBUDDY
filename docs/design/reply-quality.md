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
| 1 | `buddy.actions.is_conversation`: Brain's intent is `conversation`, and Brain listed no action, the mention detector finds none, and the message doesn't ask for a report | greeting + what Buddy can do + what to do next (D3) |
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

If a step above READ tier ran or was planned, the turn is not conversational whatever the intent: the greeting path is only reached when no file-changing step ran, and a delivered turn is rendered by the outcome block. A misread action request whose wording gives it away (a mention, a listed action, a report request) gets the "your message mentions…" sentences; one that gives nothing away gets a greeting, which claims nothing and invites the user to say what to do.

### D4. A requested report

`buddy.actions.mentions_report(message)` (deterministic) says the user asked for one. One pattern (`_REPORT_REQUEST`, the union of `_REPORT_BY_NAME` and `_REPORT_BY_ACTION`) is shared by every stage that asks "is this a report request?" (the Planner, the mention detector, the outcome renderer), so they can't disagree about a wording. It matches a request verb and determiners, the report noun or verb ("report", "summary", "analysis", "overview", "rundown", "quality check", "analyze", "summarize"), and what it is about ("of the file", "on the duplicate rows", "of everything"), and the analysis asked for by what it does ("check the file for problems", "what is wrong with my file"). It is not a request when the message negates it ("no report", "don't look at my data"; "don't forget to" and "don't hesitate to" are not refusals), when the person says they will do it themselves ("I'll check the file myself", "I'll summarize it myself": the first-person marker, the verb and "myself" in one clause; for a request with no report word in it, only "check/look at/review/inspect the file myself" and "I'll check the file"), when the report is to be sent somewhere ("email me the report"), when the noun is the name of part of the file ("the Summary sheet") or when it asks about Buddy ("an overview of what you can do", which is answered as a capability question).

- The Planner analyses the file for a report request even if it was analysed earlier, so the report can always be made.
- A planner step whose `source_phrase` is only a report request (`is_only_a_report_request`) is planned as `spreadsheet.analyze`, so a model's own name for it ("spreadsheet.report") doesn't stop the chain as a step no skill can run.
- **Delivered turn:** if the re-run of the analysis on the final file passed, the reply says `Report on the copy: <findings>.` (the findings, or the analyzer's all-clear). Otherwise it says why there is no report (`REPORT_RECHECK_FAILED_SENTENCE`, `REPORT_ANALYSIS_FAILED_SENTENCE`, `REPORT_NOT_RUN_SENTENCE`).
- **Nothing changed:** `Report on your original file: <findings>.` or the same reasons; on a file that can't be analysed, the refusal sentence.
- **Chain stopped:** `REPORT_NOT_PRODUCED_SENTENCE`.
- **A report is never also "something I can't do".** Brain's prompt names "a report" as the example of `unsupported`, so its `unsupported` entry is often the report. When the reply states the report (`build_outcome_report(report_stated=True)`), the entry's phrase is judged in one place (`_no_skill_outcome`), and **no list of words decides whether the rest of a phrase is work Buddy can't do**: three review rounds each found such a list wrong in one direction (an open filler list: a false "can't do"; a closed list of work verbs: a silent drop of an unlisted extra, which let the lead word say "Done", and a false "can't do" on everyday words such as a column called "email"). So:
  - a phrase that is **only** a report request, in nouns about the file (`is_only_a_report_request`: "give me a report", "analyze the first sheet", "a summary of the missing values") produces no sentence: the report has its own;
  - a phrase that holds a report request **and any other word** produces the `REPORT_ONLY` outcome: one fixed sentence, "I did nothing beyond the report for that part of your request.", true whether the other words ask for work Buddy can't do ("... and send it to my boss") or only describe the report ("the email column"), and true even when the report itself isn't available. It has no source, so the lead word is neither "Done" nor "Partly done" (the reply never reads as complete). The cost is a flatter reply for a descriptor not in the noun list (`_REPORT_ABOUT`); a reply is never false for it;
  - a phrase with no report request is the ordinary "I can't do that yet: “...”" (it quotes the user's words when Brain's phrase is in the message).
  - On a turn that changed nothing, the report comes first and the `REPORT_ONLY` sentence after it, with no list of what Buddy can do.
  - The alternative that needs no sentence at all is a closed `analyze` action in Brain's vocabulary, so the model, not a pattern, says which phrase is the report. That changes the ledger's action table and is the owner's decision (see the fixes file for round 3).
- A report has no "Want me to tackle any of these?" question: the user asked for a report, and Buddy can't act on most findings.
- What a report is about is not a request to remove it: "a report on the duplicate rows" does not make the mention detector add "remove duplicate rows" to the ledger.

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
