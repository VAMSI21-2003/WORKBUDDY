"""The raw-stream body cap and the last-resort 500, tested as plain ASGI."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from backend.middleware import RequestGuardMiddleware, SecurityHeadersMiddleware

CAP = 1000


class _ReadingApp:
    """An app that reads the whole body, recording how much it was given."""

    def __init__(self) -> None:
        self.bytes_read = 0
        self.finished_reading = False

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        while True:
            message = await receive()
            self.bytes_read += len(message.get("body", b""))
            if not message.get("more_body"):
                break
        self.finished_reading = True
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})


def _run(app: Any, chunks: list[bytes], headers: list[tuple[bytes, bytes]], path: str) -> tuple[
    int, dict[str, Any], dict[bytes, bytes]
]:
    sent: list[dict[str, Any]] = []
    queue = [
        {"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1}
        for index, chunk in enumerate(chunks)
    ]

    async def receive() -> dict[str, Any]:
        return queue.pop(0) if queue else {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    scope = {"type": "http", "method": "POST", "path": path, "headers": headers}
    asyncio.run(app(scope, receive, send))
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], json.loads(body) if body != b"ok" else {}, dict(start["headers"])


def _guarded(inner: Any) -> Any:
    return SecurityHeadersMiddleware(
        RequestGuardMiddleware(inner, max_body_bytes=CAP, file_too_large_message="too big")
    )


@pytest.mark.parametrize(
    "headers",
    [[], [(b"content-length", b"10")]],
    ids=["no-content-length", "false-content-length"],
)
def test_the_body_is_cut_off_as_soon_as_it_passes_the_cap(
    headers: list[tuple[bytes, bytes]],
) -> None:
    inner = _ReadingApp()
    chunks = [b"x" * 400] * 10  # 4,000 bytes, sent 400 at a time

    status, body, response_headers = _run(
        _guarded(inner), chunks, headers, "/api/v1/sessions/s_x/files"
    )

    assert status == 413
    assert body == {"error": {"code": "file_too_large", "message": "too big"}}
    assert inner.finished_reading is False
    assert inner.bytes_read <= CAP
    assert response_headers[b"x-content-type-options"] == b"nosniff"


def test_a_body_at_the_cap_reaches_the_app() -> None:
    inner = _ReadingApp()

    status, _, _ = _run(_guarded(inner), [b"x" * 500, b"x" * 500], [], "/api/v1/sessions")

    assert status == 200
    assert inner.bytes_read == CAP


def test_a_declared_length_over_the_cap_is_refused_before_reading() -> None:
    inner = _ReadingApp()

    status, body, _ = _run(
        _guarded(inner), [b"x"], [(b"content-length", str(CAP + 1).encode())], "/api/v1/sessions"
    )

    assert status == 413
    assert body["error"]["code"] == "request_too_large"
    assert inner.bytes_read == 0


@pytest.mark.parametrize("value", [b"abc", b"-5"])
def test_a_malformed_content_length_is_refused(value: bytes) -> None:
    status, body, _ = _run(_guarded(_ReadingApp()), [b"x"], [(b"content-length", value)], "/x")

    assert status == 422
    assert body["error"]["code"] == "invalid_request"


def test_an_exception_escaping_the_app_becomes_a_fixed_500_with_headers() -> None:
    async def crashing(scope: Any, receive: Any, send: Any) -> None:
        raise RuntimeError("secret detail gsk_SHOULDNOTLEAK")

    status, body, headers = _run(_guarded(crashing), [b""], [], "/api/v1/sessions")

    assert status == 500
    assert body == {
        "error": {"code": "internal_error", "message": "Something went wrong on the server."}
    }
    assert headers[b"cache-control"] == b"no-store"
