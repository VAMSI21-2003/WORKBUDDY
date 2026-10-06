"""The vendored htmx and the static files (frontend design 3.1, 11.2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

from backend.web.templating import STATIC_DIR
from tests.backend.factories import PipelineFactory, QueuedProvider
from tests.web.htmltools import parse
from tests.web.support import make_app

HTMX_VERSION = "2.0.4"
# The unmodified ``package/dist/htmx.min.js`` of ``htmx.org@2.0.4`` on npm. 2.0.5 and later write a
# sessionStorage key at load that no setting turns off, which the design forbids (impl notes, 1).
HTMX_SHA256 = "e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447"


def test_htmx_is_the_pinned_unmodified_release() -> None:
    data = (STATIC_DIR / "htmx.min.js").read_bytes()
    assert hashlib.sha256(data).hexdigest() == HTMX_SHA256
    assert f'version:"{HTMX_VERSION}"'.encode() in data
    notes = (STATIC_DIR / "VENDORED.md").read_text(encoding="utf-8")
    assert HTMX_VERSION in notes and "sessionStorage" in notes


def test_this_htmx_never_touches_web_storage() -> None:
    data = (STATIC_DIR / "htmx.min.js").read_text(encoding="utf-8")
    assert "sessionStorage.setItem" not in data
    assert "htmx-current-path-for-history" not in data


def test_the_static_files_are_served_and_nothing_else_is(tmp_path: Path) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider()))

    for name, kind in (
        ("htmx.min.js", "javascript"),
        ("app.js", "javascript"),
        ("styles.css", "css"),
    ):
        response = client.get(f"/static/{name}")
        assert response.status_code == 200, name
        assert kind in response.headers["content-type"]
        assert response.content == (STATIC_DIR / name).read_bytes()
    for path in (
        "/static/",
        "/static",
        "/static/../routes.py",
        "/static/%2e%2e/routes.py",
        "/static/missing.js",
        "/static/templates",
    ):
        assert client.get(path).status_code in (307, 404), path


def test_every_page_loads_only_its_own_scripts_and_styles(tmp_path: Path) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider()))

    tree = parse(client.get("/").text)

    scripts = [s.get("src") for s in tree.find_all("script")]
    assert scripts == ["/static/htmx.min.js", "/static/app.js"]
    assert [lk.get("href") for lk in tree.find_all("link")] == ["/static/styles.css"]
    assert tree.find("script", where=lambda e: not e.attrs.get("src")) is None  # no inline script
    assert tree.find("style") is None
    for element in tree.walk():
        assert "style" not in element.attrs  # no inline styles under style-src 'self'
        assert not any(name.startswith("on") for name in element.attrs)  # no inline handlers
