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
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from backend.confirmation import ConfirmationOutcome, PendingPlan
from backend.settings import Settings
from buddy.state import BuddyState

logger = logging.getLogger(__name__)

ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
# How often a session's use is written to disk (see ``SessionRegistry._touch``).
PERSIST_TOUCH_S = 60.0

RUNNING = "running"
AWAITING_CONFIRMATION = "awaiting_confirmation"
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

    While the turn's plan waits for the user, ``status`` is
    ``awaiting_confirmation`` and ``pending_confirmation`` holds the plan;
    both are live state only, never written to disk (a restart ends the
    turn as failed, design Section 7.5 run 8). ``confirmation`` records how
    the plan was decided, and is written with the finished turn.
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
    pending_confirmation: PendingPlan | None = None
    confirmation: ConfirmationOutcome | None = None

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
    # Epoch seconds of the last request that used the session, and the last
    # time that was written to disk (retention: a session idle for longer
    # than the TTL is swept).
    last_active: float = field(default_factory=time.time)
    last_persisted: float = 0.0

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

    def __init__(
        self,
        settings: Settings,
        store: Any = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._store = store
        self._clock = clock
        self._sessions: dict[str, SessionRecord] = {}
        # Keys the retention sweep is deleting: not served, not restored.
        self._deleting: set[str] = set()
        self._lock = threading.Lock()

    def create(self, mode: str) -> tuple[str, SessionRecord]:
        """A new, empty session. Returns ``(session_id, record)``."""
        session_id = new_session_id()
        key = session_key(session_id)
        state = BuddyState.new(session_id=key, workspace_id=key)
        state.context.set_mode(mode)
        now = self._clock()
        record = SessionRecord(
            key=key,
            created_at=state.context.created_at,
            state=state,
            last_active=now,
            last_persisted=now,
        )
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

    def save_turn(self, record: SessionRecord, turn: TurnRecord) -> None:
        """Persist one turn record. The caller holds the session's lock."""
        if self._store is not None:
            self._store.save_turn(record, turn)

    def save_session(self, record: SessionRecord) -> None:
        """Persist the session record. The caller holds its lock."""
        if self._store is not None:
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
            if key in self._deleting:
                return None
            record = self._sessions.get(key)
            if record is None and self._store is not None:
                record = self._store.load_session(key)
                if record is not None:
                    self._sessions[key] = record
            if record is not None:
                self._touch(record)
        return record

    def _touch(self, record: SessionRecord) -> None:
        """Note that ``record`` was just used; the caller holds the registry lock.

        The use is also written to disk (the session file's modification
        time), at most once a minute, so a restart doesn't forget it and
        sweep the session early.
        """
        now = self._clock()
        record.last_active = now
        if self._store is not None and now - record.last_persisted >= PERSIST_TOUCH_S:
            record.last_persisted = now
            self._store.touch(record)

    # -- Retention (``backend.retention``) ----------------------------------------------

    def live_keys(self) -> list[str]:
        """The keys of the sessions held in memory."""
        with self._lock:
            return list(self._sessions)

    def claim_idle(self, key: str, cutoff: float) -> bool:
        """Take a live session out of service for deletion, if it's idle and has no running turn.

        Idle means no request has used it since ``cutoff`` (epoch seconds).
        A session with an unfinished turn (running, or waiting for an answer)
        is never claimed, and neither is one in use right now.
        """
        with self._lock:
            record = self._sessions.get(key)
            if record is None or key in self._deleting:
                return False
            if not record.lock.acquire(blocking=False):
                return False
            try:
                if record.busy or record.last_active > cutoff:
                    return False
                del self._sessions[key]
                self._deleting.add(key)
                return True
            finally:
                record.lock.release()

    def claim_unloaded(self, key: str) -> bool:
        """Take a session that exists only on disk out of service for deletion."""
        with self._lock:
            if key in self._sessions or key in self._deleting:
                return False
            self._deleting.add(key)
            return True

    def release(self, key: str) -> None:
        """The deletion of ``key`` is over (done, or given up)."""
        with self._lock:
            self._deleting.discard(key)
