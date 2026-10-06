"""Who may call the API: the credential, the access cookie, the Origin check, and startup refusals.

Design "Decisions (2026-09-30)" item 1, "(2026-09-30, part 2)" ask 4, and
"(2026-10-01)" items 1 and 2. Every test runs on production-like settings (a
password, an https public origin, the real buckets) and an injected clock.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.access import COOKIE_NAME, AccessControl, CredentialResult, TokenStore
from backend.main import create_app
from backend.ratelimit import iter_routes
from backend.settings import Settings, SettingsError
from tests.backend.helpers import (
    BEARER,
    ORIGIN,
    PASSWORD,
    PUBLIC_ORIGIN,
    FakeClock,
    cookie_value,
    exchange,
    locked_settings,
    with_cookie,
)

UNAUTHORIZED_BODY = {
    "error": {"code": "unauthorized", "message": "The demo password is missing or wrong."}
}
REALM = 'Bearer realm="workbuddy-demo"'
FORM = "application/x-www-form-urlencoded"


def _app(tmp_path: Path, clock: FakeClock | None = None, **overrides: Any) -> Any:
    return create_app(
        locked_settings(tmp_path, **overrides), lambda provider: None, clock=clock or FakeClock()
    )


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def app(tmp_path: Path, clock: FakeClock) -> Any:
    return _app(tmp_path, clock)


@pytest.fixture
def client(app: Any) -> TestClient:
    return TestClient(app)


def _token(client: TestClient) -> str:
    response = exchange(client)
    assert response.status_code == 204, response.text
    return cookie_value(response)


# -- The credential: every route, one failure --------------------------------------------------


def _concrete(path: str) -> str:
    return re.sub(r"\{[^}]+\}", "x", path)


def test_every_api_route_refuses_a_request_without_a_credential(
    app: Any, client: TestClient
) -> None:
    """A route added later is covered too: the list comes from the app's own routes."""
    routes = [
        r for r in iter_routes(app) if r.path.startswith("/api/v1") and r.path != "/api/v1/access"
    ]
    assert len(routes) >= 8
    for route in routes:
        for method in route.methods:
            response = client.request(method, _concrete(route.path))
            assert response.status_code == 401, (method, route.path)
            assert response.json() == UNAUTHORIZED_BODY
            assert response.headers["WWW-Authenticate"] == REALM


def test_healthz_and_the_access_route_need_no_credential(client: TestClient) -> None:
    assert client.get("/healthz").status_code == 200
    assert client.post("/api/v1/access", data={"password": "no"}, headers=ORIGIN).status_code == 401
    # (the 401 above is the wrong password; the route itself was reachable)


def test_every_failure_is_the_same_401_so_nobody_learns_why(
    client: TestClient, clock: FakeClock
) -> None:
    token = _token(client)
    stale = _token(client)
    clock.advance(12 * 3600 + 1)  # both tokens expire
    fresh = _token(client)
    cases: list[dict[str, str]] = [
        {},
        {"Authorization": "Bearer wrong"},
        {"Authorization": "Bearer"},
        {"Authorization": "Bearer "},
        {"Authorization": f"Basic {PASSWORD}"},
        {"Authorization": PASSWORD},
        with_cookie("not-a-token"),
        with_cookie(token),  # expired
        with_cookie(stale),  # expired
        {**with_cookie(fresh), "Authorization": "Bearer wrong"},  # Authorization wins
    ]

    seen = set()
    for headers in cases:
        response = client.get("/api/v1/sessions/s_x", headers=headers)
        assert response.status_code == 401, headers
        seen.add((response.text, response.headers["WWW-Authenticate"]))
        assert response.headers["Content-Type"] == "application/json"

    assert seen == {(client.get("/api/v1/sessions/s_x").text, REALM)}


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
def test_the_bearer_scheme_is_matched_case_insensitively(client: TestClient, scheme: str) -> None:
    response = client.post("/api/v1/sessions", headers={"Authorization": f"{scheme} {PASSWORD}"})

    assert response.status_code == 201


def test_the_password_is_compared_as_utf8_bytes(tmp_path: Path) -> None:
    password = "pässwörd-☃"
    client = TestClient(_app(tmp_path, demo_password=password))

    ok = client.post(
        "/api/v1/sessions", headers={"Authorization": b"Bearer " + password.encode("utf-8")}
    )
    wrong = client.post(
        "/api/v1/sessions",
        headers={"Authorization": b"Bearer " + password.encode("latin-1", "replace")},
    )
    via_form = exchange(client, password)

    assert ok.status_code == 201
    assert wrong.status_code == 401
    assert via_form.status_code == 204


def test_a_longer_or_shorter_password_never_matches(client: TestClient) -> None:
    for candidate in (PASSWORD + "x", PASSWORD[:-1], PASSWORD.upper(), " " + PASSWORD):
        response = client.post("/api/v1/sessions", headers={"Authorization": f"Bearer {candidate}"})
        assert response.status_code == 401, candidate


def test_a_wrong_bearer_is_refused_even_beside_a_valid_cookie(client: TestClient) -> None:
    token = _token(client)

    wrong = client.get(
        "/api/v1/sessions/s_x", headers={**with_cookie(token), "Authorization": "Bearer nope"}
    )
    cookie_only = client.get("/api/v1/sessions/s_x", headers=with_cookie(token))
    right_with_bad_cookie = client.get(
        "/api/v1/sessions/s_x", headers={**BEARER, **with_cookie("garbage")}
    )

    assert wrong.status_code == 401
    assert cookie_only.status_code == 404  # authorized; there is just no such session
    assert right_with_bad_cookie.status_code == 404


def test_two_authorization_headers_are_refused(client: TestClient) -> None:
    response = client.get(
        "/api/v1/sessions/s_x",
        headers=[("Authorization", f"Bearer {PASSWORD}"), ("Authorization", f"Bearer {PASSWORD}")],
    )

    assert response.status_code == 401


def test_the_gate_and_the_dependency_share_one_answer(app: Any, client: TestClient) -> None:
    """The credential is decided once per request (``check_credential``), so the limiter, the
    Origin check and the dependency can't disagree: a second call would say "invalid" here."""
    calls: list[int] = []

    def check_once(request: Any) -> CredentialResult:
        calls.append(1)
        return CredentialResult(True, "bearer") if len(calls) == 1 else CredentialResult(False)

    app.state.access.check = check_once

    response = client.get("/api/v1/sessions/s_x")

    assert response.status_code == 404  # authorized (it only lacks a session)
    assert len(calls) == 1


def test_the_demo_password_is_never_shown_by_repr(tmp_path: Path) -> None:
    assert PASSWORD not in repr(locked_settings(tmp_path))


# -- The access route --------------------------------------------------------------------------


def test_the_right_password_gets_a_session_cookie_with_the_fixed_attributes(
    client: TestClient,
) -> None:
    response = exchange(client)

    assert response.status_code == 204
    assert response.content == b""
    header = response.headers["set-cookie"]
    assert re.fullmatch(
        rf"{COOKIE_NAME}=[A-Za-z0-9_-]{{43}}; Path=/; Secure; HttpOnly; SameSite=Strict", header
    ), header
    for forbidden in ("Domain", "Max-Age", "Expires"):
        assert forbidden not in header  # a session cookie: the browser drops it on close
    assert PASSWORD not in header


def test_each_exchange_makes_a_new_token_that_is_never_the_password(client: TestClient) -> None:
    tokens = {_token(client) for _ in range(5)}

    assert len(tokens) == 5
    assert all(PASSWORD not in t and t not in PASSWORD for t in tokens)


def test_the_cookie_authenticates_later_requests(client: TestClient) -> None:
    token = _token(client)

    created = client.post("/api/v1/sessions", headers=with_cookie(token, **ORIGIN))
    read = client.get(
        f"/api/v1/sessions/{created.json()['session_id']}", headers=with_cookie(token)
    )

    assert created.status_code == 201
    assert read.status_code == 200


def test_a_browser_like_client_keeps_the_cookie_over_https(app: Any) -> None:
    """With an https base URL the jar stores and sends the Secure cookie, as a browser does."""
    browser = TestClient(app, base_url=PUBLIC_ORIGIN)

    assert exchange(browser).status_code == 204
    response = browser.get("/api/v1/sessions/s_x")

    assert response.status_code == 404  # authorized by the jar's cookie alone


@pytest.mark.parametrize(
    "data",
    [{"password": "nope"}, {"password": ""}, {"other": "x"}],
    ids=["wrong", "empty", "other field"],
)
def test_a_wrong_or_missing_password_is_the_one_401_and_sets_no_cookie(
    client: TestClient, data: dict[str, str]
) -> None:
    response = client.post("/api/v1/access", data=data, headers=ORIGIN)

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY
    assert response.headers["WWW-Authenticate"] == REALM
    assert "set-cookie" not in response.headers


@pytest.mark.parametrize(
    "request_kwargs",
    [
        {"json": {"password": PASSWORD}},
        {"files": {"password": (None, PASSWORD)}},  # multipart/form-data
        {"content": PASSWORD, "headers": {"Content-Type": "text/plain"}},
        {
            "content": b"password=x&password=y",
            "headers": {"Content-Type": "application/x-www-form-urlencoded"},
        },
    ],
    ids=["json", "multipart", "text", "two password fields"],
)
def test_anything_but_one_urlencoded_password_field_is_422(
    client: TestClient, request_kwargs: dict[str, Any]
) -> None:
    headers = {**ORIGIN, **request_kwargs.pop("headers", {})}

    response = client.post("/api/v1/access", headers=headers, **request_kwargs)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert "set-cookie" not in response.headers


def test_an_empty_form_body_is_a_missing_password(client: TestClient) -> None:
    response = client.post("/api/v1/access", content=b"", headers={**ORIGIN, "Content-Type": FORM})

    assert response.status_code == 401
    assert response.json() == UNAUTHORIZED_BODY


def test_the_password_in_the_url_is_not_a_password(client: TestClient) -> None:
    response = client.post(f"/api/v1/access?password={PASSWORD}", headers=ORIGIN)

    assert response.status_code == 422  # not a form


# -- The token store ---------------------------------------------------------------------------


def test_the_server_holds_only_the_hash_of_a_token(app: Any, client: TestClient) -> None:
    token = _token(client)

    held = list(app.state.access.tokens._expiry)

    assert held == [hashlib.sha256(token.encode()).hexdigest()]
    assert token not in repr(app.state.access.tokens.__dict__)


def test_a_token_expires_after_the_ttl_and_use_does_not_renew_it(
    client: TestClient, clock: FakeClock
) -> None:
    token = _token(client)
    headers = with_cookie(token)

    clock.advance(12 * 3600 - 1)
    assert client.get("/api/v1/sessions/s_x", headers=headers).status_code == 404  # still valid
    clock.advance(2)

    assert client.get("/api/v1/sessions/s_x", headers=headers).status_code == 401


def test_the_lifetime_comes_from_its_setting(tmp_path: Path, clock: FakeClock) -> None:
    client = TestClient(_app(tmp_path, clock, access_ttl_hours=0.5))
    headers = with_cookie(_token(client))

    clock.advance(1799)
    assert client.get("/api/v1/sessions/s_x", headers=headers).status_code == 404
    clock.advance(2)
    assert client.get("/api/v1/sessions/s_x", headers=headers).status_code == 401


def test_a_restart_ends_every_cookie(tmp_path: Path, clock: FakeClock) -> None:
    token = _token(TestClient(_app(tmp_path, clock)))

    restarted = TestClient(_app(tmp_path, clock))

    assert restarted.get("/api/v1/sessions/s_x", headers=with_cookie(token)).status_code == 401


def test_the_store_is_bounded_and_evicts_the_token_closest_to_expiry(clock: FakeClock) -> None:
    store = TokenStore(ttl_s=100, max_tokens=3, clock=clock)
    tokens = []
    for _ in range(3):
        tokens.append(store.issue())
        clock.advance(1)

    newest = store.issue()  # full: the one issued first (closest to expiry) goes

    assert len(store) == 3
    assert not store.valid(tokens[0])
    assert store.valid(tokens[1]) and store.valid(tokens[2]) and store.valid(newest)


def test_expired_tokens_are_dropped_when_looked_up_and_by_a_sweep(clock: FakeClock) -> None:
    store = TokenStore(ttl_s=10, max_tokens=100, clock=clock)
    looked_up, swept = store.issue(), store.issue()
    clock.advance(11)

    assert not store.valid(looked_up)
    assert len(store) == 1
    assert store.sweep() == 1
    assert len(store) == 0 and not store.valid(swept)


def test_an_empty_or_oversized_cookie_is_never_looked_up(clock: FakeClock) -> None:
    store = TokenStore(ttl_s=10, max_tokens=10, clock=clock)

    assert not store.valid("")
    assert not store.valid("x" * 10_000)


def test_the_store_is_a_limit_on_cookies_not_on_what_the_access_bucket_allows(
    tmp_path: Path, clock: FakeClock
) -> None:
    app = _app(tmp_path, clock, access_max_tokens=2)
    client = TestClient(app)

    first, second, third = (_token(client) for _ in range(3))

    assert client.get("/api/v1/sessions/s_x", headers=with_cookie(first)).status_code == 401
    assert client.get("/api/v1/sessions/s_x", headers=with_cookie(second)).status_code == 404
    assert client.get("/api/v1/sessions/s_x", headers=with_cookie(third)).status_code == 404


# -- No demo password (dev only): open, but the access route still issues a cookie ----------------


def test_with_no_password_in_dev_every_route_is_open_and_nothing_is_checked(tmp_path: Path) -> None:
    app = create_app(Settings(data_dir=tmp_path / "d", env="dev"), lambda p: None)
    client = TestClient(app)

    assert client.post("/api/v1/sessions").status_code == 201
    assert (
        client.get("/api/v1/sessions/s_x", headers={"Authorization": "Bearer anything"}).status_code
        == 404
    )
    token_response = client.post(
        "/api/v1/access", data={"password": "whatever"}, headers={"Origin": "http://127.0.0.1:8000"}
    )
    assert token_response.status_code == 204
    assert token_response.headers["set-cookie"].startswith(COOKIE_NAME + "=")
    no_field = client.post(
        "/api/v1/access",
        content=b"",
        headers={"Origin": "http://127.0.0.1:8000", "Content-Type": FORM},
    )
    assert no_field.status_code == 204


def test_the_access_route_still_checks_the_origin_in_dev(tmp_path: Path) -> None:
    client = TestClient(create_app(Settings(data_dir=tmp_path / "d", env="dev"), lambda p: None))

    missing = client.post("/api/v1/access", data={"password": "x"})
    foreign = client.post(
        "/api/v1/access", data={"password": "x"}, headers={"Origin": "https://evil.example"}
    )

    assert missing.status_code == foreign.status_code == 403


# -- The Origin check ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "null",
        "https://evil.example",
        "http://demo.example",  # the scheme
        "https://demo.example:8443",  # the port
        "https://demo.example/",  # a trailing slash isn't an origin
        "https://demo.example.evil.example",
        "https://sub.demo.example",
        "",
        PUBLIC_ORIGIN + " x",
    ],
)
def test_a_post_with_a_missing_or_foreign_origin_is_403_and_the_handler_does_not_run(
    app: Any, client: TestClient, origin: str | None
) -> None:
    headers = {} if origin is None else {"Origin": origin}

    on_access = client.post("/api/v1/access", data={"password": PASSWORD}, headers=headers)
    token = _token(client)
    on_sessions = client.post("/api/v1/sessions", headers=with_cookie(token, **headers))

    for response in (on_access, on_sessions):
        assert response.status_code == 403
        assert response.json() == {
            "error": {
                "code": "forbidden_origin",
                "message": "This request didn't come from the demo page.",
            }
        }
        assert "set-cookie" not in response.headers
    assert app.state.registry.live_keys() == []  # no session was created


@pytest.mark.parametrize("origin", [PUBLIC_ORIGIN, "HTTPS://Demo.Example", "https://DEMO.example"])
def test_the_origin_matches_after_lower_casing(client: TestClient, origin: str) -> None:
    token = _token(client)

    response = client.post("/api/v1/sessions", headers=with_cookie(token, Origin=origin))

    assert response.status_code == 201


def test_two_origin_headers_are_refused(client: TestClient) -> None:
    token = _token(client)

    response = client.post(
        "/api/v1/sessions",
        headers=[
            ("Cookie", f"{COOKIE_NAME}={token}"),
            ("Origin", PUBLIC_ORIGIN),
            ("Origin", PUBLIC_ORIGIN),
        ],
    )

    assert response.status_code == 403


def test_a_cookie_get_needs_no_origin(client: TestClient) -> None:
    token = _token(client)

    response = client.get("/api/v1/sessions/s_x", headers=with_cookie(token))

    assert response.status_code == 404  # not 403: a read isn't checked


def test_a_bearer_request_needs_no_origin(client: TestClient) -> None:
    assert client.post("/api/v1/sessions", headers=BEARER).status_code == 201
    assert (
        client.post(
            "/api/v1/sessions", headers={**BEARER, "Origin": "https://evil.example"}
        ).status_code
        == 201
    )


def test_every_mutating_route_checks_the_origin_for_a_cookie(app: Any, client: TestClient) -> None:
    token = _token(client)
    mutating = [
        (m, r.path)
        for r in iter_routes(app)
        if r.path.startswith("/api/v1") and r.path != "/api/v1/access"
        for m in r.methods
        if m not in ("GET", "HEAD", "OPTIONS")
    ]
    assert len(mutating) >= 4

    for method, path in mutating:
        response = client.request(method, _concrete(path), headers=with_cookie(token))
        assert response.status_code == 403, (method, path)


def test_the_origin_check_runs_before_the_body_is_read(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A refused upload is never parsed (and so never spooled to disk)."""
    import starlette.formparsers as formparsers

    parsed: list[int] = []
    original = formparsers.MultiPartParser.parse

    async def counting(self: Any) -> Any:
        parsed.append(1)
        return await original(self)

    monkeypatch.setattr(formparsers.MultiPartParser, "parse", counting)
    token = _token(client)
    files = {"file": ("a.xlsx", b"x" * 100, "application/octet-stream")}

    no_credential = client.post("/api/v1/sessions/s_x/files", files=files)
    foreign_origin = client.post(
        "/api/v1/sessions/s_x/files",
        files=files,
        headers=with_cookie(token, Origin="https://evil.example"),
    )

    assert (no_credential.status_code, foreign_origin.status_code) == (401, 403)
    assert parsed == []


# -- Logs and responses never carry the password or a token ----------------------------------------


def test_neither_the_password_nor_a_token_reaches_a_response_body_or_a_log(
    app: Any, client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    secrets = ["not-the-password-but-also-secret", PASSWORD]
    bodies: list[str] = []
    with caplog.at_level(logging.DEBUG):
        good = exchange(client)
        token = cookie_value(good)
        secrets.append(token)
        calls = [
            exchange(client, "not-the-password-but-also-secret"),
            client.get(
                "/api/v1/sessions/s_x",
                headers={"Authorization": "Bearer not-the-password-but-also-secret"},
            ),
            client.get("/api/v1/sessions/s_x", headers=with_cookie("garbage")),
            client.get("/api/v1/sessions/s_x", headers=with_cookie(token)),
            client.post(
                "/api/v1/sessions", headers=with_cookie(token, Origin="https://evil.example")
            ),
            client.post("/api/v1/sessions", headers=BEARER),
            client.post(
                "/api/v1/access", content=PASSWORD, headers={**ORIGIN, "Content-Type": "text/plain"}
            ),
        ]
        # past the limits, so the 429 path is covered too
        for _ in range(70):
            calls.append(
                client.get("/api/v1/sessions/s_x", headers={"Authorization": "Bearer wrong-wrong"})
            )
        bodies = [r.text + str(dict(r.headers)) for r in calls]

    assert any(r.status_code == 429 for r in calls)
    for secret in secrets:
        assert not any(secret in b for b in bodies), secret
        assert secret not in caplog.text
        assert secret not in good.text


@pytest.mark.parametrize(
    ("raw", "gone"),
    [
        (f"Cookie: {COOKIE_NAME}=tok3n_abc-123; theme=dark", "tok3n_abc-123"),
        (f"Set-Cookie: {COOKIE_NAME}=tok3n_abc-123; Path=/; Secure", "tok3n_abc-123"),
        ("Authorization: Bearer hunter2-hunter2", "hunter2-hunter2"),
        ("form body: password=hunter2&x=1", "hunter2"),
    ],
)
def test_the_log_filter_redacts_the_cookie_the_bearer_and_a_password(raw: str, gone: str) -> None:
    from backend.logging_filter import redact

    redacted = redact(raw)

    assert gone not in redacted
    assert "[REDACTED]" in redacted


# -- Startup refusals --------------------------------------------------------------------------


def test_a_server_with_no_password_refuses_to_start_unless_it_says_dev(tmp_path: Path) -> None:
    for env in ({}, {"WORKBUDDY_ENV": "production", "WORKBUDDY_PUBLIC_ORIGIN": PUBLIC_ORIGIN}):
        with pytest.raises(SettingsError, match="WORKBUDDY_DEMO_PASSWORD"):
            Settings.from_env({"WORKBUDDY_DATA_DIR": str(tmp_path), **env})
    dev = Settings.from_env({"WORKBUDDY_DATA_DIR": str(tmp_path), "WORKBUDDY_ENV": "dev"})
    assert dev.demo_password is None and dev.env == "dev"


def test_create_app_with_no_settings_reads_the_environment_and_refuses_an_open_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    for name in ("WORKBUDDY_DEMO_PASSWORD", "WORKBUDDY_ENV", "WORKBUDDY_PUBLIC_ORIGIN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WORKBUDDY_DATA_DIR", str(tmp_path))

    with pytest.raises(SettingsError):
        create_app(engine_factory=lambda p: None)  # type: ignore[arg-type,return-value]


def test_an_empty_password_is_refused_not_treated_as_unset(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="empty"):
        Settings(data_dir=tmp_path, demo_password="", env="dev")
    with pytest.raises(SettingsError, match="WORKBUDDY_DEMO_PASSWORD"):
        Settings.from_env({"WORKBUDDY_DATA_DIR": str(tmp_path), "WORKBUDDY_DEMO_PASSWORD": ""})


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"public_origin": ""}, "WORKBUDDY_PUBLIC_ORIGIN is required"),
        ({"public_origin": "http://demo.example"}, "https://"),
        ({"public_origin": "ftp://demo.example"}, "origin"),
        ({"public_origin": "https://demo.example/app"}, "origin"),
        ({"public_origin": "https://user@demo.example"}, "origin"),
        ({"public_origin": "demo.example"}, "origin"),
        ({"public_origin": "*"}, "origin"),
    ],
)
def test_production_needs_an_https_public_origin(
    tmp_path: Path, overrides: dict[str, str], message: str
) -> None:
    with pytest.raises(SettingsError, match=message):
        locked_settings(tmp_path, **overrides)


def test_the_public_origin_is_normalized_and_defaults_in_dev(tmp_path: Path) -> None:
    assert (
        locked_settings(tmp_path, public_origin="HTTPS://Demo.Example:443").public_origin
        == PUBLIC_ORIGIN
    )
    assert (
        locked_settings(tmp_path, public_origin="https://demo.example:8443").public_origin
        == "https://demo.example:8443"
    )
    assert Settings(data_dir=tmp_path, env="dev").public_origin == "http://127.0.0.1:8000"
    assert Settings(data_dir=tmp_path, demo_password="x").public_origin == "http://127.0.0.1:8000"


def test_an_unknown_environment_name_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="env"):
        Settings(data_dir=tmp_path, env="staging", demo_password="x")


def test_the_access_class_is_the_only_place_that_knows_the_password(tmp_path: Path) -> None:
    """A real sign-in replaces ``AccessControl`` (``check`` and ``exchange``); nothing else reads a
    password, a header or a cookie to decide access."""
    settings = locked_settings(tmp_path)
    access = AccessControl(settings, FakeClock())

    assert access.exchange("wrong") is None
    assert access.exchange(None) is None
    token = access.exchange(PASSWORD)
    assert token is not None and access.tokens.valid(token)
    sources = [
        path.read_text()
        for path in Path("backend").glob("*.py")
        if path.name not in ("access.py", "settings.py", "logging_filter.py")
    ]
    assert not any("demo_password" in source for source in sources)
    assert not any("compare_digest" in source for source in sources)
