"""Sessions — the live, in-memory record of each session (design Section 3.2).

A ``SessionRecord`` holds the session's committed ``BuddyState`` (whose
``Context`` carries what persists between turns), its uploads, its turns,
and a lock. Every read or change of a record happens under that lock.

**Ids.** The server generates every id. A session id is 128 random bits
(``secrets.token_urlsafe``) and is the session's only credential, so it is
never written to disk, never put in a path, never logged and never given to
the pipeline. Everything server-side uses the session's *key* instead: the
first 128 bits of the id's SHA-256. The key names the session's folders and
is the pipeline's session and workspace id, so it is what ends up in output
file names (``{stem}_deduplicated_{key}-task-1.xlsx``), in the paths the
model sees, and in the logs. Knowing a key doesn't give access: requests
are looked up by id, and the id can't be recovered from the key.
"""

from __future__ import annotations

import hashlib
import logging
import re
import secrets
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.settings import Settings
from buddy.state import BuddyState

logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

RUNNING = "running"
FINISHED = "finished"


def utc_now() -> datetime:
    return datetime.now(UTC)


def valid_id(value: str) -> bool:
    """Whether ``value`` has the shape of a server-generated id."""
    return bool(ID_PATTERN.fullmatch(value))


def new_session_id() -> str:
    return "s_" + secrets.token_urlsafe(16)


def new_file_id() -> str:
    return "f_" + secrets.token_urlsafe(12)


def session_key(session_id: str) -> str:
    """The server-side name of a session: 32 hex digits of the id's SHA-256."""
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:32]


@dataclass
class UploadRecord:
    """One upload. Written once, read-only afterwards, never modified."""

    file_id: str
    name: str
    size: int
    sha256: str
    uploaded_at: datetime
    path: Path


@dataclass(frozen=True)
class DeliveredFile:
    """A file a finished turn handed back (``backend.delivery.delivered_files``)."""

    file_id: str
    name: str
    size: int
    sha256: str
    path: Path
    turn_id: str
    delivered_at: datetime


@dataclass
class TurnRecord:
    """One turn. Finished exactly once; immutable afterwards.

    ``reply``, ``reply_source``, ``outcome``, ``delivered_files``, ``detail``
    and ``finished_at`` are written together in one step under the session
    lock, when ``status`` becomes ``finished`` (design Section 5.1,
    invariant 7).
    """

    turn_id: str
    seq: int
    message: str
    mode: str
    started_at: datetime
    target_file_id: str | None = None
    status: str = RUNNING
    outcome: str | None = None
    finished_at: datetime | None = None
    reply: str | None = None
    reply_source: str | None = None
    delivered_files: tuple[DeliveredFile, ...] = ()
    detail: dict[str, Any] | None = None

    @property
    def finished(self) -> bool:
        return self.status == FINISHED


@dataclass
class SessionRecord:
    """A session's live state. Read and change it only under ``lock``."""

    key: str
    created_at: datetime
    state: BuddyState
    files: dict[str, UploadRecord] = field(default_factory=dict)
    turns: list[TurnRecord] = field(default_factory=list)
    active_file_id: str | None = None
    next_seq: int = 1
    running_turn_id: str | None = None
    lock: threading.RLock = field(default_factory=threading.RLock, repr=False, compare=False)

    @property
    def busy(self) -> bool:
        return self.running_turn_id is not None

    @property
    def mode(self) -> str:
        return self.state.context.mode

    def turn(self, turn_id: str) -> TurnRecord | None:
        return next((t for t in self.turns if t.turn_id == turn_id), None)

    def delivered(self) -> list[DeliveredFile]:
        """Every file handed back by a finished, delivered turn, oldest first."""
        return [
            f
            for t in self.turns
            if t.finished and t.outcome == "delivered"
            for f in t.delivered_files
        ]

    def upload_dir(self, settings: Settings, file_id: str) -> Path:
        return settings.uploads_dir / self.key / file_id

    def turn_dir(self, settings: Settings, turn_id: str) -> Path:
        return settings.outputs_dir / self.key / turn_id

    def meta_dir(self, settings: Settings) -> Path:
        return settings.sessions_dir / self.key


class SessionRegistry:
    """Every live session, by key. Sessions missing from memory are restored from disk."""

    def __init__(self, settings: Settings, store: Any = None) -> None:
        self._settings = settings
        self._store = store
        self._sessions: dict[str, SessionRecord] = {}
        self._lock = threading.Lock()

    def create(self, mode: str) -> tuple[str, SessionRecord]:
        """A new, empty session. Returns ``(session_id, record)``."""
        session_id = new_session_id()
        key = session_key(session_id)
        state = BuddyState.new(session_id=key, workspace_id=key)
        state.context.set_mode(mode)
        record = SessionRecord(key=key, created_at=state.context.created_at, state=state)
        with self._lock:
            if key in self._sessions:  # 128 random bits: never in practice
                raise RuntimeError("session key collision")
            self._sessions[key] = record
        if self._store is not None:
            try:
                self._store.create_session(record)
            except BaseException:
                with self._lock:
                    self._sessions.pop(key, None)
                raise
        logger.info("Created session %s", key)
        return session_id, record

    def saved(self, record: SessionRecord, turn: TurnRecord | None = None) -> None:
        """Persist ``record`` (and ``turn``, first) after a change. The caller holds its lock."""
        if self._store is not None:
            if turn is not None:
                self._store.save_turn(record, turn)
            self._store.save_session(record)

    def get(self, session_id: str) -> SessionRecord | None:
        """The session for ``session_id``, or ``None`` if there isn't one.

        An id that isn't shaped like one the server generates is never
        looked up. A session from an earlier run of the server is restored
        from disk on first access.
        """
        if not valid_id(session_id):
            return None
        key = session_key(session_id)
        with self._lock:
            record = self._sessions.get(key)
            if record is None and self._store is not None:
                record = self._store.load_session(key)
                if record is not None:
                    self._sessions[key] = record
        return record
