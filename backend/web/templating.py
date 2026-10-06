"""The Jinja environment: every template is HTML and autoescaped (frontend design 4.2).

API text (the reply, error messages, file names, plan prompts) reaches a page only as
``{{ value }}`` in these templates. Anything that switches escaping off (the safe filter, a markup
wrapper, an autoescape block or setting) is banned in ``backend/web`` by a test.
"""

from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

WEB_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"

environment = Environment(
    loader=FileSystemLoader(TEMPLATES_DIR),
    autoescape=True,
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
)


@cache
def static_version(name: str) -> str:
    """The first 12 hex digits of the SHA-256 of ``static/<name>``: its version in a URL."""
    return hashlib.sha256((STATIC_DIR / name).read_bytes()).hexdigest()[:12]


def static_url(name: str) -> str:
    """``/static/<name>?v=<version>``: the URL changes whenever the file does, so the file can be
    cached for good (``backend.web.routes.StaticAssets``)."""
    return f"/static/{name}?v={static_version(name)}"


environment.globals["static_url"] = static_url


def render(template: str, **context: Any) -> str:
    """Render ``template`` with ``context``."""
    return environment.get_template(template).render(**context)
