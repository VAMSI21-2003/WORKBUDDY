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
    return Settings(data_dir=tmp_path / "data", auto_approve=True)
