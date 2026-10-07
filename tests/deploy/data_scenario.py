"""The scenario that wrote the saved-data fixtures, and the helpers that read them back.

``write_scenario`` runs the real app (real pipeline, real skills; only the LLM is faked) on a data
folder: one session, one upload, one delivered turn and one rejected turn.
``scripts/make_data_fixture.py`` uses it to write a fixture, and
``tests/deploy/test_saved_data_survives.py`` uses it to check that what today's code writes still
has the shape of the newest fixture.

The files under ``fixtures/data-vN/`` were written by the version that was current when they were
made, and they stay forever: a later version of the server must still open every one of them
(``CLAUDE.md``, "Working method").
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.backend.helpers import (
    RELAXED_LIMITS,
    new_session,
    start_turn,
    upload,
    wait_finished,
    xlsx_bytes,
)

FIXTURES = Path(__file__).parent / "fixtures"
UPLOAD_NAME = "report.xlsx"
# The folder the image keeps its data in. The saved session records hold absolute paths, so a
# fixture is written for this one, and ``relocate`` points a copy at wherever a test runs.
IMAGE_DATA_DIR = "/data"


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_scenario(data_dir: Path) -> dict[str, Any]:
    """Run the scenario on ``data_dir``; return what the API said, for a manifest."""
    settings = Settings(data_dir=data_dir, rate_limits=RELAXED_LIMITS)
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows") + single("remove_empty_rows")),
        speaker="friend",
    )
    client = TestClient(create_app(settings, factory))
    session_id = new_session(client, "friend")
    uploaded = upload(client, session_id, xlsx_bytes(), UPLOAD_NAME)
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["file_id"]
    turns: list[dict[str, Any]] = []
    plan = (("remove duplicate rows", "approve"), ("remove empty rows", "reject"))
    for message, decision in plan:
        started = start_turn(client, session_id, message)
        assert started.status_code == 202, started.text
        turn = wait_finished(client, session_id, started.json()["turn_id"], decision=decision)
        turns.append(turn)
    assert [t["outcome"] for t in turns] == ["delivered", "not_delivered"]
    (original,) = [p for p in settings.uploads_dir.rglob(UPLOAD_NAME)]
    return {
        "session_id": session_id,
        "mode": "friend",
        "upload": {
            "file_id": file_id,
            "name": UPLOAD_NAME,
            "size": original.stat().st_size,
            "sha256": sha256_of(original),
        },
        "turns": [
            {
                "turn_id": t["turn_id"],
                "message": t["message"],
                "outcome": t["outcome"],
                "reply": t["reply"],
                "delivered_files": [
                    {k: f[k] for k in ("file_id", "name", "size", "sha256")}
                    for f in t["delivered_files"]
                ],
                "confirmation": t["detail"]["confirmation"],
            }
            for t in turns
        ],
    }


def relocate(folder: Path, old_root: str, new_root: str) -> None:
    """Point the absolute paths in a data folder's session records at ``new_root``.

    Only ``session.json`` holds any (the context's file paths, which the store checks against the
    upload registry). Nothing else is touched, so every upload and delivered file stays byte for
    byte what was written.
    """
    for record in (folder / "sessions").glob("*/session.json"):
        text = record.read_text(encoding="utf-8")
        record.write_text(text.replace(old_root, new_root), encoding="utf-8")


def paths_outside_sessions(folder: Path, root: str) -> list[Path]:
    """Files other than ``session.json`` that mention ``root`` (there should be none)."""
    return [
        p
        for p in folder.rglob("*")
        if p.is_file()
        and p.name != "session.json"
        and p.suffix in (".json", ".txt")
        and root in p.read_text(encoding="utf-8", errors="ignore")
    ]


def shape(value: Any) -> Any:
    """The keys and types of a JSON value, without its content (lists: their distinct shapes)."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        distinct: list[Any] = []
        for item in value:
            if (found := shape(item)) not in distinct:
                distinct.append(found)
        return sorted(distinct, key=repr)
    return type(value).__name__


def layout(folder: Path) -> list[str]:
    """Every file under ``folder`` with the ids replaced, so two runs can be compared.

    Files only: git doesn't keep an empty folder, so a fixture checked out from git lacks the empty
    ones (the rejected turn's output folder) that a fresh run leaves.
    """
    session_ids = [p.name for p in (folder / "sessions").iterdir()]
    names: list[str] = []
    for path in sorted(folder.rglob("*")):
        if not path.is_file():
            continue
        text = path.relative_to(folder).as_posix()
        for session_id in session_ids:
            text = text.replace(session_id, "<session>")
        parts = text.split("/")
        if parts[0] == "uploads" and len(parts) > 2:
            parts[2] = "<file>"
        names.append("/".join(parts))
    return names
