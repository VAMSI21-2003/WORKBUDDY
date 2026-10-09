"""Helpers for the page's tests: an app on the fake LLM, a client over https, and a small driver
that does what the browser would (create a session, upload, send, poll, answer)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.identity import DEV
from backend.main import create_app
from backend.settings import Settings
from tests.backend.helpers import RELAXED_LIMITS, xlsx_bytes
from tests.web.htmltools import Element, parse

DEDUPE = "remove duplicate rows"
CHAIN = "remove duplicate rows, then remove empty rows"


class FakeWebClock:
    """The page's clock (``app.state.web_clock``): starts at the real time, moves when told."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_app(tmp_path: Path, factory: Any, **overrides: Any) -> tuple[FastAPI, TestClient]:
    """The app on ``factory`` (a fake-LLM engine factory), with open access and relaxed limits."""
    values: dict[str, Any] = {"data_dir": tmp_path / "data", "rate_limits": RELAXED_LIMITS}
    values.update(overrides)
    app = create_app(Settings(**values), factory)
    return app, TestClient(app, base_url="https://testserver")


class Ui:
    """What the page's script does, over HTTP."""

    def __init__(self, client: TestClient) -> None:
        self.client = client
        self.app: FastAPI = client.app  # type: ignore[assignment]

    def new_session(self) -> str:
        response = self.client.post("/ui/sessions")
        assert response.status_code == 200, response.text
        match = re.search(r'id="workspace"[^>]*data-session-id="([^"]+)"', response.text)
        assert match, response.text
        return match.group(1)

    def upload(self, sid: str, data: bytes | None = None, name: str = "report.xlsx") -> Any:
        return self.client.post(
            f"/ui/s/{sid}/files",
            files={
                "file": (name, xlsx_bytes() if data is None else data, "application/octet-stream")
            },
        )

    def start(self, sid: str, message: str, mode: str = "friend", last_seq: int = 0) -> Any:
        return self.client.post(
            f"/ui/s/{sid}/turns", data={"message": message, "mode": mode, "last_seq": str(last_seq)}
        )

    def poll(self, sid: str, tid: str, v: int | None = None, **params: str) -> Any:
        query = dict(params)
        if v is not None:
            query["v"] = str(v)
        return self.client.get(f"/ui/s/{sid}/turns/{tid}/poll", params=query)

    def answer(self, sid: str, tid: str, confirmation_id: str, decision: str) -> Any:
        return self.client.post(
            f"/ui/s/{sid}/turns/{tid}/confirmation",
            data={"confirmation_id": confirmation_id, "decision": decision},
        )

    def version(self, sid: str, tid: str) -> int:
        record = self.app.state.registry.get(sid, DEV)
        turn = record.turn(tid)
        return int(turn.version)

    def api_turn(self, sid: str, tid: str) -> dict[str, Any]:
        response = self.client.get(f"/api/v1/sessions/{sid}/turns/{tid}")
        assert response.status_code == 200, response.text
        return dict(response.json())

    def run_to_plan(self, sid: str, message: str = CHAIN) -> tuple[str, Element]:
        """Start a turn and poll until its plan shows. Returns the turn id and its container."""
        started = self.start(sid, message)
        assert started.status_code == 200, started.text
        tid_value = re.search(r'data-turn-id="(t\d+)"', started.text)
        assert tid_value
        turn_id = tid_value.group(1)
        container = wait_for_container(
            self, sid, turn_id, lambda c: c.find("section", **{"class": "plan"}) is not None
        )
        return turn_id, container


def wait_for_container(
    ui: Ui,
    sid: str,
    tid: str,
    done: Callable[[Element], bool],
    timeout_s: float = 30,
) -> Element:
    """Poll the page's route (as the page would, with the version it holds) until ``done``."""
    deadline = time.monotonic() + timeout_s
    held = 0
    while True:
        response = ui.poll(sid, tid, v=held)
        assert response.status_code == 200, response.text
        tree = parse(response.text)
        container = tree.find("li", **{"data-turn-id": tid})
        if container is not None:
            held = int(container.get("data-version") or 0)
            if done(container):
                return container
        assert time.monotonic() < deadline, "the turn never reached the wanted state"
        time.sleep(0.02)


def reply_of(container: Element) -> str | None:
    slot = container.find("div", **{"data-slot": "reply"})
    return None if slot is None else slot.text()


def pending_id(container: Element) -> str:
    field = container.find("input", name="confirmation_id")
    assert field is not None
    return str(field.get("value"))


def jar_has(client: TestClient, name: str) -> bool:
    return any(c.name == name for c in client.cookies.jar)
