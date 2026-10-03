"""Routes — the HTTP endpoints (design Section 1).

Every route declares its error responses with the shared ``ErrorBody``
model, so ``openapi.json`` describes the real error bodies. Route functions
are plain ``def``: FastAPI runs them on its thread pool, so taking a
session's (threading) lock never blocks the event loop.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from backend import views
from backend.errors import ApiError, ErrorCode
from backend.schemas import (
    CreateSessionRequest,
    ErrorBody,
    Health,
    SessionCreated,
    SessionState,
)
from backend.sessions import SessionRecord, SessionRegistry
from backend.settings import Settings

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
