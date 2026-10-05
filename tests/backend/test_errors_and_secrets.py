"""Errors and secrets: one error shape, fixed text only, and no key ever leaves.

Design Sections 4.4, 6.4 and 7.3 item 11.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.errors import MESSAGES, XLS_MESSAGE, ErrorCode, file_too_large_message
from backend.logging_filter import uninstall_log_redaction
from backend.main import create_app
from backend.settings import Settings
from buddy.voice_catalog import FALLBACK_PIPELINE_ERROR
from tests.backend.factories import BlockingFactory, PipelineFactory, QueuedProvider, single
from tests.backend.helpers import new_session, start_turn, upload, wait_finished, xlsx_bytes

GROQ_PLACEHOLDER = "gsk_placeholderTESTONLY0000"
ANTHROPIC_PLACEHOLDER = "sk-ant-placeholder-TESTONLY-0000"
_PLACEHOLDERS = (GROQ_PLACEHOLDER, ANTHROPIC_PLACEHOLDER, "placeholderTESTONLY")


def _allowed_message(code: str, message: str, settings: Settings) -> bool:
    if code == ErrorCode.FILE_TOO_LARGE:
        return message == file_too_large_message(settings.max_upload_bytes)
    if code == ErrorCode.UNSUPPORTED_FILE_TYPE and message == XLS_MESSAGE:
        return True
    return message == MESSAGES[ErrorCode(code)]


def _error_requests(client: TestClient, settings: Settings) -> list[Any]:
    session_id = new_session(client)
    upload(client, session_id, xlsx_bytes())
    big = b"x" * (settings.max_body_bytes + 1)
    turns = f"/api/v1/sessions/{session_id}/turns"
    json_header = {"Content-Type": "application/json"}
    return [
        client.get("/api/v1/nope"),
        client.delete("/api/v1/sessions"),
        client.get("/api/v1/sessions/s_nope"),
        client.post("/api/v1/sessions", json={"mode": "loud"}),
        client.post("/api/v1/sessions", content=b"{bad json", headers=json_header),
        client.post(turns, json={"message": ""}),
        client.post(turns, json={"message": "hi", "file_id": "f_x"}),
        client.post(turns, content=big),
        client.get(f"/api/v1/sessions/{session_id}/turns/t0404"),
        client.get(f"/api/v1/sessions/{session_id}/files/d_t0001_1/download"),
        upload(client, session_id, b"not a workbook"),
        upload(client, session_id, b"\xd0\xcf\x11\xe0", name="old.xls"),
        upload(client, session_id, xlsx_bytes(), name="data.csv"),
        upload(client, session_id, b"x" * (settings.max_upload_bytes + 1)),
        client.post(f"/api/v1/sessions/{session_id}/files", data={"not": "a file"}),
    ]


def test_every_error_has_the_one_shape_a_known_code_and_a_fixed_message(
    settings: Settings,
) -> None:
    client = TestClient(create_app(settings, PipelineFactory()))

    responses = _error_requests(client, settings)

    for response in responses:
        assert response.status_code >= 400
        body = response.json()
        assert set(body) == {"error"}
        assert set(body["error"]) == {"code", "message"}
        code, message = body["error"]["code"], body["error"]["message"]
        assert code in {c.value for c in ErrorCode}
        assert _allowed_message(code, message, settings), (code, message)
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["Cache-Control"] == "no-store"
    assert {r.status_code for r in responses} >= {404, 405, 413, 415, 422}


def test_an_unexpected_error_is_a_fixed_500_without_its_text(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    client = TestClient(create_app(settings, PipelineFactory()))

    def explode(self: Any, mode: str) -> Any:
        raise RuntimeError(f"secret detail {GROQ_PLACEHOLDER} /srv/private/path")

    monkeypatch.setattr("backend.sessions.SessionRegistry.create", explode)
    with caplog.at_level(logging.ERROR):
        response = client.post("/api/v1/sessions")

    assert response.status_code == 500
    assert response.json() == {
        "error": {"code": "internal_error", "message": MESSAGES[ErrorCode.INTERNAL_ERROR]}
    }
    assert "secret detail" not in response.text and "/srv" not in response.text
    assert any(record.exc_info for record in caplog.records)  # the traceback is logged...
    assert GROQ_PLACEHOLDER not in caplog.text  # ...with the key masked


# -- Secrets (design 7.3 item 11) -----------------------------------------------------------


@pytest.fixture
def placeholder_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", GROQ_PLACEHOLDER)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_PLACEHOLDER)


def _assert_no_secret(*texts: str) -> None:
    for text in texts:
        for secret in _PLACEHOLDERS:
            assert secret not in text


def test_no_key_appears_in_any_response_or_log_on_any_path(
    settings: Settings,
    placeholder_keys: None,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    production = TestClient(create_app(settings))  # the real provider, built from the env
    bodies = [production.get("/healthz").text]
    assert production.get("/healthz").json() == {"ok": True, "llm_configured": True}
    bodies += [r.text for r in _error_requests(production, settings)]

    def leaky_factory(confirmation_provider: Any) -> Any:
        raise RuntimeError(f"provider refused key {GROQ_PLACEHOLDER} / {ANTHROPIC_PLACEHOLDER}")

    failing = TestClient(create_app(Settings(data_dir=settings.data_dir / "b"), leaky_factory))
    session_id = new_session(failing)
    upload(failing, session_id, xlsx_bytes())
    turn_id = start_turn(failing, session_id, "remove duplicate rows").json()["turn_id"]
    turn = wait_finished(failing, session_id, turn_id)
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    bodies += [
        failing.get(f"/api/v1/sessions/{session_id}").text,
        failing.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").text,
    ]

    _assert_no_secret(caplog.text, *bodies)
    stored = b"".join(p.read_bytes() for p in settings.data_dir.rglob("*") if p.is_file())
    _assert_no_secret(stored.decode("utf-8", errors="replace"))


def test_the_api_never_logs_a_key_even_without_redaction(
    settings: Settings, placeholder_keys: None, caplog: pytest.LogCaptureFixture
) -> None:
    app = create_app(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    uninstall_log_redaction(logging.getLogRecordFactory())
    logging.setLogRecordFactory(logging.LogRecord)
    caplog.set_level(logging.DEBUG)
    client = TestClient(app)
    session_id = new_session(client)
    upload(client, session_id, xlsx_bytes())
    turn_id = start_turn(client, session_id, "remove duplicate rows").json()["turn_id"]
    wait_finished(client, session_id, turn_id)
    _error_requests(client, settings)

    _assert_no_secret(caplog.text)


def test_the_api_logs_message_length_not_message_text(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = TestClient(
        create_app(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    )
    session_id = new_session(client)
    upload(client, session_id, xlsx_bytes())

    turn_id = start_turn(client, session_id, "remove duplicate rows QUOKKA").json()["turn_id"]
    wait_finished(client, session_id, turn_id)

    assert "QUOKKA" not in caplog.text
    assert "message length 28" in caplog.text


def test_the_session_id_is_masked_in_logs(
    settings: Settings, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    client = TestClient(create_app(settings, BlockingFactory()))
    session_id = new_session(client)
    logging.getLogger("uvicorn.access").info(
        '%s - "%s %s HTTP/1.1" %d', "127.0.0.1", "GET", f"/api/v1/sessions/{session_id}", 200
    )

    assert session_id not in caplog.text
    assert "/api/v1/sessions/[REDACTED]" in caplog.text


def test_settings_with_a_path_never_reach_a_response(settings: Settings, tmp_path: Path) -> None:
    client = TestClient(create_app(settings, PipelineFactory()))
    session_id = new_session(client)

    texts = [
        client.post("/api/v1/sessions").text,
        client.get(f"/api/v1/sessions/{session_id}").text,
        upload(client, session_id, xlsx_bytes()).text,
        client.get(f"/api/v1/sessions/{session_id}/files").text,
    ]

    for text in texts:
        assert str(settings.data_dir) not in text
        assert str(tmp_path) not in text
