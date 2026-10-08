"""The once-per-turn plan approval card on the page's routes (frontend design 1.7; 10.1)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from backend.confirmation import PLAN_NOTE_CHAIN
from backend.identity import DEV
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.web.htmltools import Element, parse
from tests.web.support import (
    DEDUPE,
    FakeWebClock,
    Ui,
    make_app,
    pending_id,
    reply_of,
    wait_for_container,
)


def chain_session(tmp_path: Path) -> tuple[Any, Ui, str]:
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    app, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)
    return app, ui, sid


def card_of(container: Element) -> Element:
    card = container.find("section", **{"class": "plan"})
    assert card is not None
    return card


def test_the_steps_appear_in_order_verbatim_in_a_list_with_the_servers_plan_note(
    tmp_path: Path,
) -> None:
    _, ui, sid = chain_session(tmp_path)

    _, container = ui.run_to_plan(sid)

    card = card_of(container)
    steps = card.find("ol")
    assert steps is not None
    assert [li.text() for li in steps.find_all("li")] == [
        "Remove duplicate rows from report.xlsx.",
        "Then remove empty rows from the result of step 1.",
    ]
    note = card.find("p", **{"class": "plan-note"})
    assert note is not None and note.text() == PLAN_NOTE_CHAIN
    assert card.find("h3").text() == "Plan for your approval"  # type: ignore[union-attr]


def test_a_single_step_plan_has_no_note_at_all(tmp_path: Path) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    _, client = make_app(tmp_path, factory)
    ui = Ui(client)
    sid = ui.new_session()
    ui.upload(sid)

    _, container = ui.run_to_plan(sid, DEDUPE)

    card = card_of(container)
    assert [li.text() for li in card.find("ol").find_all("li")] == [  # type: ignore[union-attr]
        "Remove duplicate rows from report.xlsx."
    ]
    assert card.find("p", **{"class": "plan-note"}) is None


def test_both_buttons_are_equal_the_heading_takes_focus_and_the_form_guards_the_click(
    tmp_path: Path,
) -> None:
    _, ui, sid = chain_session(tmp_path)
    _, container = ui.run_to_plan(sid)

    card = card_of(container)
    heading = card.find("h3")
    assert heading is not None and heading.get("tabindex") == "-1" and "autofocus" in heading.attrs
    form = card.find("form")
    assert form is not None
    assert form.get("hx-post") == f"/ui/s/{sid}/turns/t0001/confirmation"
    assert form.get("hx-sync") == "closest .turn:replace"  # cancels a poll sent before the click
    assert form.get("hx-disabled-elt") == "#plan-form-t0001 button"
    buttons = form.find_all("button")
    assert [(b.get("name"), b.get("value"), b.text()) for b in buttons] == [
        ("decision", "approve", "Approve plan"),
        ("decision", "reject", "Reject plan"),
    ]
    assert buttons[0].get("class") == buttons[1].get("class")  # equally prominent
    assert not any("autofocus" in b.attrs for b in buttons)
    assert not any("disabled" in b.attrs for b in buttons)
    assert form.find("input", name="confirmation_id").get("value") == pending_id(container)  # type: ignore[union-attr]


def test_the_time_left_is_the_servers_and_a_poll_with_no_change_updates_only_that(
    tmp_path: Path,
) -> None:
    app, ui, sid = chain_session(tmp_path)
    clock = FakeWebClock()
    app.state.web_clock = clock
    _, container = ui.run_to_plan(sid)
    version = int(container.get("data-version") or 0)
    record = app.state.registry.get(sid, DEV)
    expires = record.turn("t0001").pending_confirmation.expires_at
    assert isinstance(expires, datetime)

    clock.now = expires - timedelta(seconds=77)
    first = ui.poll(sid, "t0001", v=version)

    tree = parse(first.text)
    assert tree.find("section", **{"class": "plan"}) is None  # the card isn't drawn again
    assert tree.find("li") is None
    assert tree.find("div", **{"class": "poll"}) is not None
    left = tree.find("p", id="timeleft-t0001")
    assert left is not None and left.get("hx-swap-oob") == "true"
    assert left.text() == "about 77 s left"
    clock.now = expires + timedelta(seconds=30)
    assert parse(ui.poll(sid, "t0001", v=version).text).find("p", id="timeleft-t0001").text() == (  # type: ignore[union-attr]
        "about 0 s left"
    )
    # the card as first drawn says the same, from the same clock
    clock.now = expires - timedelta(seconds=45)
    drawn = card_of(parse(ui.poll(sid, "t0001", v=0).text))
    assert drawn.find("p", id="timeleft-t0001").text() == "about 45 s left"  # type: ignore[union-attr]
    # no client-side expiry copy anywhere on the page
    page = ui.client.get("/").text.lower() + ui.poll(sid, "t0001", v=0).text.lower()
    for words in ("window closed", "expired", "time's up", "timed out"):
        assert words not in page


def test_the_confirmation_response_never_contains_the_answered_card(tmp_path: Path) -> None:
    _, ui, sid = chain_session(tmp_path)
    _, container = ui.run_to_plan(sid)

    answered = ui.answer(sid, "t0001", pending_id(container), "approve")

    assert answered.status_code == 200
    tree = parse(answered.text)
    assert tree.find("section", **{"class": "plan"}) is None
    assert tree.find("form", id="plan-form-t0001") is None
    turn = tree.find("li", **{"data-turn-id": "t0001"})
    assert turn is not None and int(turn.get("data-version") or 0) > int(
        container.get("data-version") or 0
    )
    assert turn.get("data-status") in ("running", "finished")
    assert "<title>Approval needed" not in answered.text
    # and no later response shows it either
    final = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")
    assert reply_of(final)
    assert ui.poll(sid, "t0001", v=0).text.count("plan-form") == 0


def test_rejecting_answers_the_same_way_and_the_reply_is_the_pipelines(tmp_path: Path) -> None:
    _, ui, sid = chain_session(tmp_path)
    _, container = ui.run_to_plan(sid)

    answered = ui.answer(sid, "t0001", pending_id(container), "reject")

    assert answered.status_code == 200
    final = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")
    assert reply_of(final) == ui.api_turn(sid, "t0001")["reply"]
    assert final.find("ul", **{"data-slot": "downloads"}) is None
    assert ui.api_turn(sid, "t0001")["delivered_files"] == []


def test_unconfirmed_adds_the_fixed_line_only_for_the_plan_still_waiting(tmp_path: Path) -> None:
    _, ui, sid = chain_session(tmp_path)
    _, container = ui.run_to_plan(sid)
    waiting = pending_id(container)
    line = "Your answer may not have reached the server."

    same = ui.poll(sid, "t0001", unconfirmed=waiting)
    other = ui.poll(sid, "t0001", unconfirmed="c_somethingelse")

    assert same.headers["hx-retarget"] == "#turn-t0001"
    same_card = card_of(parse(same.text))
    assert same_card.find("p", **{"class": "plan-warning"}).text() == line  # type: ignore[union-attr]
    assert not any("disabled" in b.attrs for b in same_card.find_all("button"))  # live buttons
    assert line not in other.text
    assert card_of(parse(other.text)) is not None  # the plan is still shown as it is
    ui.answer(sid, "t0001", waiting, "approve")
    after = ui.poll(sid, "t0001", unconfirmed=waiting)
    assert line not in after.text
    assert parse(after.text).find("section", **{"class": "plan"}) is None
    assert parse(after.text).find("li", **{"data-turn-id": "t0001"}) is not None  # whole turn


def test_the_reply_and_downloads_come_with_the_finished_container(tmp_path: Path) -> None:
    _, ui, sid = chain_session(tmp_path)
    _, container = ui.run_to_plan(sid)
    ui.answer(sid, "t0001", pending_id(container), "approve")

    final = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")

    api = ui.api_turn(sid, "t0001")
    assert api["outcome"] == "delivered" and len(api["delivered_files"]) == 1
    links = final.find_all("a", **{"class": "download"})
    assert [a.get("href") for a in links] == [api["delivered_files"][0]["download_url"]]
    assert links[0].text().startswith("Download " + api["delivered_files"][0]["name"] + " (")
    assert "download" in links[0].attrs
    page = parse(ui.client.get(f"/ui/s/{sid}/transcript").text)
    sidebar = page.find("section", id="files")
    assert sidebar is not None
    assert [a.get("href") for a in sidebar.find_all("a")] == [links[0].get("href")]
    # the download works through the cookie-less test client too (a plain same-origin link)
    got = ui.client.get(links[0].get("href") or "")
    assert got.status_code == 200 and got.content[:2] == b"PK"


def test_a_plan_card_is_never_shown_for_a_turn_with_no_file_changing_step(tmp_path: Path) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider(single("general_question"))))
    ui = Ui(client)
    sid = ui.new_session()
    ui.start(sid, "what can you do?")

    final = wait_for_container(ui, sid, "t0001", lambda c: c.get("data-status") == "finished")

    assert final.find("section", **{"class": "plan"}) is None
