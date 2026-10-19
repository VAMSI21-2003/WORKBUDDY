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


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        "real_scan_child: run the workbook check's child process (the default leaves it out)",
    )


@pytest.fixture(autouse=True)
def no_workbook_check_child(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Leave out the workbook check's child process unless the test is marked ``real_scan_child``.

    The child costs a second or so for every workbook, which made the suite four times slower, and
    the page's rate-limit tests run a virtual clock against a real-time pipeline, so a slower
    turn made them poll more often at one instant. The check (the cheap checks in the server, then
    the child) has its own tests, which run it for real: ``tests/skills/excel/test_scan_child.py``
    and ``tests/skills/excel/test_hostile_workbooks.py``.
    """
    if request.node.get_closest_marker("real_scan_child") is not None:
        return
    from skills.excel import workbook

    monkeypatch.setattr(workbook, "_scan_in_child", lambda _path: None)
