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

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from backend import views
from backend.access import (
    AccessDependency,
    check_credential,
    exchange_password,
    forbidden_origin,
    origin_allowed,
)
from backend.delivery import check_turn_file
from backend.errors import (
    ApiError,
    ErrorCode,
    too_many_uploads_message,
    upload_bytes_quota_message,
)
from backend.names import display_names
from backend.ratelimit import ACCESS, GENERAL, HEALTHZ, POLL, TURNS, UPLOADS, limits
from backend.retention import StorageMeter
from backend.schemas import (
    AwaitingConfirmationTurn,
    ConfirmationRequest,
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
# Every route here needs a credential, and the Origin check where it applies
# (``backend.access.require_access``), set once on the whole router so a new
# route can't forget it. Each route also declares its rate-limit buckets
# (``backend.ratelimit.limits``); the app refuses to start without that.
router = APIRouter(prefix="/api/v1", dependencies=[AccessDependency])
# ``POST /api/v1/access`` takes no credential: it is how one is obtained.
access_router = APIRouter(prefix="/api/v1")

XLSX_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_DESCRIPTIONS = {
    401: "There is no valid sign-in or token on the request",
    403: "The request didn't come from the demo page, or the session is at one of its limits",
    404: "Not found",
    409: "Conflict",
    410: "Gone",
    413: "Too large",
    415: "Unsupported file type",
    422: "Invalid request or file",
    429: "Too many requests, or too many running turns",
    503: "No room for new files right now",
}


def errors(*statuses: int) -> dict[int | str, dict[str, Any]]:
    """The ``responses`` entry declaring ``ErrorBody`` for each status."""
    declared: dict[int | str, dict[str, Any]] = {}
    for status in statuses:
        entry: dict[str, Any] = {"model": ErrorBody, "description": _DESCRIPTIONS[status]}
        if status == 401:
            entry["headers"] = {
                "WWW-Authenticate": {
                    "description": 'Always `Bearer realm="workbuddy-demo"`.',
                    "schema": {"type": "string"},
                }
            }
        if status == 429:
            entry["headers"] = {
                "Retry-After": {
                    "description": (
                        "Whole seconds to wait before retrying: exact for `rate_limited`, "
                        "a hint for `server_busy`."
                    ),
                    "schema": {"type": "integer", "minimum": 1},
                }
            }
        declared[status] = entry
    return declared


def settings_of(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


def registry_of(request: Request) -> SessionRegistry:
    return request.app.state.registry  # type: ignore[no-any-return]


def _check_upload_quota(record: SessionRecord, settings: Settings, *, incoming: int) -> None:
    """Refuse an upload that would take the session past its upload count or size total.

    The caller holds the session's lock. With ``incoming=0`` it is the
    early check, before the file is written; with the file's size it is the
    exact one.
    """
    if len(record.files) >= settings.max_uploads_per_session:
        raise ApiError(
            ErrorCode.SESSION_QUOTA_EXCEEDED,
            message=too_many_uploads_message(settings.max_uploads_per_session),
        )
    held = sum(f.size for f in record.files.values())
    if held + incoming > settings.max_session_upload_bytes or (
        incoming == 0 and held >= settings.max_session_upload_bytes
    ):
        raise ApiError(
            ErrorCode.SESSION_QUOTA_EXCEEDED,
            message=upload_bytes_quota_message(settings.max_session_upload_bytes),
        )


def _encodable(text: str) -> bool:
    """Whether ``text`` is valid Unicode (JSON can carry a lone surrogate; UTF-8 can't)."""
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def runner_of(request: Request) -> TurnRunner:
    return request.app.state.runner  # type: ignore[no-any-return]


def meter_of(request: Request) -> StorageMeter:
    return request.app.state.meter  # type: ignore[no-any-return]


def session_of(request: Request, session_id: str) -> SessionRecord:
    """The session ``session_id`` if the request's principal owns it, else the one ``404``.

    Someone else's session, a missing one and a badly shaped id are the same answer. The gate
    (``backend.gate``) has already made the same check for every route with a ``{session_id}``;
    this is the registry's own check, which no code path can skip.
    """
    record = registry_of(request).get(session_id, check_credential(request).principal)
    if record is None:
        raise ApiError(ErrorCode.SESSION_NOT_FOUND)
    return record


@health_router.get(
    "/healthz",
    response_model=Health,
    responses=errors(429),
    tags=["health"],
    dependencies=[Depends(limits(HEALTHZ, credential=False))],
)
def healthz(request: Request) -> Health:
    """Liveness, and whether an LLM provider is configured. Booleans only."""
    return Health(ok=True, llm_configured=bool(request.app.state.llm_configured))


@router.post(
    "/sessions",
    status_code=201,
    response_model=SessionCreated,
    responses=errors(401, 403, 413, 422, 429, 503),
    tags=["sessions"],
    dependencies=[Depends(limits(GENERAL, mutating=True))],
)
def create_session(request: Request, body: CreateSessionRequest | None = None) -> SessionCreated:
    """Start a session. ``503 storage_full`` when the server has no room for new files."""
    if meter_of(request).full():
        raise ApiError(ErrorCode.STORAGE_FULL)
    mode = body.mode if body is not None else "friend"
    principal = check_credential(request).principal
    assert principal is not None  # the router requires a valid credential
    session_id, record = registry_of(request).create(mode, principal.owner)
    with record.lock:
        return views.session_created(session_id, record, settings_of(request))


@router.get(
    "/sessions/{session_id}",
    response_model=SessionState,
    responses=errors(401, 404, 429),
    tags=["sessions"],
    dependencies=[Depends(limits(GENERAL))],
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
    responses=errors(401, 403, 404, 409, 413, 415, 422, 429, 503),
    tags=["files"],
    dependencies=[Depends(limits(GENERAL, UPLOADS, mutating=True))],
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
        _check_upload_quota(record, settings, incoming=0)
    if meter_of(request).full():
        raise ApiError(ErrorCode.STORAGE_FULL)
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
            try:
                _check_upload_quota(record, settings, incoming=upload.size)
            except ApiError:
                discard_folder(folder)
                raise
            context = record.state.context
            rollback = (
                list(context.uploaded_files),
                context.active_file,
                record.active_file_id,
            )
            record.files[file_id] = upload
            context.add_uploaded_file(str(upload.path))
            context.switch_active_file(str(upload.path))
            record.active_file_id = file_id
            try:
                registry_of(request).saved(record)
            except BaseException:
                del record.files[file_id]
                context.uploaded_files[:] = rollback[0]
                context.switch_active_file(rollback[1])
                record.active_file_id = rollback[2]
                discard_folder(folder)
                raise

    await run_in_threadpool(register)
    return views.uploaded_file(upload)


@router.post(
    "/sessions/{session_id}/turns",
    status_code=202,
    response_model=RunningTurn,
    responses=errors(401, 403, 404, 409, 413, 422, 429),
    tags=["turns"],
    dependencies=[Depends(limits(GENERAL, TURNS, mutating=True))],
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
    if not 1 <= len(message) <= settings.max_message_chars or not _encodable(message):
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
    responses=errors(401, 404, 429),
    tags=["turns"],
    dependencies=[Depends(limits(POLL, poll=True))],
)
def get_turn(
    request: Request, session_id: str, turn_id: str
) -> RunningTurn | AwaitingConfirmationTurn | FinishedTurn:
    """One turn, with ``detail``. Poll this after starting a turn; a finished turn never changes.

    While the turn's plan waits for the user, the turn is
    ``awaiting_confirmation`` and carries the plan as ``pending_confirmation``.
    """
    record = session_of(request, session_id)
    if not valid_id(turn_id):
        raise ApiError(ErrorCode.TURN_NOT_FOUND)
    with record.lock:
        turn = record.turn(turn_id)
        if turn is None:
            raise ApiError(ErrorCode.TURN_NOT_FOUND)
        return views.turn_out(session_id, turn, record)


@router.post(
    "/sessions/{session_id}/turns/{turn_id}/confirmation",
    response_model=Turn,
    responses=errors(401, 403, 404, 409, 413, 422, 429),
    tags=["turns"],
    dependencies=[Depends(limits(GENERAL, mutating=True))],
)
def answer_confirmation(
    request: Request, session_id: str, turn_id: str, body: ConfirmationRequest
) -> RunningTurn | AwaitingConfirmationTurn | FinishedTurn:
    """Approve or reject the plan waiting on a turn, once. Returns the turn as it now stands.

    Approving approves every step of the plan; rejecting means none of them
    runs. The plan is no longer pending when this returns (usually the turn
    is ``running`` again); keep polling the turn for its reply. ``409
    no_pending_confirmation`` when no plan is waiting (none was asked, or it
    was already answered or timed out); ``409 stale_confirmation`` when
    ``confirmation_id`` isn't the waiting plan's.
    """
    record = session_of(request, session_id)
    if not valid_id(turn_id):
        raise ApiError(ErrorCode.TURN_NOT_FOUND)
    with record.lock:
        turn = runner_of(request).answer(record, turn_id, body.confirmation_id, body.decision)
        return views.turn_out(session_id, turn, record)


@router.get(
    "/sessions/{session_id}/files",
    response_model=FileList,
    responses=errors(401, 404, 429),
    tags=["files"],
    dependencies=[Depends(limits(GENERAL))],
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
        **errors(401, 404, 410, 429),
    },
    tags=["files"],
    dependencies=[Depends(limits(GENERAL))],
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
        # The name a person sees (``backend.names``); the file on disk keeps its unique name.
        shown = display_names(record.key, record.delivered()).get(file_id)
    checked = check_turn_file(delivered.path, turn_dir)
    if checked is None or hashlib.sha256(checked[1]).hexdigest() != delivered.sha256:
        logger.warning("Delivered file %s of session %s is unavailable", file_id, record.key)
        raise ApiError(ErrorCode.FILE_UNAVAILABLE)
    name = shown or sanitize_name(delivered.name) or "download.xlsx"
    media_type = XLSX_MEDIA_TYPE if name.lower().endswith(".xlsx") else "application/octet-stream"
    return Response(
        content=checked[1],
        media_type=media_type,
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(name, safe='')}"},
    )


@access_router.post(
    "/access",
    status_code=204,
    response_class=Response,
    responses={
        204: {
            "description": "The password was right. The access cookie is set.",
            "headers": {
                "Set-Cookie": {
                    "description": (
                        "`__Host-wb_access=<token>; Path=/; Secure; HttpOnly; SameSite=Strict`, "
                        "with no expiry (a session cookie); the server expires the token itself."
                    ),
                    "schema": {"type": "string"},
                }
            },
        },
        **errors(401, 403, 422, 429),
    },
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/x-www-form-urlencoded": {
                    "schema": {
                        "type": "object",
                        "required": ["password"],
                        "properties": {"password": {"type": "string", "format": "password"}},
                    }
                }
            },
        }
    },
    tags=["access"],
    dependencies=[
        Depends(limits(GENERAL, ACCESS, mutating=True, credential=False)),
    ],
)
async def access(request: Request) -> Response:
    """Exchange the demo password (a form field) for the access cookie. Not in production.

    ``application/x-www-form-urlencoded`` only: anything else is ``422``. The
    request must carry an ``Origin`` header equal to the demo's public origin
    (``403 forbidden_origin``). A wrong or missing password is the same
    ``401`` every route gives, and **in production every password is**: people
    sign in with Google (``/auth/google/start``) there, and the demo password
    is not a credential.
    """
    if not origin_allowed(request.scope, settings_of(request)):
        raise forbidden_origin()
    return await exchange_password(request)
