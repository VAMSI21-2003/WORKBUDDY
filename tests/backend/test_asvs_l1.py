"""OWASP ASVS level 1, checked end to end on the production configuration.

``docs/security/asvs-l1.md`` gives each ASVS item a verdict and points at its evidence. This file
holds the checks that don't have a better home: the cookie flags, the security headers, CSRF,
another person's session on every route that names one, the sign-in redirect, file names, error
bodies, the rate limit on guessing, and the routes that must not exist. It runs the real app on
production settings against the fake identity provider (``tests/idp``), with only the LLM faked.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.access import COOKIE_NAME, LOGIN_COOKIE
from backend.ratelimit import iter_routes
from backend.web.common import SESSION_COOKIE
from tests.backend.helpers import OPERATOR_TOKEN, PUBLIC_ORIGIN, xlsx_bytes
from tests.backend.signin_helpers import ALICE, BOB, Rig

ORIGIN = {"Origin": PUBLIC_ORIGIN}


@pytest.fixture
def rig(tmp_path: Path) -> Rig:
    return Rig(tmp_path)


@pytest.fixture
def alice(rig: Rig) -> TestClient:
    return rig.person(ALICE)


@pytest.fixture
def session_id(alice: TestClient) -> str:
    created = alice.post("/api/v1/sessions")
    assert created.status_code == 201
    return str(created.json()["session_id"])


def _set_cookies(response: object) -> list[str]:
    return response.headers.get_list("set-cookie")  # type: ignore[attr-defined, no-any-return]


# -- V3 sessions: the cookie flags ----------------------------------------------------------------


def _attributes(cookie: str) -> dict[str, str]:
    parts = [p.strip() for p in cookie.split(";")]
    attributes = {"name": parts[0].split("=", 1)[0]}
    for part in parts[1:]:
        key, _, value = part.partition("=")
        attributes[key.lower()] = value
    return attributes


def test_every_cookie_the_server_sets_is_secure_http_only_and_host_prefixed(
    rig: Rig, tmp_path: Path
) -> None:
    person = rig.person()
    started = person.get("/auth/google/start", follow_redirects=False)
    sign_in_response = rig.idp.authorize(started.headers["location"], ALICE)
    callback = person.get(sign_in_response, follow_redirects=False)
    page_session = person.post("/ui/sessions")
    signout = person.post("/auth/signout", headers=ORIGIN)

    seen = [*_set_cookies(started), *_set_cookies(callback), *_set_cookies(page_session)]
    seen += _set_cookies(signout)
    names = {_attributes(c)["name"] for c in seen}
    assert names == {LOGIN_COOKIE, COOKIE_NAME, SESSION_COOKIE}
    for cookie in seen:
        attributes = _attributes(cookie)
        assert attributes["name"].startswith("__Host-"), cookie
        assert "secure" in attributes and "httponly" in attributes, cookie
        assert attributes.get("path") == "/" and "domain" not in attributes, cookie
        expected = "lax" if attributes["name"] == LOGIN_COOKIE else "strict"
        assert attributes.get("samesite", "").lower() == expected, cookie


def test_the_access_cookie_is_a_session_cookie_with_a_long_random_value(rig: Rig) -> None:
    tokens = set()
    for email in (ALICE, BOB, ALICE):
        person = rig.person()
        started = person.get("/auth/google/start", follow_redirects=False)
        back = rig.idp.authorize(started.headers["location"], email)
        callback = person.get(back, follow_redirects=False)
        access = next(c for c in _set_cookies(callback) if c.startswith(COOKIE_NAME))
        assert "max-age" not in access.lower() and "expires" not in access.lower()
        value = access.split(";")[0].split("=", 1)[1]
        assert len(value) >= 43  # 256 bits, URL-safe base64
        tokens.add(value)
    assert len(tokens) == 3  # a new value at every sign-in, also for the same person


def test_signing_out_ends_the_token_on_the_server_not_only_in_the_browser(rig: Rig) -> None:
    person = rig.person(ALICE)
    token = person.cookies[COOKIE_NAME]
    assert person.get("/api/v1/sessions/s_x").status_code == 404  # signed in: asked, not found
    person.post("/auth/signout", headers=ORIGIN)

    stolen = TestClient(rig.app, base_url=PUBLIC_ORIGIN, headers=ORIGIN)
    stolen.cookies.set(COOKIE_NAME, token)
    assert stolen.get("/api/v1/sessions/s_x").status_code == 401


# -- V14.4, V14.3 and V8.2: headers on every kind of response -------------------------------------


def _headers_ok(response: object, *, html: bool = False, static: bool = False) -> None:
    h = {k.lower(): v for k, v in response.headers.items()}  # type: ignore[attr-defined]
    assert h["x-content-type-options"] == "nosniff"
    assert h["referrer-policy"] == "no-referrer"
    assert h["strict-transport-security"].startswith("max-age=")
    assert int(h["strict-transport-security"].split("=")[1].split(";")[0]) >= 15724800
    assert "frame-ancestors 'none'" in h["content-security-policy"]
    if html:
        csp = h["content-security-policy"]
        assert "default-src 'self'" in csp and "script-src 'self'" in csp
        assert "'unsafe-inline'" not in csp and "'unsafe-eval'" not in csp
        assert "base-uri 'none'" in csp and "form-action 'self'" in csp
        assert h["content-type"].lower().startswith("text/html; charset=utf-8")
    if not static:
        assert h["cache-control"] == "no-store"
    assert "server" not in h and "x-powered-by" not in h


def test_security_headers_are_on_every_kind_of_response(
    alice: TestClient, rig: Rig, session_id: str
) -> None:
    stranger = rig.person()
    responses = {
        "page": (alice.get("/"), {"html": True}),
        "gate": (stranger.get("/"), {"html": True}),
        "json 200": (alice.get(f"/api/v1/sessions/{session_id}"), {}),
        "json 201": (alice.post("/api/v1/sessions"), {}),
        "401": (stranger.get(f"/api/v1/sessions/{session_id}"), {}),
        "403": (alice.post("/api/v1/sessions", headers={"Origin": "https://evil.example"}), {}),
        "404 route": (alice.get("/no/such/route"), {}),
        "404 session": (alice.get("/api/v1/sessions/s_" + "a" * 22), {}),
        "405": (alice.put(f"/api/v1/sessions/{session_id}"), {}),
        "422": (
            alice.post(
                f"/api/v1/sessions/{session_id}/turns", json={"message": "", "extra": 1}
            ),
            {},
        ),
        "static": (alice.get("/static/app.js"), {"static": True}),
        "sign-in start": (stranger.get("/auth/google/start", follow_redirects=False), {}),
        "healthz": (stranger.get("/healthz"), {}),
    }
    for name, (response, options) in responses.items():
        try:
            _headers_ok(response, **options)
        except AssertionError as failure:
            raise AssertionError(f"{name}: {failure}") from failure
    # the status each of those was meant to produce
    assert [r.status_code for r, _ in responses.values()] == [
        200, 200, 200, 201, 401, 403, 404, 404, 405, 422, 200, 302, 200,
    ]


def test_no_documentation_or_schema_endpoint_exists(alice: TestClient) -> None:
    paths = ("/docs", "/redoc", "/openapi.json", "/api/v1/openapi.json", "/.env", "/.git/config")
    for path in paths:
        assert alice.get(path).status_code == 404, path


@pytest.mark.parametrize("method", ["TRACE", "OPTIONS", "PUT", "DELETE", "PATCH"])
def test_only_the_methods_the_app_uses_are_answered(
    alice: TestClient, session_id: str, method: str
) -> None:
    response = alice.request(method, f"/api/v1/sessions/{session_id}")
    assert response.status_code == 405
    assert "GET" in response.headers["allow"] and method not in response.headers["allow"]


def test_another_origin_gets_no_cross_origin_permission(
    alice: TestClient, session_id: str
) -> None:
    for headers in (
        {"Origin": "https://evil.example"},
        {"Origin": "null"},
        {"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"},
    ):
        for send in (alice.get, alice.options):
            response = send(f"/api/v1/sessions/{session_id}", headers=headers)
            assert not [h for h in response.headers if h.lower().startswith("access-control")]


# -- V4.2.2 and V13.2.3: CSRF ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "origin",
    [None, "https://evil.example", "null", "http://demo.example", "https://demo.example.evil.test"],
    ids=["missing", "foreign", "null", "http", "suffix"],
)
@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("POST", "/api/v1/sessions"),
        ("POST", "/ui/sessions"),
        ("POST", "/auth/signout"),
    ],
)
def test_a_cookie_request_that_changes_state_needs_the_pages_own_origin(
    rig: Rig, alice: TestClient, method: str, path: str, origin: str | None
) -> None:
    browser = TestClient(rig.app, base_url=PUBLIC_ORIGIN)
    browser.cookies.set(COOKIE_NAME, alice.cookies[COOKIE_NAME])
    headers = {} if origin is None else {"Origin": origin}

    response = browser.request(method, path, headers=headers)

    assert response.status_code == 403, (method, path, origin)
    assert response.json()["error"]["code"] == "forbidden_origin"


def test_the_origin_check_takes_nothing_away_from_the_pages_own_requests(
    rig: Rig, alice: TestClient
) -> None:
    assert alice.post("/api/v1/sessions", headers=ORIGIN).status_code == 201
    assert alice.post("/ui/sessions", headers=ORIGIN).status_code == 200


def test_a_script_with_a_bearer_token_needs_no_origin_and_a_cookie_is_ignored_beside_it(
    rig: Rig, alice: TestClient
) -> None:
    script = TestClient(rig.app, base_url=PUBLIC_ORIGIN)
    bearer = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    assert script.post("/api/v1/sessions", headers=bearer).status_code == 201
    wrong = {"Authorization": "Bearer not-the-token"}
    script.cookies.set(COOKIE_NAME, alice.cookies[COOKIE_NAME])
    assert script.post("/api/v1/sessions", headers=wrong).status_code == 401


def test_a_get_never_changes_state(alice: TestClient, rig: Rig) -> None:
    """Every route that changes state says so, and a GET may not (``check_route_declarations``)."""
    from backend.ratelimit import declared_limits

    for route in iter_routes(rig.app):
        declared = declared_limits(route)
        methods = getattr(route, "methods", None) or set()
        if "GET" in methods:
            assert declared is not None and not declared.mutating, route.path


# -- V4.2.1: another person's session, on every route that names one ------------------------


def _session_routes(rig: Rig) -> list[tuple[str, str]]:
    found = []
    for route in iter_routes(rig.app):
        if "{session_id}" not in getattr(route, "path", ""):
            continue
        for method in sorted(set(route.methods or ()) - {"HEAD", "OPTIONS"}):
            found.append((method, route.path))
    return found


def test_every_route_that_names_a_session_is_found(rig: Rig) -> None:
    found = _session_routes(rig)
    paths = {path for _, path in found}
    assert len(found) >= 12
    assert "/api/v1/sessions/{session_id}/files/{file_id}/download" in paths
    assert "/ui/s/{session_id}/turns/{turn_id}/poll" in paths


def test_someone_elses_session_is_the_same_404_as_none_on_every_route(
    rig: Rig, alice: TestClient, session_id: str
) -> None:
    bob = rig.person(BOB)
    operator = TestClient(rig.app, base_url=PUBLIC_ORIGIN)
    operator.headers.update({"Authorization": f"Bearer {OPERATOR_TOKEN}"})
    nobody = "s_" + "A" * 22
    for method, template in _session_routes(rig):
        kwargs = {}
        if method == "POST":
            kwargs = {"json": {"message": "remove the duplicate rows"}}
            if "/files" in template and "download" not in template:
                kwargs = {"files": {"file": ("a.xlsx", xlsx_bytes(), "application/octet-stream")}}
            if "/ui/" in template and "/files" not in template:
                kwargs = {"data": {"message": "hello", "mode": "friend"}}
        path = template.format(session_id=session_id, turn_id="t0001", file_id="f_" + "A" * 16)
        path_nobody = template.format(session_id=nobody, turn_id="t0001", file_id="f_" + "A" * 16)
        for who in (bob, operator):
            theirs = who.request(method, path, headers=ORIGIN, **kwargs)
            absent = who.request(method, path_nobody, headers=ORIGIN, **kwargs)
            assert theirs.status_code == absent.status_code == 404, (who, method, template)
            assert theirs.json() == absent.json(), (method, template)
    # ... and nothing was done to it
    assert alice.get(f"/api/v1/sessions/{session_id}/files").json()["files"] == []


def test_ids_that_are_not_ids_are_a_404_not_a_path(alice: TestClient, session_id: str) -> None:
    for bad in ("..%2f..%2fetc%2fpasswd", "%2e%2e", "a/b", "s_" + "a" * 500, "%00", "..\\..\\x"):
        for path in (
            f"/api/v1/sessions/{bad}",
            f"/api/v1/sessions/{session_id}/turns/{bad}",
            f"/api/v1/sessions/{session_id}/files/{bad}/download",
            f"/ui/s/{bad}/transcript",
        ):
            assert alice.get(path).status_code in (404, 422), path
    assert alice.get("/static/%2e%2e/main.py").status_code == 404
    assert alice.get("/static/../backend/main.py").status_code == 404
    assert alice.get("/static//etc/passwd").status_code == 404


# -- V2/V3: the sign-in ---------------------------------------------------------------------------


def test_the_sign_in_redirect_carries_state_nonce_pkce_and_the_fixed_callback(rig: Rig) -> None:
    from urllib.parse import parse_qs, urlparse

    person = rig.person()
    for host in ("demo.example", "evil.example"):
        started = person.get(
            "/auth/google/start", follow_redirects=False, headers={"Host": host}
        )
        query = parse_qs(urlparse(started.headers["location"]).query)
        assert query["redirect_uri"] == [PUBLIC_ORIGIN + "/auth/google/callback"], host
        assert query["code_challenge_method"] == ["S256"] and len(query["code_challenge"][0]) >= 43
        assert len(query["state"][0]) >= 40 and len(query["nonce"][0]) >= 40
        assert query["response_type"] == ["code"]


def test_the_callback_needs_the_browser_that_started_the_sign_in(rig: Rig) -> None:
    starter = rig.person()
    started = starter.get("/auth/google/start", follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], ALICE)

    other_browser = rig.person()  # another browser gets the link (login CSRF)
    refused = other_browser.get(back, follow_redirects=False)

    assert refused.status_code == 400 and COOKIE_NAME not in other_browser.cookies
    assert COOKIE_NAME not in "".join(_set_cookies(refused))
    # ...and the real browser can still finish: the stranger took nothing
    assert starter.get(back, follow_redirects=False).status_code == 200


def test_a_callback_is_used_once(rig: Rig) -> None:
    person = rig.person()
    started = person.get("/auth/google/start", follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], ALICE)
    assert person.get(back, follow_redirects=False).status_code == 200
    assert person.get(back, follow_redirects=False).status_code == 400


@pytest.mark.parametrize(
    "extra", ["&next=https://evil.example", "&redirect=//evil.example", "&return_to=/x"]
)
def test_no_request_parameter_chooses_where_the_browser_goes_after_sign_in(
    rig: Rig, extra: str
) -> None:
    person = rig.person()
    started = person.get("/auth/google/start" + "?" + extra.lstrip("&"), follow_redirects=False)
    back = rig.idp.authorize(started.headers["location"], ALICE)
    done = person.get(back + extra, follow_redirects=False)
    assert done.status_code == 200 and "location" not in done.headers
    assert "evil.example" not in done.text
    out = person.post("/auth/signout" + "?" + extra.lstrip("&"), headers=ORIGIN)
    assert out.headers.get("hx-redirect", "/") == "/" and "evil" not in str(out.headers)


# -- V12: the upload ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["../../etc/passwd.xlsx", "..\\..\\win.xlsx", "/abs/path.xlsx", "a\x00b.xlsx", "con.xlsx"],
)
def test_the_uploaded_name_never_decides_where_a_file_is_written(
    alice: TestClient, session_id: str, rig: Rig, name: str
) -> None:
    response = alice.post(
        f"/api/v1/sessions/{session_id}/files",
        files={"file": (name, xlsx_bytes(), "application/octet-stream")},
    )
    assert response.status_code == 201, response.text
    shown = response.json()["name"]
    assert "/" not in shown and "\\" not in shown and ".." not in shown and "\x00" not in shown
    data = rig.settings.data_dir
    stored = [p for p in data.rglob("*") if p.is_file() and p.suffix == ".xlsx"]
    assert stored and all(data.resolve() in p.resolve().parents for p in stored)
    assert not (data.parent / "etc").exists() and not (Path("/etc/passwd.xlsx")).exists()


@pytest.mark.parametrize(
    ("name", "content"),
    [
        ("report.exe", b"MZ" + b"\0" * 100),
        ("report.xlsx", b"not a zip at all"),
        ("report.xlsx", b""),
        ("report.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504),
        ("report.xlsx.exe", xlsx_bytes()),
        ("report.php", b"<?php echo 1;"),
    ],
    ids=["exe", "text-as-xlsx", "empty", "legacy-xls", "double-extension", "php"],
)
def test_only_a_real_xlsx_is_accepted(
    alice: TestClient, session_id: str, name: str, content: bytes
) -> None:
    response = alice.post(
        f"/api/v1/sessions/{session_id}/files",
        files={"file": (name, content, "application/octet-stream")},
    )
    assert response.status_code in (415, 422), (name, response.status_code)
    assert alice.get(f"/api/v1/sessions/{session_id}/files").json()["files"] == []


def test_an_upload_is_never_handed_back_as_it_came(alice: TestClient, session_id: str) -> None:
    uploaded = alice.post(
        f"/api/v1/sessions/{session_id}/files",
        files={"file": ("a.xlsx", xlsx_bytes(), "application/octet-stream")},
    ).json()
    download = alice.get(f"/api/v1/sessions/{session_id}/files/{uploaded['file_id']}/download")
    assert download.status_code == 404


# -- V7, V14.3: errors say nothing about the inside -----------------------------------------------


def test_a_crash_is_a_fixed_500_with_nothing_of_the_inside_in_it(
    alice: TestClient, session_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from backend import views

    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("secret detail /srv/data/sessions/s_abc/uploads KEY=gsk_abcdefghij")

    monkeypatch.setattr(views, "session_state", boom)
    quiet = TestClient(alice.app, base_url=PUBLIC_ORIGIN, raise_server_exceptions=False)
    quiet.cookies.set(COOKIE_NAME, alice.cookies[COOKIE_NAME])

    response = quiet.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    text = response.text
    for leak in ("secret detail", "/srv", "gsk_", "Traceback", "RuntimeError", ".py"):
        assert leak not in text
    _headers_ok(response)


def test_a_validation_error_does_not_echo_what_was_sent(
    alice: TestClient, session_id: str
) -> None:
    sent = "UNIQUE-MARKER-12345"
    response = alice.post(
        f"/api/v1/sessions/{session_id}/turns", json={"message": sent, "mode": sent}
    )
    assert response.status_code == 422
    assert sent not in response.text and "pydantic" not in response.text.lower()


# -- V2.2.1: guessing is throttled ----------------------------------------------------------------


def test_guessing_a_bearer_token_is_throttled_with_the_real_limits(tmp_path: Path) -> None:
    rig = Rig(tmp_path, relaxed=False)
    script = TestClient(rig.app, base_url=PUBLIC_ORIGIN)
    codes = [
        script.get("/api/v1/sessions/s_x", headers={"Authorization": f"Bearer guess{i}"})
        for i in range(120)
    ]
    statuses = [r.status_code for r in codes]
    assert statuses[0] == 401 and 429 in statuses
    limited = next(r for r in codes if r.status_code == 429)
    assert int(limited.headers["retry-after"]) >= 1
    # the right token is throttled too (one bucket per address), so a guesser can't just wait it out
    assert script.get(
        "/api/v1/sessions/s_x", headers={"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    ).status_code in (404, 429)


def test_starting_sign_ins_is_throttled(tmp_path: Path) -> None:
    rig = Rig(tmp_path, relaxed=False)
    person = TestClient(rig.app, base_url=PUBLIC_ORIGIN)
    statuses = [
        person.get("/auth/google/start", follow_redirects=False).status_code for _ in range(80)
    ]
    assert 302 in statuses and 429 in statuses
