"""Every error the server decides on the page's routes, the rate-limit bucket each route is charged
to, and the route declarations (frontend design 1.10, 10.1; backend "Decisions (2026-10-01)", 3)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

import backend.routes as api
from backend import schemas
from backend.main import create_app
from backend.ratelimit import check_route_declarations
from backend.settings import Settings
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    BlockingFactory,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.backend.helpers import RELAXED_LIMITS, FakeClock, locked_settings, xlsx_bytes
from tests.web.htmltools import parse
from tests.web.support import (
    CHAIN,
    FakeWebClock,
    Ui,
    make_app,
    pending_id,
    wait_for_container,
)

IP = "testclient"


def assert_api_error(response: Any, status: int, code: str, message: str | None = None) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"].startswith("application/json")
    body = response.json()
    assert set(body) == {"error"} and set(body["error"]) == {"code", "message"}
    assert body["error"]["code"] == code
    if message is not None:
        assert body["error"]["message"] == message


def plan_session(tmp_path: Path, **overrides: Any) -> tuple[Any, Ui, str]:
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    app, client = make_app(tmp_path, factory, **overrides)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    return app, ui, sid


# -- The errors the server decides, as the page's script will see them -------------------------


def test_a_confirmation_answer_with_no_plan_waiting_is_409_no_pending_confirmation(
    tmp_path: Path,
) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider(single("general_question"))))
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, "what can you do?")
    wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")

    assert_api_error(
        ui.answer(sid, "t0001", "c_1", "approve"),
        409,
        "no_pending_confirmation",
        "This turn has no plan waiting for an answer.",
    )


def test_a_stale_confirmation_id_is_409_and_the_plan_keeps_waiting(tmp_path: Path) -> None:
    _, ui, sid = plan_session(tmp_path)
    _, container = ui.run_to_plan(sid)

    assert_api_error(
        ui.answer(sid, "t0001", "c_not_the_one", "approve"),
        409,
        "stale_confirmation",
        "That answer is for a plan that isn't the one waiting.",
    )

    assert wait_for_container(
        ui, sid, "t0001", lambda c: c.find("section", **{"class": "plan"}) is not None
    )
    assert pending_id(container)


@pytest.mark.parametrize(
    ("data", "path"),
    [
        ({"confirmation_id": "c_1", "decision": "maybe"}, "confirmation"),
        ({"confirmation_id": "", "decision": "approve"}, "confirmation"),
        ({"decision": "approve"}, "confirmation"),
        ({"confirmation_id": "has spaces!", "decision": "approve"}, "confirmation"),
    ],
)
def test_an_invalid_answer_is_422_invalid_request(
    tmp_path: Path, data: dict[str, str], path: str
) -> None:
    _, ui, sid = plan_session(tmp_path)
    ui.run_to_plan(sid)

    response = ui.client.post(f"/ui/s/{sid}/turns/t0001/{path}", data=data)

    assert_api_error(response, 422, "invalid_request", "The request isn't valid.")


def test_every_html_route_refuses_a_request_with_no_credential_the_same_way(
    tmp_path: Path,
) -> None:
    settings = locked_settings(tmp_path)
    client = TestClient(
        create_app(settings, PipelineFactory(QueuedProvider())), base_url="https://demo.example"
    )

    for method, path in (
        ("post", "/ui/sessions"),
        ("get", "/ui/s/s_x/transcript"),
        ("get", "/ui/s/s_x/turns/t0001/poll"),
        ("post", "/ui/s/s_x/turns"),
        ("post", "/ui/s/s_x/files"),
        ("post", "/ui/s/s_x/turns/t0001/confirmation"),
    ):
        response = getattr(client, method)(path)
        assert_api_error(
            response, 401, "unauthorized", "You aren't signed in, or your sign-in has ended."
        )
        assert response.headers["www-authenticate"] == 'Bearer realm="workbuddy-demo"'


def test_a_wrong_origin_on_the_gate_is_403_forbidden_origin(tmp_path: Path) -> None:
    client = TestClient(
        create_app(locked_settings(tmp_path), PipelineFactory(QueuedProvider())),
        base_url="https://demo.example",
    )

    response = client.post(
        "/api/v1/access", data={"password": "x"}, headers={"Origin": "https://evil.example"}
    )

    assert_api_error(
        response, 403, "forbidden_origin", "This request didn't come from the demo page."
    )


def test_a_session_at_its_turn_or_upload_limit_gets_the_apis_message(tmp_path: Path) -> None:
    factory = PipelineFactory(QueuedProvider(single("general_question") * 2))
    _, client = make_app(tmp_path, factory, max_turns_per_session=1, max_uploads_per_session=1)
    ui = Ui(client)
    sid = ui.new_session()
    assert ui.upload(sid).status_code == 200
    ui.start(sid, "what can you do?")
    wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")

    turn = ui.start(sid, "again", last_seq=1)
    upload = ui.upload(sid, name="second.xlsx")

    assert_api_error(turn, 403, "session_quota_exceeded")
    assert "1 turns" in turn.json()["error"]["message"] and "Start a new session" in turn.text
    assert_api_error(upload, 403, "session_quota_exceeded")


def test_a_full_server_is_503_storage_full_for_a_new_session_and_an_upload(tmp_path: Path) -> None:
    clock = FakeClock()
    settings = Settings(
        data_dir=tmp_path / "data", rate_limits=RELAXED_LIMITS, max_total_bytes=3000
    )
    app = create_app(settings, PipelineFactory(QueuedProvider()), clock=clock)
    ui = Ui(TestClient(app, base_url="https://testserver"))
    first = ui.new_session()
    assert ui.upload(first, xlsx_bytes()).status_code == 200  # 5 KB: over the 3000-byte cap now
    clock.advance(3)  # the server's byte count is cached for 2 s

    full = ui.client.post("/ui/sessions")

    assert_api_error(
        full,
        503,
        "storage_full",
        "The demo has no room for new files right now. Try again later.",
    )
    assert_api_error(ui.upload(first, name="again.xlsx"), 503, "storage_full")


def test_a_busy_server_is_429_server_busy_with_retry_after_and_creates_no_turn(
    tmp_path: Path,
) -> None:
    factory = BlockingFactory()
    _, client = make_app(tmp_path, factory, max_concurrent_turns=1, busy_retry_after_s=7)
    ui = Ui(client)
    first = ui.new_session()
    second = ui.new_session()
    try:
        assert ui.start(first, "what can you do?").status_code == 200

        refused = ui.start(second, "and me?")

        assert_api_error(
            refused, 429, "server_busy", "The server is busy right now. Try again shortly."
        )
        assert refused.headers["retry-after"] == "7"
        assert 'class="turn"' not in client.get(f"/ui/s/{second}/transcript").text
    finally:
        factory.release.set()


def test_a_finished_turn_that_lacks_a_field_makes_the_page_route_a_500_with_the_fixed_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider(single("general_question"))))
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, "what can you do?")
    wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")
    real = api.get_turn

    def broken(request: Any, session_id: str, turn_id: str) -> Any:
        turn = real(request, session_id, turn_id)
        return schemas.FinishedTurn.model_construct(**{**turn.__dict__, "reply_source": None})

    monkeypatch.setattr(api, "get_turn", broken)

    response = ui.poll(sid, "t0001", v=0)

    assert_api_error(response, 500, "internal_error", "Something went wrong on the server.")


def test_a_download_of_a_file_that_changed_or_is_gone_is_410_and_an_upload_is_404(
    tmp_path: Path,
) -> None:
    app, ui, sid = plan_session(tmp_path)
    _, container = ui.run_to_plan(sid)
    ui.answer(sid, "t0001", pending_id(container), "approve")
    final = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")
    link = final.find("a", **{"class": "download"})
    assert link is not None
    href = link.get("href") or ""
    assert ui.client.get(href).status_code == 200

    delivered_name = ui.api_turn(sid, "t0001")["delivered_files"][0]["name"]
    path = next(app.state.settings.outputs_dir.rglob(delivered_name))  # not step 1's file
    path.chmod(0o644)
    path.write_bytes(b"changed")

    assert_api_error(
        ui.client.get(href), 410, "file_unavailable", "That file is no longer available."
    )
    unknown = ui.client.get(f"/api/v1/sessions/{sid}/files/f_nope/download")
    assert_api_error(unknown, 404, "file_not_found")


# -- Rate limits: each route is charged to the buckets the design maps it to --------------------


def limited_app(tmp_path: Path, factory: Any) -> tuple[Any, Ui, FakeClock]:
    clock = FakeClock()
    settings = Settings(data_dir=tmp_path / "data")  # the production limits, not relaxed ones
    app = create_app(settings, factory, clock=clock)
    ui = Ui(TestClient(app, base_url="https://testserver"))
    return app, ui, clock


def tokens(app: Any) -> dict[str, float]:
    return {
        b: app.state.limiter.tokens(IP, b)
        for b in ("general", "poll", "turns", "uploads", "access", "page")
    }


def spent(app: Any, before: dict[str, float]) -> dict[str, int]:
    after = tokens(app)
    return {b: round(before[b] - after[b]) for b in after if round(before[b] - after[b])}


def test_each_page_route_is_charged_to_the_bucket_the_design_maps_it_to(tmp_path: Path) -> None:
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    app, ui, _ = limited_app(tmp_path, factory)
    client = ui.client

    before = tokens(app)
    client.get("/")
    assert spent(app, before) == {"page": 1}  # the page doesn't touch general

    before = tokens(app)
    client.get("/static/app.js")
    assert spent(app, before) == {"page": 1}

    before = tokens(app)
    sid = ui.new_session()
    assert spent(app, before) == {"general": 1}

    before = tokens(app)
    assert ui.upload(sid).status_code == 200
    assert spent(app, before) == {"general": 1, "uploads": 1}

    before = tokens(app)
    assert ui.start(sid, CHAIN).status_code == 200
    assert spent(app, before) == {"general": 1, "turns": 1}

    before = tokens(app)
    waited = ui.poll(sid, "t0001", v=0)
    assert waited.status_code == 200
    assert spent(app, before) == {"poll": 1}

    before = tokens(app)
    assert client.get(f"/ui/s/{sid}/transcript").status_code == 200
    assert spent(app, before) == {"general": 1}

    container = wait_for_container(
        ui, sid, "t0001", lambda c: c.find("section", **{"class": "plan"}) is not None
    )
    before = tokens(app)
    assert ui.answer(sid, "t0001", pending_id(container), "approve").status_code == 200
    assert spent(app, before) == {"general": 1}

    before = tokens(app)
    assert ui.poll(sid, "t0099", v=0).status_code == 404  # a poll that isn't a 200 costs more
    assert spent(app, before) == {"poll": 1, "general": 1}


def test_a_page_request_with_no_credential_is_charged_double_and_never_to_poll(
    tmp_path: Path,
) -> None:
    settings = locked_settings(tmp_path)
    clock = FakeClock()
    app = create_app(settings, PipelineFactory(QueuedProvider()), clock=clock)
    client = TestClient(app, base_url="https://demo.example")

    before = tokens(app)
    client.get("/ui/s/s_x/turns/t0001/poll")
    assert spent(app, before) == {"general": 2}
    before = tokens(app)
    assert client.get("/").status_code == 200  # the gate costs the page bucket only
    assert spent(app, before) == {"page": 1}


def test_a_refused_page_request_is_429_with_retry_after_and_takes_nothing(tmp_path: Path) -> None:
    factory = PipelineFactory(QueuedProvider(single("general_question") * 12))
    app, ui, clock = limited_app(tmp_path, factory)
    sid = ui.new_session()
    for number in range(10):
        started = ui.start(sid, f"question {number}", last_seq=number)
        assert started.status_code == 200
        wait_for_container(
            ui, sid, f"t{number + 1:04d}", lambda c: c.get("data-status") == "finished"
        )

    refused = ui.start(sid, "one too many", last_seq=10)

    assert_api_error(
        refused, 429, "rate_limited", "Too many requests. Wait a little and try again."
    )
    wait = int(refused.headers["retry-after"])
    assert wait >= 1
    assert "one too many" not in ui.client.get(f"/ui/s/{sid}/transcript").text
    clock.advance(wait)
    assert ui.start(sid, "now it works", last_seq=10).status_code == 200


def test_a_full_turn_at_the_pages_cadence_for_300_seconds_is_never_rate_limited(
    tmp_path: Path,
) -> None:
    """Backend "Decisions (2026-10-01)" item 3: the page's own cadence stays inside the buckets."""
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    app, ui, clock = limited_app(tmp_path, factory)
    web = FakeWebClock()
    app.state.web_clock = web
    client = ui.client
    assert client.get("/").status_code == 200  # a page load, with its static files
    for asset in ("htmx.min.js", "app.js", "styles.css"):
        assert client.get(f"/static/{asset}").status_code == 200
    sid = ui.new_session()
    assert ui.upload(sid).status_code == 200
    assert ui.start(sid, CHAIN).status_code == 200

    held, polls, elapsed = 0, 0, 0.0
    plan = None
    while elapsed < 300:
        response = ui.poll(sid, "t0001", v=held)
        assert response.status_code == 200, (elapsed, response.text)
        polls += 1
        tree = parse(response.text)
        container = tree.find("li", **{"data-turn-id": "t0001"})
        if container is not None:
            held = int(container.get("data-version") or 0)
            plan = plan or (pending_id(container) if container.find("form") else None)
        age_delay = 2 if elapsed < 20 else 3
        elapsed += age_delay
        clock.advance(age_delay)
        web.advance(age_delay)
    assert plan is not None and polls >= 100
    assert ui.answer(sid, "t0001", plan, "approve").status_code == 200
    final = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")
    link = final.find("a", **{"class": "download"})
    assert link is not None and client.get(link.get("href") or "").status_code == 200
    assert client.get(f"/ui/s/{sid}/transcript").status_code == 200


# -- Declarations -------------------------------------------------------------------------------


def test_every_page_route_declares_its_buckets_and_none_is_in_the_openapi_file(
    tmp_path: Path,
) -> None:
    app, _ = make_app(tmp_path, PipelineFactory(QueuedProvider()))
    check_route_declarations(app)  # raises if one lacks its declaration

    schema = json.dumps(app.openapi())
    assert "/ui/s/{session_id}/turns" not in schema and '"/ui' not in schema
    assert '"/"' not in schema and "/static" not in schema


def test_a_page_route_added_without_a_declaration_stops_the_app(tmp_path: Path) -> None:
    app, _ = make_app(tmp_path, PipelineFactory(QueuedProvider()))

    @app.get("/ui/extra")
    def extra() -> dict[str, str]:  # no limits(...) declaration
        return {}

    with pytest.raises(RuntimeError, match="doesn't declare its rate-limit buckets"):
        check_route_declarations(app)
