"""Context — maintains Buddy's current working context."""

import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


def _default_clock() -> datetime:
    """Return the current time in UTC.

    The default time source for ``Context``. Exists as a plain, importable
    function rather than an inline lambda so tests can inject a
    deterministic replacement in its place.
    """
    return datetime.now(timezone.utc)


# The format ``Context.to_dict`` writes and ``Context.from_dict`` reads.
# Bump it, and keep reading the old one, whenever a key changes meaning.
SERIALIZATION_VERSION = 1


class ConversationRole(Enum):
    """Who produced a given turn in the conversation history."""

    USER = "user"
    BUDDY = "buddy"


@dataclass(frozen=True)
class ConversationTurn:
    """A single message recorded in the conversation history.

    Attributes:
        role: Who produced this message.
        message: The message text.
        timestamp: When the message was recorded.
    """

    role: ConversationRole
    message: str
    timestamp: datetime


@dataclass(frozen=True)
class ExecutionRecord:
    """A single entry in the execution history.

    Attributes:
        description: Plain-language description of what was done.
        timestamp: When the work was recorded.
    """

    description: str
    timestamp: datetime


@dataclass
class Context:
    """Buddy's current working context.

    Context is Buddy's short-lived awareness of *what is currently being
    worked on*: which workspace and session are active, which task and
    file the user is focused on, what's recently been said, and what's
    recently been done. It exists so Buddy doesn't have to keep asking
    "which file?" or "which task?" when the answer is already obvious from
    what just happened.

    Context is deliberately narrow:

    - It is **not** long-term memory. It holds recent, working awareness,
      not a durable history of everything that has ever happened in a
      workspace.
    - It is **not** ``BuddyState``. ``BuddyState`` remains the single
      object every stage of Buddy's cognitive cycle reads from and writes
      to; Context *enriches* ``BuddyState`` and is designed so a future
      revision of ``BuddyState`` can hold a reference to a ``Context``
      instance — it never replaces it. To keep that relationship
      one-directional, Context has no dependency on ``BuddyState``.
    - It does **not** understand language, classify intent, generate
      plans, execute work, call Skills, verify results, or speak to the
      user. It only records and returns facts about the current working
      environment; every judgment about what those facts *mean* belongs to
      Buddy's cognitive pipeline (see ``docs/WORKBUDDY_ARCHITECTURE.md``).

    The current single-item "active" pointers (``active_task``,
    ``active_file``, ``active_document``, ``active_sheet``) are
    deliberately simple today. Multiple uploaded files, multiple open
    tasks, multiple workspaces, project switching, conversation branching,
    and collaborative workspaces are all future extensions this shape is
    meant to accommodate — none of them are implemented here.

    Attributes:
        active_workspace: The workspace this context belongs to.
        current_session: The session this context belongs to.
        active_task: Identifier of the task currently being worked on.
        active_file: Reference to the file currently being worked on.
        active_document: Reference to the specific document currently open
            within the active file, when that distinction applies.
        active_sheet: Name of the sheet currently open within the active
            file, when the active file is a spreadsheet.
        uploaded_files: Files currently available in this working context.
        analyzed_files: Files that proactive analysis
            (``spreadsheet.analyze``) has already been run against and
            independently confirmed for, this session — so it isn't
            re-queued every turn the same file remains uploaded. See
            ``docs/design/proactive-analysis-design.md`` Section 1.2.
        current_topic: Plain-language label for what the conversation is
            currently about.
        mode: Buddy's conversational personality mode — ``"friend"`` or
            ``"focus"`` (see ``buddy.voice.PersonalityVoice``). A
            per-session setting: it stays whatever the user last set it
            to, rather than resetting each message.
        metadata: Auxiliary information attached to this context.
        created_at: When this context was created.
        last_updated_at: When this context was last modified.
        clock: Time source used to stamp updates. Injectable so tests can
            supply a deterministic clock instead of the wall clock.
    """

    active_workspace: str
    current_session: str
    active_task: str | None = None
    active_file: str | None = None
    active_document: str | None = None
    active_sheet: str | None = None
    uploaded_files: list[str] = field(default_factory=list)
    analyzed_files: frozenset[str] = field(default_factory=frozenset)
    current_topic: str | None = None
    mode: str = "friend"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(init=False, default_factory=_default_clock)
    last_updated_at: datetime = field(init=False, default_factory=_default_clock)
    clock: Callable[[], datetime] = field(default=_default_clock, repr=False, compare=False)
    _conversation_history: list[ConversationTurn] = field(
        default_factory=list, init=False, repr=False, compare=False
    )
    _execution_history: list[ExecutionRecord] = field(
        default_factory=list, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        """Stamp creation time using the injected clock."""
        now = self.clock()
        self.created_at = now
        self.last_updated_at = now

    # -- Switching active work ------------------------------------------------

    def switch_active_task(self, task_id: str | None) -> None:
        """Switch which task is currently active.

        Args:
            task_id: Identifier of the task to make active, or ``None`` to
                clear it.
        """
        logger.debug(
            "Switching active task: %r -> %r for session %r",
            self.active_task,
            task_id,
            self.current_session,
        )
        self.active_task = task_id
        self._touch()

    def switch_active_file(
        self,
        file_reference: str | None,
        *,
        document: str | None = None,
        sheet: str | None = None,
    ) -> None:
        """Switch which file (and optionally document/sheet) is active.

        Switching files resets the active document and sheet unless new
        values are explicitly provided, since a document or sheet pointer
        left over from the previous file is no longer meaningful.

        Args:
            file_reference: Reference to the file to make active, or
                ``None`` to clear it.
            document: The document within the file to make active, if
                applicable.
            sheet: The sheet within the file to make active, if
                applicable.
        """
        logger.debug(
            "Switching active file: %r -> %r for session %r",
            self.active_file,
            file_reference,
            self.current_session,
        )
        self.active_file = file_reference
        self.active_document = document
        self.active_sheet = sheet
        self._touch()

    def add_uploaded_file(self, file_reference: str) -> None:
        """Record a file as available in the current working context.

        Args:
            file_reference: Reference to the uploaded file.
        """
        if file_reference not in self.uploaded_files:
            self.uploaded_files.append(file_reference)
            self._touch()

    def mark_analyzed(self, file_reference: str) -> None:
        """Record a file as having been proactively analyzed.

        Only ever called once a Verifier has independently confirmed the
        analysis actually held up — never merely because a Skill
        self-reported success (see
        ``docs/design/proactive-analysis-design.md`` Section 1.2). Once
        marked, ``ProactiveAnalysisPlanner`` stops queuing a
        ``spreadsheet.analyze`` step for this file.

        Args:
            file_reference: Reference to the file to mark as analyzed.
        """
        if file_reference not in self.analyzed_files:
            self.analyzed_files = self.analyzed_files | {file_reference}
            self._touch()

    def set_current_topic(self, topic: str | None) -> None:
        """Set the current conversation topic.

        Args:
            topic: A plain-language label for the current topic, or
                ``None`` to clear it.
        """
        self.current_topic = topic
        self._touch()

    def set_mode(self, mode: str) -> None:
        """Set Buddy's conversational personality mode.

        A per-session setting: once set, ``mode`` stays in effect for the
        rest of the session until changed again — it is not reset between
        individual messages the way transient ``BuddyState`` fields are.

        Args:
            mode: The mode to switch to. Must be ``"friend"`` or
                ``"focus"``.

        Raises:
            ValueError: If ``mode`` is not one of the two valid values.
        """
        if mode not in ("friend", "focus"):
            raise ValueError(f"mode must be 'friend' or 'focus', got {mode!r}")
        logger.debug(
            "Switching mode: %r -> %r for session %r", self.mode, mode, self.current_session
        )
        self.mode = mode
        self._touch()

    # -- Metadata ---------------------------------------------------------------

    def set_metadata(self, key: str, value: Any) -> None:
        """Attach a piece of auxiliary information to the context.

        Args:
            key: The metadata key.
            value: The value to store.
        """
        self.metadata[key] = value
        self._touch()

    def get_metadata(self, key: str, default: Any = None) -> Any:
        """Retrieve a piece of auxiliary information from the context.

        Args:
            key: The metadata key.
            default: Value to return if ``key`` is not present.

        Returns:
            The stored value, or ``default`` if the key isn't present.
        """
        return self.metadata.get(key, default)

    # -- Conversation and execution history --------------------------------------

    def append_conversation_turn(self, role: ConversationRole, message: str) -> None:
        """Append a turn to the conversation history.

        Args:
            role: Who produced the message.
            message: The message text.
        """
        turn = ConversationTurn(role=role, message=message, timestamp=self.clock())
        self._conversation_history.append(turn)
        self._touch()

    def record_user_message(self, message: str) -> None:
        """Record a message received from the user.

        Args:
            message: The user's message text.
        """
        self.append_conversation_turn(ConversationRole.USER, message)

    def record_buddy_reply(self, message: str) -> None:
        """Record a reply sent by Buddy.

        Args:
            message: Buddy's reply text.
        """
        self.append_conversation_turn(ConversationRole.BUDDY, message)

    def record_execution(self, description: str) -> None:
        """Record an entry in the execution history.

        Args:
            description: Plain-language description of the work done.
        """
        self._execution_history.append(
            ExecutionRecord(description=description, timestamp=self.clock())
        )
        self._touch()

    def recent_conversation(self, limit: int = 10) -> list[ConversationTurn]:
        """Return the most recent conversation turns.

        Args:
            limit: Maximum number of turns to return.

        Returns:
            Up to ``limit`` of the most recent conversation turns, oldest
            first.
        """
        return self._conversation_history[-limit:]

    def buddy_reply_count(self) -> int:
        """Return how many replies Buddy has recorded in this conversation."""
        return sum(
            1 for turn in self._conversation_history if turn.role is ConversationRole.BUDDY
        )

    def recent_user_messages(self, limit: int = 5) -> list[str]:
        """Return the most recent messages sent by the user.

        Args:
            limit: Maximum number of messages to return.

        Returns:
            Up to ``limit`` of the most recent user messages, oldest first.
        """
        messages = [
            turn.message
            for turn in self._conversation_history
            if turn.role is ConversationRole.USER
        ]
        return messages[-limit:]

    def recent_buddy_replies(self, limit: int = 5) -> list[str]:
        """Return the most recent replies sent by Buddy.

        Args:
            limit: Maximum number of replies to return.

        Returns:
            Up to ``limit`` of the most recent Buddy replies, oldest first.
        """
        replies = [
            turn.message
            for turn in self._conversation_history
            if turn.role is ConversationRole.BUDDY
        ]
        return replies[-limit:]

    def recent_execution(self, limit: int = 10) -> list[ExecutionRecord]:
        """Return the most recent execution history entries.

        Args:
            limit: Maximum number of entries to return.

        Returns:
            Up to ``limit`` of the most recent execution records, oldest
            first.
        """
        return self._execution_history[-limit:]

    # -- Retrieval ----------------------------------------------------------------

    def active_work_item(self) -> str | None:
        """Return the single best answer to "what are we working on?".

        Falls back through the active pointers in order of specificity:
        the active task, then the active file, then the active document.
        This is a fixed lookup order, not a judgment about intent — it
        exists purely so Buddy can avoid re-asking a question it already
        has an answer to.

        Returns:
            The active task, file, or document reference, in that order
            of preference, or ``None`` if nothing is currently active.
        """
        return self.active_task or self.active_file or self.active_document

    def snapshot(self) -> dict[str, Any]:
        """Return a serializable snapshot of the current context.

        Returns:
            A plain dictionary representation of the context, suitable for
            logging or attaching to other state.
        """
        return {
            "active_workspace": self.active_workspace,
            "current_session": self.current_session,
            "active_task": self.active_task,
            "active_file": self.active_file,
            "active_document": self.active_document,
            "active_sheet": self.active_sheet,
            "uploaded_files": list(self.uploaded_files),
            "current_topic": self.current_topic,
            "mode": self.mode,
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
            "last_updated_at": self.last_updated_at,
        }

    # -- Persistence ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        """Return everything needed to restore this context, JSON-ready.

        Unlike ``snapshot()``, this includes ``analyzed_files`` and both
        histories, and writes timestamps as ISO 8601 strings. Dropping
        ``analyzed_files`` would make a restored session re-queue proactive
        analysis for a file already analyzed. ``metadata`` is copied as is,
        so it is JSON-ready only if its values are; nothing in the
        pipeline writes to it today. The clock is not stored.

        Returns:
            A plain dictionary that ``Context.from_dict`` turns back into
            an equal context.
        """
        return {
            "version": SERIALIZATION_VERSION,
            "active_workspace": self.active_workspace,
            "current_session": self.current_session,
            "active_task": self.active_task,
            "active_file": self.active_file,
            "active_document": self.active_document,
            "active_sheet": self.active_sheet,
            "uploaded_files": list(self.uploaded_files),
            "analyzed_files": sorted(self.analyzed_files),
            "current_topic": self.current_topic,
            "mode": self.mode,
            "metadata": copy.deepcopy(self.metadata),
            "created_at": self.created_at.isoformat(),
            "last_updated_at": self.last_updated_at.isoformat(),
            "conversation_history": [
                {
                    "role": turn.role.value,
                    "message": turn.message,
                    "timestamp": turn.timestamp.isoformat(),
                }
                for turn in self._conversation_history
            ],
            "execution_history": [
                {"description": record.description, "timestamp": record.timestamp.isoformat()}
                for record in self._execution_history
            ],
        }

    @classmethod
    def from_dict(
        cls, data: Mapping[str, Any], *, clock: Callable[[], datetime] = _default_clock
    ) -> "Context":
        """Restore a context written by ``to_dict``.

        Rebuilt through the same public methods the pipeline uses
        (``mark_analyzed``, ``append_conversation_turn``, and so on), not
        by writing private fields. While it replays, the context's clock
        returns each entry's original timestamp, so the histories keep
        their times; ``created_at`` and ``last_updated_at`` are then set to
        the stored values, and ``clock`` takes over.

        Args:
            data: A dictionary from ``to_dict``, possibly after a JSON
                round trip.
            clock: The time source for the restored context from now on.

        Returns:
            The restored context.

        Raises:
            ValueError: If ``data`` is from an unknown format version, or
                holds an invalid mode, role or timestamp.
            KeyError: If a required key is missing.
        """
        version = data.get("version")
        if version != SERIALIZATION_VERSION:
            raise ValueError(f"unsupported Context format version: {version!r}")

        replay_at = [datetime.fromisoformat(data["created_at"])]
        context = cls(
            active_workspace=data["active_workspace"],
            current_session=data["current_session"],
            clock=lambda: replay_at[0],
        )
        context.set_mode(data["mode"])
        for file_reference in data["uploaded_files"]:
            context.add_uploaded_file(file_reference)
        for file_reference in data["analyzed_files"]:
            context.mark_analyzed(file_reference)
        context.switch_active_file(
            data["active_file"], document=data["active_document"], sheet=data["active_sheet"]
        )
        context.switch_active_task(data["active_task"])
        context.set_current_topic(data["current_topic"])
        for key, value in data["metadata"].items():
            context.set_metadata(key, copy.deepcopy(value))
        for turn in data["conversation_history"]:
            replay_at[0] = datetime.fromisoformat(turn["timestamp"])
            context.append_conversation_turn(ConversationRole(turn["role"]), turn["message"])
        for record in data["execution_history"]:
            replay_at[0] = datetime.fromisoformat(record["timestamp"])
            context.record_execution(record["description"])

        context.created_at = datetime.fromisoformat(data["created_at"])
        context.last_updated_at = datetime.fromisoformat(data["last_updated_at"])
        context.clock = clock
        return context

    # -- Clearing -------------------------------------------------------------------

    def clear_completed_work(self) -> None:
        """Clear the active task once it has been completed.

        Only the active task is cleared. The active file, document, and
        sheet are preserved, since finishing one task on a file does not
        mean the user is done working with that file — clearing them too
        would reintroduce the "which file?" question Context exists to
        avoid.
        """
        logger.debug(
            "Clearing completed work (active task %r) for session %r",
            self.active_task,
            self.current_session,
        )
        self.active_task = None
        self._touch()

    # -- Internal ---------------------------------------------------------------------

    def _touch(self) -> None:
        """Update ``last_updated_at`` to reflect the most recent change."""
        self.last_updated_at = self.clock()
