"""ASGI middleware: security headers, the raw-body cap, and the last-resort 500.

These run outside FastAPI's routing, as plain ASGI wrappers, so they apply
to every response, including ones FastAPI itself produces (unknown routes,
validation errors).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from backend.errors import ErrorCode, error_response

logger = logging.getLogger(__name__)

_UPLOAD_PATH = re.compile(r"^/api/v1/sessions/[^/]+/files/?$")


class SecurityHeadersMiddleware:
    """Adds ``nosniff`` and ``no-store`` to every response (design Section 6.1).

    Also ``frame-ancestors 'none'`` (clickjacking: design "Decisions
    (2026-10-01)", item 2.4), set only if the response carries no
    ``Content-Security-Policy`` of its own, so the frontend's full policy
    replaces it; and, in production, HSTS.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["X-Content-Type-Options"] = "nosniff"
                headers["Cache-Control"] = "no-store"
                if "content-security-policy" not in headers:
                    headers["Content-Security-Policy"] = "frame-ancestors 'none'"
                if self.hsts:
                    headers["Strict-Transport-Security"] = "max-age=31536000"
            await send(message)

        await self.app(scope, receive, send_with_headers)


class _BodyTooLarge(Exception):
    """Raised from ``receive`` once the body passes the cap, to stop the reader."""


class RequestGuardMiddleware:
    """Caps every request body on the raw stream, and turns a crash into a fixed 500.

    **Body cap.** Enforced before any multipart parsing (design "Decisions
    (2026-09-30, part 2)", item 3, "Upload spool"): a declared
    ``Content-Length`` over the cap is refused without reading anything, and
    the bytes actually received are counted, so a body with no
    ``Content-Length``, or a false one, is cut off as soon as it passes the
    cap. Whatever the app was doing with the body is then discarded, and the
    client gets ``413``: ``file_too_large`` on the upload route,
    ``request_too_large`` elsewhere.

    **Last resort.** An exception that escapes the app becomes a fixed
    ``500 internal_error`` here, inside the security-headers middleware, so
    it gets the same headers as every other response. The traceback is
    logged server side only.
    """

    def __init__(self, app: ASGIApp, *, max_body_bytes: int, file_too_large_message: str) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.file_too_large_message = file_too_large_message

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _declared_length(scope)
        if declared == -1:
            await error_response(ErrorCode.INVALID_REQUEST)(scope, receive, send)
            return
        if declared is not None and declared > self.max_body_bytes:
            await self._too_large(scope)(scope, receive, send)
            return

        received = 0
        exceeded = False
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_body_bytes:
                    exceeded = True
                    raise _BodyTooLarge
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if exceeded:
                return  # the app's response to a cut-off body is discarded
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, guarded_send)
        except Exception as exc:  # noqa: BLE001 — the last resort; nothing escapes as text
            if exceeded:
                pass
            elif response_started:
                raise
            else:
                logger.error(
                    "Unhandled error on %s %s", scope.get("method"), scope.get("path"), exc_info=exc
                )
                await error_response(ErrorCode.INTERNAL_ERROR)(scope, receive, send)
                return
        if exceeded:
            if response_started:
                return  # can't replace a response already on the wire
            await self._too_large(scope)(scope, receive, send)

    def _too_large(self, scope: Scope) -> Any:
        if scope.get("method") == "POST" and _UPLOAD_PATH.match(scope.get("path", "")):
            return error_response(ErrorCode.FILE_TOO_LARGE, self.file_too_large_message)
        return error_response(ErrorCode.REQUEST_TOO_LARGE)


def _declared_length(scope: Scope) -> int | None:
    """The ``Content-Length`` header's value, ``None`` if absent, ``-1`` if malformed."""
    values = [v for k, v in scope.get("headers", []) if k.lower() == b"content-length"]
    if not values:
        return None
    if len(set(values)) != 1:
        return -1
    try:
        length = int(values[0])
    except ValueError:
        return -1
    return length if length >= 0 else -1
