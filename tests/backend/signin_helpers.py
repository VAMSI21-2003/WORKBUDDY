"""Signing people in against the fake identity provider (``tests/idp``), for the tests."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from tests.backend.factories import PipelineFactory, QueuedProvider
from tests.backend.helpers import (
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    IDP_ISSUER,
    PUBLIC_ORIGIN,
    RELAXED_LIMITS,
    production_settings,
)
from tests.idp.provider import FakeIdp

ACCESS_COOKIE = "__Host-wb_access"
ALICE = "alice@example.test"
BOB = "bob@example.test"


def make_idp(**overrides: Any) -> FakeIdp:
    values: dict[str, Any] = {
        "issuer": IDP_ISSUER,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": PUBLIC_ORIGIN + "/auth/google/callback",
    }
    values.update(overrides)
    return FakeIdp(**values)


def browser(app: FastAPI) -> TestClient:
    """One person's browser: its own cookie jar, https, and the page's Origin on every request."""
    return TestClient(app, base_url=PUBLIC_ORIGIN, headers={"Origin": PUBLIC_ORIGIN})


def sign_in(client: TestClient, idp: FakeIdp, email: str, *flaws: str) -> httpx.Response:
    """The whole flow, as a browser does it: start, the provider, the callback. Returns the
    callback's response (``200`` and a cookie when it worked)."""
    started = client.get("/auth/google/start", follow_redirects=False)
    assert started.status_code == 302, started.text
    back = idp.authorize(started.headers["location"], email, *flaws)
    return client.get(back, follow_redirects=False)


def signed_in(client: TestClient, idp: FakeIdp, email: str) -> TestClient:
    response = sign_in(client, idp, email)
    assert response.status_code == 200, response.text
    assert ACCESS_COOKIE in client.cookies
    return client


def token_of(client: TestClient) -> str:
    return str(client.cookies[ACCESS_COOKIE])


class Rig:
    """An app on production settings, wired to a fake identity provider, and people's browsers."""

    def __init__(
        self,
        tmp_path: Path,
        factory: Any = None,
        *,
        idp: FakeIdp | None = None,
        relaxed: bool = True,
        clock: Callable[[], float] | None = None,
        wall_clock: Callable[[], float] | None = None,
        **overrides: Any,
    ) -> None:
        self.idp = idp or make_idp()
        values: dict[str, Any] = dict(overrides)
        if relaxed:
            values.setdefault("rate_limits", RELAXED_LIMITS)
        self.settings: Settings = production_settings(tmp_path, **values)
        self.factory = factory or PipelineFactory(QueuedProvider([]))
        extra: dict[str, Any] = {}
        if clock is not None:
            extra["clock"] = clock
        if wall_clock is not None:
            extra["wall_clock"] = wall_clock
        self.app: FastAPI = create_app(
            self.settings, self.factory, oidc_http=self.idp.client(), **extra
        )

    def person(self, email: str | None = None) -> TestClient:
        """A new browser, signed in as ``email`` (or not signed in)."""
        client = browser(self.app)
        if email is not None:
            signed_in(client, self.idp, email)
        return client
