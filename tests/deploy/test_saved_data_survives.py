"""Saved data survives an upgrade: today's server opens what every earlier version wrote.

``fixtures/data-vN/`` are data folders written by older versions (``scripts/make_data_fixture.py``):
a session with an upload, a delivered turn and a rejected turn. A release replaces the server and
keeps ``/data``, so every one of them must still open: the session reads back, the files download
byte for byte, the turns say what they said. A change to the on-disk format needs a migration and a
new fixture beside the old ones (``CLAUDE.md``, "Working method"); the last test here fails when the
format changed and no new fixture was added.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from backend.store import FORMAT_VERSION
from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.backend.helpers import RELAXED_LIMITS, start_turn, wait_finished
from tests.deploy import data_scenario as scenario

FIXTURE_DIRS = sorted(scenario.FIXTURES.glob("data-v*"), key=lambda p: int(p.name[6:]))


def _manifest(fixture: Path) -> dict[str, Any]:
    return dict(json.loads((fixture / "manifest.json").read_text(encoding="utf-8")))


@pytest.fixture(params=FIXTURE_DIRS, ids=[p.name for p in FIXTURE_DIRS])
def opened(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[TestClient, Settings, Any]:
    """A copy of one fixture's data folder, served by the server as it is today."""
    fixture: Path = request.param
    manifest = _manifest(fixture)
    data = tmp_path / "data"
    shutil.copytree(fixture / "data", data)
    scenario.relocate(data, manifest["data_dir_when_written"], str(data))
    settings = Settings(data_dir=data, rate_limits=RELAXED_LIMITS)
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker="friend")
    client = TestClient(create_app(settings, factory))
    return client, settings, manifest


def test_there_is_a_fixture() -> None:
    assert FIXTURE_DIRS, "tests/deploy/fixtures has no data-vN folder"
    assert (FIXTURE_DIRS[0] / "manifest.json").is_file()


def test_the_saved_session_opens_and_lists_its_files_and_turns(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    client, _, manifest = opened
    session_id = manifest["session_id"]

    response = client.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200, response.text
    state = response.json()
    assert state["session_id"] == session_id
    assert state["busy"] is False and state["pending_confirmation"] is None
    assert [(f["file_id"], f["name"], f["sha256"]) for f in state["files"]] == [
        (
            manifest["upload"]["file_id"],
            manifest["upload"]["name"],
            manifest["upload"]["sha256"],
        )
    ]
    assert [(t["turn_id"], t["status"], t["outcome"]) for t in state["turns"]] == [
        (t["turn_id"], "finished", t["outcome"]) for t in manifest["turns"]
    ]


def test_the_saved_turns_read_back_as_they_were_written(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    client, _, manifest = opened
    session_id = manifest["session_id"]
    for expected in manifest["turns"]:
        response = client.get(f"/api/v1/sessions/{session_id}/turns/{expected['turn_id']}")

        assert response.status_code == 200, response.text
        turn = response.json()
        assert turn["message"] == expected["message"]
        assert turn["status"] == "finished"
        assert turn["outcome"] == expected["outcome"]
        assert turn["reply"] == expected["reply"]
        assert turn["detail"]["confirmation"] == expected["confirmation"]
        keys = ("file_id", "name", "size", "sha256")
        assert [{k: f[k] for k in keys} for f in turn["delivered_files"]] == expected[
            "delivered_files"
        ]


def test_the_saved_files_have_the_same_hash(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    """The delivered file downloads byte for byte; the upload (never served) is intact on disk."""
    import hashlib

    client, settings, manifest = opened
    session_id = manifest["session_id"]
    delivered = [f for turn in manifest["turns"] for f in turn["delivered_files"]]
    assert len(delivered) == 1

    for expected in delivered:
        response = client.get(f"/api/v1/sessions/{session_id}/files/{expected['file_id']}/download")

        assert response.status_code == 200, response.text
        assert hashlib.sha256(response.content).hexdigest() == expected["sha256"]
        assert len(response.content) == expected["size"]
    upload = manifest["upload"]
    (on_disk,) = [
        p for p in settings.uploads_dir.rglob(upload["name"]) if p.parent.name == upload["file_id"]
    ]
    assert scenario.sha256_of(on_disk) == upload["sha256"]
    assert on_disk.stat().st_size == upload["size"]
    # The upload isn't a delivered file: the API says 404, as on the day it was written.
    assert client.get(
        f"/api/v1/sessions/{session_id}/files/{upload['file_id']}/download"
    ).status_code == 404


def test_the_saved_session_still_takes_a_new_turn(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    """Not only readable: the restored context and files work for the next message."""
    client, settings, manifest = opened
    session_id = manifest["session_id"]
    (original,) = [
        p
        for p in settings.uploads_dir.rglob(manifest["upload"]["name"])
        if p.parent.name == manifest["upload"]["file_id"]
    ]
    before = scenario.sha256_of(original)
    assert before == manifest["upload"]["sha256"]

    started = start_turn(client, session_id, "remove duplicate rows")
    assert started.status_code == 202, started.text
    turn = wait_finished(client, session_id, started.json()["turn_id"], decision="approve")

    assert turn["turn_id"] == f"t{len(manifest['turns']) + 1:04d}"
    assert turn["outcome"] == "delivered"
    assert scenario.sha256_of(original) == before
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert len(state["turns"]) == len(manifest["turns"]) + 1


def test_a_fixtures_session_files_hold_no_path_outside_session_json() -> None:
    """``relocate`` rewrites session.json only; a path anywhere else would go unnoticed."""
    for fixture in FIXTURE_DIRS:
        manifest = _manifest(fixture)
        assert not scenario.paths_outside_sessions(
            fixture / "data", manifest["data_dir_when_written"]
        )


def test_what_the_server_writes_today_has_the_shape_of_the_newest_fixture(
    tmp_path: Path,
) -> None:
    """The guard behind the rule in CLAUDE.md: change the on-disk format, and this fails until a
    migration exists and a new fixture sits beside the old ones (``make_data_fixture.py N``)."""
    newest = FIXTURE_DIRS[-1]
    fresh = tmp_path / "data"
    scenario.write_scenario(fresh)

    def records(folder: Path) -> dict[str, Any]:
        session = next((folder / "sessions").glob("*/session.json"))
        turns = sorted((session.parent / "turns").glob("t*.json"))
        return {
            "session": scenario.shape(json.loads(session.read_text(encoding="utf-8"))),
            "turns": [scenario.shape(json.loads(t.read_text(encoding="utf-8"))) for t in turns],
        }

    hint = (
        "The on-disk format changed. Write a migration so that the older fixtures still open, "
        "keep them, and add a new fixture beside them: python scripts/make_data_fixture.py "
        f"{int(newest.name[6:]) + 1}"
    )
    assert FORMAT_VERSION == _manifest(newest)["format_version"], hint
    assert records(fresh) == records(newest / "data"), hint
    assert scenario.layout(fresh) == scenario.layout(newest / "data"), hint


def test_the_layout_ignores_empty_folders(tmp_path: Path) -> None:
    """Review round 1, M1: git keeps no empty folder, so a fixture from a clone lacks the empty
    output folder that a fresh run leaves for the rejected turn; the guard compared them."""
    with_empty = tmp_path / "a"
    without = tmp_path / "b"
    for folder in (with_empty, without):
        (folder / "sessions" / "k").mkdir(parents=True)
        (folder / "sessions" / "k" / "session.json").write_text("{}")
    (with_empty / "outputs" / "k" / "t0002").mkdir(parents=True)

    assert scenario.layout(with_empty) == scenario.layout(without)

    (without / "outputs" / "k" / "t0002").mkdir(parents=True)
    (without / "outputs" / "k" / "t0002" / "stray.xlsx").write_text("x")
    assert scenario.layout(with_empty) != scenario.layout(without)  # a real extra file still shows
