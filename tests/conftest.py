"""Pytest options shared by every test directory."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-ui-copy",
        action="store_true",
        default=False,
        help="rewrite tests/web/ui_copy_snapshot.txt from the page's templates, CSS and script "
        "(the independent reviewer reads the diff: frontend design 4.3, item 11)",
    )
