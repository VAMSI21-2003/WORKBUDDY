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

from backend.settings import DEFAULT_RATE_LIMITS, BucketSpec

# The API tests poll every 20 ms against the real clock, which the real
# per-IP buckets would refuse; they run with limits no test reaches. The tests
# of the limits themselves pass the real ones, and an injected clock.
RELAXED_LIMITS = {
    name: BucketSpec(burst=1_000_000, per_hour=3_600_000_000) for name in DEFAULT_RATE_LIMITS
}

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


PLAN_KEYS = {"confirmation_id", "steps", "plan_note", "expires_at"}


def wait_finished(
    client: TestClient,
    session_id: str,
    turn_id: str,
    timeout_s: float = 30.0,
    *,
    decision: str | None = "approve",
    plans: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Poll the turn until it's finished; fail (never hang) after ``timeout_s``.

    Every poll before the last is checked to have no reply, outcome or
    reply source (design "Decisions (2026-09-30, part 2)", ask 7). While the
    turn is ``awaiting_confirmation``, its plan is checked for the public
    shape ("Decisions (2026-10-01)", item 5: ``steps`` non-empty, no
    step-1-only fields), collected into ``plans`` once per confirmation id,
    and answered with ``decision`` (``None`` leaves it to time out). Once a
    plan is answered it must never be shown again, and the answer's
    response must already show it gone.
    """
    deadline = time.monotonic() + timeout_s
    seen: set[str] = set()
    answered: set[str] = set()
    while True:
        response = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}")
        assert response.status_code == 200, response.text
        body: dict[str, Any] = response.json()
        if body["status"] == "finished":
            assert body["reply"] and body["reply_source"] and body["outcome"]
            assert body["finished_at"] is not None
            assert body["pending_confirmation"] is None
            return body
        assert body["reply"] is None and body["reply_source"] is None
        assert body["outcome"] is None and body["delivered_files"] == []
        assert body["status"] in ("running", "awaiting_confirmation"), body["status"]
        if body["status"] == "running":
            assert body["pending_confirmation"] is None
        else:
            plan = body["pending_confirmation"]
            assert set(plan) == PLAN_KEYS, plan
            assert plan["steps"], "a plan is never published without a step"
            assert all(set(step) == {"capability", "prompt"} for step in plan["steps"])
            confirmation_id = plan["confirmation_id"]
            assert confirmation_id not in answered, "an answered plan was shown again"
            if confirmation_id not in seen:
                seen.add(confirmation_id)
                if plans is not None:
                    plans.append(plan)
            if decision is not None:
                answer = client.post(
                    f"/api/v1/sessions/{session_id}/turns/{turn_id}/confirmation",
                    json={"confirmation_id": confirmation_id, "decision": decision},
                )
                assert answer.status_code == 200, answer.text
                assert answer.json()["pending_confirmation"] is None
                answered.add(confirmation_id)
        if time.monotonic() > deadline:
            raise AssertionError(f"turn {turn_id} didn't finish within {timeout_s} s")
        time.sleep(0.02)
