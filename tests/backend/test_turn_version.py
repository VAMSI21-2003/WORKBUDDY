"""``TurnRecord.version``: the live counter of a turn's status changes, for the page's polling.

It starts at 0 (running) and goes up by one on every status change: the plan is published (1), the
plan is answered and the turn runs again (2), the turn finishes (3). A turn with no plan goes from
running (0) to finished (1). It's in no JSON response and not on disk.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.identity import DEV
from backend.main import create_app
from backend.settings import Settings
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.backend.helpers import (
    RELAXED_LIMITS,
    new_session,
    start_turn,
    upload,
    wait_finished,
    xlsx_bytes,
)


def _run(tmp_path: Path, replies: list[str], message: str, decision: str | None) -> tuple[int, Any]:
    settings = Settings(data_dir=tmp_path / "data", rate_limits=RELAXED_LIMITS)
    app = create_app(settings, PipelineFactory(QueuedProvider(replies)))
    client = TestClient(app)
    session_id = new_session(client)
    assert upload(client, session_id, xlsx_bytes()).status_code == 201
    turn = start_turn(client, session_id, message).json()
    body = wait_finished(client, session_id, turn["turn_id"], decision=decision)
    record = app.state.registry.get(session_id, DEV)
    assert record is not None
    stored = record.turn(turn["turn_id"])
    assert stored is not None
    assert "version" not in body
    return stored.version, body


def test_a_plan_goes_through_three_changes_when_approved(tmp_path: Path) -> None:
    version, body = _run(tmp_path, chain(*DEDUPE_THEN_EMPTY), "dedupe then empty rows", "approve")
    assert body["outcome"] == "delivered"
    assert version == 3


def test_a_rejected_plan_goes_through_the_same_three(tmp_path: Path) -> None:
    version, body = _run(tmp_path, chain(*DEDUPE_THEN_EMPTY), "dedupe then empty rows", "reject")
    assert body["outcome"] != "delivered"
    assert version == 3


def test_a_turn_with_no_plan_changes_once(tmp_path: Path) -> None:
    version, _ = _run(tmp_path, single("general_question"), "what can you do?", None)
    assert version == 1
