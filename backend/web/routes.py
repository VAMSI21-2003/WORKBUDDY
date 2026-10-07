"""The page's routes (frontend design Section 2).

Every route here calls the JSON routes' own functions (``backend.routes``) and renders what they
return. It never works out a reply, the downloads or how a turn ended again, and it adds no logic of
its own to what the JSON API does: same session lookup, same quotas, same errors (every error
is the API's ``{"error": {"code", "message"}}`` body, through the same handlers).

A fragment is built from one snapshot: the route holds the session's lock (re-entrant, so the JSON
functions it calls take it again) from the first read to the last, so a turn's status, its version
counter and the Files panel always agree.

The credential, the Origin check and the rate-limit buckets are the API's: ``/ui/*`` sits behind
``require_access`` and every route declares its buckets (``backend.ratelimit.limits``).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from starlette.types import Scope

from backend import routes as api
from backend.access import AccessDependency, check_credential
from backend.errors import ApiError, ErrorCode
from backend.ratelimit import GENERAL, PAGE, POLL, TURNS, UPLOADS, limits
from backend.schemas import ConfirmationRequest, CreateTurnRequest, SessionState
from backend.sessions import SessionRecord, utc_now
from backend.settings import Settings
from backend.web import views
from backend.web.templating import STATIC_DIR, render, static_version

SESSION_COOKIE = "__Host-wb_session"

# Frontend design 11.2. FE-1 serves its own pages and fragments under this policy, so the page is
# tested the way it will run; FE-2 adds the rest of the headers and the static-file caching.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)

# The static files' caching (frontend design 11.2). A page links them as ``?v=<version>`` of the
# file (``static_url``), so a URL whose version is the file's current one never changes meaning
# and is cached for a year. Any other URL for the file (no version, or an old one) is
# revalidated on every use: StaticFiles answers with an ETag and Last-Modified, so that costs a
# ``304``.
IMMUTABLE = "public, max-age=31536000, immutable"
REVALIDATE = "no-cache"


class StaticAssets(StaticFiles):
    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        if response.status_code in (200, 304):
            query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
            current = query.get("v") == [static_version(path)]  # a 200 or 304: the file exists
            response.headers["Cache-Control"] = IMMUTABLE if current else REVALIDATE
        return response


page_router = APIRouter()
ui_router = APIRouter(prefix="/ui", dependencies=[AccessDependency])


def now_of(request: Request) -> datetime:
    """The server's clock for time left and polling (tests replace ``app.state.web_clock``)."""
    clock: Callable[[], datetime] = request.app.state.web_clock
    return clock()


def settings_of(request: Request) -> Settings:
    return api.settings_of(request)


def html(content: str, status: int = 200, headers: dict[str, str] | None = None) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status, headers=headers)
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


def versions_of(record: SessionRecord) -> dict[str, int]:
    return {t.turn_id: t.version for t in record.turns}


def conversation_of(
    request: Request, record: SessionRecord, session_id: str, *, only_after: int | None = None
) -> views.ConversationView:
    """The conversation region for the session as it is now. The caller holds the lock."""
    state: SessionState = api.get_session(request, session_id)
    return views.conversation_view(
        state, versions_of(record), settings_of(request), now_of(request), only_after=only_after
    )


def turn_response(
    request: Request,
    record: SessionRecord,
    session_id: str,
    turn: Any,
    *,
    version: int,
    headers: dict[str, str] | None = None,
    answer_may_be_lost: bool = False,
) -> HTMLResponse:
    """A turn container, with the Files panel and composer that go with it (out of band).

    The panel and composer follow the turn being rendered: while it is unfinished, upload and Send
    are closed, and a turn the page shows as running contributes no delivered file.
    """
    now = now_of(request)
    state: SessionState = api.get_session(request, session_id)
    view = views.turn_view(session_id, turn, version, now, answer_may_be_lost=answer_may_be_lost)
    settings = settings_of(request)
    exclude = view.turn_id if view.unfinished else None
    files = views.files_view(
        state, settings, upload_open=not view.unfinished, exclude_turn_id=exclude
    )
    composer = views.ComposerView(
        session_id=session_id,
        mode=turn.mode,
        last_seq=max([t.seq for t in state.turns] + [view.seq]),
        max_chars=state.limits.max_message_chars,
        open=not view.unfinished,
    )
    return html(
        render("turn_fragment.html", turn=view, files=files, composer=composer), headers=headers
    )


def clear_pointer(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def set_pointer(response: Response, session_id: str) -> None:
    # A session cookie: no Max-Age or Expires, so it goes when the browser closes (design 6).
    response.set_cookie(
        SESSION_COOKIE, session_id, path="/", secure=True, httponly=True, samesite="strict"
    )


# -- The page ------------------------------------------------------------------------------------


@page_router.get(
    "/",
    response_class=HTMLResponse,
    include_in_schema=False,
    dependencies=[Depends(limits(PAGE, credential=False))],
)
def index(request: Request) -> Response:
    """The gate without a credential; with one, the workspace for the session pointer.

    Creates nothing: the workspace shell makes its own session with ``POST /ui/sessions``, so no
    GET changes state (backend "Decisions (2026-10-01)", item 2.3).
    """
    settings = settings_of(request)
    if not check_credential(request).valid:
        return html(render("gate.html", retention_hours=settings.retention_hours))
    pointer = request.cookies.get(SESSION_COOKIE)
    conversation: views.ConversationView | None = None
    gone = False
    if pointer is not None:
        record = api.registry_of(request).get(pointer)
        if record is not None:
            try:
                with record.lock:
                    conversation = conversation_of(request, record, pointer)
            except ApiError as exc:
                if exc.code is not ErrorCode.SESSION_NOT_FOUND:
                    raise
        # A pointer to a session the server doesn't have (swept, or lost in a restart): the
        # page says so exactly once, and the pointer is cleared (design 1.3).
        gone = conversation is None
    response = html(
        render(
            "workspace.html",
            conversation=conversation,
            notice_gone=gone,
            awaiting=conversation is not None and conversation.awaiting_plan,
            retention_hours=settings.retention_hours,
        )
    )
    if gone:
        clear_pointer(response)
    return response


@ui_router.post(
    "/sessions", include_in_schema=False, dependencies=[Depends(limits(GENERAL, mutating=True))]
)
def create_session(request: Request) -> Response:
    """Start a session and make it the page's: an empty workspace, and the pointer cookie."""
    created = api.create_session(request, None)
    record = api.session_of(request, created.session_id)
    with record.lock:
        conversation = conversation_of(request, record, created.session_id)
    response = html(render("workspace_body.html", conversation=conversation))
    set_pointer(response, created.session_id)
    return response


@ui_router.get(
    "/s/{session_id}/transcript",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL))],
)
def transcript(request: Request, session_id: str, after: int | None = None) -> Response:
    """The conversation as the server has it; with ``after``, only the turns past that ``seq``."""
    record = api.session_of(request, session_id)
    with record.lock:
        conversation = conversation_of(request, record, session_id, only_after=after)
    if after is not None:
        return html(render("transcript_after.html", conversation=conversation))
    return html(
        render(
            "conversation_fragment.html",
            conversation=conversation,
            awaiting=conversation.awaiting_plan,
        ),
        # The page asks for this by script and has no swap style of its own to give.
        headers={"HX-Retarget": "#conversation", "HX-Reswap": "outerHTML"},
    )


@ui_router.post(
    "/s/{session_id}/files",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL, UPLOADS, mutating=True))],
)
async def upload(
    request: Request, session_id: str, file: Annotated[UploadFile, File()]
) -> Response:
    """Upload a file, through the API's own upload route; the response is the Files panel."""
    await api.upload_file(request, session_id, file)

    def build() -> str:
        record = api.session_of(request, session_id)
        with record.lock:
            conversation = conversation_of(request, record, session_id)
        return render("files.html", files=conversation.files)

    return html(await run_in_threadpool(build))


@ui_router.post(
    "/s/{session_id}/turns",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL, TURNS, mutating=True))],
)
def start_turn(
    request: Request,
    session_id: str,
    message: Annotated[str, Form()] = "",
    mode: Annotated[str | None, Form()] = None,
) -> Response:
    """Start a turn: a new turn container, running, with its poll."""
    try:
        body = CreateTurnRequest(message=message, mode=mode)  # type: ignore[arg-type]
    except ValidationError:
        raise ApiError(ErrorCode.INVALID_REQUEST) from None
    started = api.start_turn(request, session_id, body)
    record = api.session_of(request, session_id)
    with record.lock:
        # The turn as it started (version 0), even if a fast pipeline has already moved it on:
        # the first poll then finds a higher version and brings the page up to date.
        return turn_response(request, record, session_id, started, version=0)


@ui_router.get(
    "/s/{session_id}/turns/{turn_id}/poll",
    include_in_schema=False,
    dependencies=[Depends(limits(POLL, poll=True))],
)
def poll(
    request: Request,
    session_id: str,
    turn_id: str,
    v: int | None = None,
    unconfirmed: str | None = None,
    held: int | None = None,
) -> Response:
    """One poll of a turn: a new poll element if nothing changed, else the whole turn container.

    ``v`` is the version the page holds, sent by the turn's own poll element; ``held`` is the same
    number sent by the page's other requests for the turn (a refetch after a ``409``, the lookup
    after a lost answer), which don't ask whether anything changed. Either one higher than the
    server's means the server restarted since the page rendered, and the answer is an empty
    ``HX-Refresh`` (a reload). ``unconfirmed`` names a confirmation whose answer may not have
    arrived: the turn is always returned whole then, and if that plan is still waiting, its card
    says the answer may be lost.
    """
    record = api.session_of(request, session_id)
    with record.lock:
        turn = api.get_turn(request, session_id, turn_id)
        stored = record.turn(turn_id)
        assert stored is not None  # get_turn found it, under this same lock
        version = stored.version
        now = now_of(request)
        seen = v if v is not None else held
        if seen is not None and seen > version:
            # The page holds a version this server never gave out: the server restarted, and
            # versions are memory-only, so it counts from 0 again. The page's swap guard would drop
            # every answer as older than what it holds, for good, so the page reloads instead and
            # renders the session as this server has it.
            return html("", headers={"HX-Refresh": "true"})
        if unconfirmed is None and v == version and turn.status != views.FINISHED:
            view = views.turn_view(session_id, turn, version, now)
            return html(render("poll_fragment.html", turn=view))
        lost = (
            unconfirmed is not None
            and turn.status == views.AWAITING
            and turn.pending_confirmation is not None
            and turn.pending_confirmation.confirmation_id == unconfirmed
        )
        return turn_response(
            request,
            record,
            session_id,
            turn,
            version=version,
            headers={"HX-Retarget": f"#turn-{turn_id}", "HX-Reswap": "outerHTML"},
            answer_may_be_lost=lost,
        )


@ui_router.post(
    "/s/{session_id}/turns/{turn_id}/confirmation",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL, mutating=True))],
)
def confirmation(
    request: Request,
    session_id: str,
    turn_id: str,
    confirmation_id: Annotated[str, Form()] = "",
    decision: Annotated[str, Form()] = "",
) -> Response:
    """Answer the plan, once, through the API's own route; the response is the turn as it now is."""
    try:
        body = ConfirmationRequest(
            confirmation_id=confirmation_id, decision=decision  # type: ignore[arg-type]
        )
    except ValidationError:
        raise ApiError(ErrorCode.INVALID_REQUEST) from None
    record = api.session_of(request, session_id)
    with record.lock:
        turn = api.answer_confirmation(request, session_id, turn_id, body)
        stored = record.turn(turn_id)
        assert stored is not None
        return turn_response(request, record, session_id, turn, version=stored.version)


def mount_web(app: FastAPI) -> None:
    """Add the page's routes and static files to ``app``."""
    app.state.web_clock = utc_now
    app.include_router(page_router)
    app.include_router(ui_router)
    app.mount("/static", StaticAssets(directory=STATIC_DIR), name="static")
