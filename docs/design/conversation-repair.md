# Conversation repair

Branch `fix/conversation-repair`. It answers one live transcript from the owner (Friend mode, two uploads, `messy-data.xlsx` marked active) in which Buddy lost track of the conversation. Rule 1 (never claim an outcome that didn't happen) and rule 2 (never modify an upload or a delivered file) hold for everything here. The change is rule-touching because reply wording changes (items 2 and 5).

## The transcript, and what each turn showed

| Turn | Message | What Buddy said | Cause |
|---|---|---|---|
| 1 | "remove duplicate rows" | "I couldn't work out what you asked for just now…" | Think produced no reading (`CLARIFY_FAILED`). No server logs exist: `release.sh` replaced the container and Docker deleted its logs, so the cause (an LLM call exception, non-JSON, or a wrong JSON shape) is unknown. All three paths set the same code |
| 2 | "remove duplicate rows" | "Before I start: which file do you mean?" | Think asked `which_file` although the session had an active upload |
| 3 | "messy-data.xlsx" | a greeting and the capabilities | The question was not remembered, so the answer was read as a new, empty message |
| 5 | "now remove the empty rows too in mess-data.xlsx" | "which file?" | The typo matched nothing, and Think asked again |
| 6 | "messy-data.xslx" | a greeting again | Same as 3 |
| 8 | "start over from my original file and remove the empty rows" | right file, but no "I also noticed" line | The upload had been analysed in turn 1, and a file is analysed once per session; nothing was queued, so nothing was rechecked on the new copy (reproduced with fakes: `tests/backend/test_conversation_repair.py::test_a_start_over_reports_what_the_new_file_still_has`) |

## What changed, in the order the owner asked

### 1. A question about the file is remembered

When the reply to a turn is the question "which file?", the request it was about is saved in the session (`SessionRecord.pending`, `PendingRequest(message, turn_id)`). It is saved only when the reply really is that question: `buddy.voice.asked_which_file` mirrors the order of `TemplateSpeaker._no_change_reply` (a greeting, no file, or a file the skills refuse each come before the question, and a failed stage gives no question), so it never remembers a request the reply didn't ask about.

The next message **resumes** it only if the message names a spreadsheet file (item 2's matcher), whether or not that file is one of the session's: a wrong name followed by the right one must still resume the original request (review round 1, F1). Then the waiting request is shown to Brain (`UnderstandingRequest.pending_request`), and **Brain decides** whether the message is only the answer (`Understanding.answers_pending`, true only when the prompt showed a waiting request and Brain answered a JSON `true`) or a request of its own. If it is the answer, `BuddyState.user_message` becomes the waiting request, Brain's `requested_actions` phrases are checked against it, and every later stage works on the request that was waiting. Two guards make a wrong reading visible (review round 1, F3): `MentionCrossCheck` reads **both** the waiting request and the message itself, so an action the message mentions that Brain's reading lacks is stated as not run; and the reply carries one fixed sentence, "I took your last message as the answer to my question and went on with your earlier request." (not on a reply that asks again). If Brain says it is a request of its own, nothing resumes and the message is read as usual. No code reads a word of the message for either (`CLAUDE.md`: the meaning of a request is Brain's).

A pending request lives for one turn: any turn that finishes normally replaces it with its own, or with none. A turn that **couldn't read the request** leaves it as it was, so a retry still finds it: a stage error (Brain, Think, Planner, Hands, Inspector), Think or Brain failing twice, or the run raising or passing its deadline (review round 1, F2).

### 2. "Which file?" is asked only when it is truly unclear

The file is code's to settle (`buddy.think.settle_file_question`):

- The message is looked at for a spreadsheet file name (`buddy.source.match_file_name`). A name matches the session's own file names (uploads, and delivered files as the person sees them) with close-match tolerance: at least 0.85 alike by `difflib`, case ignored, so `mess-data.xlsx` and `messy-data.xslx` find `messy-data.xlsx`. An exact name beats a near one; two different names equally close, or a spreadsheet-shaped name that matches none, are *unclear*. A spreadsheet-shaped name that is none of the files (`save it as cleaned.xlsx`) is a question only when the session has **more than one upload**; with one there is nothing to choose, the turn goes on, and the reply names the file it used (review round 1, F4; the multi-upload case is the owner's to confirm). Two or more names equally close are a question unless they are all of one upload (a file and its copy). Only a token with the shape of a spreadsheet's name counts (a stem and an extension close to `xlsx`, `xlsm`, `xls` or `csv`), so no ordinary word, and no stem like "report", matches a file. This is matching a known list of names, not deciding what a request means.
- A named upload (or the upload a named delivered file was made from) is the turn's file. With no name, it is the session's active upload. In both cases the turn is offered that upload's **latest delivered copy** (decision A of `follow-up-turns.md`), and Brain's `file_to_use` still decides whether to start over.
- Think's `which_file` is dropped when a file is settled this way. If the file is unclear (`FileChoice.unclear`), the question is asked in its fixed words even when the model didn't ask. The model is told only that the file is settled (no name; nothing about a copy but that one exists reaches a model).
- Every delivered reply now **names the file it worked on**: ``I worked on `messy-data.xlsx`, your original upload.``, ``…your original upload, not on a copy I handed back earlier.``, or the existing sentence for a copy.

### 3. A failed Think or Brain call is tried once more

`ClaudeThinker` and `ClaudeInterpreter` try the call again once when it raised, returned non-JSON or returned the wrong shape. It has no side effects. Each failure is logged at WARNING with the turn id (`BuddyState.turn_id`, set by `TurnRunner`) and the kind of failure (for a raised call, the exception's class name only). Neither the reply, the message nor the exception text is logged; the previous code logged the model's raw reply, and that is removed. Two failures in a row give exactly the old result (`Reasoning` with `CLARIFY_FAILED`, an `Understanding` with no intent), so the reply is the existing honest one.

### 4. Turn 8: the missing findings

Cause: the analysis is skipped for a file already analysed this session (`Context.analyzed_files`), and findings come only from an analysis that ran this turn (rechecked on the file handed back). The upload was analysed in turn 1, so a start-over from it handed back a new file with no findings about it. A follow-up that builds on a *copy* was never affected: a copy's path is dropped from `analyzed_files` after the turn, so each follow-up analyses it.

Fix (`backend.turns._restore_upload_focus`): a turn that **delivers** a file also drops its upload from `analyzed_files`, so a later turn that goes back to the upload analyses it again and the reply says what the new file still has. The once-per-file rule still holds where it was meant to: a question, a declined plan or a failed turn leaves the upload analysed, so their findings are not repeated. **Known, accepted:** after a declined plan the findings were shown in that reply, so the retry that delivers doesn't say them again (tested: `test_a_turn_that_delivers_nothing_does_not_repeat_the_findings_next_time`).

### 5. Honesty about "duplicate"

- Every dedupe reply says "exact duplicate rows" (`buddy.actions`), as does the capabilities line.
- `spreadsheet.analyze` adds a finding for rows that match an earlier row once spaces around the text are trimmed and case is ignored, but aren't exact repeats: "N rows match an earlier row once spaces around the text and capital letters are ignored, but aren't exact repeats". The skill has its own analyzer, and the verifier recomputes it independently (`buddy.inspector._independent_loose_duplicate_row_findings`); the reply uses the verifier's. Nothing is removed (that would be a new skill, later).

### 6. The page shows one current file

The "active" tag next to an upload is removed. The line "Your next request will use …" is the only statement of which file is current (`tests/web/ui_copy_snapshot.txt` loses the string "active").

### 7. Logs survive releases

`deploy/release.sh` saves the running app's logs to `deploy/logs/app-<commit>-<UTC time>.log` before it replaces the container, and the failed release's own logs before a rollback replaces them. The folder is mode 700, files 600, git-ignored, and holds the newest 20. The logs go through `python -m backend.logging_filter` (new; runs `redact` over stdin) in the new image before they are kept, and nothing is kept if the filter can't run. It is best effort: a failure to save is a warning and never stops a release. Tests: `tests/deploy/test_release_scripts.py` ("the running app's logs are saved…").

**`deploy/` changed: the owner must `git pull` before `./deploy/release.sh`** (the script refuses to run otherwise).

## On-disk format and migration

`session.json` gains the key `pending_request`: `null`, or `{"message": <str, 1 to 20,000 characters>, "turn_id": <a turn of the session>}`. It is always written; `FORMAT_VERSION` stays 1, as it did when `confirmation` and `file_used` were added.

- **Migration:** the store accepts a session record with or without the key (`_SESSION_OPTIONAL_KEYS`); without it the session has no waiting request. Every earlier fixture (`data-v1` to `data-v3`) is untouched and still opens (`tests/deploy/test_saved_data_survives.py`).
- **New fixture:** `data-v4` (`python scripts/make_data_fixture.py 4`), beside the old ones. The shape guard now compares today's output with `data-v4`.
- **Going back a version:** a server from before this change checks `session.json`'s keys exactly, so after a rollback a session saved by this version reads as not found. This is the situation `docs/deploy.md` ("Rolling back doesn't restore data") already describes; the remedy is the same, to restore the backup the release took first.
- The turn record, the file registry and delivered files are unchanged. The API's schemas are unchanged (`backend/openapi.json` is not touched).

## Decided behaviour that this leaves alone

The Voice model still writes no reply text. Brain's intents and actions are the same closed vocabularies (one new boolean field, `answers_pending`). Multi-step chains, the report rules and the planner's rules are untouched.

## Not done here

- Removing rows that match after trimming spaces or ignoring case (a new skill).
- Naming a file by its stem only ("clean messy-data"): a stem alone is not matched, by design, so a word like "report" can't select a file.
- A pending request that outlives one turn.
- Live runs: none in the session. The owner checks on the server (`docs/process/review-loop.md`, "Live runs").
