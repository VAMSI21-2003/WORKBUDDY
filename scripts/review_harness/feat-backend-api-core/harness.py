# ruff: noqa: E501 — reviewer tooling, not product code
"""Shared pieces for the review probes: a fake LLM, fixtures, an independent file check.

Reviewer tooling, not part of the test suite. Everything here runs the real
pipeline (``buddy.engine_factory.build_engine``: real skills, Hands,
Inspector, verifiers, Voice) and fakes only the LLM.

The independent check reads delivered files with ``openpyxl`` (not pandas,
which the skills use), so a bug shared by the skill and the verifier
doesn't hide itself.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import openpyxl
import pandas as pd

# -- Fake LLM -----------------------------------------------------------------------


class FakeLLM:
    """Canned replies in call order; records every prompt. Thread-safe.

    A reply may be a callable ``(system, messages) -> str`` (for a model
    that raises, or sleeps) instead of a string. When the queue is empty
    the call raises, like a provider error.
    """

    def __init__(self, replies: list[Any] | None = None) -> None:
        self._replies = list(replies or [])
        self._lock = threading.Lock()
        self.calls: list[tuple[str, str]] = []

    def add(self, replies: list[Any]) -> None:
        with self._lock:
            self._replies.extend(replies)

    def complete(self, *, system: str, messages: Any, max_tokens: int = 1024) -> str:
        with self._lock:
            text = "\n".join(getattr(m, "content", str(m)) for m in messages)
            self.calls.append((system, text))
            if not self._replies:
                raise RuntimeError("fake LLM: no reply queued (secret gsk_FAKEFAKE in error)")
            reply = self._replies.pop(0)
        if callable(reply):
            return reply(system, text)
        return reply


def understand(intent: str, confidence: float = 0.95) -> str:
    return json.dumps(
        {"intent": intent, "confidence": confidence, "missing_information": [], "notes": ""}
    )


def think(clarify: bool = False) -> str:
    return json.dumps(
        {
            "objective": "Clean up the spreadsheet.",
            "summary": "Clear enough to proceed.",
            "clarification_required": clarify,
            "confidence": 0.9,
        }
    )


def plan(*steps: tuple[str, str]) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "description": f"do {cap}",
                    "capability": cap,
                    "depends_on_step_numbers": [],
                    "source_phrase": phrase,
                }
                for cap, phrase in steps
            ]
        }
    )


def single(intent: str) -> list[str]:
    return [understand(intent), think()]


def chain(*steps: tuple[str, str]) -> list[str]:
    return [understand("multi_step_request", 0.9), think(), plan(*steps)]


def raising(exc: BaseException) -> Callable[[str, str], str]:
    def reply(system: str, text: str) -> str:
        raise exc

    return reply


def sleeping(seconds: float, then: str) -> Callable[[str, str], str]:
    def reply(system: str, text: str) -> str:
        time.sleep(seconds)
        return then

    return reply


# -- Fixtures -----------------------------------------------------------------------

DUP_ROWS = {
    "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
    "amount": [10, 20, None, 10, 30, 20],
}
NO_DUP_ROWS = {"name": ["Alice", "Bob", "Carol", "Dana"], "amount": [10, 20, 30, 40]}


def xlsx(rows: dict[str, list[Any]] | None = None) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(DUP_ROWS if rows is None else rows).to_excel(buffer, index=False)
    return buffer.getvalue()


def sha(data: bytes | Path) -> str:
    if isinstance(data, Path):
        data = data.read_bytes()
    return hashlib.sha256(data).hexdigest()


# -- Independent check ----------------------------------------------------------------


def read_rows(data: bytes | Path) -> list[tuple[Any, ...]]:
    """Every data row (header excluded) of the first sheet, read with openpyxl."""
    source = io.BytesIO(data) if isinstance(data, bytes) else data
    book = openpyxl.load_workbook(source, read_only=True, data_only=True)
    try:
        sheet = book.worksheets[0]
        rows = [tuple(r) for r in sheet.iter_rows(values_only=True)]
    finally:
        book.close()
    return rows[1:]


def describe(rows: list[tuple[Any, ...]]) -> dict[str, int]:
    """Row count, exact-duplicate count, and fully-empty-row count."""
    seen: set[tuple[Any, ...]] = set()
    dups = 0
    empty = 0
    for row in rows:
        if all(v is None or (isinstance(v, str) and not v.strip()) for v in row):
            empty += 1
        if row in seen:
            dups += 1
        seen.add(row)
    return {"rows": len(rows), "duplicates": dups, "empty": empty}


# -- Rule-1 helpers -----------------------------------------------------------------

_CLAIM = re.compile(
    r"\b(removed|deleted|cleaned|done|ready|saved|fixed|here is|here's|i've|i have)\b",
    re.IGNORECASE,
)


def claim_words(reply: str) -> list[str]:
    """Completion-claim words in ``reply`` (a prompt for a human check, not a verdict)."""
    return sorted({m.group(0).lower() for m in _CLAIM.finditer(reply)})


def numbers(reply: str, *strip: str) -> list[int]:
    for s in strip:
        reply = reply.replace(s, "")
    return [int(n) for n in re.findall(r"\b\d+\b", reply)]


def asdict(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return obj
