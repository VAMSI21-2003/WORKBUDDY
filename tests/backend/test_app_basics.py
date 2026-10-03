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


def test_docs_and_openapi_are_not_served(settings: Settings) -> None:
    client = _client(settings)

    for path in ("/docs", "/redoc", "/openapi.json"):
        assert client.get(path).status_code == 404


def test_every_error_code_has_a_status_and_a_fixed_message() -> None:
    for code in ErrorCode:
        assert code in STATUS
        assert code is ErrorCode.FILE_TOO_LARGE or MESSAGES[code]


def test_the_413_message_is_built_from_the_cap() -> None:
    assert file_too_large_message(DEFAULT_MAX_UPLOAD_BYTES) == "That file is larger than 5 MB."
    assert file_too_large_message(2 * 1024 * 1024) == "That file is larger than 2 MB."
    assert file_too_large_message(1536) == "That file is larger than 1536 bytes."


# -- Settings -----------------------------------------------------------------------


def test_settings_defaults_match_the_design(tmp_path: Path) -> None:
    settings = Settings.from_env({"WORKBUDDY_DATA_DIR": str(tmp_path)})

    assert settings.data_dir == tmp_path.resolve()
    assert settings.max_upload_bytes == 5 * 1024 * 1024
    assert settings.max_message_chars == 4000
    assert settings.accepted_extensions == (".xlsx",)
    assert settings.turn_deadline_s == 180
    assert settings.max_concurrent_turns == 2
    assert settings.busy_retry_after_s == 10
    assert settings.auto_approve is False


def test_the_data_root_is_absolute_even_when_given_relative(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)

    settings = Settings.from_env({"WORKBUDDY_DATA_DIR": "relative/data"})

    assert settings.data_dir.is_absolute()
    assert settings.data_dir == (tmp_path / "relative" / "data").resolve()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKBUDDY_MAX_UPLOAD_BYTES", "lots"),
        ("WORKBUDDY_MAX_UPLOAD_BYTES", "0"),
        ("WORKBUDDY_MAX_CONCURRENT_TURNS", "-1"),
        ("WORKBUDDY_AUTO_APPROVE", "maybe"),
    ],
)
def test_an_invalid_setting_refuses_to_start(tmp_path: Path, name: str, value: str) -> None:
    with pytest.raises(SettingsError):
        Settings.from_env({"WORKBUDDY_DATA_DIR": str(tmp_path), name: value})


@pytest.mark.parametrize("value", ["1", "true", "YES", "on"])
def test_auto_approve_is_on_only_when_asked(tmp_path: Path, value: str) -> None:
    env = {"WORKBUDDY_DATA_DIR": str(tmp_path), "WORKBUDDY_AUTO_APPROVE": value}

    assert Settings.from_env(env).auto_approve is True


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
