"""What the page shows once there is a real sign-in: the gate, the header, New session."""

from __future__ import annotations

import re
from pathlib import Path

from backend.main import create_app
from backend.settings import Settings
from tests.backend.factories import PipelineFactory, QueuedProvider
from tests.backend.helpers import new_session
from tests.backend.signin_helpers import ALICE, BOB, Rig, browser, sign_in
from tests.idp.provider import Account
from tests.web.htmltools import parse


def test_the_gate_is_a_google_button_and_no_password_field(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    page = rig.person().get("/")

    tree = parse(page.text)
    (button,) = tree.find_all("a", id="google-signin")
    assert button.get("href") == "/auth/google/start"
    assert button.text().strip() == "Continue with Google"
    assert button.has_class("btn-primary")
    assert tree.find("form") is None and tree.find("input") is None  # a link, not a form
    # (form-action 'self' would block a form's redirect to Google; a link is not a form)
    assert "form-action 'self'" in page.headers["content-security-policy"]
    assert page.headers["cache-control"] == "no-store"


def test_the_header_has_the_email_sign_out_and_new_session(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    tree = parse(rig.person(ALICE).get("/").text)

    account = tree.find("div", **{"class": "account"})
    assert account is not None
    assert tree.find("span", id="account-email").text().strip() == f"Signed in as {ALICE}"  # type: ignore[union-attr]
    (form,) = tree.find_all("form", action="/auth/signout")
    assert form.get("method") == "post"
    assert form.find("button", id="signout").text().strip() == "Sign out"  # type: ignore[union-attr]
    new = tree.find("button", id="new-session")
    assert new is not None and new.text().strip() == "New session"
    assert new.get("hx-post") == "/ui/sessions" and new.get("hx-target") == "#workspace"
    assert new.get("hx-swap") == "outerHTML" and "data-new-session" in new.attrs


def test_an_email_is_shown_as_text_and_never_as_markup(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    nasty = '"><img src=x onerror=alert(1)>@example.test'
    rig.idp.accounts[nasty.lower()] = Account(nasty.lower(), "sub-nasty")

    page = rig.person(nasty.lower()).get("/").text

    tree = parse(page)
    assert tree.find("img") is None  # no element was made from it
    assert tree.find("span", id="account-email").text().strip() == f"Signed in as {nasty.lower()}"  # type: ignore[union-attr]
    assert "&lt;img" in page


def test_the_dev_server_has_new_session_but_no_account_controls(tmp_path: Path) -> None:
    app = create_app(
        Settings(data_dir=tmp_path / "d", env="dev"), PipelineFactory(QueuedProvider([]))
    )
    from fastapi.testclient import TestClient

    tree = parse(TestClient(app, base_url="https://testserver").get("/").text)

    assert tree.find("button", id="new-session") is not None
    assert tree.find("span", id="account-email") is None and tree.find("form") is None


def test_new_session_makes_a_session_that_is_the_pages_and_the_persons(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    alice, bob = rig.person(ALICE), rig.person(BOB)
    opened = alice.post("/ui/sessions")  # what the page does on load
    first = re.search(r'id="workspace"[^>]*data-session-id="([^"]+)"', opened.text).group(1)  # type: ignore[union-attr]

    made = alice.post("/ui/sessions")  # the New session button

    assert made.status_code == 200
    second = re.search(r'id="workspace"[^>]*data-session-id="([^"]+)"', made.text).group(1)  # type: ignore[union-attr]
    assert second != first
    assert f"__Host-wb_session={second}" in made.headers["set-cookie"]
    # Both are Alice's; neither is Bob's.
    assert alice.get(f"/api/v1/sessions/{first}").status_code == 200
    assert alice.get(f"/api/v1/sessions/{second}").status_code == 200
    for sid in (first, second):
        assert bob.get(f"/api/v1/sessions/{sid}").status_code == 404
    assert second in alice.get("/").text  # the page now opens the new one


def test_the_script_lets_new_session_replace_the_session_and_nothing_else() -> None:
    script = Path("backend/web/static/app.js").read_text()

    assert "[data-new-session]" in script
    assert script.count("data-new-session") == 1  # one exemption, in the swap guard
    assert "sign-in-again" in script and "enter-password-again" not in script


def test_the_401_notice_offers_to_sign_in_again_not_to_enter_a_password(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    page = rig.person(ALICE).get("/").text

    template = re.search(r'<template data-copy="sign-in-again">(.*?)</template>', page).group(1)  # type: ignore[union-attr]
    assert template == "Sign in again"
    assert "password" not in page.lower()


def test_a_refused_sign_in_shows_one_notice_on_the_gate(tmp_path: Path) -> None:
    rig = Rig(tmp_path, allowed_emails=(ALICE,))
    client = rig.person()

    wrong = sign_in(client, rig.idp, ALICE, "expired")
    denied = sign_in(rig.person(), rig.idp, BOB)

    for response, text in (
        (wrong, "Sign-in didn't work. Try again."),
        (denied, "That Google account isn't on the list of people who may use this demo."),
    ):
        tree = parse(response.text)
        (notice,) = tree.find_all("p", **{"class": "notice"})
        assert notice.text().strip() == text and notice.get("role") == "alert"
        assert tree.find("a", id="google-signin") is not None  # and the way to try again
        assert "content-security-policy" in response.headers


def test_after_signing_in_the_browser_is_sent_on_by_the_page_not_by_a_redirect(
    tmp_path: Path,
) -> None:
    """A redirect from Google's site to ours would be a cross-site chain, and the SameSite=Strict
    access cookie would not be sent on it: the person would see the gate. A 200 page that moves on
    by itself starts a same-site navigation."""
    rig = Rig(tmp_path)

    response = sign_in(rig.person(), rig.idp, ALICE)

    assert response.status_code == 200 and "location" not in response.headers
    tree = parse(response.text)
    meta = [m for m in tree.find_all("meta") if m.get("http-equiv") == "refresh"]
    assert [m.get("content") for m in meta] == ["0; url=/"]
    assert tree.find("a", href="/") is not None  # and a link, for a browser that doesn't refresh


def test_the_gate_and_header_say_nothing_about_a_users_file(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    for page in (rig.person().get("/").text, rig.person(ALICE).get("/").text):
        text = " ".join(re.sub(r"<[^>]+>", " ", page).split()).lower()
        for word in ("done", "success", "removed", "cleaned", "saved"):
            assert not re.search(rf"\b{word}\b", text), word


def test_a_second_browser_signed_in_as_the_same_person_sees_the_same_sessions(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    one = rig.person(ALICE)
    sid = new_session(one)

    two = rig.person(ALICE)

    assert sid in two.get("/").text
    assert two.get(f"/api/v1/sessions/{sid}").status_code == 200
    assert browser(rig.app).get(f"/api/v1/sessions/{sid}").status_code == 401
