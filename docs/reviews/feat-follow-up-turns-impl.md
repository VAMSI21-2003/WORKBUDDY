# feat/follow-up-turns: implementation note

Writer's note for the reviewer. Design: `docs/design/follow-up-turns.md`. Base: `origin/main` at `f01de0a` (contains `report_on_file`, PR #21).

## What changed

**Pipeline (`buddy/`)**
- `buddy/actions.py`, `buddy/models.py`: Brain's closed field `file_to_use` (`latest` | `original`; anything else is `latest`); `FileChoice` (what a caller offers) and `FileUsed` (what the turn worked on).
- `buddy/source.py` (new): `choose_file`, called by `Brain.__call__` right after the interpreter. It sets `Context.active_file` and `BuddyState.file_used`. No word of the message is read.
- `buddy/brain.py`: the prompt describes `file_to_use` and tells the model only *that* a copy exists; the parser accepts the two values.
- `buddy/outcome.py`: `already_satisfied`, from the verifier's measured `removed == 0` for every file-changing step.
- `buddy/voice.py`, `buddy/voice_catalog.py`: the file-used sentence (3 fixed sentences) replaces `FROM_ORIGINAL_SENTENCE`; the nothing-left reply; report and failed-analysis wording for a turn on a copy. `BuddyState.earlier_deliveries` and `SpeakingRequest.earlier_deliveries` are gone.

**API (`backend/`)**
- `backend/sessions.py`: `FileUsedRecord`, `TurnRecord.file_used`, `SessionRecord.latest_copy`.
- `backend/turns.py`: `_offer` (read the latest delivered file back, check its SHA-256, write a read-only copy into the turn's `input/` folder), `_file_used_record`, `_restore_upload_focus`; the integrity check also covers the delivered file and the turn's copy.
- `backend/store.py`: `file_used` saved and validated; records without it open.
- `backend/schemas.py`, `views.py`, `openapi.json`: `SessionState.next_request_file`.
- `backend/web/*`: one line in the Files panel (`next-file`), copy in `_copy.html`; snapshot updated.

**On disk.** Turn records gain `file_used` (optional). `data-v3` added; `data-v1` and `data-v2` untouched (`git diff origin/main --stat -- tests/deploy/fixtures` shows additions only).

## Choices I made that the owner didn't spell out

1. **Nothing-left applies only when the turn worked on a copy.** A first request on an upload that has nothing to remove still delivers its copy ("there were no duplicate rows to remove"), as before. Making it uniform would change an existing, tested behaviour; flagged for the owner.
2. **An explicit `file_id` on a request means "a file the person picked": the upload is used as itself.** The page never sends one. An API client that sends the active file's id on every turn therefore never chains, and the reply says it worked on the upload.
3. **"Start over" is not remembered on its own** (changed after review round 1, F1). A start-over turn that delivers makes its file the latest; one that delivers nothing changes nothing for the next request.
4. **The pipeline reads a copy of the delivered file**, not the delivered file: otherwise generated names grow ~54 characters per follow-up and a step fails after a few (`docs/design/follow-up-turns.md`).
5. **Sentence for the copy** says "the latest copy I handed back", not "from your previous request", because a failed turn can sit between.
6. A delivered file that is gone or changed on disk is not used; the reply says the upload was.
7. The skill still writes an identical file into the turn's folder in the nothing-left case; it is not listed or downloadable. The reply says "handed back no new file", not "made no new file".

## Tests

- `tests/buddy/test_follow_up_files.py` (31): through `build_engine`, three speakers, independent openpyxl reads of the file used; two and three requests, start over, bad `file_to_use` values, new and picked upload, unavailable copy, nothing left (single and chained), a report on a copy, no choice offered, no file, the model told only that a copy exists.
- `tests/backend/test_follow_up_turns.py` (18): through the API, same checks on downloaded files; failed turn and rejected plan between; greeting between; another person; restart; the record on disk; delivered file and upload unchanged; copy gone or changed; corrupt `file_used` not restored.
- `tests/web/test_next_file_line.py`; `tests/deploy/test_saved_data_survives.py` (old session builds on its delivery, and can start over).
- Updated: tests that assumed "every turn reads the upload" now use Brain's start over to get a second delivery (`single(intent, file_to_use="original")` in `tests/backend/factories.py`).

## Not run

No live runs (the owner checks on the server).
