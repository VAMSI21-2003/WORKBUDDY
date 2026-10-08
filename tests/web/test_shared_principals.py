"""The shared principals (the demo password, a dev server, the operator) are never taken to
"their" session: it would be somebody else's (review round 1, H1).

Outside production, everyone who holds the demo password is the same owner. What keeps their
sessions apart is that a session id is a secret, so the page must never hand one out.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from backend.identity import DEMO, DEV, OPERATOR, Principal
from backend.main import create_app
from backend.settings import Settings
from tests.backend.factories import PipelineFactory, QueuedProvider
from tests.backend.helpers import (
    ORIGIN,
    RELAXED_LIMITS,
    exchange,
    locked_settings,
    new_session,
    upload,
    xlsx_bytes,
)

SECRET_NAME = "payroll_private.xlsx"


def _app(settings: Settings) -> object:
    return create_app(settings, PipelineFactory(QueuedProvider([])))


def _holder_of_the_password(app: object) -> TestClient:
    client = TestClient(app, base_url="https://testserver", headers=ORIGIN)  # type: ignore[arg-type]
    assert exchange(client).status_code == 204
    return client


def test_a_second_holder_of_the_demo_password_is_not_put_in_the_first_ones_session(
    tmp_path: Path,
) -> None:
    app = _app(locked_settings(tmp_path, rate_limits=RELAXED_LIMITS))
    first = _holder_of_the_password(app)
    sid = new_session(first)
    assert upload(first, sid, xlsx_bytes(), SECRET_NAME).status_code == 201

    second = _holder_of_the_password(app)
    page = second.get("/")

    assert page.status_code == 200
    assert SECRET_NAME not in page.text and sid not in page.text
    assert "__Host-wb_session" not in page.headers.get("set-cookie", "")
    assert 'hx-post="/ui/sessions"' in page.text  # an empty workspace, made on load
    # The first holder still has theirs.
    first.cookies.set("__Host-wb_session", sid)
    assert SECRET_NAME in first.get("/").text


def test_two_browsers_on_a_dev_server_do_not_share_a_session(tmp_path: Path) -> None:
    app = _app(Settings(data_dir=tmp_path / "d", env="dev", rate_limits=RELAXED_LIMITS))
    one = TestClient(app, base_url="https://testserver")  # type: ignore[arg-type]
    sid = new_session(one)
    upload(one, sid, xlsx_bytes(), SECRET_NAME)

    two = TestClient(app, base_url="https://testserver")  # type: ignore[arg-type]
    page = two.get("/")

    assert SECRET_NAME not in page.text and sid not in page.text
    assert "__Host-wb_session" not in page.headers.get("set-cookie", "")


def test_a_script_with_the_operator_token_is_not_put_in_an_earlier_scripts_session(
    tmp_path: Path,
) -> None:
    from tests.backend.helpers import OPERATOR_TOKEN, production_settings

    app = create_app(
        production_settings(tmp_path, rate_limits=RELAXED_LIMITS),
        PipelineFactory(QueuedProvider([])),
    )
    headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    script = TestClient(app, base_url="https://demo.example")  # type: ignore[arg-type]
    sid = script.post("/api/v1/sessions", json={}, headers=headers).json()["session_id"]

    page = script.get("/", headers=headers)

    assert sid not in page.text and "__Host-wb_session" not in page.headers.get("set-cookie", "")


def test_the_registry_has_no_latest_session_for_a_shared_owner(tmp_path: Path) -> None:
    app = _app(locked_settings(tmp_path, rate_limits=RELAXED_LIMITS))
    first = _holder_of_the_password(app)
    new_session(first)
    registry = app.state.registry  # type: ignore[attr-defined]

    assert registry.get(new_session(first), DEMO) is not None  # they do own sessions
    for shared in (DEMO, DEV, OPERATOR):
        assert registry.latest_for(shared.owner) is None
    assert registry.latest_for(Principal.google("sub-nobody", "n@example.test").owner) is None
