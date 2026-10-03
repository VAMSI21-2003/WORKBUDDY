"""Routes — the HTTP endpoints (design Section 1).

Every route declares its error responses with the shared ``ErrorBody``
model, so ``openapi.json`` describes the real error bodies. Route functions
are plain ``def``: FastAPI runs them on its thread pool, so taking a
session's (threading) lock never blocks the event loop.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Request, UploadFile
from starlette.concurrency import run_in_threadpool

from backend import views
from backend.errors import ApiError, ErrorCode
from backend.schemas import (
    CreateSessionRequest,
    ErrorBody,
    Health,
    SessionCreated,
    SessionState,
    UploadedFile,
)
from backend.sessions import (
    SessionRecord,
    SessionRegistry,
    UploadRecord,
    new_file_id,
    utc_now,
)
from backend.settings import Settings
from backend.uploads import check_extension, discard_folder, sanitize_name, store_upload

health_router = APIRouter()
router = APIRouter(prefix="/api/v1")

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
