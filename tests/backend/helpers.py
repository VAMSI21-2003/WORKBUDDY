"""Workbook bytes and small HTTP helpers for the API tests."""

from __future__ import annotations

import io
import time
import warnings
import zipfile
from collections.abc import Callable
from typing import Any

import openpyxl
import pandas as pd
from fastapi.testclient import TestClient

# 6 rows: 2 exact duplicates and 1 fully empty row (mid-file, so it survives
# the openpyxl round trip), the same shape as the live-run fixture.
DEFAULT_ROWS: dict[str, list[Any]] = {
    "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
    "amount": [10, 20, None, 10, 30, 20],
}
NO_DUPLICATE_ROWS: dict[str, list[Any]] = {
    "name": ["Alice", "Bob", "Carol", "Dana"],
    "amount": [10, 20, 30, 40],
}


def xlsx_bytes(rows: dict[str, list[Any]] | None = None) -> bytes:
    buffer = io.BytesIO()
    pd.DataFrame(DEFAULT_ROWS if rows is None else rows).to_excel(
        buffer, index=False, engine="openpyxl"
    )
    return buffer.getvalue()


def workbook_bytes(build: Callable[[openpyxl.Workbook], None]) -> bytes:
    book = openpyxl.Workbook()
    book.active["A1"] = "name"
    book.active["A2"] = "Alice"
    build(book)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def two_sheets(hidden: str | None = None) -> bytes:
    def build(book: openpyxl.Workbook) -> None:
        sheet = book.create_sheet("Q2")
        sheet["A1"] = "x"
        if hidden is not None:
            sheet.sheet_state = hidden

    return workbook_bytes(build)


def sheet_and_chartsheet() -> bytes:
    def build(book: openpyxl.Workbook) -> None:
        book.create_chartsheet("Chart1")

    return workbook_bytes(build)


def zip_bytes(entries: dict[str, bytes] | list[tuple[str, bytes]]) -> bytes:
    items = list(entries.items()) if isinstance(entries, dict) else entries
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in items:
            archive.writestr(name, data)
    return buffer.getvalue()


def rezip(data: bytes, extra: list[tuple[str, bytes]]) -> bytes:
    """``data`` (a ZIP) with ``extra`` entries appended, repeated names allowed."""
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        items = [(info.filename, source.read(info.filename)) for info in source.infolist()]
    buffer = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # zipfile warns on the repeated names some tests want
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, payload in items + extra:
                archive.writestr(name, payload)
    return buffer.getvalue()


def new_session(client: TestClient, mode: str = "friend") -> str:
    response = client.post("/api/v1/sessions", json={"mode": mode})
    assert response.status_code == 201, response.text
    return str(response.json()["session_id"])


def upload(
    client: TestClient, session_id: str, data: bytes, name: str = "report.xlsx"
) -> Any:
    return client.post(
        f"/api/v1/sessions/{session_id}/files",
        files={"file": (name, data, "application/octet-stream")},
    )


def start_turn(client: TestClient, session_id: str, message: str, **extra: Any) -> Any:
    return client.post(
        f"/api/v1/sessions/{session_id}/turns", json={"message": message, **extra}
    )


def wait_finished(
    client: TestClient, session_id: str, turn_id: str, timeout_s: float = 30.0
) -> dict[str, Any]:
    """Poll the turn until it's finished; fail (never hang) after ``timeout_s``.

    Every poll before the last is checked to have no reply, outcome or
    reply source (design "Decisions (2026-09-30, part 2)", ask 7).
    """
    deadline = time.monotonic() + timeout_s
    while True:
        response = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if body["status"] == "finished":
            assert body["reply"] and body["reply_source"] and body["outcome"]
            assert body["finished_at"] is not None
            return body
        assert body["reply"] is None and body["reply_source"] is None
        assert body["outcome"] is None and body["delivered_files"] == []
        if time.monotonic() > deadline:
            raise AssertionError(f"turn {turn_id} didn't finish within {timeout_s} s")
        time.sleep(0.02)
