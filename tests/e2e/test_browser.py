"""The five end-to-end tests of frontend design 10.3, in Chromium against the real app.

The pipeline is the real one; only the LLM is a queued fake. The rate limits are the production
ones. Each test checks what the user sees against what the server's JSON API says about the same
turn, and that the user's original upload is byte-identical afterwards (rule 2).
"""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl
from playwright.sync_api import Page, Route, expect

from backend.confirmation import PLAN_NOTE_CHAIN
from backend.uploads import sanitize_name
from buddy.voice_catalog import FALLBACK_PIPELINE_ERROR
from tests.backend.factories import DEDUPE_THEN_EMPTY, chain, single
from tests.e2e.conftest import PASSWORD, LiveApp
from tests.e2e.pages import (
    CHAIN,
    Watch,
    no_horizontal_overflow,
    notices,
    reachable,
    reply_raw,
    send,
    session_id,
    sha256,
    sign_in,
    upload,
    web_storage,
)

LiveFactory = Callable[..., LiveApp]
NewPage = Callable[..., Page]


def test_golden_path_at_360_by_640(
    live_app: LiveFactory, new_page: NewPage, tmp_path: Path
) -> None:
    live = live_app(chain(*DEDUPE_THEN_EMPTY))
    page = new_page()
    watch = Watch(page)

    sign_in(page, live.base)
    sid = session_id(page)
    assert page.locator("#gate-form").count() == 0
    upload(page)
    (original,) = live.uploads()
    before = sha256(original)
    assert no_horizontal_overflow(page)
    send(page, CHAIN)

    # the plan card: both steps in order, the chain wording, the server's plan note
    card = page.locator(f"#turn-t0001 .plan")
    expect(card).to_be_visible()
    steps = card.locator("ol li").all_inner_texts()
    assert steps == [
        "Remove duplicate rows from report.xlsx.",
        "Then remove empty rows from the result of step 1.",
    ]
    assert card.locator(".plan-note").inner_text() == PLAN_NOTE_CHAIN
    plan = live.turn(sid)["pending_confirmation"]
    assert [s["prompt"] for s in plan["steps"]] == steps and plan["plan_note"] == PLAN_NOTE_CHAIN
    assert page.evaluate("document.activeElement.id") == "plan-h-t0001"
    assert page.title() == "Approval needed – WorkBuddy"
    assert card.locator("button").count() == 2
    assert reachable(page, "#turn-t0001 .plan button[value=approve]")
    assert reachable(page, "#turn-t0001 .plan button[value=reject]")
    assert no_horizontal_overflow(page)
    assert page.locator("#composer textarea").is_disabled()  # one turn at a time
    assert page.locator("#upload-form input[type=file]").is_disabled()

    page.click("#turn-t0001 .plan button[value=approve]")
    reply = reply_raw(page)  # waits for the reply slot

    # the page shows the reply exactly as the API returned it
    api = live.turn(sid)
    assert api["outcome"] == "delivered" and api["status"] == "finished"
    assert reply == api["reply"]
    assert page.title() == "WorkBuddy demo"
    assert page.evaluate("document.activeElement.id") == "message"  # focus goes to the composer
    assert not page.locator("#composer textarea").is_disabled()
    assert page.locator(".plan").count() == 0 and page.locator(".poll").count() == 0

    # the download, through the plain link
    (link,) = page.locator("#turn-t0001 a.download").all()
    (delivered,) = api["delivered_files"]
    assert link.get_attribute("href") == delivered["download_url"]
    with page.expect_download() as info:
        link.click()
    saved = tmp_path / "downloaded.xlsx"
    info.value.save_as(saved)
    # (the download route stores the name under the upload rules: at most 100 characters)
    assert info.value.suggested_filename == sanitize_name(delivered["name"])
    assert sha256(saved) == delivered["sha256"]  # the file the API says it handed back
    rows = list(openpyxl.load_workbook(saved).active.iter_rows(values_only=True))  # type: ignore[union-attr]
    assert rows == [("name", "amount"), ("Alice", 10), ("Bob", 20), ("Carol", 30)]
    # the sidebar lists it too, as a link
    assert page.locator("#files a[download]").count() == 1

    # rule 2, and everything else the design says never happens
    assert sha256(original) == before
    assert notices(page) == []  # no page notice, not even for a moment, at production limits (H2)
    assert watch.bad() == [], watch.bad()
    assert watch.errors == []
    assert no_horizontal_overflow(page)
    assert web_storage(page) == {"local": [], "session": []}
    cookies = page.context.cookies()
    assert sorted(c["name"] for c in cookies) == ["__Host-wb_access", "__Host-wb_session"]
    assert all(c["httpOnly"] and c["secure"] and c["sameSite"] == "Strict" for c in cookies)
    assert not any(PASSWORD in c["value"] for c in cookies)
    assert PASSWORD not in page.url and PASSWORD not in page.content()

    # a reload resumes the session from the server's state, with no client code
    page.reload()
    expect(page.locator("#turn-t0001 [data-slot=reply]")).to_have_text(api["reply"])
    assert page.locator("#files a[download]").count() == 1
    assert notices(page) == []


def forget_every_session(live: LiveApp) -> None:
    """What a restart on an ephemeral disk does: the server no longer has any session."""
    live.app.state.registry._sessions.clear()
    for folder in live.settings.sessions_dir.iterdir():
        shutil.rmtree(folder)


def plan_page(
    live_app: LiveFactory, new_page: NewPage, **settings: Any
) -> tuple[LiveApp, Page, str, Watch]:
    """A signed-in page with a file uploaded and a two-step plan waiting for its answer."""
    live = live_app(chain(*DEDUPE_THEN_EMPTY), **settings)
    page = new_page()
    watch = Watch(page)
    sign_in(page, live.base)
    upload(page)
    send(page, CHAIN)
    expect(page.locator("#turn-t0001 .plan")).to_be_visible()
    return live, page, session_id(page), watch


def test_rejecting_shows_the_pipelines_reply_and_no_download(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, watch = plan_page(live_app, new_page)
    (original,) = live.uploads()
    before = sha256(original)

    page.click("#turn-t0001 .plan button[value=reject]")
    reply = reply_raw(page)

    api = live.turn(sid)
    assert api["outcome"] != "delivered" and api["delivered_files"] == []
    assert reply == api["reply"]
    assert api["detail"]["confirmation"] == {"decision": "reject", "decision_source": "user"}
    assert page.locator("#turn-t0001 .downloads, #turn-t0001 a").count() == 0
    assert page.locator("#files a[download]").count() == 0
    assert page.locator("#files h3", has_text="Files from Buddy").count() == 0
    assert page.locator(".plan").count() == 0
    assert sha256(original) == before
    assert notices(page) == [] and watch.bad() == []


def test_a_pipeline_failure_shows_the_fallback_reply_verbatim_with_no_download(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    def broken(confirmation_provider: Any) -> Any:
        raise RuntimeError("secret cell 4111 at /srv/data")  # the pipeline can't even be built

    live = live_app(factory=broken)
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)
    upload(page)
    (original,) = live.uploads()
    before = sha256(original)

    send(page, "remove duplicate rows")
    reply = reply_raw(page)

    api = live.turn(sid)
    assert api["reply_source"] == "pipeline_fallback" and api["outcome"] == "failed"
    assert reply == api["reply"] == FALLBACK_PIPELINE_ERROR
    assert "4111" not in reply and "/srv" not in reply
    assert page.locator("#turn-t0001 .downloads, #turn-t0001 a").count() == 0
    assert page.locator(".plan").count() == 0
    assert notices(page) == []
    assert sha256(original) == before


def test_a_plan_nobody_answers_times_out_with_no_download_and_the_page_never_says_it_closed(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live, page, sid, _ = plan_page(live_app, new_page, confirmation_timeout_s=4)
    samples: list[str] = []
    enabled: list[bool] = []

    deadline = time.monotonic() + 30
    while page.locator("#turn-t0001 [data-slot=reply]").count() == 0:
        assert time.monotonic() < deadline, "the turn never finished"
        samples.append(page.inner_text("body").lower())
        if page.locator("#turn-t0001 .plan").count():
            enabled.append(not page.locator("#turn-t0001 .plan button").first.is_disabled())
        page.wait_for_timeout(250)

    api = live.turn(sid)
    assert api["detail"]["confirmation"] == {"decision": "reject", "decision_source": "timeout"}
    assert api["delivered_files"] == [] and api["outcome"] != "delivered"
    assert reply_raw(page) == api["reply"]
    assert page.locator("#turn-t0001 a").count() == 0
    assert enabled and all(enabled)  # the page never disabled the buttons by its own clock
    for text in samples:
        for words in ("window closed", "expired", "closed", "time's up", "timed out"):
            assert words not in text
    assert any("s left" in text for text in samples)  # the server's time left was shown
    assert notices(page) == []


def test_losing_the_connection_shows_a_page_notice_outside_the_reply_and_a_reload_recovers(
    live_app: LiveFactory, new_page: NewPage
) -> None:
    live = live_app(single("general_question"))
    page = new_page()
    Watch(page)
    sign_in(page, live.base)
    sid = session_id(page)
    attempts: list[str] = []

    def lose(route: Route) -> None:
        attempts.append(route.request.url)
        route.abort()

    page.route("**/poll**", lose)
    send(page, "what can you do?")
    expect(page.locator("#turn-t0001")).to_be_visible()

    page.wait_for_selector("#page-notices .notice", timeout=90_000)

    lost = (
        "This page lost contact with the server before the turn finished, so its result isn't "
        "shown. Reload the page to check."
    )
    assert notices(page) == [lost]
    assert page.locator("#page-notices .notice").inner_text() == lost
    assert page.locator("#turn-t0001 [data-slot=reply]").count() == 0  # never in the reply slot
    assert page.locator("#turn-t0001 .reply, #turn-t0001 .bubble-buddy").count() == 0
    assert page.locator(".poll").count() == 0
    assert len(attempts) == 6  # the first poll and five retries (2, 4, 8, 8 and 8 s), then it stops
    assert live.turn(sid)["status"] == "finished"  # the server did finish it

    page.unroute("**/poll**")
    page.reload()

    assert reply_raw(page) == live.turn(sid)["reply"]
    assert page.locator("#page-notices .notice").count() == 0
