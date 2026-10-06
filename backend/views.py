"""Views — turn live records into response models.

Every function here reads a record that the caller holds the session lock
for. Nothing here composes prose: ``reply`` is copied from the turn record
as it is, and every other field is structured data.
"""

from __future__ import annotations

from typing import Any

from backend.confirmation import PendingPlan
from backend.schemas import (
    AwaitingConfirmationTurn,
    AwaitingConfirmationTurnSummary,
    DeliveredFileOut,
    FileList,
    FinishedTurn,
    FinishedTurnSummary,
    Limits,
    PendingConfirmation,
    PlanStep,
    RunningTurn,
    RunningTurnSummary,
    SessionCreated,
    SessionFile,
    SessionState,
    TurnDetail,
    UploadedFile,
)
from backend.sessions import (
    AWAITING_CONFIRMATION,
    DeliveredFile,
    SessionRecord,
    TurnRecord,
    UploadRecord,
)
from backend.settings import Settings


def limits(settings: Settings) -> Limits:
    return Limits(
        max_upload_bytes=settings.max_upload_bytes,
        accepted_extensions=list(settings.accepted_extensions),
        max_message_chars=settings.max_message_chars,
    )


def session_created(session_id: str, record: SessionRecord, settings: Settings) -> SessionCreated:
    return SessionCreated(
        session_id=session_id,
        mode=record.mode,  # type: ignore[arg-type]
        created_at=record.created_at,
        limits=limits(settings),
        retention_hours=settings.retention_hours,
    )


def session_state(session_id: str, record: SessionRecord, settings: Settings) -> SessionState:
    running = record.turn(record.running_turn_id) if record.running_turn_id else None
    return SessionState(
        session_id=session_id,
        mode=record.mode,  # type: ignore[arg-type]
        busy=record.busy,
        active_file_id=record.active_file_id,
        files=[uploaded_file(f) for f in record.files.values()],
        turns=[turn_summary(session_id, t) for t in record.turns],
        pending_confirmation=_waiting_plan(running) if running is not None else None,
        limits=limits(settings),
        retention_hours=settings.retention_hours,
    )


def pending_confirmation(plan: PendingPlan) -> PendingConfirmation:
    """The public shape of a waiting plan: its steps and note, nothing step-1-only."""
    return PendingConfirmation(
        confirmation_id=plan.confirmation_id,
        steps=[PlanStep(capability=s.capability, prompt=s.prompt) for s in plan.steps],
        plan_note=plan.plan_note,
        expires_at=plan.expires_at,
    )


def _waiting_plan(turn: TurnRecord) -> PendingConfirmation | None:
    """The turn's plan, if it's waiting for an answer right now."""
    if turn.finished or turn.status != AWAITING_CONFIRMATION:
        return None
    if turn.pending_confirmation is None:
        return None
    return pending_confirmation(turn.pending_confirmation)


def uploaded_file(upload: UploadRecord) -> UploadedFile:
    return UploadedFile(
        file_id=upload.file_id,
        name=upload.name,
        size=upload.size,
        sha256=upload.sha256,
        uploaded_at=upload.uploaded_at,
    )


def download_url(session_id: str, file_id: str) -> str:
    return f"/api/v1/sessions/{session_id}/files/{file_id}/download"


def delivered_file(session_id: str, delivered: DeliveredFile) -> DeliveredFileOut:
    return DeliveredFileOut(
        file_id=delivered.file_id,
        name=delivered.name,
        size=delivered.size,
        sha256=delivered.sha256,
        download_url=download_url(session_id, delivered.file_id),
    )


def file_list(record: SessionRecord) -> FileList:
    files = [
        SessionFile(
            file_id=f.file_id,
            name=f.name,
            size=f.size,
            kind="upload",
            downloadable=False,
            uploaded_at=f.uploaded_at,
        )
        for f in record.files.values()
    ]
    files.extend(
        SessionFile(
            file_id=d.file_id,
            name=d.name,
            size=d.size,
            kind="delivered",
            downloadable=True,
            turn_id=d.turn_id,
            delivered_at=d.delivered_at,
        )
        for d in record.delivered()
    )
    return FileList(files=files)


def _turn_fields(session_id: str, turn: TurnRecord) -> dict[str, object]:
    common: dict[str, object] = {
        "turn_id": turn.turn_id,
        "seq": turn.seq,
        "message": turn.message,
        "mode": turn.mode,
        "started_at": turn.started_at,
        "pending_confirmation": None,
    }
    if not turn.finished:
        plan = _waiting_plan(turn)
        if plan is not None:
            return {
                **common,
                "status": AWAITING_CONFIRMATION,
                "pending_confirmation": plan,
                "delivered_files": [],
            }
        return {**common, "status": "running", "delivered_files": []}
    return {
        **common,
        "status": "finished",
        "outcome": turn.outcome,
        "finished_at": turn.finished_at,
        "reply": turn.reply,
        "reply_source": turn.reply_source,
        "delivered_files": [delivered_file(session_id, d) for d in turn.delivered_files],
    }


def turn_as_started(turn: TurnRecord) -> RunningTurn:
    """The ``202`` body of ``POST .../turns``: the turn, ``running``, whatever it is now."""
    return RunningTurn(
        turn_id=turn.turn_id,
        seq=turn.seq,
        message=turn.message,
        mode=turn.mode,  # type: ignore[arg-type]
        started_at=turn.started_at,
        delivered_files=[],
        status="running",
        detail=None,
    )


def turn_summary(
    session_id: str, turn: TurnRecord
) -> RunningTurnSummary | AwaitingConfirmationTurnSummary | FinishedTurnSummary:
    fields = _turn_fields(session_id, turn)
    if turn.finished:
        return FinishedTurnSummary.model_validate(fields)
    if fields["status"] == AWAITING_CONFIRMATION:
        return AwaitingConfirmationTurnSummary.model_validate(fields)
    return RunningTurnSummary.model_validate(fields)


def turn_out(
    session_id: str, turn: TurnRecord
) -> RunningTurn | AwaitingConfirmationTurn | FinishedTurn:
    fields = _turn_fields(session_id, turn)
    if turn.finished:
        return FinishedTurn.model_validate({**fields, "detail": _detail(turn)})
    if fields["status"] == AWAITING_CONFIRMATION:
        return AwaitingConfirmationTurn.model_validate({**fields, "detail": None})
    return RunningTurn.model_validate({**fields, "detail": None})


def _detail(turn: TurnRecord) -> TurnDetail | None:
    """The finished turn's ``detail``, with how its plan was decided (from the record)."""
    if turn.detail is None:
        return None
    confirmation: dict[str, Any] | None = None
    if turn.confirmation is not None:
        confirmation = {
            "decision": turn.confirmation.decision,
            "decision_source": turn.confirmation.source,
        }
    return TurnDetail.model_validate({**turn.detail, "confirmation": confirmation})
