"""Routes — the HTTP endpoints (design Section 1).

Every route declares its error responses with the shared ``ErrorBody``
model, so ``openapi.json`` describes the real error bodies. Route functions
are plain ``def``: FastAPI runs them on its thread pool, so taking a
session's (threading) lock never blocks the event loop.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from backend import views
from backend.delivery import check_turn_file
from backend.errors import ApiError, ErrorCode
from backend.schemas import (
    CreateSessionRequest,
    CreateTurnRequest,
    ErrorBody,
    FileList,
    FinishedTurn,
    Health,
    RunningTurn,
    SessionCreated,
    SessionState,
    Turn,
    UploadedFile,
)
from backend.sessions import (
    SessionRecord,
    SessionRegistry,
    UploadRecord,
    new_file_id,
    utc_now,
    valid_id,
)
from backend.settings import Settings
from backend.turns import TurnRunner
from backend.uploads import check_extension, discard_folder, sanitize_name, store_upload

logger = logging.getLogger(__name__)

health_router = APIRouter()
router = APIRouter(prefix="/api/v1")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_DESCRIPTIONS = {
    404: "Not found",
    409: "Conflict",
    410: "Gone",
    413: "Too large",
    415: "Unsupported file type",
    422: "Invalid request or file",
    429: "Too many running turns",
}


def errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` entry declaring ``ErrorBody`` for each status."""
    declared: dict[int | str, dict[str, Any]] = {}
    for status in statuses:
        entry: dict[str, Any] = {"model": ErrorBody, "description": _DESCRIPTIONS[status]}
        if status == 429:
            entry["headers"] = {
                "Retry-After": {
                    "description": "Seconds to wait before retrying (a hint, not a promise).",
                    "schema": {"type": "integer", "minimum": 1},
                }
            }
        declared[status] = entry
    return declared


def settings_of(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def registry_of(request: Request) -> SessionRegistry:
    return request.app.state.registry  # type: ignore[no-any-return]


def runner_of(request: Request) -> TurnRunner:
    return request.app.state.runner  # type: ignore[no-any-return]


def session_of(request: Request, session_id: str) -> SessionRecord:
    record = registry_of(request).get(session_id)
    if record is None:
        raise ApiError(ErrorCode.SESSION_NOT_FOUND)
    return record


@health_router.get("/healthz", response_model=Health, tags=["health"])
def healthz(request: Request) -> Health:
    """Liveness, and whether an LLM provider is configured. Booleans only."""
    return Health(ok=True, llm_configured=bool(request.app.state.llm_configured))


@router.post(
    "/sessions",
    status_code=201,
    response_model=SessionCreated,
    responses=errors(413, 422),
    tags=["sessions"],
)
def create_session(request: Request, body: CreateSessionRequest | None = None) -> SessionCreated:
    """Start a session. Its id is the only credential: keep it secret."""
    mode = body.mode if body is not None else "friend"
    session_id, record = registry_of(request).create(mode)
    with record.lock:
        return views.session_created(session_id, record, settings_of(request))


@router.get(
    "/sessions/{session_id}",
    response_model=SessionState,
    responses=errors(404),
    tags=["sessions"],
)
def get_session(request: Request, session_id: str) -> SessionState:
    """The whole session, for a page load or a reconnect."""
    record = session_of(request, session_id)
    with record.lock:
        return views.session_state(session_id, record, settings_of(request))


@router.post(
    "/sessions/{session_id}/files",
    status_code=201,
    response_model=UploadedFile,
    responses=errors(404, 409, 413, 415, 422),
    tags=["files"],
)
async def upload_file(
    request: Request, session_id: str, file: Annotated[UploadFile, File()]
) -> UploadedFile:
    """Upload one ``.xlsx`` (one part named ``file``). It becomes the active file.

    The stored name is sanitized, so it may differ from what was sent. The
    file is never modified afterwards, and isn't downloadable through the
    API (the user already has it).
    """
    settings = settings_of(request)
    record = await run_in_threadpool(session_of, request, session_id)
    form = await request.form()
    if len(form.getlist("file")) != 1 or any(
        key != "file" and not isinstance(value, str) for key, value in form.multi_items()
    ):
        raise ApiError(ErrorCode.INVALID_REQUEST)
    with record.lock:
        if record.busy:
            raise ApiError(ErrorCode.SESSION_BUSY)
    name = sanitize_name(file.filename or "")
    check_extension(name, settings)
    assert name is not None

    file_id = new_file_id()
    folder = record.upload_dir(settings, file_id)
    stored = await run_in_threadpool(store_upload, file.file, folder, name, settings)
    upload = UploadRecord(
        file_id=file_id,
        name=stored.name,
        size=stored.size,
        sha256=stored.sha256,
        uploaded_at=utc_now(),
        path=stored.path,
    )

    def register() -> None:
        with record.lock:
            if record.busy:  # a turn started while the file was being written
                discard_folder(folder)
                raise ApiError(ErrorCode.SESSION_BUSY)
            record.files[file_id] = upload
            context = record.state.context
            context.add_uploaded_file(str(upload.path))
            context.switch_active_file(str(upload.path))
            record.active_file_id = file_id
            registry_of(request).saved(record)

    await run_in_threadpool(register)
    return views.uploaded_file(upload)


@router.post(
    "/sessions/{session_id}/turns",
    status_code=202,
    response_model=RunningTurn,
    responses=errors(404, 409, 413, 422, 429),
    tags=["turns"],
)
def start_turn(request: Request, session_id: str, body: CreateTurnRequest) -> RunningTurn:
    """Start a turn. Returns at once, ``running``; poll the turn until it's ``finished``.

    One turn at a time per session (``409 session_busy``), and a global cap
    on running turns (``429 server_busy``, with ``Retry-After``). A refused
    request creates no turn.
    """
    settings = settings_of(request)
    record = session_of(request, session_id)
    message = body.message.strip()
    if not 1 <= len(message) <= settings.max_message_chars:
        raise ApiError(ErrorCode.INVALID_REQUEST)
    if body.file_id is not None and not valid_id(body.file_id):
        raise ApiError(ErrorCode.FILE_NOT_FOUND)
    turn = runner_of(request).start(record, message, body.mode, body.file_id)
    with record.lock:
        # The turn as it started, even if a fast pipeline has already finished it.
        return views.turn_as_started(turn)


@router.get(
    "/sessions/{session_id}/turns/{turn_id}",
    response_model=Turn,
    responses=errors(404),
    tags=["turns"],
)
def get_turn(request: Request, session_id: str, turn_id: str) -> RunningTurn | FinishedTurn:
    """One turn, with ``detail``. Poll this after starting a turn; a finished turn never changes."""
    record = session_of(request, session_id)
    if not valid_id(turn_id):
        raise ApiError(ErrorCode.TURN_NOT_FOUND)
    with record.lock:
        turn = record.turn(turn_id)
        if turn is None:
            raise ApiError(ErrorCode.TURN_NOT_FOUND)
        return views.turn_out(session_id, turn)


@router.get(
    "/sessions/{session_id}/files",
    response_model=FileList,
    responses=errors(404),
    tags=["files"],
)
def list_files(request: Request, session_id: str) -> FileList:
    """Uploads (not downloadable) and the files finished turns delivered (downloadable)."""
    record = session_of(request, session_id)
    with record.lock:
        return views.file_list(record)


@router.get(
    "/sessions/{session_id}/files/{file_id}/download",
    response_class=Response,
    responses={
        200: {
            "description": "The delivered file",
            "content": {XLSX_MEDIA_TYPE: {"schema": {"type": "string", "format": "binary"}}},
        },
        **errors(404, 410),
    },
    tags=["files"],
)
def download_file(request: Request, session_id: str, file_id: str) -> Response:
    """A file a finished turn delivered. Uploads and anything else are ``404``.

    The file is re-checked first: if it's gone, or its SHA-256 no longer
    matches the one recorded when it was delivered, the answer is ``410``.
    The bytes sent are the bytes checked.
    """
    settings = settings_of(request)
    record = session_of(request, session_id)
    if not valid_id(file_id):
        raise ApiError(ErrorCode.FILE_NOT_FOUND)
    with record.lock:
        delivered = next((d for d in record.delivered() if d.file_id == file_id), None)
        if delivered is None:
            raise ApiError(ErrorCode.FILE_NOT_FOUND)
        turn_dir = record.turn_dir(settings, delivered.turn_id)
    checked = check_turn_file(delivered.path, turn_dir)
    if checked is None or hashlib.sha256(checked[1]).hexdigest() != delivered.sha256:
        logger.warning("Delivered file %s of session %s is unavailable", file_id, record.key)
        raise ApiError(ErrorCode.FILE_UNAVAILABLE)
    name = sanitize_name(delivered.name) or "download.xlsx"
    media_type = XLSX_MEDIA_TYPE if name.lower().endswith(".xlsx") else "application/octet-stream"
    return Response(
        content=checked[1],
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"},
    )
