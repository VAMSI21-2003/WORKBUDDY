"""How ``deploy/smoke.sh`` authenticates: the operator token, and the old way for a rollback.

It runs the real script against a small fake server that answers only up to the point under test
(the authentication probe), in the temporary copy of ``deploy/`` that the release tests use.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from tests.deploy.test_release_scripts import PASSWORD, Site, ok_env, out


@pytest.fixture
def site(tmp_path: Path) -> Site:
    return Site(tmp_path)


TOKEN = "operator-token-0123456789abcdef-0123456789abcdef"


class Fake:
    """A server that knows one operator token (or none: an old release) and one password."""

    def __init__(self, knows_token: bool) -> None:
        self.knows_token = knows_token
        self.seen: list[str] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: object) -> None:
                pass

            def _send(
                self, status: int, body: str = "{}", headers: dict[str, str] | None = None
            ) -> None:
                self.send_response(status)
                for key, value in (headers or {}).items():
                    self.send_header(key, value)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body.encode())

            def do_GET(self) -> None:  # noqa: N802
                outer.seen.append(f"GET {self.path}")
                if self.path == "/healthz":
                    self._send(200, '{"ok": true, "llm_configured": true}')
                elif self.path.startswith("/api/v1/sessions/"):
                    given = self.headers.get("Authorization", "")
                    ok = outer.knows_token and given == f"Bearer {TOKEN}"
                    self._send(404 if ok else 401)
                else:
                    self._send(404)

            def do_POST(self) -> None:  # noqa: N802
                outer.seen.append(f"POST {self.path}")
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length).decode()
                if self.path == "/api/v1/access" and PASSWORD in body:
                    self._send(204, "", {"Set-Cookie": "__Host-wb_access=abc; Path=/; Secure; HttpOnly"})
                else:
                    self._send(500, '{"error": "the fake stops here"}')

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


@pytest.fixture
def fake_new() -> Iterator[Fake]:
    fake = Fake(knows_token=True)
    yield fake
    fake.close()


@pytest.fixture
def fake_old() -> Iterator[Fake]:
    fake = Fake(knows_token=False)
    yield fake
    fake.close()


def _smoke(site: Site, base: str) -> tuple[int, str]:
    done = site.run([str(site.deploy / "smoke.sh"), base], SMOKE_RESOLVE_LOCAL="0")
    return done.returncode, out(done)


def test_smoke_authenticates_with_the_operator_token(site: Site, fake_new: Fake) -> None:
    site.write_env(ok_env(TOKEN))

    code, text = _smoke(site, fake_new.url)

    assert "ok: the operator token is accepted" in text
    assert "POST /api/v1/access" not in fake_new.seen  # no password, no cookie exchange
    assert code != 0 and "the fake stops here" in text  # (it went on to the next step)
    assert TOKEN not in text


def test_smoke_falls_back_to_the_password_only_for_a_release_from_before_sign_in(
    site: Site, fake_old: Fake
) -> None:
    site.write_env(ok_env(TOKEN) + f"WORKBUDDY_DEMO_PASSWORD={PASSWORD}\n")

    code, text = _smoke(site, fake_old.url)

    assert "from before sign-in with Google" in text
    assert "ok: the password was exchanged for the access cookie" in text
    assert "POST /api/v1/access" in fake_old.seen
    assert TOKEN not in text and PASSWORD not in text


def test_smoke_does_not_use_a_password_the_new_app_would_ignore(site: Site) -> None:
    """A server that knows the token never gets the password sent to it."""
    fake = Fake(knows_token=True)
    try:
        site.write_env(ok_env(TOKEN) + f"WORKBUDDY_DEMO_PASSWORD={PASSWORD}\n")

        _smoke(site, fake.url)

        assert "POST /api/v1/access" not in fake.seen
    finally:
        fake.close()


def test_smoke_says_what_is_wrong_when_the_token_is_refused_and_there_is_no_password(
    site: Site, fake_old: Fake
) -> None:
    site.write_env(ok_env(TOKEN))

    code, text = _smoke(site, fake_old.url)

    assert code == 1
    assert 'SMOKE FAILED at "authenticate"' in text and "WORKBUDDY_OPERATOR_TOKEN" in text
    assert TOKEN not in text


def test_smoke_needs_the_operator_token_in_env(site: Site, tmp_path: Path) -> None:
    site.write_env("WORKBUDDY_DOMAIN=example.test\nGROQ_API_KEY=x\n")

    code, text = _smoke(site, "http://127.0.0.1:9")

    assert code != 0 and "WORKBUDDY_OPERATOR_TOKEN" in text
