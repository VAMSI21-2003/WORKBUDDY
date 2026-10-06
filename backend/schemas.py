"""Schemas — the API's request and response models (its OpenAPI contract).

``backend/openapi.json`` is generated from these (``python -m
backend.export_openapi``) and checked by ``tests/backend/test_openapi_sync.py``.

A turn is a union discriminated on ``status``: only the ``finished``
variant has ``reply``, ``reply_source`` and ``outcome``, and there they are
required and non-null (design "Decisions (2026-09-30, part 2)", ask 7). The
other variants still carry those keys, as ``null``, so the JSON shape of
design Section 1.3.1 doesn't change. Only the ``awaiting_confirmation``
variant has a ``pending_confirmation``, and there it is required: the
plan's ``steps`` (at least one) and ``plan_note``, with no step-1-only
fields ("Decisions (2026-10-01)", items 4 and 5).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from backend.errors import ErrorCode

Mode = Literal["friend", "focus"]
Outcome = Literal["delivered", "not_delivered", "failed", "integrity_failure"]
ReplySource = Literal["pipeline", "pipeline_fallback"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# -- Errors -----------------------------------------------------------------------


class ErrorDetail(BaseModel):
    code: ErrorCode
    message: str


class ErrorBody(BaseModel):
    """The body of every non-2xx response."""

    error: ErrorDetail


# -- Health -----------------------------------------------------------------------


class Health(BaseModel):
    ok: bool
    llm_configured: bool


# -- Sessions ---------------------------------------------------------------------


class CreateSessionRequest(_Strict):
    mode: Mode = "friend"


class Limits(BaseModel):
    """The limits the server enforces, read from the same settings it enforces them with."""

    max_upload_bytes: int
    accepted_extensions: list[str]
    max_message_chars: int


class SessionCreated(BaseModel):
    session_id: str
    mode: Mode
    created_at: datetime
    limits: Limits
    retention_hours: int | float | None = Field(
        description=(
            "Hours a session may sit idle before the retention sweep deletes it and its files: "
            "the sweep's own setting. Null only if no sweep runs."
        )
    )


# -- Files ------------------------------------------------------------------------


class UploadedFile(BaseModel):
    file_id: str
    name: str
    size: int
    sha256: str
    kind: Literal["upload"] = "upload"
    uploaded_at: datetime


class DeliveredFileOut(BaseModel):
    file_id: str
    name: str
    size: int
    sha256: str
    download_url: str


class SessionFile(BaseModel):
    file_id: str
    name: str
    size: int
    kind: Literal["upload", "delivered"]
    downloadable: bool
    uploaded_at: datetime | None = None
    turn_id: str | None = None
    delivered_at: datetime | None = None


class FileList(BaseModel):
    files: list[SessionFile]


# -- Turns ------------------------------------------------------------------------


class CreateTurnRequest(_Strict):
    message: str
    mode: Mode | None = None
    file_id: str | None = None


class ConfirmationRequest(_Strict):
    """The user's answer to the plan waiting on a turn."""

    confirmation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    decision: Literal["approve", "reject"]


class PlanStep(BaseModel):
    """One step of the plan: its capability, and what it proposes to do (no result)."""

    capability: str
    prompt: str


class PendingConfirmation(BaseModel):
    """The plan waiting for the user's answer: every file-changing step, in order.

    Approving it approves every step; rejecting it, or no answer by
    ``expires_at``, means none of them runs.
    """

    confirmation_id: str
    steps: list[PlanStep] = Field(min_length=1)
    plan_note: str | None = Field(
        description="Set, to a fixed line, when the plan has two or more steps; else null."
    )
    expires_at: datetime


class ConfirmationDetail(BaseModel):
    """How the turn's plan was decided. Only the user ever approves."""

    decision: Literal["approve", "reject"]
    decision_source: Literal["user", "timeout", "shutdown", "turn_finished"]


class StepDetail(BaseModel):
    capability: str
    permission_tier: str
    status: str
    verified: bool
    recheck: bool
    input_name: str | None
    output_names: list[str]


class TurnDetail(BaseModel):
    """The structured data behind a turn. No Skill prose, no exception text, no paths."""

    intent: str | None
    clarification_required: bool | None
    steps: list[StepDetail]
    stopped_at: str | None
    confirmation: ConfirmationDetail | None = None


class _TurnCommon(BaseModel):
    turn_id: str
    seq: int
    message: str
    mode: Mode
    started_at: datetime
    delivered_files: list[DeliveredFileOut]


class RunningTurnSummary(_TurnCommon):
    status: Literal["running"]
    pending_confirmation: None = None
    outcome: None = None
    finished_at: None = None
    reply: None = None
    reply_source: None = None


class AwaitingConfirmationTurnSummary(_TurnCommon):
    status: Literal["awaiting_confirmation"]
    pending_confirmation: PendingConfirmation
    outcome: None = None
    finished_at: None = None
    reply: None = None
    reply_source: None = None


class FinishedTurnSummary(_TurnCommon):
    status: Literal["finished"]
    pending_confirmation: None = None
    outcome: Outcome
    finished_at: datetime
    reply: str
    reply_source: ReplySource


class RunningTurn(RunningTurnSummary):
    detail: TurnDetail | None = None


class AwaitingConfirmationTurn(AwaitingConfirmationTurnSummary):
    detail: TurnDetail | None = None


class FinishedTurn(FinishedTurnSummary):
    detail: TurnDetail | None = None


TurnSummary = Annotated[
    RunningTurnSummary | AwaitingConfirmationTurnSummary | FinishedTurnSummary,
    Field(discriminator="status"),
]
Turn = Annotated[
    RunningTurn | AwaitingConfirmationTurn | FinishedTurn, Field(discriminator="status")
]


class SessionState(BaseModel):
    session_id: str
    mode: Mode
    busy: bool
    active_file_id: str | None
    files: list[UploadedFile]
    turns: list[TurnSummary]
    pending_confirmation: PendingConfirmation | None = Field(
        description="The plan waiting on the session's unfinished turn, if one is waiting."
    )
    limits: Limits
    retention_hours: int | float | None
