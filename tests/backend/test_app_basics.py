"""The app's frame: health, security headers, uniform error bodies, settings."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.errors import MESSAGES, STATUS, ErrorCode, file_too_large_message
from backend.logging_filter import REDACTED, install_log_redaction, redact
from backend.main import build_llm_provider_from_env, create_app
from backend.settings import DEFAULT_MAX_UPLOAD_BYTES, Settings, SettingsError
from buddy.llm import GroqProvider


def _client(settings: Settings, **kwargs: object) -> TestClient:
    return TestClient(create_app(settings, lambda provider: None, **kwargs))  # type: ignore[arg-type,return-value]


def test_healthz_reports_booleans_only(settings: Settings) -> None:
    response = _client(settings).get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "llm_configured": True}


def test_healthz_reports_an_unconfigured_llm(settings: Settings) -> None:
    response = _client(settings, llm_configured=False).get("/healthz")

    assert response.json() == {"ok": True, "llm_configured": False}


@pytest.mark.parametrize("path", ["/healthz", "/nope", "/api/v1/nothing-here"])
def test_every_response_carries_nosniff_and_no_store(settings: Settings, path: str) -> None:
    response = _client(settings).get(path)

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["Referrer-Policy"] == "no-referrer"  # review L9 of frontend-demo


def test_an_unknown_route_gets_the_uniform_error_body(settings: Settings) -> None:
    response = _client(settings).get("/api/v1/nothing-here")

    assert response.status_code == 404
    assert response.json() == {
        "error": {"code": "not_found", "message": MESSAGES[ErrorCode.NOT_FOUND]}
    }


def test_a_wrong_method_gets_the_uniform_error_body(settings: Settings) -> None:
    response = _client(settings).post("/healthz")

    assert response.status_code == 405
    assert response.json()["error"]["code"] == "method_not_allowed"
    assert response.headers["Allow"] == "GET"  # review L8


def test_docs_and_openapi_are_not_served(settings: Settings) -> None:
    client = _client(settings)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_every_error_code_has_a_status_and_a_fixed_message() -> None:
    for code in ErrorCode:
        assert code in STATUS
        assert code is ErrorCode.FILE_TOO_LARGE or MESSAGES[code]


def test_the_413_message_is_built_from_the_cap() -> None:
    assert file_too_large_message(DEFAULT_MAX_UPLOAD_BYTES) == "That file is larger than 2 MB."
    assert file_too_large_message(5 * 1024 * 1024) == "That file is larger than 5 MB."
    assert file_too_large_message(1536) == "That file is larger than 1536 bytes."


# -- Settings -----------------------------------------------------------------------

# A server that sets no password must say it's a dev server (PR-4), so every
# environment these tests build says so.
DEV = {"WORKBUDDY_ENV": "dev"}


def test_settings_defaults_match_the_design(tmp_path: Path) -> None:
    settings = Settings.from_env({**DEV, "WORKBUDDY_DATA_DIR": str(tmp_path)})

    assert settings.data_dir == tmp_path.resolve()
    assert settings.max_upload_bytes == 2 * 1024 * 1024  # "Decisions (2026-10-04)", item 3
    assert settings.max_message_chars == 4000
    assert settings.accepted_extensions == (".xlsx", ".pdf")
    assert settings.turn_deadline_s == 180
    assert settings.max_concurrent_turns == 2
    assert settings.busy_retry_after_s == 10
    assert settings.confirmation_timeout_s == 120  # design Section 1.5
    assert settings.auto_approve is False


def test_the_data_root_is_absolute_even_when_given_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env({**DEV, "WORKBUDDY_DATA_DIR": "relative/data"})

    assert settings.data_dir.is_absolute()
    assert settings.data_dir == (tmp_path / "relative" / "data").resolve()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKBUDDY_MAX_UPLOAD_BYTES", "lots"),
        ("WORKBUDDY_MAX_UPLOAD_BYTES", "0"),
        ("WORKBUDDY_MAX_CONCURRENT_TURNS", "-1"),
        ("WORKBUDDY_CONFIRMATION_TIMEOUT_S", "0"),
        ("WORKBUDDY_CONFIRMATION_TIMEOUT_S", "soon"),
        ("WORKBUDDY_CONFIRMATION_TIMEOUT_S", "3601"),  # review round 1, L7
        ("WORKBUDDY_CONFIRMATION_TIMEOUT_S", "99999999999"),
        ("WORKBUDDY_TURN_DEADLINE_S", "0"),
        ("WORKBUDDY_TURN_DEADLINE_S", "3601"),  # PR-3 review round 2, R2-L5
        ("WORKBUDDY_TURN_DEADLINE_S", "99999999999"),
    ],
)
def test_an_invalid_setting_refuses_to_start(tmp_path: Path, name: str, value: str) -> None:
    valid = {**DEV, "WORKBUDDY_DATA_DIR": str(tmp_path)}
    Settings.from_env(valid)  # the same environment, without the bad value, starts
    with pytest.raises(SettingsError):
        Settings.from_env({**valid, name: value})


def test_the_longest_turn_deadline_is_accepted(tmp_path: Path) -> None:
    env = {**DEV, "WORKBUDDY_DATA_DIR": str(tmp_path), "WORKBUDDY_TURN_DEADLINE_S": "3600"}

    assert Settings.from_env(env).turn_deadline_s == 3600


def test_auto_approve_is_off_unless_set(tmp_path: Path) -> None:
    """Review L5: the dataclass default is pinned, not only ``from_env``'s."""
    assert Settings(data_dir=tmp_path).auto_approve is False


@pytest.mark.parametrize("value", ["1", "true", "YES", "on", "maybe"])
def test_auto_approve_is_never_read_from_the_environment(
    tmp_path: Path, value: str, caplog: pytest.LogCaptureFixture
) -> None:
    """PR-3: approving without asking is a test setting; a server always asks."""
    env = {**DEV, "WORKBUDDY_DATA_DIR": str(tmp_path), "WORKBUDDY_AUTO_APPROVE": value}

    with caplog.at_level(logging.WARNING, logger="backend.settings"):
        settings = Settings.from_env(env)

    assert settings.auto_approve is False
    assert "WORKBUDDY_AUTO_APPROVE is no longer read" in caplog.text


def test_the_confirmation_timeout_comes_from_its_variable(tmp_path: Path) -> None:
    env = {**DEV, "WORKBUDDY_DATA_DIR": str(tmp_path), "WORKBUDDY_CONFIRMATION_TIMEOUT_S": "30"}

    assert Settings.from_env(env).confirmation_timeout_s == 30
    longest = {**env, "WORKBUDDY_CONFIRMATION_TIMEOUT_S": "3600"}
    assert Settings.from_env(longest).confirmation_timeout_s == 3600


# -- LLM provider and secrets ----------------------------------------------------------


def test_the_provider_comes_from_groq_when_its_key_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_placeholderTESTONLY")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert isinstance(build_llm_provider_from_env(), GroqProvider)


def test_no_key_means_no_provider_and_an_unconfigured_health(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    assert build_llm_provider_from_env() is None
    response = TestClient(create_app(settings)).get("/healthz")
    assert response.json() == {"ok": True, "llm_configured": False}


@pytest.mark.parametrize(
    "secret",
    [
        "gsk_abcDEF123456",
        "sk-ant-api03-abc_DEF-123",
        "Bearer some.token.value",
        "api_key=abc123secret",
        "API-KEY: 'abc123secret'",
    ],
)
def test_redact_masks_each_secret_pattern(secret: str) -> None:
    redacted = redact(f"before {secret} after")

    assert REDACTED in redacted
    assert "abc" not in redacted.lower().replace("[redacted]", "")
    assert redacted.startswith("before ") and redacted.endswith(" after")


def test_redact_masks_a_session_id_in_a_request_path() -> None:
    line = 'GET /api/v1/sessions/s_AbC-123_xyz/turns/t0001 HTTP/1.1" 200'

    assert redact(line) == f'GET /api/v1/sessions/{REDACTED}/turns/t0001 HTTP/1.1" 200'


def test_installed_redaction_masks_messages_args_and_tracebacks(
    caplog: pytest.LogCaptureFixture,
) -> None:
    install_log_redaction()
    caplog.set_level(logging.DEBUG)
    log = logging.getLogger("tests.backend.redaction")

    log.info("key is %s", "gsk_SECRET123")
    try:
        raise RuntimeError("failed with api_key=SECRET456")
    except RuntimeError:
        log.exception("boom")

    assert "SECRET123" not in caplog.text
    assert "SECRET456" not in caplog.text
    assert REDACTED in caplog.text


def test_a_redacted_access_log_record_still_formats_with_uvicorns_formatter() -> None:
    """Review L1: redaction keeps ``record.args``' shape, which uvicorn unpacks."""
    from uvicorn.logging import AccessFormatter

    install_log_redaction()
    record = logging.getLogger("uvicorn.access").makeRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        1,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:5000", "GET", "/api/v1/sessions/s_SECRETID123/turns/t0001", "1.1", 200),
        None,
    )

    formatted = AccessFormatter(use_colors=False).format(record)

    assert "s_SECRETID123" not in formatted
    assert f"/api/v1/sessions/{REDACTED}/turns/t0001" in formatted
    assert "200" in formatted


def test_a_secret_inside_a_non_string_argument_is_still_masked() -> None:
    install_log_redaction()
    record = logging.getLogger("x").makeRecord(
        "x", logging.INFO, __file__, 1, "failed: %s", (RuntimeError("key gsk_ABC123"),), None
    )

    assert "gsk_ABC123" not in record.getMessage()


@pytest.mark.parametrize(
    ("fmt", "arg"),
    [
        ("Authorization: Bearer %s", "tok_plain_SECRET123"),
        ("api_key=%s", "tok_plain_SECRET123"),
        ("GET /api/v1/sessions/%s/files", "s_tok_plain_SECRET123"),
    ],
)
def test_a_secret_split_between_format_and_argument_formats_and_stays_masked(
    fmt: str, arg: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Round 2, R2-L1: redaction never drops a placeholder, so nothing raw is printed."""
    import io

    install_log_redaction()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    log = logging.getLogger(f"tests.backend.split.{abs(hash(fmt))}")
    log.addHandler(handler)
    log.propagate = False
    try:
        log.warning(fmt, arg)
    finally:
        log.removeHandler(handler)

    written = stream.getvalue()
    errors = capsys.readouterr().err
    assert REDACTED in written
    assert "SECRET123" not in written and "SECRET123" not in errors
    assert "Logging error" not in errors
