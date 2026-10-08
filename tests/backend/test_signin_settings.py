"""The settings that sign-in adds, and what each environment needs."""

from __future__ import annotations

from pathlib import Path

import pytest

from backend.settings import Settings, SettingsError
from tests.backend.helpers import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OPERATOR_TOKEN,
    PASSWORD,
    PUBLIC_ORIGIN,
    production_settings,
)

BASE = {"WORKBUDDY_PUBLIC_ORIGIN": PUBLIC_ORIGIN}
GOOGLE = {
    "WORKBUDDY_GOOGLE_CLIENT_ID": GOOGLE_CLIENT_ID,
    "WORKBUDDY_GOOGLE_CLIENT_SECRET": GOOGLE_CLIENT_SECRET,
}


def _env(tmp_path: Path, **extra: str) -> dict[str, str]:
    return {"WORKBUDDY_DATA_DIR": str(tmp_path), **extra}


def test_production_needs_google_and_nothing_else_secret(tmp_path: Path) -> None:
    settings = Settings.from_env(_env(tmp_path, WORKBUDDY_ENV="production", **BASE, **GOOGLE))

    assert settings.google_enabled and settings.demo_password is None
    assert settings.operator_token is None  # optional: only scripts and smoke.sh use it
    assert settings.oidc_issuer == "https://accounts.google.com"
    assert settings.allowed_emails == ()


@pytest.mark.parametrize(
    "missing", ["WORKBUDDY_GOOGLE_CLIENT_ID", "WORKBUDDY_GOOGLE_CLIENT_SECRET"]
)
def test_the_client_id_and_secret_go_together(tmp_path: Path, missing: str) -> None:
    env = {k: v for k, v in GOOGLE.items() if k != missing}

    for environment in ("production", "dev", ""):
        with pytest.raises(SettingsError, match="go together|required in production"):
            Settings.from_env(
                _env(
                    tmp_path, WORKBUDDY_ENV=environment, WORKBUDDY_DEMO_PASSWORD="x", **BASE, **env
                )
            )


def test_production_without_google_refuses_to_start_and_names_the_setting(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="WORKBUDDY_GOOGLE_CLIENT_ID"):
        Settings.from_env(_env(tmp_path, WORKBUDDY_ENV="production", **BASE))
    with pytest.raises(SettingsError, match="WORKBUDDY_GOOGLE_CLIENT_ID"):
        # a password and an operator token don't stand in for it
        Settings.from_env(
            _env(
                tmp_path,
                WORKBUDDY_ENV="production",
                WORKBUDDY_DEMO_PASSWORD=PASSWORD,
                WORKBUDDY_OPERATOR_TOKEN=OPERATOR_TOKEN,
                **BASE,
            )
        )


def test_google_alone_is_enough_outside_production(tmp_path: Path) -> None:
    settings = Settings.from_env(_env(tmp_path, WORKBUDDY_ENV="", **GOOGLE))

    assert settings.google_enabled and not settings.password_login


def test_dev_may_run_with_nothing(tmp_path: Path) -> None:
    settings = Settings.from_env(_env(tmp_path, WORKBUDDY_ENV="dev"))

    assert not settings.google_enabled and not settings.password_login


def test_a_password_is_a_credential_outside_production_only(tmp_path: Path) -> None:
    prod = production_settings(tmp_path, demo_password=PASSWORD)
    dev = Settings(data_dir=tmp_path, env="dev", demo_password=PASSWORD)

    assert prod.password_login is False and dev.password_login is True


@pytest.mark.parametrize(
    "emails",
    [" Alice@Example.test , bob@example.test ,, ", "alice@example.test,BOB@EXAMPLE.TEST"],
)
def test_the_allowed_emails_are_a_lower_case_list(tmp_path: Path, emails: str) -> None:
    settings = Settings.from_env(
        _env(tmp_path, WORKBUDDY_ENV="dev", WORKBUDDY_ALLOWED_EMAILS=emails)
    )

    assert settings.allowed_emails == ("alice@example.test", "bob@example.test")


@pytest.mark.parametrize(
    "bad", ["alice", "alice@", "@example.test", "a@b", "a b@example.test", "a@@b.c"]
)
def test_an_allowed_email_that_isnt_one_is_refused(tmp_path: Path, bad: str) -> None:
    with pytest.raises(SettingsError, match="WORKBUDDY_ALLOWED_EMAILS"):
        Settings.from_env(_env(tmp_path, WORKBUDDY_ENV="dev", WORKBUDDY_ALLOWED_EMAILS=bad))


@pytest.mark.parametrize(
    "token",
    ["short", "x" * 31, " " + "x" * 40, "x" * 20 + " " + "x" * 20, "é" * 40, "x" * 40 + "\n"],
)
def test_the_operator_token_must_be_long_and_plain(tmp_path: Path, token: str) -> None:
    with pytest.raises(SettingsError, match="WORKBUDDY_OPERATOR_TOKEN"):
        Settings.from_env(_env(tmp_path, WORKBUDDY_ENV="dev", WORKBUDDY_OPERATOR_TOKEN=token))


def test_the_operator_token_must_differ_from_the_password(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="differ"):
        Settings(
            data_dir=tmp_path, env="", demo_password=OPERATOR_TOKEN, operator_token=OPERATOR_TOKEN
        )


def test_an_operator_token_alone_is_a_credential_so_the_server_is_not_open(tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    from backend.main import create_app

    settings = Settings(data_dir=tmp_path, env="dev", operator_token=OPERATOR_TOKEN)
    client = TestClient(create_app(settings, lambda p: None))

    assert client.post("/api/v1/sessions", json={}).status_code == 401
    ok = client.post(
        "/api/v1/sessions", json={}, headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    )
    assert ok.status_code == 201


def test_the_issuer_is_an_https_origin_in_production(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="OIDC_ISSUER"):
        production_settings(tmp_path, oidc_issuer="http://idp.test")
    with pytest.raises(SettingsError, match="OIDC_ISSUER"):
        production_settings(tmp_path, oidc_issuer="https://idp.test/some/path")
    assert Settings(data_dir=tmp_path, env="dev", oidc_issuer="http://127.0.0.1:9100")


def test_the_secrets_are_not_in_the_settings_repr(tmp_path: Path) -> None:
    text = repr(production_settings(tmp_path, demo_password=PASSWORD))

    for secret in (GOOGLE_CLIENT_SECRET, OPERATOR_TOKEN, PASSWORD):
        assert secret not in text
