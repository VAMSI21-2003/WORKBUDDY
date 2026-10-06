"""The Jinja environment: every template is HTML and autoescaped (frontend design 4.2).

API text (the reply, error messages, file names, plan prompts) reaches a page only as
``{{ value }}`` in these templates. Anything that switches escaping off (the safe filter, a markup
wrapper, an autoescape block or setting) is banned in ``backend/web`` by a test.
"""

from __future__ import annotations

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


def render(template: str, **context: Any) -> str:
    """Render ``template`` with ``context``."""
    return environment.get_template(template).render(**context)
