"""Views — turn live records into response models.

Every function here reads a record that the caller holds the session lock
for. Nothing here composes prose: ``reply`` is copied from the turn record
as it is, and every other field is structured data.
"""

from __future__ import annotations

from backend.schemas import (
    DeliveredFileOut,
    FileList,
    FinishedTurn,
    FinishedTurnSummary,
    Limits,
    RunningTurn,
    RunningTurnSummary,
    SessionCreated,
    SessionFile,
    SessionState,
    TurnDetail,
    UploadedFile,
)
from backend.sessions import DeliveredFile, SessionRecord, TurnRecord, UploadRecord
from backend.settings import Settings

# Until the retention sweep exists (PR-4), nothing is deleted, so no
# retention period may be advertised (design "Decisions (2026-09-30, part
# 2)", ask 2).
RETENTION_HOURS: int | None = None


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
        retention_hours=RETENTION_HOURS,
    )


def session_state(session_id: str, record: SessionRecord, settings: Settings) -> SessionState:
    return SessionState(
        session_id=session_id,
        mode=record.mode,  # type: ignore[arg-type]
        busy=record.busy,
        active_file_id=record.active_file_id,
        files=[uploaded_file(f) for f in record.files.values()],
        turns=[turn_summary(session_id, t) for t in record.turns],
        pending_confirmation=None,
        limits=limits(settings),
        retention_hours=RETENTION_HOURS,
    )


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


def turn_summary(session_id: str, turn: TurnRecord) -> RunningTurnSummary | FinishedTurnSummary:
    fields = _turn_fields(session_id, turn)
    if turn.finished:
        return FinishedTurnSummary.model_validate(fields)
    return RunningTurnSummary.model_validate(fields)


def turn_out(session_id: str, turn: TurnRecord) -> RunningTurn | FinishedTurn:
    fields = _turn_fields(session_id, turn)
    detail = TurnDetail.model_validate(turn.detail) if turn.detail is not None else None
    if turn.finished:
        return FinishedTurn.model_validate({**fields, "detail": detail})
    return RunningTurn.model_validate({**fields, "detail": None})
