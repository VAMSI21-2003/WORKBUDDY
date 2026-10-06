"""The gate and the session lifecycle on the page's routes (frontend design 1.2, 1.3, 6; 10.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.backend.helpers import (
    BEARER,
    COOKIE_NAME,
    ORIGIN,
    PASSWORD,
    RELAXED_LIMITS,
    exchange,
    locked_settings,
)
from tests.web.htmltools import parse
from tests.web.support import Ui, jar_has, make_app

POINTER = "__Host-wb_session"


def locked(tmp_path: Path, factory: Any | None = None) -> TestClient:
    settings = locked_settings(tmp_path, rate_limits=RELAXED_LIMITS)
    app = create_app(settings, factory or PipelineFactory(QueuedProvider()))
    return TestClient(app, base_url="https://demo.example")


def signed_in(tmp_path: Path, factory: Any | None = None) -> TestClient:
    client = locked(tmp_path, factory)
    assert exchange(client).status_code == 204
    client.headers.update(ORIGIN)
    return client


# -- The gate -------------------------------------------------------------------------------------


def test_without_a_credential_the_page_is_the_gate_and_nothing_else(tmp_path: Path) -> None:
    client = locked(tmp_path)

    response = client.get("/")

    assert response.status_code == 200
    tree = parse(response.text)
    form = tree.find("form", id="gate-form")
    assert form is not None
    assert form.get("hx-post") == "/api/v1/access"
    # A form with no JavaScript must never put the password in a URL.
    assert form.get("method") == "post" and form.get("action") == "/api/v1/access"
    password = form.find("input", name="password")
    assert password is not None and password.get("type") == "password"
    assert tree.find("label", **{"for": "password"}) is not None
    assert tree.find(id="workspace") is None
    assert "set-cookie" not in response.headers
    # the demo notice, with the retention the server really enforces
    assert "Don't upload sensitive data." in response.text
    assert "idle for 24 hours" in response.text


def test_a_wrong_password_gets_the_apis_own_401_and_sets_no_cookie(tmp_path: Path) -> None:
    client = locked(tmp_path)

    response = exchange(client, "not the password")

    assert response.status_code == 401
    assert response.json() == {
        "error": {"code": "unauthorized", "message": "The demo password is missing or wrong."}
    }
    assert "set-cookie" not in response.headers
    assert parse(client.get("/").text).find("form", id="gate-form") is not None


def test_the_right_password_sets_the_access_cookie_and_nothing_else(tmp_path: Path) -> None:
    client = locked(tmp_path)

    response = exchange(client)

    assert response.status_code == 204
    assert [h for h in response.headers.get_list("set-cookie")] == [response.headers["set-cookie"]]
    assert response.headers["set-cookie"].startswith(COOKIE_NAME + "=")
    assert [c.name for c in client.cookies.jar] == [COOKIE_NAME]
    assert PASSWORD not in response.headers["set-cookie"]
    assert PASSWORD not in client.get("/").text


def test_with_the_cookie_the_page_is_the_workspace_shell(tmp_path: Path) -> None:
    client = signed_in(tmp_path)

    response = client.get("/")

    tree = parse(response.text)
    assert tree.find("form", id="gate-form") is None
    shell = tree.find("div", id="workspace")
    assert shell is not None
    assert shell.get("hx-post") == "/ui/sessions" and shell.get("hx-trigger") == "load"
    assert shell.get("data-session-id") is None
    assert tree.find(id="page-notices") is not None
    assert tree.find(id="live") is not None


def test_a_wrong_bearer_gets_the_gate_even_beside_a_valid_cookie(tmp_path: Path) -> None:
    client = signed_in(tmp_path)

    assert parse(client.get("/", headers={"Authorization": "Bearer nope"}).text).find(
        "form", id="gate-form"
    )
    assert parse(client.get("/", headers=BEARER).text).find("div", id="workspace") is not None


def test_the_html_routes_need_a_credential_and_the_origin_check(tmp_path: Path) -> None:
    client = locked(tmp_path)

    anonymous = client.post("/ui/sessions")
    assert anonymous.status_code == 401
    assert anonymous.json()["error"]["code"] == "unauthorized"
    assert client.get("/ui/s/s_x/transcript").status_code == 401

    assert exchange(client).status_code == 204
    foreign = client.post("/ui/sessions", headers={"Origin": "https://evil.example"})
    assert foreign.status_code == 403
    assert foreign.json()["error"]["code"] == "forbidden_origin"
    assert client.post("/ui/sessions").status_code == 403  # no Origin at all
    assert client.post("/ui/sessions", headers=ORIGIN).status_code == 200
    # a read needs no Origin
    assert client.get("/ui/s/s_unknown/transcript").status_code == 404


def test_the_page_carries_the_security_headers(tmp_path: Path) -> None:
    client = signed_in(tmp_path)

    for response in (client.get("/"), client.post("/ui/sessions")):
        assert "script-src 'self'" in response.headers["content-security-policy"]
        assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
        assert "style-src 'self'" in response.headers["content-security-policy"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert response.headers["strict-transport-security"]


# -- The session lifecycle -----------------------------------------------------------------------


def test_get_creates_nothing_and_post_sessions_sets_the_pointer(tmp_path: Path) -> None:
    client = signed_in(tmp_path)
    app: Any = client.app

    client.get("/")
    client.get("/")

    assert app.state.registry.live_keys() == []
    assert not list(app.state.settings.sessions_dir.iterdir())
    assert not jar_has(client, POINTER)

    response = client.post("/ui/sessions")

    assert response.status_code == 200
    header = response.headers["set-cookie"]
    assert header.startswith(POINTER + "=s_")
    attributes = {a.strip().lower() for a in header.split(";")[1:]}
    assert attributes == {"httponly", "path=/", "samesite=strict", "secure"}  # a session cookie
    assert len(app.state.registry.live_keys()) == 1
    tree = parse(response.text)
    workspace = tree.find("div", id="workspace")
    assert workspace is not None and workspace.get("data-session-id", "").startswith("s_")
    assert tree.find("li", **{"class": "turn"}) is None  # an empty workspace
    assert tree.find("section", id="files") is not None
    assert tree.find("form", id="composer") is not None


def test_a_pointer_to_a_live_session_resumes_it_with_no_notice(tmp_path: Path) -> None:
    factory = PipelineFactory(QueuedProvider(single("general_question")))
    client = signed_in(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    started = ui.start(sid, "what can you do?")
    assert started.status_code == 200

    page = parse(client.get("/").text)

    assert page.find("div", id="workspace").get("data-session-id") == sid  # type: ignore[union-attr]
    turn = page.find("li", **{"data-turn-id": "t0001"})
    assert turn is not None
    assert turn.find("p", **{"data-slot": "message"}).text() == "what can you do?"  # type: ignore[union-attr]
    assert page.find("span", **{"class": "file-name"}).text() == "report.xlsx"  # type: ignore[union-attr]
    assert "no longer exists" not in client.get("/").text


def test_a_pointer_to_a_missing_session_shows_the_notice_once_and_clears_the_pointer(
    tmp_path: Path,
) -> None:
    client = signed_in(tmp_path)
    ui = Ui(client)
    ui.new_session()
    assert jar_has(client, POINTER)
    # The server forgets every session: a restart on an ephemeral disk.
    app: Any = client.app
    app.state.registry._sessions.clear()
    for folder in app.state.settings.sessions_dir.iterdir():
        for leftover in sorted(folder.rglob("*"), reverse=True):
            leftover.unlink() if leftover.is_file() else leftover.rmdir()
        folder.rmdir()

    first = client.get("/")

    notice = "That session no longer exists on the server, so this is a new one."
    assert first.text.count(notice) == 1
    tree = parse(first.text)
    assert tree.find("div", id="workspace").get("hx-post") == "/ui/sessions"  # type: ignore[union-attr]
    assert tree.find(id="page-notices").find("p", **{"class": "notice"}) is not None  # type: ignore[union-attr]
    cleared = first.headers["set-cookie"]
    assert cleared.startswith(POINTER + "=") and "Max-Age=0" in cleared
    assert not jar_has(client, POINTER)

    second = client.get("/")

    assert notice not in second.text  # exactly once
    assert "set-cookie" not in second.headers


def test_a_malformed_pointer_is_treated_as_a_missing_session(tmp_path: Path) -> None:
    client = signed_in(tmp_path)
    client.cookies.set(POINTER, "../../etc/passwd", domain="demo.example")

    response = client.get("/")

    assert response.status_code == 200
    assert "no longer exists" in response.text


def test_fragment_routes_name_their_session_and_another_session_is_a_404(tmp_path: Path) -> None:
    client = signed_in(tmp_path, PipelineFactory(QueuedProvider(single("general_question"))))
    ui = Ui(client)
    sid = ui.new_session()

    for method, path in (
        ("get", "/ui/s/s_nope/transcript"),
        ("get", "/ui/s/s_nope/turns/t0001/poll"),
        ("post", "/ui/s/s_nope/turns"),
        ("post", "/ui/s/s_nope/files"),
        ("post", "/ui/s/s_nope/turns/t0001/confirmation"),
    ):
        extra: dict[str, Any] = {}
        if path.endswith("/files"):
            extra = {"files": {"file": ("a.xlsx", b"x")}}
        elif path.endswith("/turns"):
            extra = {"data": {"message": "hello"}}
        elif path.endswith("/confirmation"):
            extra = {"data": {"confirmation_id": "c_1", "decision": "approve"}}
        response = getattr(client, method)(path, **extra)
        assert response.status_code == 404, path
        assert response.json()["error"]["code"] == "session_not_found", path
    unknown_turn = client.get(f"/ui/s/{sid}/turns/t0099/poll")
    assert unknown_turn.status_code == 404
    assert unknown_turn.json()["error"]["code"] == "turn_not_found"


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_the_composers_mode_follows_the_last_turn(tmp_path: Path, mode: str) -> None:
    factory = PipelineFactory(QueuedProvider(single("general_question")))
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, "what can you do?", mode=mode)

    tree = parse(client.get(f"/ui/s/{sid}/transcript").text)

    checked = [r.get("value") for r in tree.find_all("input", type="radio") if "checked" in r.attrs]
    assert checked == [mode]
