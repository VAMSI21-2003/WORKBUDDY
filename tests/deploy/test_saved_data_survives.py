"""Saved data survives an upgrade: today's server opens what every earlier version wrote.

``fixtures/data-vN/`` are data folders written by older versions (``scripts/make_data_fixture.py``):
a session with an upload, a delivered turn and a rejected turn. A release replaces the server and
keeps ``/data``, so every one of them must still open: the session reads back, the files download
byte for byte, the turns say what they said. A change to the on-disk format needs a migration and a
new fixture beside the old ones (``CLAUDE.md``, "Working method"); the last test here fails when the
format changed and no new fixture was added.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.retention import MaintenanceLoop
from backend.sessions import session_key
from backend.settings import Settings
from backend.store import FORMAT_VERSION, SessionStore
from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.backend.helpers import (
    OPERATOR_TOKEN,
    RELAXED_LIMITS,
    production_settings,
    start_turn,
    wait_finished,
)
from tests.backend.signin_helpers import ACCESS_COOKIE, ALICE, BOB, browser, make_idp, signed_in
from tests.deploy import data_scenario as scenario
from tests.file_sentences import worked_on_original_skipping_copy

FIXTURE_DIRS = sorted(scenario.FIXTURES.glob("data-v*"), key=lambda p: int(p.name[6:]))


def _manifest(fixture: Path) -> dict[str, Any]:
    return dict(json.loads((fixture / "manifest.json").read_text(encoding="utf-8")))


def _copy(fixture: Path, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    manifest = _manifest(fixture)
    data = tmp_path / "data"
    shutil.copytree(fixture / "data", data)
    scenario.relocate(data, manifest["data_dir_when_written"], str(data))
    return data, manifest


def _owned(fixture: Path) -> bool:
    """Whether the fixture was written by a server that had session owners (data-v2 on)."""
    return "owner" in _manifest(fixture)


OWNED_DIRS = [d for d in FIXTURE_DIRS if _owned(d)]
LEGACY_DIRS = [d for d in FIXTURE_DIRS if not _owned(d)]


@pytest.fixture(params=OWNED_DIRS, ids=[p.name for p in OWNED_DIRS])
def opened(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[TestClient, Settings, Any]:
    """A copy of one fixture's data folder, served by the server as it is today, to the person
    who owns the session (their sign-in survived the "restart": it is read from the data)."""
    data, manifest = _copy(request.param, tmp_path)
    settings = production_settings(tmp_path, data_dir=data, rate_limits=RELAXED_LIMITS)
    # Two sets of replies: a request as it is, then the same request with Brain reading "start
    # over" (a test uses the second only after a turn has used the first).
    provider = QueuedProvider(
        single("remove_duplicate_rows") + single("remove_duplicate_rows", file_to_use="original")
    )
    factory = PipelineFactory(provider, speaker="friend")
    app = create_app(
        settings,
        factory,
        oidc_http=make_idp().client(),
        wall_clock=lambda: manifest["signed_in_at"] + 3600,
    )
    client = browser(app)
    client.headers["Cookie"] = f"{ACCESS_COOKIE}={manifest['cookie_token']}"
    return client, settings, manifest


def test_there_is_a_fixture() -> None:
    assert FIXTURE_DIRS, "tests/deploy/fixtures has no data-vN folder"
    assert (FIXTURE_DIRS[0] / "manifest.json").is_file()
    assert OWNED_DIRS, "no fixture holds an owned session (data-v2 or later)"


def test_the_signed_in_owner_is_who_the_fixture_says(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    """The sign-in written by an earlier version still signs its person in, after a restart."""
    client, _, manifest = opened

    assert client.get("/").status_code == 200
    assert manifest["email"] in client.get("/").text
    # ...and the page, with no pointer, takes them to the session they had.
    assert manifest["session_id"] in client.get("/").text


def test_nobody_else_can_open_the_saved_session(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    client, settings, manifest = opened
    session_id = manifest["session_id"]
    other_app = create_app(
        production_settings(Path(settings.data_dir).parent, data_dir=settings.data_dir),
        PipelineFactory(QueuedProvider([])),
        oidc_http=(idp := make_idp()).client(),
    )
    bob = signed_in(browser(other_app), idp, BOB)
    operator = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}
    first_turn = manifest["turns"][0]["turn_id"]
    delivered = manifest["turns"][0]["delivered_files"][0]["file_id"]

    for path in (
        f"/api/v1/sessions/{session_id}",
        f"/api/v1/sessions/{session_id}/files",
        f"/api/v1/sessions/{session_id}/turns/{first_turn}",
        f"/api/v1/sessions/{session_id}/files/{delivered}/download",
    ):
        assert bob.get(path).status_code == 404, path
        assert browser(other_app).get(path, headers=operator).status_code == 404, path


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
        # A turn saved before replies had messages reads back with its reply as its one message.
        assert turn["messages"] == expected.get("messages", [expected["reply"]])
        assert turn["detail"]["confirmation"] == expected["confirmation"]
        keys = ("file_id", "name", "size", "sha256")
        # What is saved is unchanged: a delivered file's stored name is its name on disk, with
        # the internal session key and task number. The API shows it without them
        # (`backend.names`), so the name read back is the saved one minus those, whichever
        # server version wrote it.
        saved = [
            {**f, "name": re.sub(r"_[0-9a-f]{32}-task-\d+", "", f["name"])}
            for f in expected["delivered_files"]
        ]
        assert [{k: f[k] for k in keys} for f in turn["delivered_files"]] == saved


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


def _saved_original(settings: Settings, manifest: dict[str, Any]) -> Path:
    (original,) = [
        p
        for p in settings.uploads_dir.rglob(manifest["upload"]["name"])
        if p.parent.name == manifest["upload"]["file_id"]
    ]
    return original


def test_the_saved_session_still_takes_a_new_turn_and_builds_on_what_it_delivered(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    """Not only readable: the restored context and files work for the next message.

    A session saved before follow-up turns has no record of which file each turn worked on; the
    file its first turn delivered was made from its upload, so a new request builds on it. That
    file has no exact duplicate rows left, so the reply says so and hands back nothing.
    """
    client, settings, manifest = opened
    session_id = manifest["session_id"]
    original = _saved_original(settings, manifest)
    before = scenario.sha256_of(original)
    assert before == manifest["upload"]["sha256"]
    delivered = manifest["turns"][0]["delivered_files"][0]

    started = start_turn(client, session_id, "remove duplicate rows")
    assert started.status_code == 202, started.text
    turn = wait_finished(client, session_id, started.json()["turn_id"], decision="approve")

    assert turn["turn_id"] == f"t{len(manifest['turns']) + 1:04d}"
    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert "already has no exact duplicate rows" in turn["reply"]
    assert scenario.sha256_of(original) == before
    download = client.get(f"/api/v1/sessions/{session_id}/files/{delivered['file_id']}/download")
    assert hashlib.sha256(download.content).hexdigest() == delivered["sha256"]
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert len(state["turns"]) == len(manifest["turns"]) + 1
    assert state["next_request_file"]["file_id"] == delivered["file_id"]


def test_the_saved_session_can_start_over_from_its_upload(
    opened: tuple[TestClient, Settings, Any],
) -> None:
    client, settings, manifest = opened
    session_id = manifest["session_id"]
    original = _saved_original(settings, manifest)
    before = scenario.sha256_of(original)
    first = start_turn(client, session_id, "remove duplicate rows")
    wait_finished(client, session_id, first.json()["turn_id"], decision="approve")

    started = start_turn(client, session_id, "start over and remove duplicate rows")
    turn = wait_finished(client, session_id, started.json()["turn_id"], decision="approve")

    assert turn["outcome"] == "delivered"
    assert worked_on_original_skipping_copy("report.xlsx") in turn["reply"]
    assert scenario.sha256_of(original) == before


# -- Sessions saved before sign-in existed -----------------------------------------------------


@pytest.fixture(params=LEGACY_DIRS, ids=[p.name for p in LEGACY_DIRS])
def legacy(request: pytest.FixtureRequest, tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    return _copy(request.param, tmp_path)


def _tree_hashes(folder: Path, skip: str = "owner.json") -> dict[str, str]:
    return {
        str(p.relative_to(folder)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name != skip and p.relative_to(folder).parts[0] != "auth"
    }


def test_a_session_saved_before_sign_in_still_parses(legacy: tuple[Path, dict[str, Any]]) -> None:
    """The format an earlier server wrote still opens (the store reads it); it just has no owner."""
    data, manifest = legacy
    settings = Settings(data_dir=data, rate_limits=RELAXED_LIMITS)
    key = session_key(manifest["session_id"])

    record = SessionStore(settings).load_session(key)

    assert record is not None
    assert [t.turn_id for t in record.turns] == [t["turn_id"] for t in manifest["turns"]]
    assert record.owner is None


def test_a_session_saved_before_sign_in_belongs_to_nobody(
    legacy: tuple[Path, dict[str, Any]],
) -> None:
    """The decision in docs/design/google-sign-in.md: never readable by anyone, never changed."""
    data, manifest = legacy
    before = _tree_hashes(data)
    settings = production_settings(data.parent, data_dir=data, rate_limits=RELAXED_LIMITS)
    idp = make_idp()
    app = create_app(settings, PipelineFactory(QueuedProvider([])), oidc_http=idp.client())
    session_id = manifest["session_id"]
    key = session_key(session_id)
    delivered = manifest["turns"][0]["delivered_files"][0]["file_id"]
    operator = {"Authorization": f"Bearer {OPERATOR_TOKEN}"}

    # The migration marked it, explicitly, and touched nothing else.
    marker = json.loads((data / "sessions" / key / "owner.json").read_text())
    assert marker == {"version": 1, "owner": None, "session_id": None}
    assert _tree_hashes(data) == before

    people = {
        "alice": signed_in(browser(app), idp, ALICE),
        "bob": signed_in(browser(app), idp, BOB),
    }
    for path in (
        f"/api/v1/sessions/{session_id}",
        f"/api/v1/sessions/{session_id}/files",
        f"/api/v1/sessions/{session_id}/turns/t0001",
        f"/api/v1/sessions/{session_id}/files/{delivered}/download",
        f"/ui/s/{session_id}/transcript",
    ):
        for name, client in people.items():
            assert client.get(path).status_code == 404, (name, path)
        assert browser(app).get(path, headers=operator).status_code == 404, path
    for client in people.values():  # a pointer cookie to it is a pointer to nothing
        client.cookies.set("__Host-wb_session", session_id)
        assert "no longer exists" in client.get("/").text
    assert _tree_hashes(data) == before  # opening nothing changed nothing


def test_a_session_saved_before_sign_in_is_deleted_by_the_ordinary_sweep(
    legacy: tuple[Path, dict[str, Any]],
) -> None:
    data, manifest = legacy
    settings = production_settings(data.parent, data_dir=data, rate_limits=RELAXED_LIMITS)
    day_later = __import__("time").time() + (settings.session_ttl_hours + 1) * 3600
    app = create_app(
        settings,
        PipelineFactory(QueuedProvider([])),
        oidc_http=make_idp().client(),
        wall_clock=lambda: day_later,
    )
    key = session_key(manifest["session_id"])
    assert (data / "sessions" / key).is_dir() and (data / "uploads" / key).is_dir()

    maintenance: MaintenanceLoop = app.state.maintenance
    assert maintenance.run_once().removed == (key,)

    for folder in ("sessions", "uploads", "outputs"):
        assert not (data / folder / key).exists()


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
        owner = session.parent / "owner.json"
        (signin,) = (folder / "auth" / "signins").glob("*.json")
        return {
            "session": scenario.shape(json.loads(session.read_text(encoding="utf-8"))),
            "turns": [scenario.shape(json.loads(t.read_text(encoding="utf-8"))) for t in turns],
            "owner": scenario.shape(json.loads(owner.read_text(encoding="utf-8"))),
            "signin": scenario.shape(json.loads(signin.read_text(encoding="utf-8"))),
        }

    hint = (
        "The on-disk format changed. Write a migration so that the older fixtures still open, "
        "keep them, and add a new fixture beside them: python scripts/make_data_fixture.py "
        f"{int(newest.name[6:]) + 1}"
    )
    assert FORMAT_VERSION == _manifest(newest)["format_version"], hint
    assert _owned(newest), hint
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
