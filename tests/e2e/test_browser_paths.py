"""The page script's other paths, in Chromium (frontend design 1.2 to 1.7, 4.3 item 8, 10.2).

``TestClient`` doesn't run ``app.js``, so the paths that only a browser can show are here: the
swap guard, the ``Retry-After`` wait on a poll, a lost answer to a plan, an ambiguous message POST,
the reset on ``404``, the gate's errors, the keys, and a browser on ``localhost``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable

from playwright.sync_api import Page, Route, expect

from tests.backend.factories import DEDUPE_THEN_EMPTY, chain, single
from tests.backend.helpers import two_sheets
from tests.e2e.conftest import PASSWORD, LiveApp
from tests.e2e.pages import (
    Watch,
    notices,
    reply_raw,
    send,
    session_id,
    sign_in,
    upload,
    wait_until,
)
from tests.e2e.test_browser import forget_every_session, plan_page

LiveFactory = Callable[..., LiveApp]
NewPage = Callable[..., Page]


# -- Polling and the swap guard -----------------------------------------------------------------


def test_a_429_on_a_poll_waits_retry_after_and_is_not_a_lost_connection(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question"))
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)
    refused: list[float] = []

    def too_many(route: Route) -> None:
        if len(refused) < 8:  # more than the six a connection problem would be allowed
            refused.append(time.monotonic())
            route.fulfill(
                status=429,
                headers={"Retry-After": "1", "Content-Type": "application/json"},
                body=json.dumps(
                    {"error": {"code": "rate_limited", "message": "Too many requests. Wait."}}
                ),
            )
        else:
            route.continue_()

    page.route("**/poll**", too_many)
    send(page, "what can you do?")

    reply = reply_raw(page)

    assert len(refused) == 8
    gaps = [b - a for a, b in zip(refused, refused[1:], strict=False)]
    assert all(gap >= 0.9 for gap in gaps)  # it waited Retry-After each time
    assert reply == live.turn(sid)["reply"]
    assert notices(page) == []  # a refused poll is not a lost connection
    assert page.locator("#page-notices .notice").count() == 0


def test_a_fragment_for_another_session_or_an_older_version_is_dropped_by_the_page(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, _ = plan_page(live_app, new_page)
    card = page.locator("#turn-t0001 .plan")
    steps_before = card.locator("ol").inner_html()
    served: list[str] = []

    def forged(route: Route) -> None:
        served.append(route.request.url)
        if len(served) % 2:
            other = (
                '<li id="turn-t0001" class="turn" data-session-id="s_another" '
                'data-turn-id="t0001" data-seq="1" data-version="99" data-status="finished">'
                '<div data-slot="reply">FROM ANOTHER SESSION</div></li>'
            )
        else:
            other = (
                f'<li id="turn-t0001" class="turn" data-session-id="{sid}" data-turn-id="t0001" '
                'data-seq="1" data-version="0" data-status="finished">'
                '<div data-slot="reply">FROM AN OLDER VERSION</div></li>'
            )
        route.fulfill(
            status=200,
            headers={
                "Content-Type": "text/html",
                "HX-Retarget": "#turn-t0001",
                "HX-Reswap": "outerHTML",
            },
            body=other,
        )

    page.route("**/poll**", forged)
    page.wait_for_timeout(9000)  # four or more forged answers

    assert len(served) >= 3
    body = page.inner_text("body")
    assert "FROM ANOTHER SESSION" not in body and "FROM AN OLDER VERSION" not in body
    assert card.is_visible() and card.locator("ol").inner_html() == steps_before
    assert page.locator("#turn-t0001[data-version]").get_attribute("data-version") != "99"
    # the page keeps polling after a dropped fragment, and takes the real one when it comes
    page.unroute("**/poll**")
    page.click("#turn-t0001 .plan button[value=approve]")
    assert reply_raw(page) == live.turn(sid)["reply"]


# -- A session the server no longer has -------------------------------------------------------


def test_a_404_for_the_session_reloads_into_a_new_one_with_nothing_of_the_old(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question") + single("general_question"))
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    old = session_id(page)
    upload(page, name="old-file.xlsx")
    send(page, "first question")
    reply_raw(page)
    assert page.locator("#transcript .turn").count() == 1
    forget_every_session(live)

    send(page, "second question")  # POST for the old session: 404 session_not_found
    wait_until(
        page, "(old) => document.querySelector('#workspace').dataset.sessionId !== old", arg=old
    )

    gone = "That session no longer exists on the server, so this is a new one."
    assert notices(page) == [gone]
    assert page.locator("#page-notices .notice").inner_text() == gone
    assert session_id(page) != old
    assert page.locator("#transcript .turn").count() == 0  # no transcript of the old session
    assert page.locator(".file-list .file-name").count() == 0  # none of its files
    assert "old-file.xlsx" not in page.content() and "first question" not in page.content()
    # and the notice shows exactly once
    page.reload()
    assert page.locator("#page-notices .notice").count() == 0


def test_a_404_on_a_poll_also_resets_the_page(live_app: LiveFactory, new_page: NewPage) -> None:
    live, page, old, _ = plan_page(live_app, new_page)
    forget_every_session(live)

    wait_until(
        page, "(old) => document.querySelector('#workspace').dataset.sessionId !== old", arg=old
    )

    assert page.locator("#page-notices .notice").count() == 1
    assert page.locator(".plan").count() == 0


# -- A lost answer to a plan ----------------------------------------------------------------------


def test_a_plan_answer_that_never_reached_the_server_comes_back_as_the_card_with_a_warning(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, _ = plan_page(live_app, new_page)
    answers: list[str] = []

    def never_arrives(route: Route) -> None:
        answers.append(route.request.url)
        route.abort()

    page.route("**/confirmation", never_arrives)
    page.click("#turn-t0001 .plan button[value=approve]")

    warning = page.locator("#turn-t0001 .plan .plan-warning")
    expect(warning).to_have_text("Your answer may not have reached the server.")
    assert len(answers) == 1
    assert live.turn(sid)["status"] == "awaiting_confirmation"  # nothing was approved
    buttons = page.locator("#turn-t0001 .plan button")
    assert buttons.count() == 2 and all(not b.is_disabled() for b in buttons.all())  # live again
    assert "approved" not in page.inner_text("body").lower()  # the page claims nothing

    page.unroute("**/confirmation")
    page.click("#turn-t0001 .plan button[value=approve]")
    assert reply_raw(page) == live.turn(sid)["reply"]
    assert live.turn(sid)["outcome"] == "delivered"


def test_a_plan_answer_whose_reply_was_lost_shows_the_turn_as_the_server_has_it(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, _ = plan_page(live_app, new_page)

    def reply_lost(route: Route) -> None:
        route.fetch()  # the server gets the answer and acts on it
        route.abort()  # the page never hears back

    page.route("**/confirmation", reply_lost)
    page.click("#turn-t0001 .plan button[value=approve]")

    reply = reply_raw(page)

    assert reply == live.turn(sid)["reply"]
    assert live.turn(sid)["detail"]["confirmation"]["decision"] == "approve"
    assert page.locator(".plan").count() == 0
    assert "may not have reached" not in page.content()  # that plan is no longer waiting


def test_while_an_answer_is_in_flight_both_buttons_are_off_and_the_page_says_only_that(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, _ = plan_page(live_app, new_page)
    held: list[Route] = []
    page.route("**/confirmation", lambda route: held.append(route))  # the answer waits here

    page.click("#turn-t0001 .plan button[value=approve]")

    expect(page.locator("#turn-t0001 .plan .sending")).to_have_text("Sending your answer\u2026")
    buttons = page.locator("#turn-t0001 .plan button")
    assert buttons.count() == 2 and all(b.is_disabled() for b in buttons.all())
    assert page.locator("#turn-t0001 .plan .plan-warning").count() == 0
    assert live.turn(sid)["status"] == "awaiting_confirmation"  # nothing has reached the server
    text = page.inner_text("body").lower()
    assert "approved" not in text and "running" not in text

    held[0].continue_()

    assert reply_raw(page) == live.turn(sid)["reply"]
    assert page.locator(".sending:visible").count() == 0


# -- An ambiguous message POST --------------------------------------------------------------------


def test_a_message_the_server_never_got_keeps_the_draft_and_says_so(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question"))
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    page.route(
        "**/turns",
        lambda route: route.abort() if route.request.method == "POST" else route.continue_(),
    )

    send(page, "a draft that never arrives")

    page.wait_for_selector("#page-notices .notice")
    assert notices(page) == ["The server didn't confirm receiving that message."]
    assert page.input_value("#message") == "a draft that never arrives"
    expect(page.locator("#composer button[type=submit]")).to_be_enabled()
    assert page.locator("#transcript .turn").count() == 0
    assert live.api(f"/api/v1/sessions/{session_id(page)}").json()["turns"] == []


def test_a_message_the_server_got_but_never_answered_is_found_and_not_sent_twice(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question") + single("general_question"))
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)

    def reply_lost(route: Route) -> None:
        if route.request.method == "POST":
            route.fetch()
            route.abort()
        else:
            route.continue_()

    page.route("**/turns", reply_lost)
    send(page, "what can you do?")

    reply = reply_raw(page)

    assert reply == live.turn(sid)["reply"]
    assert page.locator("#transcript .turn").count() == 1
    assert len(live.api(f"/api/v1/sessions/{sid}").json()["turns"]) == 1  # one turn, not two
    assert notices(page) == []
    assert page.input_value("#message") == ""  # the draft is gone: it was sent


# -- The gate, uploads and the keys -----------------------------------------------------------


def test_a_wrong_password_shows_the_apis_message_clears_the_field_and_keeps_focus(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app()
    page = new_page()
    page.goto(live.base + "/")

    page.fill("#password", "not the password")
    page.click("#gate-form button[type=submit]")

    expect(page.locator("#gate-error")).to_have_text(
        "You aren't signed in, or your sign-in has ended."
    )
    assert page.input_value("#password") == ""
    assert page.evaluate("document.activeElement.id") == "password"
    assert page.locator("#workspace").count() == 0
    assert [c["name"] for c in page.context.cookies()] == []


def test_the_gate_stops_guessing_with_the_apis_429_and_disables_continue(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app()
    page = new_page()
    page.goto(live.base + "/")

    for _ in range(11):  # the access bucket allows a burst of 10
        page.fill("#password", "guess")
        page.click("#gate-form button[type=submit]")
        wait_until(page, "() => document.querySelector('#gate-error').textContent !== ''")
        if "Too many requests" in page.inner_text("#gate-error"):
            break
        wait_until(page, "() => document.querySelector('#password').value === ''")

    assert page.inner_text("#gate-error") == "Too many requests. Wait a little and try again."
    assert page.locator("#gate-form button[type=submit]").is_disabled()


def test_upload_errors_appear_verbatim_beside_the_control_and_leave_the_list_alone(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app()
    page = new_page()
    sign_in(page, live.base)
    upload(page, name="keep.xlsx")
    names = page.locator(".file-name").all_inner_texts()

    page.set_input_files("#file", {"name": "notes.txt", "mimeType": "text/plain", "buffer": b"hi"})
    page.click("#upload-form button[type=submit]")
    expect(page.locator("#upload-error")).to_have_text("Only .xlsx files can be uploaded.")
    page.set_input_files("#file", {"name": "two.xlsx", "mimeType": "x", "buffer": two_sheets()})
    page.click("#upload-form button[type=submit]")
    expect(page.locator("#upload-error")).to_have_text(
        "This demo works on one sheet at a time, and this workbook has more than one. "
        "Save the sheet you want in its own workbook and upload that."
    )

    assert page.locator(".file-name").all_inner_texts() == names


def test_enter_sends_and_shift_enter_adds_a_line(live_app: LiveFactory, new_page: NewPage) -> None:
    live = live_app(single("general_question"))
    page = new_page()
    sign_in(page, live.base)
    sid = session_id(page)

    page.click("#message")
    page.keyboard.type("first line")
    page.keyboard.press("Shift+Enter")
    page.keyboard.type("second line")
    assert page.input_value("#message") == "first line\nsecond line"
    assert page.locator("#transcript .turn").count() == 0
    page.keyboard.press("Enter")

    reply_raw(page)

    shown = page.locator("#turn-t0001 [data-slot=message]").text_content() or ""
    assert re.sub(r"\r\n?", "\n", shown) == "first line\nsecond line"
    assert live.api(f"/api/v1/sessions/{sid}/turns/t0001").json()["message"].replace(
        "\r\n", "\n"
    ) == ("first line\nsecond line")


def test_a_browser_on_localhost_passes_the_gate_on_the_started_port(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    """Review L5 of backend-api-retention: the origin follows the port, and localhost works."""
    live = live_app(chain(*DEDUPE_THEN_EMPTY))
    page = new_page()
    Watch(page)

    sign_in(page, live.base.replace("127.0.0.1", "localhost"))

    assert page.locator("#workspace[data-session-id]").count() == 1
    assert notices(page) == []
    assert PASSWORD not in page.url
    expect(page.locator("#composer")).to_be_visible()
    assert any(c["name"] == "__Host-wb_access" for c in page.context.cookies())
