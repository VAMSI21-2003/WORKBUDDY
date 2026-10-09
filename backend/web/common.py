"""What the page's routes share: the HTML response with its policy, and the pointer cookie."""

from __future__ import annotations

from fastapi.responses import HTMLResponse, Response

SESSION_COOKIE = "__Host-wb_session"

# Frontend design 11.2. FE-1 serves its own pages and fragments under this policy, so the page is
# tested the way it will run; FE-2 adds the rest of the headers and the static-file caching.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
    "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'"
)


def html(content: str, status: int = 200, headers: dict[str, str] | None = None) -> HTMLResponse:
    response = HTMLResponse(content, status_code=status, headers=headers)
    response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    return response


def clear_pointer(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def set_pointer(response: Response, session_id: str) -> None:
    # A session cookie: no Max-Age or Expires, so it goes when the browser closes (design 6).
    response.set_cookie(
        SESSION_COOKIE, session_id, path="/", secure=True, httponly=True, samesite="strict"
    )
