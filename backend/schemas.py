"""Schemas — the API's request and response models (its OpenAPI contract).

``backend/openapi.json`` is generated from these (``python -m
backend.export_openapi``) and checked by ``tests/backend/test_openapi_sync.py``.

A turn is a union discriminated on ``status``: only the ``finished``
variant has ``reply``, ``reply_source`` and ``outcome``, and there they are
required and non-null (design "Decisions (2026-09-30, part 2)", ask 7). The
other variant still carries those keys, as ``null``, so the JSON shape of
design Section 1.3.1 doesn't change. ``awaiting_confirmation`` and the
shape of ``pending_confirmation`` belong to PR-3; until then
``pending_confirmation`` is always ``null``.
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
    retention_hours: int | None = Field(
        description="Hours an idle session is kept. Always null until a retention sweep exists."
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


class _TurnCommon(BaseModel):
    turn_id: str
    seq: int
    message: str
    mode: Mode
    started_at: datetime
    delivered_files: list[DeliveredFileOut]
    pending_confirmation: None = None


class RunningTurnSummary(_TurnCommon):
    status: Literal["running"]
    outcome: None = None
    finished_at: None = None
    reply: None = None
    reply_source: None = None


class FinishedTurnSummary(_TurnCommon):
    status: Literal["finished"]
    outcome: Outcome
    finished_at: datetime
    reply: str
    reply_source: ReplySource


class RunningTurn(RunningTurnSummary):
    detail: TurnDetail | None = None


class FinishedTurn(FinishedTurnSummary):
    detail: TurnDetail | None = None


TurnSummary = Annotated[RunningTurnSummary | FinishedTurnSummary, Field(discriminator="status")]
Turn = Annotated[RunningTurn | FinishedTurn, Field(discriminator="status")]


class SessionState(BaseModel):
    session_id: str
    mode: Mode
    busy: bool
    active_file_id: str | None
    files: list[UploadedFile]
    turns: list[TurnSummary]
    pending_confirmation: None = None
    limits: Limits
    retention_hours: int | None
