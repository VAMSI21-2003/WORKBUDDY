# Follow-up turns

Branch `feat/follow-up-turns`. The owner's decision: **a follow-up request builds on the work already done, with judgment.** "Remove the duplicates", then "now remove the empty rows too", gives one file with both done. This replaces the rule recorded in `docs/design/backend-api-design.md` ("Implementation note, recorded with PR-2", item 2) that every turn works from the original upload and says so with `FROM_ORIGINAL_SENTENCE`, and settles backlog item "pipeline-failure-and-output-dir D3".

Rule 1 (never claim an outcome that didn't happen) and rule 2 (never modify an upload or a delivered file; every result is a new file) hold for everything here.

## What was asked, and where it lives

| # | The owner's point | Where |
|---|---|---|
| 1 | By default a turn works on the latest file Buddy delivered **from the same upload** in this session | `SessionRecord.latest_copy` (`backend/sessions.py`), offered by `TurnRunner._offer` (`backend/turns.py`); chosen by `buddy.source.choose_file` |
| 2 | **Brain** decides, through its closed vocabulary only, when the person wants to start again from the upload | `Understanding.file_to_use`, `buddy.actions.FILE_VOCABULARY` (`latest` \| `original`); Brain's prompt and parser (`buddy/brain.py`). No code reads the message for it |
| 3 | A newly uploaded file, or a file the person picked, is used as itself | A new upload has no delivered file of its own, so there is no copy to offer. A `file_id` on the request sets `FileChoice.pinned`: the upload is used, even if a copy exists |
| 4 | When the latest copy already satisfies the request, **as measured by the verifier**, the reply says so and no new file is made | `buddy.outcome.already_satisfied`: every file-changing step passed its check and the verifier's own count says it removed exactly 0 rows. Applies when the turn worked on a copy |
| 5 | Every delivered reply says, in one fixed sentence chosen by code, which file it worked on | `buddy.voice._file_used_sentence`, from `BuddyState.file_used`; the three sentences are in `buddy/voice_catalog.py` |
| 6 | No upload and no delivered file is ever changed in place; the latest copy belongs to the same session and person | The copy is found only through the session record the ownership check returned (`SessionRegistry.get`); it is re-read and its SHA-256 checked, then **copied** read-only into the new turn's folder and the pipeline reads the copy; both the delivered file and the copy are in the turn's integrity check |
| 7 | Record which file each output came from in the turn record | `file_used` on the turn record (`backend/store.py`); migration below |
| 8 | On the page, one plain line saying which file the next request will use | `SessionState.next_request_file`; the Files panel (`backend/web/templates/_parts.html`, `_copy.html`) |

## How a turn picks its file

1. **Before the run** (`TurnRunner.start`, under the session lock). The turn's upload is the one named by `file_id`, else the session's active upload. If a file was delivered from that upload (`latest_copy`), it is read back from disk, checked against the SHA-256 recorded when it was delivered, and written read-only to `outputs/<key>/<turn>/input/<short name>`. The pipeline is offered `FileChoice(original, copy, copy_name, copy_unavailable, pinned)`. A delivered file that is gone, or whose hash no longer matches, is **not used**: the turn is offered the upload alone and flagged `copy_unavailable`, so the reply says "I worked on your original upload, not on a copy I handed back earlier."
2. **After Brain** (`Brain.__call__` calls `choose_file`). The copy is used unless `FileChoice.pinned` or Brain's `file_to_use` is `original`. `Context.active_file` is pointed at the chosen path (Planner, Delegate, Hands, Inspector and Voice all read it), and `BuddyState.file_used` records which kind it was.
3. **Why a copy in the turn folder.** Skills name their output `<input stem>_<operation>_<session>-task-<n>.xlsx`. Reading the delivered file directly would put the previous output's whole generated name in the stem of the next, 54 characters longer each time, until a name passed the file system's limit and the step failed. The copy is named as the person sees the file (`backend.names.download_name`: `report_deduplicated.xlsx`), so the next name is `report_deduplicated_no_empty_rows_<key>-task-1.xlsx`, shown as `report_deduplicated_cleaned.xlsx`, and the growth is bounded.
4. **After the run** (`_record_in_session`). The session's focus is set back to the upload and `analyzed_files` loses any copy path, because what `session.json` stores names uploads only.

"Start over" needs no memory of its own. A turn that starts over and delivers makes its own file the latest, so the next request builds on that. A turn that starts over and **delivers nothing** ("use my original file" alone, a declined plan, a report) changes nothing for the next request, and its reply, which says nothing ran, is true (review round 1, F1). `start_over` is recorded on the turn only when the turn delivered. A failed turn records nothing.

## The reply

Three fixed sentences for a delivered turn, chosen from `FileUsed`:

- `I worked on your original upload.` (no copy was on offer)
- `I worked on your original upload, not on a copy I handed back earlier.` (a copy existed and wasn't used: start over, a picked file, or a copy that couldn't be read)
- ``I worked on `<name>`, the latest copy I handed back, not on your original upload.`` (the name is the one in the Files panel and the download)

A caller that offers no `FileChoice` (the command-line scripts, the unit tests) gets the first sentence for its file.

**Nothing left to do.** If the turn worked on a copy and every file-changing step passed with a measured `removed == 0`, the reply is ``The latest copy, `<name>`, already has no <rows>, so there was nothing to remove and I handed back no new file.`` plus the not-done sentences of anything else asked, a report if one was asked (labelled "Report on the file I worked on:"), and "No changes were made to any file this turn." `is_verified_delivery` is false for such a turn, so the API hands back nothing and the turn is `not_delivered`. The skill did write an identical file into the turn's own folder; it is never listed, offered or downloadable. A first request on an upload that has nothing to remove still gets its copy and "there were no duplicate rows to remove", as before. (Owner question: should that case follow the same rule?)

A report on a copy (no file changed) is labelled "Report on the file I worked on:", not "Report on your original file:", and the failed-initial-analysis sentence says "the file I worked on" when that was a copy.

## On-disk format (migration)

The turn record gains an optional key, `file_used`: `{"kind": "upload" | "delivered", "file_id": <id>, "start_over": bool}`; the key is left out when the turn has none (a failed turn, a record from before). `FORMAT_VERSION` stays 1, as it did when `confirmation` was added; the store accepts a record with or without either key (`_TURN_OPTIONAL_KEYS`) and checks a stored `file_used` (shape, and that it names an upload of the session, equal to the turn's `target_file_id`, or a file delivered by an earlier turn).

- **Old sessions keep working.** A turn without `file_used` delivered files that were made from its `target_file_id` upload, and `latest_copy` reads it that way. `tests/deploy/test_saved_data_survives.py` opens `data-v2` and checks that a new request on it builds on what it delivered, and that it can start over.
- **Fixtures.** `data-v1` and `data-v2` are as they were. `data-v3` (`python scripts/make_data_fixture.py 3`) holds a delivered turn and a rejected turn that used the delivered copy. The shape guard now compares today's output with `data-v3`.
- **Going back a version.** A server from before this change would not open a turn record that has `file_used` (its key check is exact), so after a rollback to an older release, a session that had a new turn saved reads as not found. This is the situation `docs/deploy.md` ("Rolling back doesn't restore data") already describes, and the same remedy applies: restore the backup the release took first. It is the same trade `confirmation` made when it was added.
- The API's `SessionState` gains `next_request_file` (`backend/openapi.json` regenerated). It is additive.

## Not done here

- Picking a delivered file by its id as the file for a turn (only uploads can be picked).
- Telling a person that a copy lost the formulas of the upload more than once: the values-only sentence is about the copy the turn just wrote, and the formula count is measured on the file the turn read (a copy, which has none).
- The page offers no way to start over or to pick a file; both are Brain's reading or an API field.
