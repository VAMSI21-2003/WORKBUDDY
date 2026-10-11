"""View models for the page's templates (frontend design Sections 2, 4 and 6).

Every value here is read from the response models the JSON routes return (the same turn objects),
or from the settings. Nothing here writes a sentence about work on the user's file: the reply is
copied as it is, the plan's text comes from the server, and the only text built here is a size
label ("16 KB") and a number of seconds.

**Rule 1, structurally (design 4.3, item 1).** ``should_show_downloads`` is the only function in
``backend/web`` that reads how a turn ended. ``TurnView``, the view the templates get, has no such
field, so a template can't branch on it, and the downloads in it come only from the files the turn
lists as delivered. A test fails if the word appears anywhere else under ``backend/web``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from backend.errors import ApiError, ErrorCode
from backend.schemas import FinishedTurnSummary, SessionState
from backend.settings import Settings

RUNNING = "running"
AWAITING = "awaiting_confirmation"
FINISHED = "finished"

# A turn under this age by the server's clock is polled every FAST_POLL_S, then every SLOW_POLL_S
# (design 1.6).
FAST_POLL_AGE_S = 20.0
FAST_POLL_S = 2
SLOW_POLL_S = 3


def should_show_downloads(turn: Any) -> bool:
    """Whether the page shows a download area for ``turn``: only for a delivered turn.

    The only place the page looks at how a turn ended, and only to decide layout. The downloads
    themselves are the turn's own ``delivered_files``, never anything read from its reply.
    """
    return bool(getattr(turn, "outcome", None) == "delivered")


# The fields a finished turn must carry: whatever its model requires (the reply, the reply source,
# how it ended, when). Read from the schema, so this list can't drift from the contract.
_REQUIRED_WHEN_FINISHED = tuple(
    name for name, field in FinishedTurnSummary.model_fields.items() if field.is_required()
)


def format_size(size: int) -> str:
    """A size as the page labels it: ``512 bytes``, ``16 KB``, ``1.5 MB``."""
    if size < 1024:
        return f"{size} bytes"
    if size < 1024 * 1024:
        return f"{round(size / 1024)} KB"
    megabytes = f"{size / (1024 * 1024):.1f}".removesuffix(".0")
    return f"{megabytes} MB"


def poll_delay_s(now: datetime, started_at: datetime) -> int:
    """Seconds before the next poll of a turn, by the server's clock (design 1.6)."""
    age = (now - started_at).total_seconds()
    return FAST_POLL_S if age < FAST_POLL_AGE_S else SLOW_POLL_S


def seconds_left(now: datetime, expires_at: datetime) -> int:
    """Whole seconds until ``expires_at``, by the server's clock, never below zero (design 1.7)."""
    return max(0, round((expires_at - now).total_seconds()))


@dataclass(frozen=True)
class DownloadView:
    """One delivered file: its name and size label, and the API's own link."""

    name: str
    size: str
    url: str


@dataclass(frozen=True)
class PlanView:
    """The plan card: the server's steps and note, the time left, and which buttons show."""

    confirmation_id: str
    steps: tuple[str, ...]
    note: str | None
    seconds_left: int
    can_approve: bool
    answer_may_be_lost: bool = False


@dataclass(frozen=True)
class TurnView:
    """One turn as a template sees it. There is no field for how the turn ended."""

    session_id: str
    turn_id: str
    seq: int
    version: int
    message: str
    status: str
    reply: str | None
    downloads: tuple[DownloadView, ...] | None
    plan: PlanView | None
    poll_delay_s: int

    @property
    def unfinished(self) -> bool:
        return self.status != FINISHED


@dataclass(frozen=True)
class FileView:
    file_id: str
    name: str
    size: str
    active: bool


@dataclass(frozen=True)
class NextFileView:
    """The file the next request will use (``SessionState.next_request_file``): a name and a kind.

    A plain statement of which file is on offer; the page says nothing about any result.
    """

    name: str
    delivered: bool


@dataclass(frozen=True)
class FilesView:
    """The Files panel: uploads, the files Buddy delivered, and whether upload is open."""

    session_id: str
    uploads: tuple[FileView, ...]
    delivered: tuple[DownloadView, ...]
    upload_open: bool
    accept: str
    limit_text: str
    next_file: NextFileView | None = None


@dataclass(frozen=True)
class ComposerView:
    session_id: str
    mode: str
    last_seq: int
    max_chars: int
    open: bool


@dataclass(frozen=True)
class ConversationView:
    session_id: str
    turns: tuple[TurnView, ...]
    composer: ComposerView
    files: FilesView
    awaiting_plan: bool


def plan_view(pending: Any, now: datetime, *, answer_may_be_lost: bool = False) -> PlanView:
    """The card for ``pending`` (a ``pending_confirmation``).

    ``can_approve`` is true only when the server sent a non-empty ``steps`` (design 1.7, M3): with
    none, the page can only offer to reject, so a user can never approve a plan they didn't see.
    """
    steps = tuple(step.prompt for step in (getattr(pending, "steps", None) or ()))
    return PlanView(
        confirmation_id=pending.confirmation_id,
        steps=steps,
        note=getattr(pending, "plan_note", None),
        seconds_left=seconds_left(now, pending.expires_at),
        can_approve=bool(steps),
        answer_may_be_lost=answer_may_be_lost,
    )


def download_views(turn: Any, session_id: str) -> tuple[DownloadView, ...] | None:
    """The turn's downloads, or ``None`` when the page shows no download area for it."""
    if not should_show_downloads(turn):
        return None
    return tuple(
        DownloadView(name=f.name, size=format_size(f.size), url=f.download_url)
        for f in turn.delivered_files
    )


def turn_view(
    session_id: str,
    turn: Any,
    version: int,
    now: datetime,
    *,
    answer_may_be_lost: bool = False,
) -> TurnView:
    """The page's view of ``turn``, a turn model as the JSON API returns it.

    Raises:
        ApiError: ``internal_error``, when a finished turn lacks a field its model requires (the
            reply, its source, how it ended): the page never guesses what a turn that says nothing
            did (design 12, item 7).
    """
    if turn.status == FINISHED:
        if any(getattr(turn, name, None) is None for name in _REQUIRED_WHEN_FINISHED):
            raise ApiError(ErrorCode.INTERNAL_ERROR)
        return TurnView(
            session_id=session_id,
            turn_id=turn.turn_id,
            seq=turn.seq,
            version=version,
            message=turn.message,
            status=FINISHED,
            reply=turn.reply,
            downloads=download_views(turn, session_id),
            plan=None,
            poll_delay_s=0,
        )
    pending = turn.pending_confirmation if turn.status == AWAITING else None
    return TurnView(
        session_id=session_id,
        turn_id=turn.turn_id,
        seq=turn.seq,
        version=version,
        message=turn.message,
        status=AWAITING if pending is not None else RUNNING,
        reply=None,
        downloads=None,
        plan=(
            plan_view(pending, now, answer_may_be_lost=answer_may_be_lost)
            if pending is not None
            else None
        ),
        poll_delay_s=poll_delay_s(now, turn.started_at),
    )


def files_view(
    state: SessionState,
    settings: Settings,
    *,
    upload_open: bool,
    exclude_turn_id: str | None = None,
) -> FilesView:
    """The Files panel for a session snapshot.

    The files Buddy delivered are those of the finished turns that show a download area, so this
    panel and the transcript can't disagree. ``exclude_turn_id`` leaves one turn's files out (the
    turn a response is rendering as running).
    """
    delivered: list[DownloadView] = []
    for turn in state.turns:
        if turn.turn_id == exclude_turn_id or turn.status != FINISHED:
            continue
        downloads = download_views(turn, state.session_id)
        if downloads:
            delivered.extend(downloads)
    return FilesView(
        session_id=state.session_id,
        uploads=tuple(
            FileView(
                file_id=f.file_id,
                name=f.name,
                size=format_size(f.size),
                active=f.file_id == state.active_file_id,
            )
            for f in state.files
        ),
        delivered=tuple(delivered),
        upload_open=upload_open,
        accept=",".join(state.limits.accepted_extensions),
        limit_text=limit_text(state, settings),
        next_file=(
            NextFileView(
                name=state.next_request_file.name,
                delivered=state.next_request_file.kind == "delivered",
            )
            if state.next_request_file is not None
            else None
        ),
    )


def limit_text(state: SessionState, settings: Settings) -> str:
    """What the server accepts, from its own limits: ``.xlsx up to 2 MB``."""
    kinds = ", ".join(state.limits.accepted_extensions)
    return f"{kinds} up to {format_size(state.limits.max_upload_bytes)}"


def conversation_view(
    state: SessionState,
    versions: dict[str, int],
    settings: Settings,
    now: datetime,
    *,
    only_after: int | None = None,
) -> ConversationView:
    """The conversation region for a session snapshot (``versions``: each turn's counter)."""
    all_turns = [
        turn_view(state.session_id, t, versions.get(t.turn_id, 0), now) for t in state.turns
    ]
    turns = tuple(t for t in all_turns if only_after is None or t.seq > only_after)
    busy = any(t.unfinished for t in all_turns)
    mode = state.turns[-1].mode if state.turns else state.mode
    return ConversationView(
        session_id=state.session_id,
        turns=turns,
        composer=ComposerView(
            session_id=state.session_id,
            mode=mode,
            last_seq=max((t.seq for t in all_turns), default=0),
            max_chars=state.limits.max_message_chars,
            open=not busy,
        ),
        files=files_view(state, settings, upload_open=not busy),
        awaiting_plan=any(t.plan is not None for t in all_turns),
    )
