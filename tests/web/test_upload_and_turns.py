"""Upload, the composer, and polling on the page's routes (frontend design 1.4 to 1.6; 10.1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from backend import routes as api_routes
from backend.errors import XLS_MESSAGE
from tests.backend.factories import BlockingFactory, PipelineFactory, QueuedProvider, single
from tests.backend.helpers import two_sheets, zip_bytes
from tests.web.htmltools import parse
from tests.web.support import (
    DEDUPE,
    FakeWebClock,
    Ui,
    make_app,
    reply_of,
    wait_for_container,
)

QUESTION = "what can you do?"


def general_factory() -> PipelineFactory:
    return PipelineFactory(QueuedProvider(single("general_question")))


# -- Upload ---------------------------------------------------------------------------------------


def test_an_upload_re_renders_the_list_with_the_servers_name_and_names_the_next_file_once(
    tmp_path: Path,
) -> None:
    _, client = make_app(tmp_path, general_factory())
    ui = Ui(client)
    sid = ui.new_session()

    first = ui.upload(sid, name="../evil/Q3 report?.xlsx")
    second = ui.upload(sid, name="second.xlsx")

    assert first.status_code == 200
    tree = parse(first.text)
    panel = tree.find("section", id="files")
    assert panel is not None and panel.get("hx-swap-oob") is None  # the response itself
    names = [e.text() for e in panel.find_all("span", **{"class": "file-name"})]
    assert names == ["Q3 report_.xlsx"]  # the server's sanitized name, not the browser's
    # One statement of which file is current, never two: the list has no "active" tag.
    assert panel.find("span", **{"class": "tag"}) is None
    assert "Your next request will use Q3 report_.xlsx, your upload." in panel.find(  # type: ignore[union-attr]
        "p", id="next-file"
    ).text()
    form = panel.find("form", id="upload-form")
    assert form is not None and form.find("input", type="file").get("accept") == ".xlsx,.pdf"  # type: ignore[union-attr]
    assert "Accepts .xlsx, .pdf up to 2 MB." in first.text
    after = parse(second.text)
    rows = after.find_all(
        "li", where=lambda e: e.find("span", **{"class": "file-name"}) is not None
    )
    assert [r.find("span", **{"class": "file-name"}).text() for r in rows] == [  # type: ignore[union-attr]
        "Q3 report_.xlsx",
        "second.xlsx",
    ]
    assert not [r for r in rows if r.find("span", **{"class": "tag"}) is not None]
    assert "Your next request will use second.xlsx, your upload." in after.find(  # type: ignore[union-attr]
        "p", id="next-file"
    ).text()


@pytest.mark.parametrize(
    ("name", "data", "status", "code", "message"),
    [
        (
            "notes.txt",
            b"hello",
            415,
            "unsupported_file_type",
            "Only .xlsx and .pdf files can be uploaded.",
        ),
        ("old.xls", b"\xd0\xcf\x11\xe0", 415, "unsupported_file_type", XLS_MESSAGE),
        (
            "broken.xlsx",
            b"not a workbook",
            422,
            "invalid_file",
            "That file is empty or isn't a valid .xlsx workbook or PDF.",
        ),
        (
            "two.xlsx",
            two_sheets(),
            422,
            "multiple_sheets",
            "This demo works on one sheet at a time, and this workbook has more than one. "
            "Save the sheet you want in its own workbook and upload that.",
        ),
        ("empty.xlsx", zip_bytes({"[Content_Types].xml": b"<x/>"}), 422, "invalid_file", None),
    ],
)
def test_a_refused_upload_gives_the_apis_message_and_leaves_the_list_alone(
    tmp_path: Path, name: str, data: bytes, status: int, code: str, message: str | None
) -> None:
    _, client = make_app(tmp_path, general_factory())
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid, name="keep.xlsx")
    before = client.get(f"/ui/s/{sid}/transcript").text

    response = ui.upload(sid, data, name)

    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    if message is not None:
        assert body["error"]["message"] == message
    assert client.get(f"/ui/s/{sid}/transcript").text == before


def test_an_oversized_upload_is_413_with_the_apis_own_message_on_the_pages_route(
    tmp_path: Path,
) -> None:
    _, client = make_app(tmp_path, general_factory(), max_upload_bytes=1024)
    ui = Ui(client)
    sid = ui.new_session()

    response = ui.upload(sid, b"x" * (200 * 1024), "big.xlsx")

    assert response.status_code == 413
    assert response.json()["error"] == {
        "code": "file_too_large",
        "message": "That file is larger than 1 KB.",
    }


def test_the_upload_control_is_closed_while_a_turn_is_unfinished_and_the_server_says_409(
    tmp_path: Path,
) -> None:
    factory = BlockingFactory()
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    try:
        started = ui.start(sid, QUESTION)
        assert started.status_code == 200
        assert factory.started.wait(10)

        page = parse(client.get(f"/ui/s/{sid}/transcript").text)
        control = page.find("section", id="files").find("input", type="file")  # type: ignore[union-attr]
        button = (
            page.find("section", id="files")
            .find("form", id="upload-form")
            .find("button")  # type: ignore[union-attr]
        )
        assert "disabled" in control.attrs  # type: ignore[union-attr]
        assert "disabled" in button.attrs  # type: ignore[union-attr]
        refused = ui.upload(sid, name="more.xlsx")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "session_busy"
    finally:
        factory.release.set()


# -- Starting a turn ------------------------------------------------------------------------------


def test_a_new_turn_is_a_running_container_with_a_poll_and_nothing_that_looks_like_a_result(
    tmp_path: Path,
) -> None:
    factory = BlockingFactory()
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    try:
        response = ui.start(sid, "  remove duplicate rows\nplease  ", mode="focus")

        assert response.status_code == 200
        tree = parse(response.text)
        turn = tree.find("li", **{"data-turn-id": "t0001"})
        assert turn is not None and turn.get("id") == "turn-t0001"
        assert turn.get("data-session-id") == sid and turn.get("data-version") == "0"
        assert turn.find("p", **{"data-slot": "message"}).text() == "remove duplicate rows\nplease"  # type: ignore[union-attr]
        poll = turn.find("div", **{"class": "poll"})
        assert poll is not None
        assert poll.get("hx-get") == f"/ui/s/{sid}/turns/t0001/poll?v=0"
        assert poll.get("hx-trigger") == "load delay:2s, wb:retry"
        assert poll.get("hx-sync") == "closest .turn:drop"
        assert "Buddy is working" in turn.text()
        assert turn.find("div", **{"data-slot": "reply"}) is None
        assert turn.find("ul", **{"data-slot": "downloads"}) is None
        # out of band: the composer and upload are closed, the files panel and live region follow
        composer = tree.find("form", id="composer")
        assert composer is not None and composer.get("hx-swap-oob") == "true"
        assert "disabled" in composer.find("textarea").attrs  # type: ignore[union-attr]
        assert "disabled" in composer.find("button", type="submit").attrs  # type: ignore[union-attr]
        assert [
            r.get("value") for r in composer.find_all("input", type="radio") if "checked" in r.attrs
        ] == ["focus"]
        assert composer.find("input", name="last_seq").get("value") == "1"  # type: ignore[union-attr]
        files = tree.find("section", id="files")
        assert files is not None and files.get("hx-swap-oob") == "true"
        live = tree.find("div", **{"hx-swap-oob": "innerHTML:#live"})
        assert live is not None and live.text() == "Buddy is working"
        assert "<title>WorkBuddy demo</title>" in response.text
    finally:
        factory.release.set()


def test_a_refused_message_creates_no_turn(tmp_path: Path) -> None:
    _, client = make_app(tmp_path, general_factory())
    ui = Ui(client)
    sid = ui.new_session()

    empty = ui.start(sid, "   ")
    too_long = ui.start(sid, "x" * 4001)
    bad_mode = client.post(f"/ui/s/{sid}/turns", data={"message": "hi", "mode": "loud"})

    for response in (empty, too_long, bad_mode):
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
    assert parse(client.get(f"/ui/s/{sid}/transcript").text).find("li", **{"class": "turn"}) is None


def test_a_second_message_while_a_turn_runs_is_409(tmp_path: Path) -> None:
    factory = BlockingFactory()
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    try:
        assert ui.start(sid, QUESTION).status_code == 200
        refused = ui.start(sid, "again")
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "session_busy"
        tree = parse(client.get(f"/ui/s/{sid}/transcript").text)
        assert len(tree.find_all("li", **{"class": "turn"})) == 1
    finally:
        factory.release.set()


# -- Polling --------------------------------------------------------------------------------------


def test_a_poll_with_no_change_is_a_rearmed_poll_element_at_2_s_then_3_s_by_the_servers_clock(
    tmp_path: Path,
) -> None:
    factory = BlockingFactory()
    app, client = make_app(tmp_path, factory)
    clock = FakeWebClock()
    app.state.web_clock = clock
    ui = Ui(client)
    sid = ui.new_session()
    try:
        ui.start(sid, QUESTION)
        started_at = parse(ui.poll(sid, "t0001", v=0).text)

        young = started_at.find("div", **{"class": "poll"})
        assert young is not None and young.get("hx-trigger") == "load delay:2s, wb:retry"
        assert young.get("hx-get") == f"/ui/s/{sid}/turns/t0001/poll?v=0"
        assert started_at.find("li") is None  # only the poll element, nothing else
        clock.advance(19)
        assert "delay:2s" in ui.poll(sid, "t0001", v=0).text
        clock.advance(2)
        old = parse(ui.poll(sid, "t0001", v=0).text).find("div", **{"class": "poll"})
        assert old is not None and old.get("hx-trigger") == "load delay:3s, wb:retry"
    finally:
        factory.release.set()


def test_a_status_change_returns_the_whole_container_with_a_higher_version_and_retarget(
    tmp_path: Path,
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    ui.start(sid, DEDUPE)
    container = wait_for_container(
        ui, sid, "t0001", lambda c: c.find("section", **{"class": "plan"}) is not None
    )

    changed = ui.poll(sid, "t0001", v=0)

    assert changed.headers["hx-retarget"] == "#turn-t0001"
    assert changed.headers["hx-reswap"] == "outerHTML"
    tree = parse(changed.text)
    turn = tree.find("li", **{"data-turn-id": "t0001"})
    assert turn is not None and int(turn.get("data-version") or 0) > 0
    assert turn.get("data-version") == container.get("data-version")
    assert "<title>Approval needed – WorkBuddy</title>" in changed.text
    live = tree.find("div", **{"hx-swap-oob": "innerHTML:#live"})
    assert live is not None and live.text() == "A plan needs your approval"
    # the same version again: no change
    assert (
        parse(ui.poll(sid, "t0001", v=int(turn.get("data-version") or 0)).text).find("li") is None
    )


def test_a_finished_turn_is_a_container_with_no_poll_and_the_composer_takes_focus(
    tmp_path: Path,
) -> None:
    _, client = make_app(tmp_path, general_factory())
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, QUESTION)
    container = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")

    assert container.find("div", **{"class": "poll"}) is None
    final = ui.poll(sid, "t0001", v=0)
    tree = parse(final.text)
    assert tree.find("div", **{"class": "poll"}) is None
    live = tree.find("div", **{"hx-swap-oob": "innerHTML:#live"})
    assert live is not None and live.text() == reply_of(container)
    composer = tree.find("form", id="composer")
    assert composer is not None
    box = composer.find("textarea")
    assert box is not None and "disabled" not in box.attrs and "autofocus" in box.attrs
    assert "<title>WorkBuddy demo</title>" in final.text
    # polling a finished turn again, with its own version, still gives the finished container
    again = ui.poll(sid, "t0001", v=int(container.get("data-version") or 0))
    assert parse(again.text).find("div", **{"data-slot": "reply"}) is not None


def test_the_transcript_route_gives_the_turns_after_a_seq(tmp_path: Path) -> None:
    provider = QueuedProvider(single("general_question") + single("general_question"))
    _, client = make_app(tmp_path, PipelineFactory(provider))
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, "first question")
    wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")
    ui.start(sid, "second question", last_seq=1)
    wait_for_container(ui, sid, "t0002", lambda c: c.get("data-status") == "finished")

    everything = parse(client.get(f"/ui/s/{sid}/transcript").text)
    after_one = parse(client.get(f"/ui/s/{sid}/transcript", params={"after": 1}).text)
    after_two = parse(client.get(f"/ui/s/{sid}/transcript", params={"after": 2}).text)

    def messages(tree: Any) -> list[str]:
        return [e.text() for e in tree.find_all("p", **{"data-slot": "message"})]

    assert messages(everything) == ["first question", "second question"]
    assert messages(after_one) == ["second question"]
    assert messages(after_two) == []
    assert everything.find("section", id="conversation") is not None
    assert after_one.find("ol", id="transcript-check") is not None
    assert everything.find("form", id="composer").find("input", name="last_seq").get("value") == "2"  # type: ignore[union-attr]


# -- A server that restarted under a page (review M1 of frontend-demo) ---------------------------


def test_a_poll_from_a_page_that_outlived_a_restart_is_an_empty_reload_instruction(
    tmp_path: Path,
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    ui.start(sid, DEDUPE)
    container = wait_for_container(
        ui, sid, "t0001", lambda c: c.find("section", **{"class": "plan"}) is not None
    )
    held = int(container.get("data-version") or 0)
    assert held > 0  # the plan card is on a version after the first
    # The same data root under a new process: versions are memory-only, so they count from 0.
    _, restarted_client = make_app(tmp_path, PipelineFactory(QueuedProvider([])))
    restarted = Ui(restarted_client)
    assert restarted.version(sid, "t0001") < held

    answer = restarted.poll(sid, "t0001", v=held)

    assert answer.status_code == 200
    assert answer.headers["hx-refresh"] == "true"
    assert answer.text == ""  # nothing to swap in: the page reloads
    assert "hx-retarget" not in answer.headers
    # A reload renders the session as this server has it, and its poll element holds that version.
    for cookie in client.cookies.jar:  # the browser's cookies survive the server's restart
        restarted_client.cookies.jar.set_cookie(cookie)
    page = restarted_client.get("/")
    assert page.status_code == 200 and 'data-turn-id="t0001"' in page.text
    assert "hx-refresh" not in page.headers
    assert restarted.version(sid, "t0001") < held
    for v in (None, restarted.version(sid, "t0001")):  # a page that holds the server's own version
        normal = restarted.poll(sid, "t0001", v=v)
        assert "hx-refresh" not in normal.headers
        assert 'data-turn-id="t0001"' in normal.text
    # the page never asks to reload for a version the server has reached itself
    assert "hx-refresh" not in ui.poll(sid, "t0001", v=held).headers
    assert "hx-refresh" not in ui.poll(sid, "t0001", v=0).headers


def test_the_files_panel_leaves_out_what_the_turn_being_rendered_as_running_has_delivered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    app, client = make_app(tmp_path, factory, auto_approve=True)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    real = api_routes.start_turn

    def finished_before_the_response(request: Any, session_id: str, body: Any) -> Any:
        started = real(request, session_id, body)
        wait_for_container(
            ui, session_id, started.turn_id, lambda c: c.get("data-status") == "finished"
        )
        return started  # the turn as it started, though the server has now finished it

    monkeypatch.setattr(api_routes, "start_turn", finished_before_the_response)

    response = ui.start(sid, DEDUPE)

    tree = parse(response.text)
    turn = tree.find("li", **{"data-turn-id": "t0001"})
    assert turn is not None and turn.get("data-status") == "running"
    assert turn.find("div", **{"data-slot": "reply"}) is None
    panel = tree.find("section", id="files")
    assert panel is not None
    assert panel.find("a") is None  # the running turn's file isn't offered yet
    assert "Files from Buddy" not in panel.text()
    # the poll then brings the page up to date: the reply, the download and the panel agree
    done = ui.poll(sid, "t0001", v=0)
    final = parse(done.text)
    assert final.find("div", **{"data-slot": "reply"}) is not None
    assert final.find("section", id="files").find("a") is not None  # type: ignore[union-attr]


def test_a_refetch_that_names_the_version_the_page_holds_gets_the_reload_after_a_restart(
    tmp_path: Path,
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    ui.start(sid, DEDUPE)
    container = wait_for_container(
        ui, sid, "t0001", lambda c: c.find("section", **{"class": "plan"}) is not None
    )
    held = int(container.get("data-version") or 0)
    _, restarted_client = make_app(tmp_path, PipelineFactory(QueuedProvider([])))
    restarted = Ui(restarted_client)
    server_version = restarted.version(sid, "t0001")
    assert server_version < held

    for params in ({"held": str(held)}, {"held": str(held), "unconfirmed": "c_x"}):
        refetch = restarted.poll(sid, "t0001", **params)  # the page's 409 refetch, and its lookup
        assert refetch.headers["hx-refresh"] == "true" and refetch.text == "", params
    # a page that holds the server's own version, or none, gets the turn as it is
    for params in ({"held": str(server_version)}, {"held": "0"}, {}):
        whole = restarted.poll(sid, "t0001", **params)
        assert "hx-refresh" not in whole.headers, params
        assert 'data-turn-id="t0001"' in whole.text, params
        assert whole.headers["hx-retarget"] == "#turn-t0001"  # the whole container, not a poll
    # and ``v`` still takes precedence over ``held``, as before
    assert restarted.poll(sid, "t0001", v=held, held="0").headers["hx-refresh"] == "true"
