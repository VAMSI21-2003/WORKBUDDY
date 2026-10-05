"""Errors — the closed set of error codes and their fixed messages.

Every non-2xx response has the body ``{"error": {"code", "message"}}``
(``docs/design/backend-api-design.md`` Section 4.4). Codes are a closed
enum and messages are fixed strings about a *rejected request*: none of
them describes the outcome of work on a file, because a rejected request
did no work. No exception text (``str(exc)``) ever reaches a response;
tracebacks go to the server log only.
"""

from __future__ import annotations

import logging
from enum import StrEnum

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class ErrorCode(StrEnum):
    """Every error code the API returns. Nothing outside this enum is ever sent."""

    SESSION_NOT_FOUND = "session_not_found"
    FILE_NOT_FOUND = "file_not_found"
    TURN_NOT_FOUND = "turn_not_found"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    FILE_TOO_LARGE = "file_too_large"
    REQUEST_TOO_LARGE = "request_too_large"
    UNSUPPORTED_FILE_TYPE = "unsupported_file_type"
    INVALID_FILE = "invalid_file"
    MULTIPLE_SHEETS = "multiple_sheets"
    INVALID_REQUEST = "invalid_request"
    SESSION_BUSY = "session_busy"
    SERVER_BUSY = "server_busy"
    FILE_UNAVAILABLE = "file_unavailable"
    INTERNAL_ERROR = "internal_error"


STATUS: dict[ErrorCode, int] = {
    ErrorCode.SESSION_NOT_FOUND: 404,
    ErrorCode.FILE_NOT_FOUND: 404,
    ErrorCode.TURN_NOT_FOUND: 404,
    ErrorCode.NOT_FOUND: 404,
    ErrorCode.METHOD_NOT_ALLOWED: 405,
    ErrorCode.FILE_TOO_LARGE: 413,
    ErrorCode.REQUEST_TOO_LARGE: 413,
    ErrorCode.UNSUPPORTED_FILE_TYPE: 415,
    ErrorCode.INVALID_FILE: 422,
    ErrorCode.MULTIPLE_SHEETS: 422,
    ErrorCode.INVALID_REQUEST: 422,
    ErrorCode.SESSION_BUSY: 409,
    ErrorCode.SERVER_BUSY: 429,
    ErrorCode.FILE_UNAVAILABLE: 410,
    ErrorCode.INTERNAL_ERROR: 500,
}

# The fixed message for each code. ``FILE_TOO_LARGE`` is built from the
# configured cap at startup (``file_too_large_message``), so it can't
# disagree with it.
MESSAGES: dict[ErrorCode, str] = {
    ErrorCode.SESSION_NOT_FOUND: "That session doesn't exist.",
    ErrorCode.FILE_NOT_FOUND: "That file doesn't exist in this session.",
    ErrorCode.TURN_NOT_FOUND: "That turn doesn't exist in this session.",
    ErrorCode.NOT_FOUND: "There's nothing at that address.",
    ErrorCode.METHOD_NOT_ALLOWED: "That method isn't allowed at this address.",
    ErrorCode.REQUEST_TOO_LARGE: "The request is too large.",
    ErrorCode.UNSUPPORTED_FILE_TYPE: "Only .xlsx files can be uploaded.",
    ErrorCode.INVALID_FILE: "That file is empty or isn't a valid .xlsx workbook.",
    ErrorCode.MULTIPLE_SHEETS: (
        "This demo works on one sheet at a time, and this workbook has more than one. "
        "Save the sheet you want in its own workbook and upload that."
    ),
    ErrorCode.INVALID_REQUEST: "The request isn't valid.",
    ErrorCode.SESSION_BUSY: (
        "Something is still running in this session. Wait for it to finish and try again."
    ),
    ErrorCode.SERVER_BUSY: "The server is busy right now. Try again shortly.",
    ErrorCode.FILE_UNAVAILABLE: "That file is no longer available.",
    ErrorCode.INTERNAL_ERROR: "Something went wrong on the server.",
}

# Owner decision (design "Decisions (2026-10-04)"): ``.xls`` is out of scope,
# and an ``.xls`` upload gets this fixed message under UNSUPPORTED_FILE_TYPE.
XLS_MESSAGE = (
    "Older .xls workbooks aren't supported. Save the file as .xlsx (Excel Workbook) "
    "and upload that."
)


def file_too_large_message(max_upload_bytes: int) -> str:
    """The ``413 file_too_large`` message, from the configured cap."""
    return f"That file is larger than {_format_size(max_upload_bytes)}."


def _format_size(size: int) -> str:
    for unit, scale in (("MB", 1024 * 1024), ("KB", 1024)):
        if size >= scale and size % scale == 0:
            return f"{size // scale} {unit}"
    return f"{size} bytes"


class ApiError(Exception):
    """A rejected request. Raised by route code; rendered by ``api_error_handler``."""

    def __init__(
        self,
        code: ErrorCode,
        *,
        message: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code.value)
        self.code = code
        self.status = STATUS[code]
        self.message = message if message is not None else MESSAGES[code]
        self.headers = headers


def error_response(
    code: ErrorCode, message: str | None = None, headers: dict[str, str] | None = None
) -> JSONResponse:
    """The uniform error response for ``code``."""
    return JSONResponse(
        status_code=STATUS[code],
        content=error_body(code, message if message is not None else MESSAGES[code]),
        headers=headers,
    )


def error_body(code: ErrorCode, message: str) -> dict[str, dict[str, str]]:
    return {"error": {"code": code.value, "message": message}}


async def api_error_handler(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiError)
    return error_response(exc.code, exc.message, exc.headers)


async def http_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Starlette's own errors (unknown route, wrong method, body parse failure)."""
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        return error_response(ErrorCode.NOT_FOUND)
    if exc.status_code == 405:
        allow = (exc.headers or {}).get("Allow")
        return error_response(
            ErrorCode.METHOD_NOT_ALLOWED, headers={"Allow": allow} if allow else None
        )
    if 400 <= exc.status_code < 500:
        response = error_response(ErrorCode.INVALID_REQUEST)
        response.status_code = exc.status_code
        return response
    logger.error("HTTP %s raised inside the app", exc.status_code)
    return error_response(ErrorCode.INTERNAL_ERROR)


async def validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """A body or parameter that failed validation. The details are not echoed."""
    return error_response(ErrorCode.INVALID_REQUEST)


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """The last resort: a fixed ``500``, with the traceback in the server log only."""
    logger.error("Unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(ErrorCode.INTERNAL_ERROR)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
