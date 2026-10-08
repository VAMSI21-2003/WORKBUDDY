"""Sign in and sign out (docs/design/google-sign-in.md, "Routes").

Three routes, none of them in ``openapi.json`` (they are the page's, not the JSON API's):

- ``GET /auth/google/start``: sends the browser to Google.
- ``GET /auth/google/callback``: where Google sends it back. The only place a sign-in is made.
- ``POST /auth/signout``: ends the sign-in.

They take no credential (``credential=False``): they are how one is obtained and dropped. Every
failure of the callback is the same page, with the same words, whatever went wrong, except an
email that isn't on the allowed list; the reason goes to the log as a short code, with nothing
from the token in it. Nothing here reads a redirect target from the request: after a sign-in the
browser is always sent to ``/``.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, Response

from backend.access import COOKIE_NAME, LOGIN_COOKIE, access_cookie_header, access_of
from backend.errors import ApiError, ErrorCode
from backend.oidc import OidcClient, SignInError
from backend.ratelimit import GENERAL, SIGNIN, limits
from backend.settings import Settings
from backend.web.common import clear_pointer, html
from backend.web.templating import render

logger = logging.getLogger(__name__)

auth_router = APIRouter(prefix="/auth")

_MAX_PARAM_CHARS = 2048
LOGIN_MAX_AGE_S = 600


def oidc_of(request: Request) -> OidcClient:
    return request.app.state.oidc  # type: ignore[no-any-return]


def gate_context(settings: Settings, notice: str | None = None) -> dict[str, Any]:
    """What ``gate.html`` needs: which ways in are on, the retention notice, a sign-in notice."""
    return {
        "retention_hours": settings.retention_hours,
        "google_enabled": settings.google_enabled,
        "password_login": settings.password_login,
        "notice": notice,
    }


def _clear_login_cookie(response: Response) -> None:
    response.delete_cookie(LOGIN_COOKIE, path="/", secure=True, httponly=True, samesite="lax")


def _refused(
    request: Request,
    reason: str,
    notice: str = "problem",
    status: int = 400,
    *,
    clear_login: bool = True,
) -> Response:
    logger.info("Sign-in refused: %s", reason)  # a short code; nothing from the token or the code
    response = html(
        render("gate.html", **gate_context(request.app.state.settings, notice)), status=status
    )
    if clear_login:
        _clear_login_cookie(response)
    return response


@auth_router.get(
    "/google/start",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL, SIGNIN, credential=False))],
)
def start(request: Request) -> Response:
    """Send the browser to Google, with a state, a nonce and a PKCE challenge made for it."""
    oidc = oidc_of(request)
    if not oidc.enabled:
        raise ApiError(ErrorCode.NOT_FOUND)
    try:
        url, binder = oidc.begin()
    except SignInError as exc:
        return _refused(request, exc.reason, status=502)
    response = Response(status_code=302, headers={"Location": url})
    # Lax, not Strict: the browser comes back from another site and Strict wouldn't be sent.
    response.set_cookie(
        LOGIN_COOKIE,
        binder,
        max_age=LOGIN_MAX_AGE_S,
        path="/",
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@auth_router.get(
    "/google/callback",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL, SIGNIN, credential=False))],
)
def callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> Response:
    """Finish a sign-in: the pending login, the code, the token's checks, the allowed list."""
    oidc = oidc_of(request)
    if not oidc.enabled:
        raise ApiError(ErrorCode.NOT_FOUND)
    access = access_of(request)
    pending = oidc.pending.take(state, request.cookies.get(LOGIN_COOKIE))
    if pending is None:
        # (the browser's binder is left alone: it may belong to a newer sign-in that is still
        # going on in another tab, and this callback is a stale or stray one)
        return _refused(request, "no_pending_login", clear_login=False)
    if error is not None or not code or len(code) > _MAX_PARAM_CHARS:
        return _refused(request, "provider_error" if error is not None else "no_code")
    try:
        token = oidc.exchange_code(code, pending.verifier)
        identity = oidc.verify_id_token(token, pending.nonce)
    except SignInError as exc:
        return _refused(request, exc.reason)
    if not access.email_allowed(identity.email):
        return _refused(request, "email_not_allowed", notice="not_allowed", status=403)
    # A browser that was signed in as someone else is signed out of that first.
    access.sign_out(request)
    cookie = access.sign_in(identity.subject, identity.email)
    response = html(render("signed_in.html"))
    response.headers.append("Set-Cookie", access_cookie_header(cookie))
    _clear_login_cookie(response)
    clear_pointer(response)  # the last person's session isn't this person's
    logger.info("A person signed in")
    return response


@auth_router.post(
    "/signout",
    include_in_schema=False,
    dependencies=[Depends(limits(GENERAL, mutating=True, credential=False))],
)
def signout(request: Request) -> Response:
    """End the sign-in the cookie names (if any), clear the cookies, and go to the gate."""
    access_of(request).sign_out(request)
    # The page sends this with htmx (an XHR sends its Origin; a plain form POST would send
    # ``Origin: null`` under the page's ``Referrer-Policy: no-referrer`` and be refused).
    if request.headers.get("hx-request") == "true":
        response: Response = Response(status_code=204, headers={"HX-Redirect": "/"})
    else:
        response = RedirectResponse("/", status_code=303)
    response.delete_cookie(COOKIE_NAME, path="/", secure=True, httponly=True, samesite="strict")
    clear_pointer(response)
    return response
