"""Store — durable session state, restored lazily, with interrupted turns repaired.

Design Sections 3.1, 3.2 and 7.5 (run 8), and item 7 of "What a PR-2 author
must do" (review M2). Layout under the data root::

    sessions/{key}/owner.json          who the session belongs to (docs/design/google-sign-in.md)
    sessions/{key}/session.json        Context, file registry, turn ids, next turn number
    sessions/{key}/turns/{turn_id}.json   one turn record

``owner.json`` is ``{"version": 1, "owner": <owner id> | null, "session_id": <id> | null}``, written
before ``session.json`` when a session is created and never rewritten. ``null`` is a session
saved before sign-in existed (``migrate_ownership``): it belongs to nobody. A session without the
file, or with one that doesn't read back, belongs to nobody either. ``session.json`` is as it was
(version 1), so an older server still opens these sessions and ignores the file.

Every write goes to a temporary file that is then ``os.replace``d over the
target, so a crash leaves the old file or the new one, never half of one.

Everything read back is validated before it's used: types, id shapes,
aware timestamps, hashes, and that the context's files are exactly the
registry's uploads. ``Context.from_dict`` documents only ``ValueError`` and
``KeyError`` but can raise anything on malformed input, and accepts wrongly
typed data silently, so it runs only on data that passed validation, and
inside a catch-all. A session that fails any check is not restored: it
reads as not found, and the reason is logged without the file's content.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backend.confirmation import DECISION_SOURCES, DECISIONS, ConfirmationOutcome
from backend.schemas import TurnDetail
from backend.sessions import (
    FINISHED,
    RUNNING,
    USED_DELIVERED,
    USED_UPLOAD,
    DeliveredFile,
    FileUsedRecord,
    PendingRequest,
    SessionRecord,
    TurnRecord,
    UploadRecord,
    utc_now,
    valid_id,
)
from backend.settings import Settings
from buddy.context import Context
from buddy.state import BuddyState
from buddy.voice_catalog import FALLBACK_PIPELINE_ERROR

logger = logging.getLogger(__name__)

FORMAT_VERSION = 1
OWNER_FORMAT_VERSION = 1
OWNER_FILE = "owner.json"
MAX_RECORD_BYTES = 16 * 1024 * 1024

_KEY = re.compile(r"^[0-9a-f]{32}$")
_TURN_ID = re.compile(r"^t(\d{4,9})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MODES = ("friend", "focus")
_OUTCOMES = ("delivered", "not_delivered", "failed", "integrity_failure")
_REPLY_SOURCES = ("pipeline", "pipeline_fallback")
# A turn record's keys. PR-2 wrote them without "confirmation", and every server before the
# follow-up turns without "file_used"; a record with either, both or neither restores.
_TURN_KEYS = {
    "version", "turn_id", "seq", "message", "mode", "started_at", "target_file_id",
    "status", "outcome", "finished_at", "reply", "reply_source", "delivered_files",
    "detail",
}
_TURN_OPTIONAL_KEYS = {"confirmation", "file_used"}
# A session record's keys. Every server before the conversation repair wrote it without
# "pending_request"; a record with or without it restores.
_SESSION_KEYS = {
    "version", "created_at", "next_seq", "active_file_id", "files", "turn_ids", "context",
}
_SESSION_OPTIONAL_KEYS = {"pending_request"}
MAX_PENDING_CHARS = 20_000


@dataclass(frozen=True)
class OwnerFile:
    """What ``owner.json`` says: ``owner`` is ``None`` for a session nobody owns."""

    owner: str | None
    session_id: str | None


class CorruptRecord(ValueError):
    """A stored record failed validation. The message names the field, never its value."""


class SessionStore:
    """Reads and writes sessions under ``settings.sessions_dir``."""

    def __init__(self, settings: Settings, clock: Callable[[], datetime] = utc_now) -> None:
        self._settings = settings
        self._clock = clock

    # -- Writing -------------------------------------------------------------------

    def create_session(self, record: SessionRecord) -> None:
        folder = record.meta_dir(self._settings)
        folder.mkdir(parents=True, mode=0o700, exist_ok=False)
        (folder / "turns").mkdir(mode=0o700)
        # The owner first: a session.json with no owner file belongs to nobody.
        self.write_owner(record.key, record.owner, record.session_id)
        self.save_session(record)

    def write_owner(self, key: str, owner: str | None, session_id: str | None) -> None:
        atomic_write_json(
            self._settings.sessions_dir / key / OWNER_FILE,
            {"version": OWNER_FORMAT_VERSION, "owner": owner, "session_id": session_id},
        )

    def read_owner(self, key: str) -> OwnerFile | None:
        """What ``owner.json`` of session ``key`` says, or ``None`` if it's missing or unreadable.

        ``None`` means the same as an owner of ``None`` to every caller: nobody owns the session.
        """
        path = self._settings.sessions_dir / key / OWNER_FILE
        try:
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 4096:
                return None
            data = json.loads(path.read_text(encoding="utf-8"))
            _expect_keys(data, "owner", {"version", "owner", "session_id"})
            _check(data["version"] == OWNER_FORMAT_VERSION and _is_int(data["version"]), "version")
            owner, session_id = data["owner"], data["session_id"]
            _check(owner is None or (isinstance(owner, str) and 0 < len(owner) <= 300), "owner")
            _check(
                session_id is None or (isinstance(session_id, str) and valid_id(session_id)),
                "session_id",
            )
            _check((owner is None) == (session_id is None), "owner: owner and session_id")
        except (OSError, ValueError):  # CorruptRecord is a ValueError
            logger.error("The owner file of session %s doesn't read back; it has no owner", key)
            return None
        return OwnerFile(owner=owner, session_id=session_id)

    def owners(self) -> Iterator[tuple[str, OwnerFile]]:
        """``(key, owner file)`` for every session that has an owner."""
        try:
            keys = sorted(p.name for p in self._settings.sessions_dir.iterdir() if p.is_dir())
        except OSError:
            return
        for key in keys:
            if not _KEY.fullmatch(key):
                continue
            found = self.read_owner(key)
            if found is not None and found.owner is not None:
                yield key, found

    def migrate_ownership(self) -> int:
        """Give every session saved before sign-in existed an explicit "nobody" owner.

        A session folder with a ``session.json`` and no ``owner.json`` is from a server that had
        no owners (or from a creation that stopped half way). It is marked ``owner: null`` and
        nothing else about it is touched, so it stays on disk, unreachable, until the retention
        sweep deletes it for being idle. Returns how many were marked.
        """
        marked = 0
        try:
            folders = sorted(p for p in self._settings.sessions_dir.iterdir() if p.is_dir())
        except OSError:
            return 0
        for folder in folders:
            if not _KEY.fullmatch(folder.name) or (folder / OWNER_FILE).exists():
                continue
            if not (folder / "session.json").is_file():
                continue
            self.write_owner(folder.name, None, None)
            marked += 1
        if marked:
            logger.warning(
                "Marked %d session(s) saved before sign-in as owned by nobody; they can't be "
                "opened and will be deleted by the retention sweep",
                marked,
            )
        return marked

    def save_session(self, record: SessionRecord) -> None:
        """Rewrite ``session.json``. The caller holds the record's lock."""
        data = {
            "version": FORMAT_VERSION,
            "created_at": record.created_at.isoformat(),
            "next_seq": record.next_seq,
            "active_file_id": record.active_file_id,
            "files": [
                {
                    "file_id": f.file_id,
                    "name": f.name,
                    "size": f.size,
                    "sha256": f.sha256,
                    "uploaded_at": f.uploaded_at.isoformat(),
                }
                for f in record.files.values()
            ],
            "turn_ids": [t.turn_id for t in record.turns],
            "context": record.state.context.to_dict(),
            # The request waiting on an answer to "which file?", or null. Always written, so a
            # record says whether one is waiting; one without the key (an earlier server's)
            # reads as none.
            "pending_request": (
                {"message": record.pending.message, "turn_id": record.pending.turn_id}
                if record.pending is not None
                else None
            ),
        }
        atomic_write_json(record.meta_dir(self._settings) / "session.json", data)

    def touch(self, record: SessionRecord, now: float) -> None:
        """Set ``session.json``'s modification time to ``now`` (epoch seconds): it was just used.

        Retention reads that time for a session that isn't in memory. Never
        raises: a session that can't be touched is just swept sooner.
        """
        try:
            os.utime(record.meta_dir(self._settings) / "session.json", (now, now))
        except OSError:
            logger.warning("Session %s couldn't be marked as used", record.key)

    def save_turn(self, record: SessionRecord, turn: TurnRecord) -> None:
        """Rewrite one turn's record. The caller holds the session's lock.

        An unfinished turn is written as ``running``: a waiting plan is live
        state only, and a restart ends the turn as failed either way.
        """
        confirmation = turn.confirmation
        used = turn.file_used
        data = {
            "version": FORMAT_VERSION,
            "turn_id": turn.turn_id,
            "seq": turn.seq,
            "message": turn.message,
            "mode": turn.mode,
            "started_at": turn.started_at.isoformat(),
            "target_file_id": turn.target_file_id,
            "status": FINISHED if turn.finished else RUNNING,
            "outcome": turn.outcome,
            "finished_at": turn.finished_at.isoformat() if turn.finished_at else None,
            "reply": turn.reply,
            "reply_source": turn.reply_source,
            "delivered_files": [
                {
                    "file_id": d.file_id,
                    "name": d.name,
                    "size": d.size,
                    "sha256": d.sha256,
                    "delivered_at": d.delivered_at.isoformat(),
                }
                for d in turn.delivered_files
            ],
            "detail": turn.detail,
            "confirmation": (
                {"decision": confirmation.decision, "decision_source": confirmation.source}
                if confirmation is not None
                else None
            ),
        }
        if used is not None:
            # Which file the turn worked on, so which file each file it delivered was made from.
            # Left out when there is none, so a record that says nothing about it is the same
            # as one an older server wrote (and one that older server can still open).
            data["file_used"] = {
                "kind": used.kind,
                "file_id": used.file_id,
                "start_over": used.start_over,
            }
        path = record.meta_dir(self._settings) / "turns" / f"{turn.turn_id}.json"
        atomic_write_json(path, data)

    # -- Reading ---------------------------------------------------------------------

    def load_session(self, key: str) -> SessionRecord | None:
        """The session stored under ``key``, repaired, or ``None``.

        ``None`` when nothing is stored, and when what is stored fails any
        check (logged). Any turn still ``running`` on disk was interrupted
        by a restart: it is finished as ``failed`` with the fallback reply
        and no files, and written back, so a reconnecting client isn't left
        polling forever.
        """
        folder = self._settings.sessions_dir / key
        path = folder / "session.json"
        if not path.is_file():
            return None
        try:
            record = self._restore(key, folder)
        except Exception as exc:  # noqa: BLE001 — any bad record is "not restorable"
            logger.error(
                "Session %s couldn't be restored (%s: %s)",
                key,
                type(exc).__name__,
                exc if isinstance(exc, CorruptRecord) else "details withheld",
            )
            return None
        self._repair(record)
        logger.info("Restored session %s with %d turn(s)", key, len(record.turns))
        return record

    def _restore(self, key: str, folder: Path) -> SessionRecord:
        data = _read_json(folder / "session.json")
        _check(
            isinstance(data, dict)
            and _SESSION_KEYS <= set(data) <= _SESSION_KEYS | _SESSION_OPTIONAL_KEYS,
            "session: keys",
        )
        _check(data["version"] == FORMAT_VERSION and _is_int(data["version"]), "version")
        created_at = _aware(data["created_at"], "created_at")

        files: dict[str, UploadRecord] = {}
        _check(isinstance(data["files"], list), "files")
        for entry in data["files"]:
            upload = self._upload(key, entry)
            _check(upload.file_id not in files, "files: repeated id")
            files[upload.file_id] = upload

        paths = [str(f.path) for f in files.values()]
        context = _restore_context(data["context"], key, paths)
        active_file_id = data["active_file_id"]
        _check(active_file_id is None or active_file_id in files, "active_file_id")
        expected_active = files[active_file_id].path if active_file_id else None
        _check(
            context.active_file == (str(expected_active) if expected_active else None),
            "active_file_id: disagrees with the context",
        )

        turn_ids = data["turn_ids"]
        _check(isinstance(turn_ids, list) and all(isinstance(t, str) for t in turn_ids), "turn_ids")
        on_disk = sorted(
            p.stem for p in (folder / "turns").glob("t*.json") if _TURN_ID.match(p.stem)
        )
        all_ids = sorted(set(turn_ids) | set(on_disk), key=_seq_of)
        turns = [self._turn(key, folder, turn_id) for turn_id in all_ids]
        _check(len({t.seq for t in turns}) == len(turns), "turns: repeated number")
        _check_files_used(turns, files)

        next_seq = data["next_seq"]
        _check(_is_int(next_seq) and next_seq >= 1, "next_seq")
        used = [t.seq for t in turns] + self._turn_folders_in_use(key)
        next_seq = max([next_seq, *(seq + 1 for seq in used)])

        pending = _pending_request(
            data.get("pending_request"),
            {t.turn_id for t in turns},
            max(MAX_PENDING_CHARS, self._settings.max_message_chars),
        )

        state = BuddyState(context=context)
        owned = self.read_owner(key)
        return SessionRecord(
            key=key,
            owner=owned.owner if owned is not None else None,
            session_id=owned.session_id if owned is not None else None,
            created_at=created_at,
            state=state,
            files=files,
            turns=turns,
            active_file_id=active_file_id,
            pending=pending,
            next_seq=next_seq,
        )

    def _upload(self, key: str, entry: Any) -> UploadRecord:
        _expect_keys(entry, "file", {"file_id", "name", "size", "sha256", "uploaded_at"})
        file_id, name = entry["file_id"], entry["name"]
        _check(isinstance(file_id, str) and valid_id(file_id), "file: file_id")
        _check(_plain_name(name), "file: name")
        _check(_is_int(entry["size"]) and entry["size"] > 0, "file: size")
        _check(isinstance(entry["sha256"], str) and bool(_SHA256.match(entry["sha256"])), "sha256")
        return UploadRecord(
            file_id=file_id,
            name=name,
            size=entry["size"],
            sha256=entry["sha256"],
            uploaded_at=_aware(entry["uploaded_at"], "file: uploaded_at"),
            path=self._settings.uploads_dir / key / file_id / name,
        )

    def _turn(self, key: str, folder: Path, turn_id: str) -> TurnRecord:
        _check(bool(_TURN_ID.match(turn_id)), "turn id")
        data = _read_json(folder / "turns" / f"{turn_id}.json")
        _check(
            isinstance(data, dict)
            and _TURN_KEYS <= set(data) <= _TURN_KEYS | _TURN_OPTIONAL_KEYS,
            "turn: keys",
        )
        _check(data["version"] == FORMAT_VERSION and _is_int(data["version"]), "turn: version")
        _check(data["turn_id"] == turn_id, "turn: turn_id")
        _check(_is_int(data["seq"]) and data["seq"] == _seq_of(turn_id), "turn: seq")
        _check(isinstance(data["message"], str) and bool(data["message"]), "turn: message")
        _check(data["mode"] in _MODES, "turn: mode")
        target = data["target_file_id"]
        _check(target is None or (isinstance(target, str) and valid_id(target)), "turn: target")
        status = data["status"]
        _check(status in (RUNNING, FINISHED), "turn: status")
        turn = TurnRecord(
            turn_id=turn_id,
            seq=data["seq"],
            message=data["message"],
            mode=data["mode"],
            started_at=_aware(data["started_at"], "turn: started_at"),
            target_file_id=target,
            status=status,
        )
        confirmation = data.get("confirmation")
        file_used = _file_used(data.get("file_used"))
        if status == RUNNING:
            for name in ("outcome", "finished_at", "reply", "reply_source", "detail"):
                _check(data[name] is None, f"turn: {name} on a running turn")
            _check(data["delivered_files"] == [], "turn: files on a running turn")
            _check(confirmation is None, "turn: confirmation on a running turn")
            _check(file_used is None, "turn: file_used on a running turn")
            return turn
        turn.confirmation = _confirmation(confirmation)
        turn.file_used = file_used
        _check(data["outcome"] in _OUTCOMES, "turn: outcome")
        _check(data["reply_source"] in _REPLY_SOURCES, "turn: reply_source")
        _check(isinstance(data["reply"], str) and bool(data["reply"].strip()), "turn: reply")
        _check(data["detail"] is None or _valid_detail(data["detail"]), "turn: detail")
        turn.outcome = data["outcome"]
        turn.reply = data["reply"]
        turn.reply_source = data["reply_source"]
        turn.finished_at = _aware(data["finished_at"], "turn: finished_at")
        turn.detail = data["detail"]
        _check(isinstance(data["delivered_files"], list), "turn: delivered_files")
        delivered = tuple(
            self._delivered(key, turn_id, index, entry)
            for index, entry in enumerate(data["delivered_files"], start=1)
        )
        _check((turn.outcome == "delivered") == bool(delivered), "turn: outcome and files")
        turn.delivered_files = delivered
        return turn

    def _delivered(self, key: str, turn_id: str, index: int, entry: Any) -> DeliveredFile:
        _expect_keys(entry, "delivered file", {"file_id", "name", "size", "sha256", "delivered_at"})
        _check(entry["file_id"] == f"d_{turn_id}_{index}", "delivered file: file_id")
        _check(_plain_name(entry["name"]), "delivered file: name")
        _check(_is_int(entry["size"]) and entry["size"] > 0, "delivered file: size")
        sha256 = entry["sha256"]
        _check(isinstance(sha256, str) and bool(_SHA256.match(sha256)), "delivered file: sha256")
        return DeliveredFile(
            file_id=entry["file_id"],
            name=entry["name"],
            size=entry["size"],
            sha256=sha256,
            path=self._settings.outputs_dir / key / turn_id / entry["name"],
            turn_id=turn_id,
            delivered_at=_aware(entry["delivered_at"], "delivered file: delivered_at"),
        )

    def _turn_folders_in_use(self, key: str) -> list[int]:
        folder = self._settings.outputs_dir / key
        if not folder.is_dir():
            return []
        return [_seq_of(p.name) for p in folder.iterdir() if _TURN_ID.match(p.name)]

    def _repair(self, record: SessionRecord) -> None:
        """Finish every turn a restart interrupted, as failed, and write it back."""
        for turn in record.turns:
            if turn.finished:
                continue
            logger.warning(
                "Turn %s of session %s was interrupted by a restart; recording it as failed",
                turn.turn_id,
                record.key,
            )
            context = record.state.context
            context.record_user_message(turn.message)
            context.record_buddy_reply(FALLBACK_PIPELINE_ERROR)
            turn.outcome = "failed"
            turn.reply = FALLBACK_PIPELINE_ERROR
            turn.reply_source = "pipeline_fallback"
            turn.delivered_files = ()
            turn.detail = None
            turn.finished_at = self._clock()
            turn.status = FINISHED
            self.save_turn(record, turn)
        record.running_turn_id = None
        self.save_session(record)


# -- Helpers ---------------------------------------------------------------------------


def atomic_write_json(path: Path, data: Any) -> None:
    """Write ``data`` as JSON to ``path`` atomically (temp file, fsync, ``os.replace``)."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        with open(temporary, "x", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, sort_keys=True, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any:
    _check(path.is_file() and not path.is_symlink(), f"{path.name}: missing")
    _check(path.stat().st_size <= MAX_RECORD_BYTES, f"{path.name}: too large")
    return json.loads(path.read_text(encoding="utf-8"))


def _confirmation(data: Any) -> ConfirmationOutcome | None:
    """A stored plan decision, checked against the closed sets."""
    if data is None:
        return None
    _expect_keys(data, "turn: confirmation", {"decision", "decision_source"})
    _check(data["decision"] in DECISIONS, "turn: confirmation: decision")
    _check(data["decision_source"] in DECISION_SOURCES, "turn: confirmation: decision_source")
    return ConfirmationOutcome(decision=data["decision"], source=data["decision_source"])


def _file_used(data: Any) -> FileUsedRecord | None:
    """A stored ``file_used``, checked: ``None`` for a turn saved without one."""
    if data is None:
        return None
    _expect_keys(data, "turn: file_used", {"kind", "file_id", "start_over"})
    _check(data["kind"] in (USED_UPLOAD, USED_DELIVERED), "turn: file_used: kind")
    file_id = data["file_id"]
    _check(isinstance(file_id, str) and valid_id(file_id), "turn: file_used: file_id")
    _check(isinstance(data["start_over"], bool), "turn: file_used: start_over")
    return FileUsedRecord(kind=data["kind"], file_id=file_id, start_over=data["start_over"])


def _pending_request(data: Any, turn_ids: set[str], limit: int) -> PendingRequest | None:
    """A stored ``pending_request``, checked: ``None`` for none, and for a record without one."""
    if data is None:
        return None
    _expect_keys(data, "session: pending_request", {"message", "turn_id"})
    message, turn_id = data["message"], data["turn_id"]
    _check(
        isinstance(message, str) and 0 < len(message) <= limit,
        "session: pending_request: message",
    )
    _check(
        isinstance(turn_id, str) and turn_id in turn_ids, "session: pending_request: turn_id"
    )
    return PendingRequest(message=message, turn_id=turn_id)


def _check_files_used(turns: list[TurnRecord], files: dict[str, UploadRecord]) -> None:
    """Every ``file_used`` names a file that exists: the turn's upload, or an earlier delivery."""
    delivered_by = {d.file_id: t.seq for t in turns for d in t.delivered_files}
    for turn in turns:
        used = turn.file_used
        if used is None:
            continue
        if used.kind == USED_UPLOAD:
            _check(
                used.file_id in files and used.file_id == turn.target_file_id,
                "turn: file_used: upload",
            )
        else:
            _check(
                delivered_by.get(used.file_id, turn.seq) < turn.seq, "turn: file_used: delivered"
            )


def _valid_detail(detail: Any) -> bool:
    """Whether a stored ``detail`` has exactly the shape the API serves."""
    try:
        TurnDetail.model_validate(detail, strict=True)
    except ValidationError:
        return False
    return True


def _check(condition: bool, what: str) -> None:
    if not condition:
        raise CorruptRecord(what)


def _expect_keys(data: Any, what: str, keys: set[str]) -> None:
    _check(isinstance(data, dict) and set(data) == keys, f"{what}: keys")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _plain_name(name: Any) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name) <= 255
        and name not in (".", "..")
        and "/" not in name
        and "\\" not in name
        and "\0" not in name
    )


def _seq_of(turn_id: str) -> int:
    match = _TURN_ID.match(turn_id)
    _check(match is not None, "turn id")
    assert match is not None
    return int(match.group(1))


def _aware(value: Any, what: str) -> datetime:
    _check(isinstance(value, str), what)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise CorruptRecord(what) from None
    _check(parsed.tzinfo is not None and parsed.utcoffset() is not None, f"{what}: no time zone")
    return parsed


def _optional_str(value: Any) -> bool:
    return value is None or isinstance(value, str)


def _restore_context(data: Any, key: str, upload_paths: list[str]) -> Context:
    """``Context.from_dict``, on data checked first (review M2)."""
    _check(isinstance(data, dict), "context")
    _check(_is_int(data.get("version")), "context: version")
    _check(data.get("active_workspace") == key, "context: active_workspace")
    _check(data.get("current_session") == key, "context: current_session")
    for name in ("active_task", "active_file", "active_document", "active_sheet", "current_topic"):
        _check(_optional_str(data.get(name)), f"context: {name}")
    _check(data.get("mode") in _MODES, "context: mode")
    uploaded = data.get("uploaded_files")
    _check(
        isinstance(uploaded, list) and all(isinstance(p, str) for p in uploaded),
        "context: uploaded_files",
    )
    _check(uploaded == upload_paths, "context: uploaded_files disagree with the registry")
    analyzed = data.get("analyzed_files")
    _check(
        isinstance(analyzed, list) and all(p in upload_paths for p in analyzed),
        "context: analyzed_files",
    )
    _check(data.get("active_file") is None or data["active_file"] in upload_paths, "active_file")
    _check(isinstance(data.get("metadata"), dict), "context: metadata")
    _aware(data.get("created_at"), "context: created_at")
    _aware(data.get("last_updated_at"), "context: last_updated_at")
    history = data.get("conversation_history")
    _check(isinstance(history, list), "context: conversation_history")
    for turn in history:
        _check(isinstance(turn, dict) and set(turn) == {"role", "message", "timestamp"}, "history")
        _check(turn["role"] in ("user", "buddy"), "history: role")
        _check(isinstance(turn["message"], str), "history: message")
        _aware(turn["timestamp"], "history: timestamp")
    executions = data.get("execution_history")
    _check(isinstance(executions, list), "context: execution_history")
    for record in executions:
        _check(
            isinstance(record, dict) and set(record) == {"description", "timestamp"}, "executions"
        )
        _check(isinstance(record["description"], str), "executions: description")
        _aware(record["timestamp"], "executions: timestamp")
    return Context.from_dict(data)
