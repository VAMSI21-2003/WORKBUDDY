"""The vendored htmx and the static files (frontend design 3.1, 11.2)."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

import pytest

from backend.web import templating
from backend.web.templating import STATIC_DIR, static_url, static_version
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
    assert scripts == [static_url("htmx.min.js"), static_url("app.js")]
    assert [lk.get("href") for lk in tree.find_all("link")] == [static_url("styles.css")]
    assert tree.find("script", where=lambda e: not e.attrs.get("src")) is None  # no inline script
    assert tree.find("style") is None
    for element in tree.walk():
        assert "style" not in element.attrs  # no inline styles under style-src 'self'
        assert not any(name.startswith("on") for name in element.attrs)  # no inline handlers


# -- Caching (frontend design 11.2) --------------------------------------------------------------


def test_a_static_url_carries_the_files_version_and_the_page_links_only_those(
    tmp_path: Path,
) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider()))

    for name in ("htmx.min.js", "app.js", "styles.css"):
        digest = hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()
        assert static_url(name) == f"/static/{name}?v={digest[:12]}"
        assert static_version(name) == digest[:12]
    page = client.get("/").text
    assert re.findall(r'(?:href|src)="(/static/[^"]*)"', page) == [
        static_url("styles.css"),
        static_url("htmx.min.js"),
        static_url("app.js"),
    ]  # every link is versioned


def test_a_file_under_its_current_version_is_cached_for_good_and_any_other_url_revalidates(
    tmp_path: Path,
) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider()))

    for name in ("htmx.min.js", "app.js", "styles.css"):
        current = client.get(static_url(name))
        assert current.status_code == 200
        assert current.headers["cache-control"] == "public, max-age=31536000, immutable"
        assert current.headers["x-content-type-options"] == "nosniff"
        assert current.headers["referrer-policy"] == "no-referrer"
        for url in (f"/static/{name}", f"/static/{name}?v=0123456789ab", f"/static/{name}?x=1"):
            other = client.get(url)  # no version, an old one, or another parameter
            assert other.status_code == 200
            assert other.headers["cache-control"] == "no-cache"
        again = client.get(
            static_url(name), headers={"If-None-Match": current.headers["etag"]}
        )
        assert again.status_code == 304
        assert again.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_everything_else_stays_no_store_including_a_missing_static_file(tmp_path: Path) -> None:
    _, client = make_app(tmp_path, PipelineFactory(QueuedProvider()))

    for response in (
        client.get("/"),
        client.get("/healthz"),
        client.get("/static/missing.js?v=0123456789ab"),
        client.get("/static/../routes.py"),
        client.get("/nope"),
    ):
        assert response.headers["cache-control"] == "no-store", response.request.url


def test_an_edited_static_file_gets_a_new_version_under_a_running_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review L5 of frontend-deploy: the version follows the file, so a dev server never serves
    new content under the old ``?v=`` that a browser has cached for good."""
    monkeypatch.setattr(templating, "STATIC_DIR", tmp_path)
    (tmp_path / "x.js").write_text("one", encoding="utf-8")
    first = templating.static_version("x.js")
    assert first == hashlib.sha256(b"one").hexdigest()[:12]
    assert templating.static_version("x.js") == first

    (tmp_path / "x.js").write_text("two", encoding="utf-8")  # the same size, edited
    os.utime(tmp_path / "x.js", ns=(1, 1_000_000_000))  # and a modification time that moved

    assert templating.static_version("x.js") == hashlib.sha256(b"two").hexdigest()[:12]
    assert templating.static_version("x.js") != first
