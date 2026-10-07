"""The page's races and failures that only a browser shows (frontend-demo review: M1, M2, L3, L4).

A server restart under a waiting plan card, a ``409`` on the plan card with and without a poll in
flight, a ``5xx`` on the message POST (which must ask the server, never send again), the plan's
buttons while a lost answer is being looked up, and a long file name at 360 px.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable

from playwright.sync_api import Page, Route, expect

from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.e2e.conftest import LiveApp
from tests.e2e.pages import (
    CHAIN,
    Watch,
    no_horizontal_overflow,
    notices,
    reachable,
    reply_raw,
    send,
    session_id,
    sign_in,
    upload,
    wait_until,
)

LiveFactory = Callable[..., LiveApp]
NewPage = Callable[..., Page]

JSON = {"Content-Type": "application/json"}
INTERNAL_ERROR = {"error": {"code": "internal_error", "message": "Something went wrong."}}


def two_tabs_on_one_plan(
    live_app: LiveFactory, new_page: NewPage
) -> tuple[LiveApp, Page, Page, str, Watch]:
    """Tab A and tab B on one session (one browser, one cookie jar), both showing the plan card."""
    live = live_app(chain(*DEDUPE_THEN_EMPTY))
    first = new_page()
    sign_in(first, live.base)
    sid = session_id(first)
    upload(first)
    send(first, CHAIN)
    expect(first.locator("#turn-t0001 .plan")).to_be_visible()
    second = first.context.new_page()
    watch = Watch(second)
    second.goto(live.base + "/")
    expect(second.locator("#turn-t0001 .plan")).to_be_visible()
    return live, first, second, sid, watch


def settle_stale_card(live: LiveApp, sid: str, tab_b: Page, watch: Watch) -> None:
    """Tab B has clicked Reject on a plan tab A answered: the server said ``409``. The page must go
    and fetch the turn as the server has it, and show it."""
    reply = reply_raw(tab_b)
    assert reply == live.turn(sid)["reply"]
    assert tab_b.locator("#turn-t0001 .plan").count() == 0
    assert tab_b.title() == "WorkBuddy demo"  # no longer "Approval needed"
    assert tab_b.locator("#composer button[type=submit]").is_enabled()
    answers = [i for i, (status, url) in enumerate(watch.statuses) if status == 409]
    assert len(answers) == 1 and watch.statuses[answers[0]][1].endswith("/confirmation")
    # the refetch is the one poll request that names ``held`` and no ``v`` (the page's own polls
    # carry ``?v=``)
    refetched = [
        status
        for status, url in watch.statuses[answers[0] + 1 :]
        if "/turns/t0001/poll?held=" in url and "v=" not in url.replace("held=", "")
    ]
    assert refetched and refetched[0] == 200
    # tab A answered it: the server ran the plan, and B's click changed nothing
    assert live.turn(sid)["detail"]["confirmation"]["decision"] == "approve"


# -- M1: a server restart under a waiting plan card ------------------------------------------------


def test_a_server_restart_under_a_waiting_plan_card_reloads_into_what_the_server_has(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(chain(*DEDUPE_THEN_EMPTY), env="dev", demo_password=None)
    page = new_page()
    watch = Watch(page)
    page.goto(live.base + "/")
    page.wait_for_selector("#workspace[data-session-id]")
    sid = session_id(page)
    upload(page)
    send(page, CHAIN)
    expect(page.locator("#turn-t0001 .plan")).to_be_visible()
    held_version = int(page.get_attribute("#turn-t0001", "data-version") or 0)
    assert held_version > 0
    (original,) = live.uploads()

    live.restart(PipelineFactory(QueuedProvider([])))  # the plan is gone with the process

    wait_until(page, "() => !document.querySelector('#turn-t0001 .plan')", timeout_s=30)
    reply = reply_raw(page)
    api = live.turn(sid)
    assert api["status"] == "finished" and api["delivered_files"] == []
    assert reply == api["reply"]  # the server's own account, not a card that can't be answered
    assert page.title() == "WorkBuddy demo"
    assert page.locator("#turn-t0001 .plan, #turn-t0001 .poll").count() == 0
    assert page.locator("#composer button[type=submit]").is_enabled()
    assert original.exists()
    # one reload, not a loop: the page that comes back holds the server's own versions
    page.wait_for_timeout(7000)
    loads = [url for status, url in watch.statuses if url == live.base + "/" and status == 200]
    assert len(loads) == 2
    assert notices(page) == []


def stranded_card_after_a_restart(
    live_app: LiveFactory, new_page: NewPage
) -> tuple[LiveApp, Page, str, Watch]:
    """A password-less dev server restarted under a waiting plan card, with the card's poll
    element gone from the page, so only the page's own refetches can bring it up to date."""
    live = live_app(chain(*DEDUPE_THEN_EMPTY), env="dev", demo_password=None)
    page = new_page()
    watch = Watch(page)
    page.goto(live.base + "/")
    page.wait_for_selector("#workspace[data-session-id]")
    sid = session_id(page)
    upload(page)
    send(page, CHAIN)
    expect(page.locator("#turn-t0001 .plan")).to_be_visible()
    assert int(page.get_attribute("#turn-t0001", "data-version") or 0) > 0
    page.evaluate("document.querySelectorAll('.poll').forEach((poll) => poll.remove())")
    live.restart(PipelineFactory(QueuedProvider([])))  # the plan is gone with the process
    return live, page, sid, watch


def test_a_409_after_a_restart_with_no_poll_armed_reloads_into_what_the_server_has(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, watch = stranded_card_after_a_restart(live_app, new_page)

    page.click("#turn-t0001 .plan button[value=approve]")  # the restarted server: 409

    reply = reply_raw(page)
    assert reply == live.turn(sid)["reply"]
    assert page.locator("#turn-t0001 .plan").count() == 0
    assert page.title() == "WorkBuddy demo"
    assert any(status == 409 for status, _ in watch.statuses)
    assert live.turn(sid)["delivered_files"] == []


def test_a_lost_answer_after_a_restart_with_no_poll_armed_reloads_into_what_the_server_has(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, _ = stranded_card_after_a_restart(live_app, new_page)
    page.route("**/confirmation", lambda route: route.abort())  # the answer never arrives

    page.click("#turn-t0001 .plan button[value=approve]")

    reply = reply_raw(page)
    assert reply == live.turn(sid)["reply"]
    assert page.locator("#turn-t0001 .plan").count() == 0
    assert page.title() == "WorkBuddy demo"


# -- M2: a 409 on the plan card --------------------------------------------------------------------


def test_a_409_on_the_plan_card_with_a_poll_in_flight_shows_the_turn_as_the_server_has_it(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, tab_a, tab_b, sid, watch = two_tabs_on_one_plan(live_app, new_page)
    held: list[Route] = []

    def hold_the_first_poll(route: Route) -> None:
        if not held:
            held.append(route)  # tab B's poll stays in flight
        else:
            route.continue_()

    tab_b.route("**/poll**", hold_the_first_poll)
    deadline_polls = 0
    while not held and deadline_polls < 100:
        tab_b.wait_for_timeout(100)
        deadline_polls += 1
    assert held, "tab B never sent a poll"
    tab_a.click("#turn-t0001 .plan button[value=approve]")
    reply_raw(tab_a)

    tab_b.click("#turn-t0001 .plan button[value=reject]")  # B's poll is in flight; the 409 follows

    settle_stale_card(live, sid, tab_b, watch)
    try:
        held[0].abort()
    except Exception:  # noqa: BLE001 - the browser already cancelled it
        pass


def test_a_409_on_the_plan_card_with_no_poll_in_flight_shows_the_turn_as_the_server_has_it(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, tab_a, tab_b, sid, watch = two_tabs_on_one_plan(live_app, new_page)
    quiet = tab_b.evaluate("document.querySelector('#turn-t0001 .poll').outerHTML")
    quiet = re.sub(r"delay:\d+s", "delay:600s", quiet)  # B's next poll is ten minutes away
    served: list[str] = []

    def nothing_new(route: Route) -> None:
        served.append(route.request.url)
        route.fulfill(status=200, headers={"Content-Type": "text/html"}, body=quiet)

    tab_b.route("**/poll**", nothing_new)
    deadline_polls = 0
    while not served and deadline_polls < 100:
        tab_b.wait_for_timeout(100)
        deadline_polls += 1
    assert served, "tab B never sent a poll"
    tab_a.click("#turn-t0001 .plan button[value=approve]")
    reply_raw(tab_a)
    tab_b.unroute("**/poll**")  # from here on B's requests reach the server
    assert tab_b.locator("#turn-t0001 .plan").is_visible()  # B still shows the card

    tab_b.click("#turn-t0001 .plan button[value=reject]")

    settle_stale_card(live, sid, tab_b, watch)
    assert len(served) == 1  # no poll of B's own was what brought the turn up to date


# -- L4 / m22: while a lost answer is looked up, the buttons stay off ------------------------------


def test_after_a_lost_plan_answer_the_buttons_stay_off_until_the_server_says_what_state_it_is_in(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(chain(*DEDUPE_THEN_EMPTY))
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)
    upload(page)
    send(page, CHAIN)
    expect(page.locator("#turn-t0001 .plan")).to_be_visible()
    lookups: list[Route] = []

    def answer(route: Route) -> None:
        route.abort()  # the answer never reaches the server

    def look_up(route: Route) -> None:
        if "unconfirmed=" in route.request.url:
            lookups.append(route)  # the page's question about that answer waits here
        else:
            route.continue_()

    page.route("**/confirmation", answer)
    page.route("**/poll**", look_up)

    page.click("#turn-t0001 .plan button[value=approve]")

    deadline_polls = 0
    while not lookups and deadline_polls < 100:
        page.wait_for_timeout(100)
        deadline_polls += 1
    assert lookups, "the page never asked the server about that answer"
    page.wait_for_timeout(300)  # htmx has long since ended the failed request
    buttons = page.locator("#turn-t0001 .plan button")
    assert buttons.count() == 2 and all(b.is_disabled() for b in buttons.all())
    assert live.turn(sid)["status"] == "awaiting_confirmation"  # nothing was approved

    lookups[0].continue_()

    warning = page.locator("#turn-t0001 .plan .plan-warning")
    expect(warning).to_have_text("Your answer may not have reached the server.")
    assert all(not b.is_disabled() for b in page.locator("#turn-t0001 .plan button").all())


# -- L4 / m23: a 5xx on the message POST asks the server and never sends again ---------------------


def test_a_5xx_on_the_message_post_the_server_never_got_asks_once_and_keeps_the_draft(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question"))
    page = new_page()
    watch = Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)
    posts: list[str] = []

    def server_error(route: Route) -> None:
        if route.request.method == "POST":
            posts.append(route.request.post_data or "")
            route.fulfill(status=500, headers=JSON, body=json.dumps(INTERNAL_ERROR))
        else:
            route.continue_()

    page.route("**/turns", server_error)

    send(page, "a draft the server never received")

    page.wait_for_selector("#page-notices .notice")
    page.wait_for_timeout(1500)  # a resend would have happened by now
    assert len(posts) == 1  # sent once, and never again
    assert notices(page) == ["The server didn't confirm receiving that message."]
    assert page.inner_text("#composer-error") == INTERNAL_ERROR["error"]["message"]
    assert page.input_value("#message") == "a draft the server never received"
    expect(page.locator("#composer button[type=submit]")).to_be_enabled()
    assert page.locator("#transcript .turn").count() == 0
    assert live.api(f"/api/v1/sessions/{sid}").json()["turns"] == []
    asked = [url for status, url in watch.statuses if "/transcript?after=" in url]
    assert len(asked) == 1  # the page asked the server what it has


def test_a_5xx_on_the_message_post_the_server_did_get_finds_the_turn_and_sends_nothing_again(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question") + single("general_question"))
    page = new_page()
    watch = Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)
    posts: list[str] = []

    def server_error_after_the_server_acts(route: Route) -> None:
        if route.request.method == "POST":
            posts.append(route.request.post_data or "")
            route.fetch()  # the server gets the message and starts the turn
            route.fulfill(status=500, headers=JSON, body=json.dumps(INTERNAL_ERROR))
        else:
            route.continue_()

    page.route("**/turns", server_error_after_the_server_acts)

    send(page, "what can you do?")

    reply = reply_raw(page)
    page.wait_for_timeout(1500)
    assert len(posts) == 1
    assert reply == live.turn(sid)["reply"]
    assert page.locator("#transcript .turn").count() == 1
    assert len(live.api(f"/api/v1/sessions/{sid}").json()["turns"]) == 1  # one turn, not two
    assert notices(page) == []
    assert page.input_value("#message") == ""  # the draft was sent
    assert len([1 for _, url in watch.statuses if "/transcript?after=" in url]) == 1


# -- L3: a long file name at 360 px ----------------------------------------------------------------


def test_a_long_unbroken_file_name_overflows_nothing_at_360_px(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(chain(*DEDUPE_THEN_EMPTY))
    page = new_page({"width": 360, "height": 640})
    Watch(page)
    sign_in(page, live.base)
    name = "q3_regional_sales_report_" + "x" * 65 + ".xlsx"  # 95 characters, no break to wrap at
    upload(page, name=name)
    send(page, CHAIN)

    expect(page.locator("#turn-t0001 .plan")).to_be_visible()
    assert name[:-5] in page.inner_text("#turn-t0001 .plan-steps")
    assert no_horizontal_overflow(page)
    assert reachable(page, "#turn-t0001 .plan button[value=approve]")
    assert reachable(page, "#turn-t0001 .plan button[value=reject]")

    page.click("#turn-t0001 .plan button[value=approve]")
    reply_raw(page)
    assert no_horizontal_overflow(page)
    assert page.locator("#turn-t0001 .download").count() == 1
    assert reachable(page, "#turn-t0001 .download")
