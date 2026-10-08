"""Sign in, sign out and New session in Chromium, against the real app and a fake Google.

The fake identity provider (``tests/idp``) runs as a real server on ``localhost`` while the app is
on ``127.0.0.1``: two different sites, so the browser treats the way back from "Google" as
cross-site, as it does with the real one. That is what shows the SameSite=Strict access cookie
survives the callback (the page moves on by itself rather than by a redirect).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
import uvicorn
from playwright.sync_api import Page, expect

from tests.backend.helpers import GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from tests.e2e.conftest import LiveApp, _free_port
from tests.e2e.pages import Watch, session_id, upload
from tests.idp.provider import FakeIdp
from tests.idp.server import make_app

LiveFactory = Callable[..., LiveApp]
NewPage = Callable[..., Page]

ALICE = "alice@example.test"
BOB = "bob@example.test"


class Idp:
    def __init__(self, idp: FakeIdp, server: uvicorn.Server) -> None:
        self.idp = idp
        self.server = server


@pytest.fixture
def google_app(live_app: LiveFactory) -> Iterator[Callable[..., tuple[LiveApp, Idp]]]:
    """``google_app(replies, **settings)``: the app signed into a fake Google, and that Google."""
    servers: list[uvicorn.Server] = []

    def start(replies: list[str] | None = None, **overrides: Any) -> tuple[LiveApp, Idp]:
        app_port, idp_port = _free_port(), _free_port()
        issuer = f"http://localhost:{idp_port}"
        idp = FakeIdp(
            issuer=issuer,
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            redirect_uri=f"http://127.0.0.1:{app_port}/auth/google/callback",
        )
        server = uvicorn.Server(
            uvicorn.Config(make_app(idp), host="127.0.0.1", port=idp_port, log_level="warning")
        )
        threading.Thread(target=server.run, daemon=True).start()
        deadline = time.monotonic() + 15
        while not server.started and time.monotonic() < deadline:
            time.sleep(0.02)
        assert server.started, "the fake identity provider didn't start"
        servers.append(server)
        live = live_app(
            replies,
            demo_password=None,
            google_client_id=GOOGLE_CLIENT_ID,
            google_client_secret=GOOGLE_CLIENT_SECRET,
            oidc_issuer=issuer,
            port=app_port,
            **overrides,
        )
        return live, Idp(idp, server)

    yield start
    for server in servers:
        server.should_exit = True


def sign_in_with_google(page: Page, base: str, email: str) -> None:
    page.goto(base + "/")
    page.click("#google-signin")
    page.click(f"a:text-is('{email}')")  # the fake provider's account chooser
    page.wait_for_selector("#workspace[data-session-id]")


def visible_email(page: Page) -> str:
    return str(page.inner_text("#account-email"))


def test_sign_in_shows_who_you_are_and_sign_out_returns_to_the_gate(
    google_app: Callable[..., tuple[LiveApp, Idp]], new_page: NewPage
) -> None:
    live, _ = google_app()
    page = new_page()
    watch = Watch(page)

    page.goto(live.base + "/")
    expect(page.locator("#google-signin")).to_have_text("Continue with Google")
    assert page.locator("input").count() == 0  # no password field

    sign_in_with_google(page, live.base, ALICE)

    expect(page.locator("#account-email")).to_have_text(f"Signed in as {ALICE}")
    expect(page.locator("#signout")).to_have_text("Sign out")
    expect(page.locator("#new-session")).to_have_text("New session")
    sid = session_id(page)
    page.reload()  # still signed in, and back in the same session
    expect(page.locator("#account-email")).to_have_text(f"Signed in as {ALICE}")
    assert session_id(page) == sid

    page.click("#signout")
    expect(page.locator("#google-signin")).to_be_visible()
    assert page.locator("#account-email").count() == 0
    assert not any(c["name"] == "__Host-wb_access" for c in page.context.cookies())
    page.reload()
    expect(page.locator("#google-signin")).to_be_visible()  # the gate, not the workspace
    # The session id the signed-out page held is useless now.
    assert httpx.get(f"{live.base}/api/v1/sessions/{sid}").status_code == 401
    assert watch.errors == []


def test_signing_in_again_lands_in_the_most_recent_session(
    google_app: Callable[..., tuple[LiveApp, Idp]], new_page: NewPage
) -> None:
    live, _ = google_app()
    page = new_page()
    sign_in_with_google(page, live.base, ALICE)
    first = session_id(page)
    upload(page)
    page.click("#new-session")
    expect(page.locator("#workspace")).not_to_have_attribute("data-session-id", first)
    second = session_id(page)
    expect(page.locator(".file-list .file-name")).to_have_count(0)  # an empty session
    page.click("#signout")
    expect(page.locator("#google-signin")).to_be_visible()

    page.click("#google-signin")
    page.click(f"a:text-is('{ALICE}')")
    page.wait_for_selector("#workspace[data-session-id]")

    assert session_id(page) == second  # the newest, not the first (which holds the upload)


def test_new_session_starts_an_empty_session_and_keeps_the_old_one(
    google_app: Callable[..., tuple[LiveApp, Idp]], new_page: NewPage
) -> None:
    live, _ = google_app()
    page = new_page()
    watch = Watch(page)
    sign_in_with_google(page, live.base, ALICE)
    first = session_id(page)
    upload(page, name="first.xlsx")
    expect(page.locator(".file-list .file-name")).to_have_text("first.xlsx")

    page.click("#new-session")

    expect(page.locator("#workspace")).not_to_have_attribute("data-session-id", first)
    second = session_id(page)
    assert second != first
    expect(page.locator(".file-list .file-name")).to_have_count(0)
    expect(page.locator("#composer textarea")).to_be_enabled()
    # The page now works on the new session: an upload goes to it.
    upload(page, name="second.xlsx")
    expect(page.locator(".file-list .file-name")).to_have_text("second.xlsx")
    state = page.evaluate("(id) => fetch('/api/v1/sessions/' + id).then(r => r.json())", second)
    assert [f["name"] for f in state["files"]] == ["second.xlsx"]
    old = page.evaluate("(id) => fetch('/api/v1/sessions/' + id).then(r => r.json())", first)
    assert [f["name"] for f in old["files"]] == ["first.xlsx"]  # still Alice's, still there
    page.reload()
    assert session_id(page) == second  # the pointer follows the new session
    assert watch.errors == [] and watch.bad() == []


def test_two_people_in_two_browsers_never_see_each_other(
    google_app: Callable[..., tuple[LiveApp, Idp]], new_page: NewPage
) -> None:
    live, _ = google_app()
    alice, bob = new_page(), new_page()
    sign_in_with_google(alice, live.base, ALICE)
    sign_in_with_google(bob, live.base, BOB)
    alice_sid = session_id(alice)
    upload(alice, name="alices-private.xlsx")
    expect(bob.locator("#account-email")).to_have_text(f"Signed in as {BOB}")
    assert session_id(bob) != alice_sid

    for path in (
        f"/api/v1/sessions/{alice_sid}",
        f"/api/v1/sessions/{alice_sid}/files",
        f"/ui/s/{alice_sid}/transcript",
    ):
        status = bob.evaluate("(p) => fetch(p).then(r => r.status)", path)
        assert status == 404, path
    # Bob typing Alice's address into his browser gets the same answer as for no session at all.
    bob.goto(f"{live.base}/ui/s/{alice_sid}/transcript")
    shown = bob.inner_text("body")
    assert "session_not_found" in shown and "alices-private.xlsx" not in shown
    bob.goto(f"{live.base}/ui/s/s_{'x' * 22}/transcript")
    assert bob.inner_text("body") == shown
    bob.goto(live.base + "/")
    assert "alices-private.xlsx" not in bob.inner_text("body")
    assert session_id(bob) != alice_sid


def test_a_person_who_is_not_on_the_list_is_told_so_at_the_gate(
    google_app: Callable[..., tuple[LiveApp, Idp]], new_page: NewPage
) -> None:
    live, _ = google_app(allowed_emails=(ALICE,))
    page = new_page()

    page.goto(live.base + "/")
    page.click("#google-signin")
    page.click(f"a:text-is('{BOB}')")

    expect(page.locator(".notice")).to_have_text(
        "That Google account isn't on the list of people who may use this demo."
    )
    expect(page.locator("#google-signin")).to_be_visible()
    assert page.locator("#workspace").count() == 0
    page.click("#google-signin")  # and another account still works
    page.click(f"a:text-is('{ALICE}')")
    expect(page.locator("#account-email")).to_have_text(f"Signed in as {ALICE}")


def test_a_restart_of_the_server_does_not_sign_anyone_out(
    google_app: Callable[..., tuple[LiveApp, Idp]], new_page: NewPage
) -> None:
    live, _ = google_app()
    page = new_page()
    sign_in_with_google(page, live.base, ALICE)
    sid = session_id(page)

    live.restart(live.factory)
    page.reload()

    expect(page.locator("#account-email")).to_have_text(f"Signed in as {ALICE}")
    assert session_id(page) == sid
