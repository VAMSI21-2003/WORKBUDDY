"""Sign in with Google, against a fake identity provider that issues real signed tokens.

Everything runs through the real app: the real routes, the real gate, the real token checks, the
real sign-in files on disk. Only Google is faked (``tests/idp``), and it signs RS256 tokens with a
real key and publishes it at ``/jwks``, so a token the app accepts is one it verified.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.oidc import PENDING_TTL_S
from tests.backend.factories import PipelineFactory, QueuedProvider
from tests.backend.helpers import (
    BEARER,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    OPERATOR_TOKEN,
    PASSWORD,
    PUBLIC_ORIGIN,
    FakeClock,
    production_settings,
)
from tests.backend.signin_helpers import (
    ACCESS_COOKIE,
    ALICE,
    BOB,
    Rig,
    browser,
    sign_in,
    signed_in,
    token_of,
)
from tests.idp.provider import FLAWS

PROBLEM = "Sign-in didn't work. Try again."
NOT_ALLOWED = "That Google account isn't on the list of people who may use this demo."
LOGIN_COOKIE = "__Host-wb_login"


def _refused(client: TestClient, response: object, status: int = 400) -> None:
    """A sign-in that didn't happen: the one page, no access cookie, no way in."""
    assert response.status_code == status, response.text  # type: ignore[attr-defined]
    assert PROBLEM in response.text  # type: ignore[attr-defined]
    assert ACCESS_COOKIE not in client.cookies
    assert client.get("/api/v1/sessions/s_x").status_code == 401


# -- The flow, when everything is right ----------------------------------------------------------


def test_a_person_signs_in_and_the_page_knows_who(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    assert "Continue with Google" in client.get("/").text

    response = sign_in(client, rig.idp, ALICE)

    assert response.status_code == 200
    assert 'http-equiv="refresh" content="0; url=/"' in response.text
    home = client.get("/")
    assert home.status_code == 200
    assert f"Signed in as {ALICE}" in home.text and "Sign out" in home.text
    assert client.post("/api/v1/sessions", json={}).status_code == 201


def test_the_start_sends_the_browser_to_google_with_a_state_a_nonce_and_pkce(
    tmp_path: Path,
) -> None:
    rig = Rig(tmp_path)
    client = rig.person()

    started = client.get("/auth/google/start", follow_redirects=False)

    assert started.status_code == 302
    target = urlsplit(started.headers["location"])
    query = {k: v[0] for k, v in parse_qs(target.query).items()}
    assert f"{target.scheme}://{target.netloc}{target.path}" == "https://idp.test/authorize"
    assert query["client_id"] == GOOGLE_CLIENT_ID
    assert query["redirect_uri"] == PUBLIC_ORIGIN + "/auth/google/callback"
    assert query["response_type"] == "code"
    assert query["scope"] == "openid email"
    assert query["code_challenge_method"] == "S256"
    assert len(query["state"]) >= 40 and len(query["nonce"]) >= 40
    assert len(query["code_challenge"]) == 43
    assert GOOGLE_CLIENT_SECRET not in started.headers["location"]
    cookie = started.headers["set-cookie"]
    assert cookie.startswith(LOGIN_COOKIE + "=")
    for attribute in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
        assert attribute.lower() in cookie.lower(), cookie
    assert "domain" not in cookie.lower()
    assert started.headers["cache-control"] == "no-store"


def test_each_start_makes_a_new_state_nonce_and_challenge(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    seen = []
    for _ in range(3):
        started = rig.person().get("/auth/google/start", follow_redirects=False)
        seen.append(parse_qs(urlsplit(started.headers["location"]).query))

    for name in ("state", "nonce", "code_challenge"):
        assert len({q[name][0] for q in seen}) == 3


def test_the_access_cookie_is_the_existing_one_with_the_same_attributes(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()

    response = sign_in(client, rig.idp, ALICE)

    cookies = [v for k, v in response.headers.multi_items() if k == "set-cookie"]
    (access,) = [c for c in cookies if c.startswith(ACCESS_COOKIE + "=")]
    assert access.split(";", 1)[1].strip().startswith("Path=/")
    assert "; Secure; HttpOnly; SameSite=Strict" in access
    assert "max-age" not in access.lower() and "expires" not in access.lower()
    assert any(c.startswith(LOGIN_COOKIE + "=") for c in cookies)  # the login cookie, cleared
    assert PUBLIC_ORIGIN not in response.headers.get("location", "")


def test_the_callback_clears_the_last_persons_session_pointer(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    client.cookies.set("__Host-wb_session", "s_left_by_the_last_person")

    response = sign_in(client, rig.idp, ALICE)

    cookies = [v for k, v in response.headers.multi_items() if k == "set-cookie"]
    (pointer,) = [c for c in cookies if c.startswith("__Host-wb_session=")]
    assert "Max-Age=0" in pointer


def test_a_discovery_document_for_another_issuer_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    honest = rig.idp.discovery
    rig.idp.discovery = lambda: {**honest(), "issuer": "https://evil.example"}  # type: ignore[method-assign]
    client = rig.person()

    started = client.get("/auth/google/start", follow_redirects=False)

    assert started.status_code == 502 and PROBLEM in started.text


def test_an_email_is_stored_and_shown_in_lower_case(tmp_path: Path) -> None:
    from tests.idp.provider import Account

    rig = Rig(tmp_path)
    rig.idp.accounts["Mixed.Case@Example.Test"] = Account("Mixed.Case@Example.Test", "sub-mixed")
    client = rig.person()

    sign_in(client, rig.idp, "Mixed.Case@Example.Test")

    (record,) = rig.app.state.access.signins.files()
    assert json.loads(record.read_text())["email"] == "mixed.case@example.test"
    assert "Signed in as mixed.case@example.test" in client.get("/").text


def test_a_stale_callback_does_not_break_a_sign_in_still_going_on(tmp_path: Path) -> None:
    """Two tabs, or a stray link to the callback: the newer sign-in must still be finishable."""
    rig = Rig(tmp_path)
    client = rig.person()
    older = client.get("/auth/google/start", follow_redirects=False)
    older_back = rig.idp.authorize(older.headers["location"], ALICE)
    newer = client.get("/auth/google/start", follow_redirects=False)  # replaces the binder cookie
    newer_back = rig.idp.authorize(newer.headers["location"], ALICE)

    stale = client.get(older_back)  # the older tab: its binder was replaced
    _refused(client, stale)
    assert LOGIN_COOKIE not in stale.headers.get("set-cookie", "")  # the binder is left alone
    stray = client.get("/auth/google/callback?code=x&state=nothing")
    _refused(client, stray)
    assert LOGIN_COOKIE not in stray.headers.get("set-cookie", "")

    assert client.get(newer_back).status_code == 200  # and the newer one finishes


def test_the_redirect_address_comes_from_the_setting_not_from_the_request(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()

    started = client.get(
        "/auth/google/start",
        headers={
            "Host": "evil.example",
            "X-Forwarded-Host": "evil.example",
            "Origin": "https://evil.example",
        },
        follow_redirects=False,
    )

    query = parse_qs(urlsplit(started.headers["location"]).query)
    assert query["redirect_uri"] == [PUBLIC_ORIGIN + "/auth/google/callback"]
    assert "evil" not in started.headers["location"]


def test_no_parameter_can_choose_where_the_browser_goes_after_sign_in(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    started = client.get(
        "/auth/google/start?next=https://evil.example&redirect=https://evil.example&return_to=/x",
        follow_redirects=False,
    )
    assert "evil" not in started.headers["location"]

    back = rig.idp.authorize(started.headers["location"], ALICE)
    response = client.get(back + "&next=https://evil.example&redirect_uri=https://evil.example")

    assert response.status_code == 200
    assert "evil" not in response.text and "location" not in response.headers
    assert 'content="0; url=/"' in response.text


# -- What must be refused ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "flaw",
    [
        "wrong_nonce",
        "wrong_audience",
        "wrong_issuer",
        "expired",
        "unverified_email",
        "email_not_a_boolean",
        "tampered_signature",
        "tampered_payload",
        "unknown_kid",
        "wrong_key",
        "no_kid",
        "no_email",
        "no_subject",
        "wrong_azp",
        "future_iat",
        "alg_none",
        "alg_hs256",
        "two_audiences",
    ],
)
def test_a_token_that_is_wrong_in_any_way_signs_nobody_in(tmp_path: Path, flaw: str) -> None:
    assert flaw in FLAWS
    rig = Rig(tmp_path)
    client = rig.person()

    response = sign_in(client, rig.idp, ALICE, flaw)

    _refused(client, response)
    assert rig.app.state.access.signins.files() == []


def test_every_flaw_is_covered_by_a_test() -> None:
    parametrize = test_a_token_that_is_wrong_in_any_way_signs_nobody_in.pytestmark[0]  # type: ignore[attr-defined]
    assert set(parametrize.args[1]) == set(FLAWS)


def test_a_wrong_state_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    started = client.get("/auth/google/start", follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], ALICE)
    wrong = re.sub(r"state=[^&]+", "state=not-the-state", back)

    response = client.get(wrong)

    _refused(client, response)
    assert "POST /token" not in rig.idp.log  # refused before the code is even used


def test_a_missing_state_or_code_is_refused(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    client.get("/auth/google/start", follow_redirects=False)

    for query in ("", "?code=x", "?state=x", "?error=access_denied&state=x"):
        _refused(client, client.get("/auth/google/callback" + query))


def test_the_provider_saying_no_is_refused_and_uses_up_the_login(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    started = client.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(started.headers["location"]).query)["state"][0]

    cancelled = client.get(f"/auth/google/callback?error=access_denied&state={state}")
    _refused(client, cancelled)

    back = rig.idp.authorize(started.headers["location"], ALICE)
    _refused(client, client.get(back))  # the login was used up by the answer above


def test_a_code_used_twice_signs_in_once(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    started = client.get("/auth/google/start", follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], ALICE)

    first = client.get(back)
    assert first.status_code == 200
    first_token = token_of(client)

    # The same callback again, in the same browser: the pending login is gone.
    again = client.get(back)
    assert again.status_code == 400 and PROBLEM in again.text
    assert rig.idp.log.count("POST /token") == 1  # refused before the provider was asked at all
    assert token_of(client) == first_token  # (the existing sign-in is untouched)

    # The same code under a fresh login (a different state): the provider refuses the code.
    other = rig.person()
    second = other.get("/auth/google/start", follow_redirects=False)
    state = parse_qs(urlsplit(second.headers["location"]).query)["state"][0]
    code = parse_qs(urlsplit(back).query)["code"][0]
    _refused(other, other.get(f"/auth/google/callback?code={code}&state={state}"))
    assert rig.idp.log.count("POST /token") == 2  # the replayed code did reach the provider


def test_a_pending_login_can_be_taken_once_even_with_the_right_binder() -> None:
    """The callback clears the binder cookie, so the app-level replay can't show single use;
    this is the pending store's own rule."""
    from backend.oidc import PendingLogins

    clock = FakeClock()
    pending = PendingLogins(clock)
    state, nonce, verifier, binder = pending.start()

    first = pending.take(state, binder)
    second = pending.take(state, binder)

    assert first is not None and (first.nonce, first.verifier) == (nonce, verifier)
    assert second is None
    assert len(pending) == 0


def test_a_callback_without_the_browsers_own_binder_is_refused(tmp_path: Path) -> None:
    """Login CSRF: an attacker starts a login, and gets the victim's browser to finish it."""
    rig = Rig(tmp_path)
    attacker = rig.person()
    started = attacker.get("/auth/google/start", follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], BOB)

    victim = rig.person()
    _refused(victim, victim.get(back))  # no binder cookie
    victim.cookies.set(LOGIN_COOKIE, "some-other-binder")
    _refused(victim, victim.get(back))  # a wrong one

    # ...and neither attempt used up the real login: the attacker's own browser can finish it.
    assert attacker.get(back).status_code == 200


def test_a_pending_login_expires(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = Rig(tmp_path, clock=clock)
    client = rig.person()
    started = client.get("/auth/google/start", follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], ALICE)

    clock.advance(PENDING_TTL_S + 1)

    _refused(client, client.get(back))


def test_the_provider_being_unreachable_is_the_same_page(tmp_path: Path) -> None:
    import httpx

    rig = Rig(tmp_path)
    broken = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(500)), follow_redirects=False
    )
    app = create_app(rig.settings, rig.factory, oidc_http=broken)
    client = browser(app)

    response = client.get("/auth/google/start", follow_redirects=False)

    assert response.status_code == 502 and PROBLEM in response.text


def test_the_reason_is_in_the_log_and_nothing_from_the_token_is(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    rig = Rig(tmp_path)
    client = rig.person()
    caplog.set_level(logging.DEBUG)

    response = sign_in(client, rig.idp, ALICE, "wrong_audience")

    assert "token_audience" in caplog.text
    forbidden = [ALICE, "sub-alice", GOOGLE_CLIENT_SECRET, "someone-elses-client"]
    for text in (caplog.text, response.text, str(response.headers)):
        for secret in forbidden:
            assert secret not in text, secret


def test_every_refusal_looks_the_same_to_the_person(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    bodies = set()
    for flaw in ("wrong_nonce", "expired", "unverified_email", "tampered_signature"):
        client = rig.person()
        response = sign_in(client, rig.idp, ALICE, flaw)
        bodies.add((response.status_code, re.sub(r"\s+", " ", response.text)))

    assert len(bodies) == 1


# -- Google's keys -------------------------------------------------------------------------------


def test_the_keys_are_fetched_once_and_cached(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    for email in (ALICE, BOB, ALICE):
        signed_in(rig.person(), rig.idp, email)

    assert rig.idp.log.count("GET /jwks") == 1
    assert rig.idp.log.count("GET /.well-known/openid-configuration") == 1


def test_an_unknown_key_id_forces_at_most_one_refetch_a_minute(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = Rig(tmp_path, clock=clock)
    signed_in(rig.person(), rig.idp, ALICE)
    clock.advance(61)
    fetched = rig.idp.log.count("GET /jwks")

    for _ in range(5):
        _refused(rig.person(), sign_in(rig.person(), rig.idp, ALICE, "unknown_kid"))
    assert rig.idp.log.count("GET /jwks") == fetched + 1  # one refetch, not five

    clock.advance(61)
    sign_in(rig.person(), rig.idp, ALICE, "unknown_kid")
    assert rig.idp.log.count("GET /jwks") == fetched + 2


def test_a_rotated_key_is_picked_up(tmp_path: Path) -> None:
    clock = FakeClock()
    rig = Rig(tmp_path, clock=clock)
    signed_in(rig.person(), rig.idp, ALICE)
    clock.advance(61)
    rig.idp.kid = "fake-key-2"  # the provider publishes its key under a new id

    signed_in(rig.person(), rig.idp, BOB)


# -- The allowed list ----------------------------------------------------------------------------


def test_anyone_with_a_verified_email_may_sign_in_when_there_is_no_list(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    rig.idp.accounts["carol@elsewhere.test"] = type(rig.idp.accounts[ALICE])(
        "carol@elsewhere.test", "sub-carol"
    )

    signed_in(rig.person(), rig.idp, "carol@elsewhere.test")


def test_with_an_allowed_list_only_those_emails_may(tmp_path: Path) -> None:
    rig = Rig(tmp_path, allowed_emails=("Alice@Example.test",))

    signed_in(rig.person(), rig.idp, ALICE)  # (case doesn't matter)
    bob = rig.person()
    response = sign_in(bob, rig.idp, BOB)

    assert response.status_code == 403 and NOT_ALLOWED in response.text
    assert ACCESS_COOKIE not in bob.cookies


def test_a_verified_but_unlisted_email_is_not_remembered_anywhere(tmp_path: Path) -> None:
    rig = Rig(tmp_path, allowed_emails=(ALICE,))

    sign_in(rig.person(), rig.idp, BOB)

    assert rig.app.state.access.signins.files() == []
    assert BOB.encode() not in b"".join(
        p.read_bytes() for p in rig.settings.data_dir.rglob("*") if p.is_file()
    )


def test_removing_someone_from_the_list_ends_their_sign_in(tmp_path: Path) -> None:
    first = Rig(tmp_path)
    client = first.person(ALICE)
    token = token_of(client)
    assert client.post("/api/v1/sessions", json={}).status_code == 201

    # The server restarts with a list that no longer has Alice.
    second = create_app(
        production_settings(tmp_path, allowed_emails=(BOB,)),
        first.factory,
        oidc_http=first.idp.client(),
    )
    restarted = browser(second)
    restarted.headers["Cookie"] = f"{ACCESS_COOKIE}={token}"

    assert restarted.post("/api/v1/sessions", json={}).status_code == 401


# -- Sign-out and expiry ---------------------------------------------------------------------


def test_sign_out_ends_the_sign_in_on_the_server_and_in_the_browser(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person(ALICE)
    token = token_of(client)
    client.cookies.set("__Host-wb_session", "s_something")

    response = client.post("/auth/signout", follow_redirects=False)

    assert response.status_code == 303 and response.headers["location"] == "/"
    cleared = {c.split("=", 1)[0] for c in response.headers.get_list("set-cookie")}
    assert {ACCESS_COOKIE, "__Host-wb_session"} <= cleared
    assert ACCESS_COOKIE not in client.cookies
    assert rig.app.state.access.signins.files() == []
    # Even a copy of the old cookie, replayed, is dead.
    stolen = browser(rig.app)
    stolen.headers["Cookie"] = f"{ACCESS_COOKIE}={token}"
    assert stolen.post("/api/v1/sessions", json={}).status_code == 401
    assert "Continue with Google" in client.get("/").text


def test_the_page_signs_out_with_a_request_that_sends_its_origin_and_is_told_where_to_go(
    tmp_path: Path,
) -> None:
    """A plain form POST would send ``Origin: null`` under the page's no-referrer policy and be
    refused, so the page's sign-out is an htmx request, answered with ``HX-Redirect``."""
    rig = Rig(tmp_path)
    client = rig.person(ALICE)

    response = client.post("/auth/signout", headers={"HX-Request": "true"})

    assert response.status_code == 204 and response.headers["HX-Redirect"] == "/"
    assert ACCESS_COOKIE not in client.cookies
    assert rig.app.state.access.signins.files() == []
    assert 'hx-post="/auth/signout"' in rig.person(ALICE).get("/").text


def test_sign_out_needs_the_pages_origin(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person(ALICE)

    for headers in ({"Origin": "https://evil.example"}, {"Origin": "null"}):
        assert client.post("/auth/signout", headers=headers).status_code == 403
    bare = browser(rig.app)
    bare.headers.pop("Origin")
    bare.cookies.set(ACCESS_COOKIE, token_of(client))
    assert bare.post("/auth/signout").status_code == 403
    assert client.post("/api/v1/sessions", json={}).status_code == 201  # still signed in


def test_sign_out_with_no_sign_in_still_works(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    response = rig.person().post("/auth/signout", follow_redirects=False)

    assert response.status_code == 303


def test_a_sign_in_expires_and_is_not_renewed_by_use(tmp_path: Path) -> None:
    wall = FakeClock(1_900_000_000.0)
    rig = Rig(tmp_path, wall_clock=wall, access_ttl_hours=2)
    client = rig.person(ALICE)

    wall.advance(3599)
    assert client.post("/api/v1/sessions", json={}).status_code == 201
    wall.advance(3599)  # 7198 s in: the use above did not extend it
    assert client.post("/api/v1/sessions", json={}).status_code == 201
    wall.advance(3)

    assert client.post("/api/v1/sessions", json={}).status_code == 401
    assert rig.app.state.access.signins.files() == []  # and the file is gone


def test_the_sweep_removes_expired_sign_ins(tmp_path: Path) -> None:
    wall = FakeClock(1_900_000_000.0)
    rig = Rig(tmp_path, wall_clock=wall, access_ttl_hours=1)
    rig.person(ALICE), rig.person(BOB)
    assert len(rig.app.state.access.signins.files()) == 2

    wall.advance(3601)
    rig.app.state.maintenance.run_once()

    assert rig.app.state.access.signins.files() == []


def test_signing_in_again_signs_the_old_cookie_out(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = rig.person(ALICE)
    old = token_of(client)

    signed_in(client, rig.idp, BOB)

    assert token_of(client) != old
    stale = browser(rig.app)
    stale.headers["Cookie"] = f"{ACCESS_COOKIE}={old}"
    assert stale.post("/api/v1/sessions", json={}).status_code == 401


def test_at_most_the_configured_number_of_sign_ins_are_kept(tmp_path: Path) -> None:
    rig = Rig(tmp_path, access_max_tokens=2)

    first = rig.person(ALICE)
    rig.person(BOB)
    rig.person(ALICE)

    assert len(rig.app.state.access.signins.files()) == 2
    assert first.post("/api/v1/sessions", json={}).status_code == 401  # the soonest to expire


# -- A restart -----------------------------------------------------------------------------------


def test_a_restart_keeps_people_signed_in_and_stores_only_a_hash(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    alice = rig.person(ALICE)
    token = token_of(alice)
    session_id = alice.post("/api/v1/sessions", json={}).json()["session_id"]

    restarted = create_app(rig.settings, rig.factory, oidc_http=rig.idp.client())
    again = browser(restarted)
    again.headers["Cookie"] = f"{ACCESS_COOKIE}={token}"

    assert again.get(f"/api/v1/sessions/{session_id}").status_code == 200
    assert f"Signed in as {ALICE}" in again.get("/").text
    # Disk: the hash, the subject, the email and the times; the token itself nowhere.
    on_disk = b"".join(p.read_bytes() for p in rig.settings.data_dir.rglob("*") if p.is_file())
    assert token.encode() not in on_disk
    (signin,) = (rig.settings.data_dir / "auth" / "signins").glob("*.json")
    assert json.loads(signin.read_text()).keys() == {
        "version",
        "owner",
        "email",
        "issued_at",
        "expires_at",
    }
    import hashlib

    assert signin.stem == hashlib.sha256(token.encode()).hexdigest()


def test_a_restart_does_not_resurrect_a_signed_out_person(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    alice = rig.person(ALICE)
    token = token_of(alice)
    alice.post("/auth/signout")

    restarted = create_app(rig.settings, rig.factory, oidc_http=rig.idp.client())
    again = browser(restarted)
    again.headers["Cookie"] = f"{ACCESS_COOKIE}={token}"

    assert again.post("/api/v1/sessions", json={}).status_code == 401


def test_bad_sign_in_files_are_ignored_and_removed_at_start(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    folder = rig.settings.data_dir / "auth" / "signins"
    (folder / ("0" * 64 + ".json")).write_text("not json")
    (folder / ("1" * 64 + ".json")).write_text(
        json.dumps(
            {"version": 1, "owner": "dev", "email": "x@y.z", "issued_at": 0, "expires_at": 9e12}
        )
    )
    (folder / "stray.tmp").write_text("x")

    app = create_app(rig.settings, rig.factory, oidc_http=rig.idp.client())

    assert len(app.state.access.signins) == 0
    assert list(folder.iterdir()) == []


# -- Production has no password ------------------------------------------------------------------


def test_in_production_the_demo_password_is_not_a_credential(tmp_path: Path) -> None:
    rig = Rig(tmp_path, demo_password=PASSWORD)
    client = browser(rig.app)

    assert client.post("/api/v1/access", data={"password": PASSWORD}).status_code == 401
    assert ACCESS_COOKIE not in client.cookies
    assert client.get("/api/v1/sessions/s_x", headers=BEARER).status_code == 401
    assert "password" not in client.get("/").text.lower()
    assert "Continue with Google" in client.get("/").text


def test_a_password_left_in_the_settings_is_warned_about_not_fatal(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)

    production_settings(tmp_path, demo_password=PASSWORD)

    assert "WORKBUDDY_DEMO_PASSWORD is set but ignored" in caplog.text
    assert PASSWORD not in caplog.text


def test_outside_production_the_password_still_works_beside_google(tmp_path: Path) -> None:
    rig = Rig(tmp_path, env="", demo_password=PASSWORD)
    client = browser(rig.app)
    gate = client.get("/").text

    assert "Continue with Google" in gate and 'name="password"' in gate
    assert client.post("/api/v1/access", data={"password": PASSWORD}).status_code == 204


def test_dev_with_nothing_configured_is_still_open(tmp_path: Path) -> None:
    from backend.settings import Settings

    app = create_app(
        Settings(data_dir=tmp_path / "d", env="dev"), PipelineFactory(QueuedProvider([]))
    )
    client = TestClient(app, base_url="http://127.0.0.1:8000")

    assert client.post("/api/v1/sessions", json={}).status_code == 201
    assert "Sign out" not in client.get("/").text
    assert client.get("/auth/google/start").status_code == 404  # no Google configured


# -- The operator --------------------------------------------------------------------------------


def test_the_operator_token_reaches_only_the_sessions_it_made(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    alice = rig.person(ALICE)
    mine = alice.post("/api/v1/sessions", json={}).json()["session_id"]
    operator = browser(rig.app)
    headers = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

    made = operator.post("/api/v1/sessions", json={}, headers=headers)
    assert made.status_code == 201
    theirs = made.json()["session_id"]

    assert operator.get(f"/api/v1/sessions/{theirs}", headers=headers).status_code == 200
    assert operator.get(f"/api/v1/sessions/{mine}", headers=headers).status_code == 404
    assert alice.get(f"/api/v1/sessions/{theirs}").status_code == 404


def test_a_wrong_operator_token_is_the_one_401(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = browser(rig.app)

    for token in (OPERATOR_TOKEN[:-1], OPERATOR_TOKEN + "x", "", "Bearer"):
        response = client.get("/api/v1/sessions/s_x", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401


def test_the_operator_token_is_not_a_cookie(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    client = browser(rig.app)
    client.headers["Cookie"] = f"{ACCESS_COOKIE}={OPERATOR_TOKEN}"

    assert client.post("/api/v1/sessions", json={}).status_code == 401


def test_the_secrets_never_appear_in_a_response_a_log_or_the_settings_repr(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    rig = Rig(tmp_path)
    alice = rig.person(ALICE)
    texts = [repr(rig.settings), caplog.text]
    for path in ("/", "/auth/google/start", "/api/v1/sessions/s_x"):
        response = alice.get(path, follow_redirects=False)
        texts += [response.text, str(response.headers)]

    for text in texts:
        assert GOOGLE_CLIENT_SECRET not in text
        assert OPERATOR_TOKEN not in text


def test_start_and_callback_are_limited_per_address(tmp_path: Path) -> None:
    rig = Rig(tmp_path, relaxed=False)
    client = rig.person()
    codes = [
        client.get("/auth/google/start", follow_redirects=False).status_code for _ in range(12)
    ]

    assert codes[:10] == [302] * 10 and codes[10:] == [429, 429]
