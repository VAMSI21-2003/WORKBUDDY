# fix/conversation-repair: implementation note

Writer's note for the reviewer. Design: `docs/design/conversation-repair.md`. Base: `origin/main` at `a4c959b`. The owner's prompt is the list of seven items, in order; each section below says where it lives.

## What changed

**Pipeline (`buddy/`)**
- `buddy/think.py`: one retry on any failed call, with the failure logged at WARNING by turn id and kind (3); `settle_file_question` (2), called by the Think stage after the thinker; the prompt says only that the file is settled (no name).
- `buddy/brain.py`: the same retry (3); `UnderstandingRequest.pending_request`, the prompt line, the JSON field `answers_pending` and its parser; `MentionCrossCheck` reads the waiting request when Brain says it was answered; `Brain.__call__` swaps `state.user_message` for the waiting request when `answers_pending` (1).
- `buddy/source.py`: `match_file_name` (2).
- `buddy/models.py`, `buddy/state.py`: `FileChoice.unclear`, `Understanding.answers_pending`, `BuddyState.turn_id`, `BuddyState.pending_request`.
- `buddy/voice.py`, `buddy/voice_catalog.py`: the file-used sentences name the file (2); `asked_which_file` (1).
- `buddy/actions.py`, `buddy/inspector.py`, `buddy/voice_catalog.py`: "exact duplicate row(s)" in counts and in the capabilities line (5). The person's own words in "Your message mentions duplicate rows" and "I read your request as…" are not changed: they are not claims about what was done.
- `buddy/inspector.py`, `skills/excel/analyze.py`: the trim/case finding (5), written twice on purpose (the verifier recomputes it).

**API (`backend/`)**
- `backend/sessions.py`: `PendingRequest`, `SessionRecord.pending`, `SessionRecord.upload_of_delivered`.
- `backend/store.py`: `pending_request` in `session.json` (always written; optional when read) and its validation.
- `backend/turns.py`: `_named_upload` and the offer's `unclear` (2); the waiting request offered to Brain only when the message names a file (1); `record.pending` set from `asked_which_file` when a turn commits (1); `work.turn_id` (3); a delivered turn drops its upload from `analyzed_files` (4).
- `backend/logging_filter.py`: `python -m backend.logging_filter` (7).
- `backend/web/*`: the "active" tag is gone (6).

**Deploy**
- `deploy/lib.sh` (`save_app_logs`), `deploy/release.sh`, `.gitignore`, `docs/deploy.md` (7). **`deploy/` changed: the owner pulls before releasing.**

**On disk.** `session.json` gains `pending_request`. Migration: the store reads a record with or without it. `data-v1` to `data-v3` untouched; `data-v4` added (`python scripts/make_data_fixture.py 4`).

## Choices I made that the owner didn't spell out

1. **Where the file is settled.** In code, in the backend (`TurnRunner.start`) and in Think's stage, not in Brain. A named file is the turn's upload; its *latest delivered copy* is offered as for any follow-up (decision A). A name that matches nothing or two files equally asks the existing question.
2. **What counts as a file name.** Only a token shaped like a spreadsheet file's name (a stem and an extension that is close to `xlsx`, `xlsm`, `xls` or `csv`). A stem alone ("messy-data") is not matched, so a word like "report" can't select a file. A delivered file's name (as shown on the page) is matched too, and means the upload it was made from.
3. **The waiting request lives for one turn** and is offered to Brain only when the new message names a session file. A turn that fails leaves it alone.
4. **Brain's `answers_pending` is a new field, not a new intent.** It is true only when the prompt showed a waiting request *and* Brain answered a JSON `true`.
5. **Turn 8.** The cause was the once-per-file analysis; the fix is in the backend only (a delivery makes its upload "not analysed" again), so the planner and the CLI behave as before. After a declined plan the retry that delivers does not repeat the findings the declined reply showed (tested, noted in the design).
6. **The retry sends the same prompt again.** Nothing about the failure is added to it.
7. **The raw model reply is no longer logged** on a non-JSON reply (it was before; the owner asked that logs never carry file contents, and a reply can echo the message).
8. **Logs on a failed release.** `release.sh` also saves the failed release's logs before the rollback replaces the container. The owner asked only for the old container's logs; this is the same mechanism for the logs most worth keeping.
9. **The filter runs in the new image,** because the host has only Docker. If it can't run, no log file is kept.
10. **Clarification wording is unchanged** ("remove duplicate rows, remove empty rows, or both?"). It asks about the person's request, not a result.

## Tests

- `tests/backend/test_conversation_repair.py` (30): the transcript, end to end through the API in the real pipeline with only the LLM faked: settled file; slips in names (`mess-data.xlsx`, `messy-data.xslx`, case, quotes); a delivered file's name; an unknown name asks and the next message resumes; Brain decides a message is a request of its own; the pending request only reaches Brain when a file is named; a restart; ambiguous names; a failed Think or Brain once and twice; the log line; turn 8; the trim/case finding; "exact duplicate rows"; upload hashes unchanged.
- `tests/buddy/test_file_names.py`, `tests/buddy/test_retry.py`: the matcher, `settle_file_question`, Brain's parsing of `answers_pending`, the retry and its logs.
- `tests/backend/test_store.py`: `pending_request` read, written, absent in an old record, corrupt values refused.
- `tests/deploy/test_release_scripts.py`: logs saved before the container is replaced, mode 600/700, newest 20, filter failure keeps nothing, a failed release saves its own logs, the folder is git-ignored, the saved file passes `redact` unchanged.
- Updated: the sentences that now name the file (`tests/file_sentences.py`), the reply counts that now say "exact duplicate row(s)", the Brain-fails double (it must fail twice), the copy snapshot of the page, the web test for the removed tag, the `analyzed_files` persistence test (it needs a turn that delivers nothing).

## Not run

- No live runs in this session (the owner's prompt; `docs/process/review-loop.md`, "Live runs"). The owner checks on the server: the transcript's turns 1 to 8, and `deploy/logs/` after the release.
- `release.sh` against real Docker: only the stand-in `docker` of the test suite.
