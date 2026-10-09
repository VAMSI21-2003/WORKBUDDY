"""The browser tests' launcher (frontend design 10.3).

``live_app`` starts the real app (``create_app``: the real pipeline on a queued fake LLM, a
temporary data root, a known demo password and the **production rate limits**) with uvicorn on a
free loopback port, and the tests drive it in Chromium. Nothing here is a test-only route: the
tests reach into the server only to read what it holds, or to make it forget a session.

Chromium is the browser that comes with the cloud session (``/opt/pw-browsers``); don't run
``playwright install``. If ``playwright`` isn't installed, these tests are skipped.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest

pytest.importorskip("playwright.sync_api")

import uvicorn  # noqa: E402
from playwright.sync_api import Browser, BrowserContext, Page  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402
from tests.backend.factories import PipelineFactory, QueuedProvider  # noqa: E402

PASSWORD = "e2e demo password"
MOBILE = {"width": 360, "height": 640}


@dataclass
class LiveApp:
    base: str
    app: Any
    settings: Settings
    factory: Any
    server: uvicorn.Server
    thread: threading.Thread

    def api(self, path: str) -> httpx.Response:
        """A read of the JSON API, as a script would make it (Bearer, no cookie)."""
        headers = {"Authorization": f"Bearer {PASSWORD}"} if self.settings.demo_password else {}
        return httpx.get(self.base + path, headers=headers, timeout=10)

    def restart(self, factory: Any) -> None:
        """Stop the server and start a new process's worth of app on the same port and data root:
        everything held in memory (turn versions, access tokens, pending plans) is gone, what is on
        disk stays, as after a restart of the real server."""
        self.server.should_exit = True
        self.thread.join(15)
        assert not self.thread.is_alive(), "the app didn't stop"
        self.app, self.server, self.thread = _serve(self.settings, factory)
        self.factory = factory

    def turn(self, session_id: str, turn_id: str = "t0001") -> dict[str, Any]:
        response = self.api(f"/api/v1/sessions/{session_id}/turns/{turn_id}")
        assert response.status_code == 200, response.text
        return dict(response.json())

    def uploads(self) -> list[Path]:
        return sorted(p for p in self.settings.uploads_dir.rglob("*.xlsx"))


def _serve(settings: Settings, factory: Any) -> tuple[Any, uvicorn.Server, threading.Thread]:
    app = create_app(settings, factory)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=settings.port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the app didn't start"
    return app, server, thread


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture
def live_app(tmp_path: Path) -> Iterator[Callable[..., LiveApp]]:
    """A factory for a running app: ``live_app(replies, **settings)``. One per test, so every test
    starts with full rate-limit buckets, and stops it afterwards."""
    started: list[LiveApp] = []

    def start(
        replies: list[str] | None = None, *, factory: Any = None, **overrides: Any
    ) -> LiveApp:
        port = _free_port()
        values: dict[str, Any] = {
            "data_dir": tmp_path / f"data{len(started)}",
            "env": "",
            "demo_password": PASSWORD,
            "port": port,  # the public origin is http://127.0.0.1:<port> (review L5)
        }
        values.update(overrides)  # ``env="dev", demo_password=None`` is the no-password dev server
        settings = Settings(**values)
        engine = factory if factory is not None else PipelineFactory(QueuedProvider(replies or []))
        app, server, thread = _serve(settings, engine)
        live = LiveApp(f"http://127.0.0.1:{settings.port}", app, settings, engine, server, thread)
        started.append(live)
        return live

    yield start
    for live in started:
        live.server.should_exit = True


@pytest.fixture
def new_page(browser: Browser) -> Iterator[Callable[..., Page]]:
    """A page in a fresh browser context (its own cookies), by default 360x640."""
    contexts: list[BrowserContext] = []

    def make(viewport: dict[str, int] | None = None, **kwargs: Any) -> Page:
        context = browser.new_context(viewport=viewport or MOBILE, accept_downloads=True, **kwargs)
        contexts.append(context)
        page = context.new_page()
        page.set_default_timeout(20_000)
        return page

    yield make
    for context in contexts:
        context.close()
