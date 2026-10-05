"""Shared fixtures for the API tests.

Every test runs the app on a temporary data root, and every LLM call is
faked. The global log-record factory is restored after each test, so the
API's log redaction never hides a leak from another suite's log assertions.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from backend.logging_filter import uninstall_log_redaction
from backend.settings import Settings


@pytest.fixture(autouse=True)
def _restore_log_record_factory() -> Iterator[None]:
    previous = logging.getLogRecordFactory()
    yield
    uninstall_log_redaction(previous)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """The default configuration: every file-changing step asks for confirmation.

    ``wait_finished`` approves each plan it sees unless told otherwise, so
    every turn goes through the real confirmation flow, as in production
    (design Section 7.1). ``auto_approve`` is for tests that need it.
    """
    return Settings(data_dir=tmp_path / "data")
