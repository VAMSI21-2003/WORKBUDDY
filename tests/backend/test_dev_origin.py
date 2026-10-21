"""L5 of the backend-api-retention review: the dev public origin follows the port.

Outside production, with no ``WORKBUDDY_PUBLIC_ORIGIN``, the origin is derived from the port the
server listens on, and the same port on ``localhost`` is accepted too. An origin that is set
explicitly is matched exactly. (Before, it was fixed at ``http://127.0.0.1:8000``, so a browser on
``http://localhost:8000``, or on any other port, got ``403`` from the access route.)
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import DEFAULT_PORT, Settings, SettingsError
from tests.backend.helpers import PASSWORD


def _status(settings: Settings, origin: str | None) -> int:
    client = TestClient(create_app(settings, lambda p: None))
    headers = {"Origin": origin} if origin is not None else {}
    return client.post("/api/v1/access", data={"password": PASSWORD}, headers=headers).status_code


def _dev(tmp_path: Path, **overrides: Any) -> Settings:
    return Settings(data_dir=tmp_path / "d", env="dev", demo_password=PASSWORD, **overrides)


def test_the_default_origin_follows_the_port(tmp_path: Path) -> None:
    assert _dev(tmp_path).public_origin == "http://127.0.0.1:8000"
    assert _dev(tmp_path, port=8123).public_origin == "http://127.0.0.1:8123"
    assert _dev(tmp_path, port=80).public_origin == "http://127.0.0.1"


@pytest.mark.parametrize("origin", ["http://127.0.0.1:8123", "http://localhost:8123"])
def test_both_loopback_names_pass_the_access_route_on_the_started_port(
    tmp_path: Path, origin: str
) -> None:
    assert _status(_dev(tmp_path, port=8123), origin) == 204


@pytest.mark.parametrize(
    "origin",
    [
        "http://localhost:8000",  # the old fixed port
        "http://127.0.0.1:8000",
        "http://localhost",
        "https://localhost:8123",
        "http://localhost.evil.example:8123",
        "http://[::1]:8123",
        "null",
        None,
    ],
)
def test_nothing_else_passes(tmp_path: Path, origin: str | None) -> None:
    assert _status(_dev(tmp_path, port=8123), origin) == 403


def test_an_origin_set_explicitly_is_matched_exactly_with_no_localhost_alias(
    tmp_path: Path,
) -> None:
    settings = _dev(tmp_path, port=8123, public_origin="http://127.0.0.1:9000")
    assert settings.accepted_origins == ("http://127.0.0.1:9000",)
    assert _status(settings, "http://127.0.0.1:9000") == 204
    assert _status(settings, "http://localhost:9000") == 403
    assert _status(settings, "http://localhost:8123") == 403


def test_production_never_accepts_localhost(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "d",
        env="production",
        google_client_id="client.apps.test",
        google_client_secret="secret",
        public_origin="https://demo.example",
        port=8123,
    )
    assert settings.accepted_origins == ("https://demo.example",)
    assert _status(settings, "http://localhost:8123") == 403


def test_from_env_takes_the_port_and_a_set_origin_wins() -> None:
    assert Settings.from_env({"WORKBUDDY_ENV": "dev"}, port=8123).public_origin == (
        "http://127.0.0.1:8123"
    )
    assert Settings.from_env({"WORKBUDDY_ENV": "dev"}).public_origin == "http://127.0.0.1:8000"
    env = {"WORKBUDDY_ENV": "dev", "WORKBUDDY_PUBLIC_ORIGIN": "http://127.0.0.1:9000"}
    assert Settings.from_env(env, port=8123).public_origin == "http://127.0.0.1:9000"
    assert DEFAULT_PORT == 8000


@pytest.mark.parametrize("port", [0, 65536, -1, True])
def test_a_bad_port_is_refused(tmp_path: Path, port: Any) -> None:
    with pytest.raises(SettingsError, match="port"):
        _dev(tmp_path, port=port)


def test_the_server_passes_its_port_to_the_settings(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import backend.__main__ as entry

    served: dict[str, Any] = {}

    def fake_run(app: Any, **kwargs: Any) -> None:
        served["settings"] = app.state.settings
        served["port"] = kwargs["port"]
        served["server_header"] = kwargs.get("server_header")

    monkeypatch.setattr(entry.uvicorn, "run", fake_run)
    monkeypatch.setattr(sys, "argv", ["backend", "--port", "8123"])
    monkeypatch.setenv("WORKBUDDY_ENV", "dev")
    monkeypatch.setenv("WORKBUDDY_DATA_DIR", str(tmp_path / "d"))
    monkeypatch.delenv("WORKBUDDY_PUBLIC_ORIGIN", raising=False)
    entry.main()

    assert served["port"] == 8123
    assert served["server_header"] is False  # no "Server: uvicorn" on any response
    assert served["settings"].accepted_origins == ("http://127.0.0.1:8123", "http://localhost:8123")


def test_a_data_folder_this_user_cant_write_is_named_not_a_traceback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Review L7 of frontend-deploy: a volume mounted with the wrong owner."""
    import backend.__main__ as entry

    def refused(settings: Settings) -> None:
        raise PermissionError(13, "Permission denied", str(settings.data_dir / "uploads"))

    monkeypatch.setattr(entry, "create_app", refused)
    monkeypatch.setattr(entry.uvicorn, "run", lambda *a, **k: pytest.fail("it must not serve"))
    monkeypatch.setattr(sys, "argv", ["backend"])
    monkeypatch.setenv("WORKBUDDY_ENV", "dev")
    monkeypatch.setenv("WORKBUDDY_DATA_DIR", str(tmp_path / "d"))

    with pytest.raises(SystemExit) as stopped:
        entry.main()

    assert stopped.value.code == 2
    message = capsys.readouterr().err
    assert str(tmp_path / "d") in message and "Permission denied" in message
    assert "Traceback" not in message
