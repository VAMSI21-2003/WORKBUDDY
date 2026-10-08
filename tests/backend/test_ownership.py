"""Every session belongs to one person; nobody else reaches it (docs/design/google-sign-in.md).

Security tests: a failure here is one person's data reaching another, which is HIGH. They run on
production settings with two people signed in through the fake identity provider.

The matrix is built from the app's own route table, so a route added later is covered without
anyone remembering to add it: for every route with a ``{session_id}`` in its path, person B gets
exactly the answer for a session that doesn't exist.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient

from backend.access import AccessDependency
from backend.identity import DEV, OPERATOR, Principal
from backend.main import create_app
from backend.ratelimit import GENERAL, iter_routes, limits
from backend.sessions import session_key
from tests.backend.factories import PipelineFactory, QueuedProvider, single
from tests.backend.helpers import (
    OPERATOR_TOKEN,
    new_session,
    upload,
    wait_finished,
    xlsx_bytes,
)
from tests.backend.signin_helpers import ALICE, BOB, Rig, browser

SESSION_NOT_FOUND = {
    "error": {"code": "session_not_found", "message": "That session doesn't exist."}
}
UNKNOWN_ID = "s_" + "x" * 22


class World:
    """Alice has a session with an upload and a delivered file; Bob has a session of his own."""

    def __init__(self, tmp_path: Path) -> None:
        self.rig = Rig(
            tmp_path,
            PipelineFactory(
                QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows")),
                speaker="friend",
            ),
        )
        self.app: FastAPI = self.rig.app
        self.alice = self.rig.person(ALICE)
        self.bob = self.rig.person(BOB)
        self.sid = new_session(self.alice)
        self.upload = upload(self.alice, self.sid, xlsx_bytes()).json()
        started = self.alice.post(
            f"/api/v1/sessions/{self.sid}/turns", json={"message": "remove duplicate rows"}
        )
        self.turn_id = started.json()["turn_id"]
        self.turn = wait_finished(self.alice, self.sid, self.turn_id)
        assert self.turn["outcome"] == "delivered", self.turn
        self.file_id = self.turn["delivered_files"][0]["file_id"]
        self.bob_sid = new_session(self.bob)
        self.operator = browser(self.app)
        self.operator.headers["Authorization"] = f"Bearer {OPERATOR_TOKEN}"
        self.operator.headers.pop("Origin")

    def path(self, template: str, session_id: str | None = None) -> str:
        values = {
            "session_id": session_id or self.sid,
            "turn_id": self.turn_id,
            "file_id": self.file_id,
        }
        return re.sub(r"\{(\w+)\}", lambda m: values[m.group(1)], template)

    def tree(self) -> dict[str, str]:
        """Every file under Alice's session folders, hashed: what a stranger must not change."""
        key = session_key(self.sid)
        out: dict[str, str] = {}
        data = self.rig.settings.data_dir
        for name in ("sessions", "uploads", "outputs"):
            folder = data / name / key
            for path in sorted(folder.rglob("*")) if folder.exists() else []:
                if path.is_file():
                    out[str(path.relative_to(data))] = hashlib.sha256(path.read_bytes()).hexdigest()
        return out


@pytest.fixture
def world(tmp_path: Path) -> World:
    return World(tmp_path)


def _session_routes(app: FastAPI) -> list[Any]:
    return [r for r in iter_routes(app) if "{session_id}" in getattr(r, "path", "")]


def _send(client: TestClient, route: Any, method: str, path: str) -> Any:
    kwargs: dict[str, Any] = {}
    if method in ("POST", "PUT", "PATCH"):
        kwargs["json"] = {"message": "hi", "confirmation_id": "c", "decision": "approve"}
    return client.request(method, path, follow_redirects=False, **kwargs)


def _same(a: Any, b: Any) -> bool:
    """Whether two responses are alike in every way a person could tell apart (not cookies)."""
    headers = lambda r: sorted(  # noqa: E731
        (k, v) for k, v in r.headers.items() if k.lower() not in ("set-cookie", "content-length")
    )
    return a.status_code == b.status_code and a.content == b.content and headers(a) == headers(b)


# -- The matrix, from the route table ------------------------------------------------------------


def test_the_matrix_covers_the_api_and_the_page(world: World) -> None:
    routes = _session_routes(world.app)
    api = [r for r in routes if r.path.startswith("/api/v1/")]
    ui = [r for r in routes if r.path.startswith("/ui/")]

    assert len(api) >= 7 and len(ui) >= 5, [r.path for r in routes]
    assert {m for r in routes for m in r.methods} >= {"GET", "POST"}


def test_a_route_parameter_that_names_a_session_is_always_called_session_id(world: World) -> None:
    """The gate looks for ``session_id``. Another spelling would slip past it."""
    for route in iter_routes(world.app):
        for name in re.findall(r"\{(\w+)", getattr(route, "path", "")):
            if "sess" in name.lower() or name.lower() in ("sid", "id"):
                assert name == "session_id", (route.path, name)


def test_person_b_gets_exactly_the_answer_for_a_session_that_doesnt_exist(world: World) -> None:
    checked = 0
    for route in _session_routes(world.app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            theirs = _send(world.bob, route, method, world.path(route.path))
            nothing = _send(world.bob, route, method, world.path(route.path, UNKNOWN_ID))
            assert theirs.status_code == 404, (method, route.path)
            assert _same(theirs, nothing), (method, route.path, theirs.text, nothing.text)
            if route.path.startswith("/api/"):
                assert theirs.json() == SESSION_NOT_FOUND
            checked += 1
    assert checked >= 12


def test_the_operator_and_a_sessionless_stranger_get_the_same_answer_too(world: World) -> None:
    for route in _session_routes(world.app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            path = world.path(route.path)
            nothing = _send(world.operator, route, method, world.path(route.path, UNKNOWN_ID))
            assert nothing.status_code == 404, (method, route.path)
            assert _same(_send(world.operator, route, method, path), nothing), (method, path)


def test_the_owner_is_not_told_404_by_the_same_routes(world: World) -> None:
    """The positive control: the matrix isn't passing because everything is 404."""
    seen = 0
    for route in _session_routes(world.app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            response = _send(world.alice, route, method, world.path(route.path))
            body = response.text
            assert "session_not_found" not in body, (method, route.path, response.status_code)
            seen += 1
    assert seen >= 12


def test_without_a_credential_it_is_401_not_404(world: World) -> None:
    stranger = browser(world.app)
    for route in _session_routes(world.app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            assert _send(stranger, route, method, world.path(route.path)).status_code == 401


def test_nothing_changes_when_a_stranger_tries_every_route(world: World) -> None:
    before = world.tree()
    state = world.alice.get(f"/api/v1/sessions/{world.sid}").json()

    for route in _session_routes(world.app):
        for method in sorted(route.methods - {"HEAD", "OPTIONS"}):
            _send(world.bob, route, method, world.path(route.path))

    assert world.tree() == before
    assert world.alice.get(f"/api/v1/sessions/{world.sid}").json() == state


def test_a_new_route_that_forgets_the_check_is_still_refused_by_the_gate(world: World) -> None:
    """The guard is not vacuous: a route added without any ownership check answers person B 404
    and the owner 200, because the gate does it for every ``{session_id}`` route."""
    router = APIRouter(prefix="/api/v1", dependencies=[AccessDependency])

    @router.get("/probe/{session_id}", dependencies=[Depends(limits(GENERAL))])
    def probe(request: Request, session_id: str) -> dict[str, str]:
        return {"secret": "alice's data"}  # never calls session_of

    world.app.include_router(router)

    assert world.alice.get(f"/api/v1/probe/{world.sid}").json() == {"secret": "alice's data"}
    refused = world.bob.get(f"/api/v1/probe/{world.sid}")
    assert refused.status_code == 404 and refused.json() == SESSION_NOT_FOUND
    assert "secret" not in refused.text
    assert _same(refused, world.bob.get(f"/api/v1/probe/{UNKNOWN_ID}"))


def test_the_registry_gives_no_record_without_the_owner_as_the_principal(world: World) -> None:
    registry = world.app.state.registry
    owner = Principal.google("sub-alice", ALICE)

    assert registry.get(world.sid, owner) is not None
    for stranger in (None, DEV, OPERATOR, Principal.google("sub-bob", BOB), Principal("", "dev")):
        assert registry.get(world.sid, stranger) is None
        assert registry.authorize(world.sid, stranger) is None
    assert registry.get(UNKNOWN_ID, owner) is None
    assert registry.get("not-an-id!", owner) is None
    with pytest.raises(TypeError):
        registry.get(world.sid)  # type: ignore[call-arg]  # an id alone never gets a record


def test_a_stranger_does_not_make_the_server_load_the_session(world: World) -> None:
    registry = world.app.state.registry
    registry._sessions.clear()  # as after a restart: only the disk has it

    world.bob.get(f"/api/v1/sessions/{world.sid}")
    world.operator.get(f"/api/v1/sessions/{world.sid}")

    assert registry.live_keys() == []  # not restored, so not touched either
    world.alice.get(f"/api/v1/sessions/{world.sid}")
    assert registry.live_keys() == [session_key(world.sid)]


# -- What Bob tries, one thing at a time ---------------------------------------------------------


def test_bob_cannot_read_alices_session_files_turns_or_downloads(world: World) -> None:
    sid, bob = world.sid, world.bob

    assert bob.get(f"/api/v1/sessions/{sid}").json() == SESSION_NOT_FOUND
    assert bob.get(f"/api/v1/sessions/{sid}/files").json() == SESSION_NOT_FOUND
    assert bob.get(f"/api/v1/sessions/{sid}/turns/{world.turn_id}").json() == SESSION_NOT_FOUND
    download = bob.get(f"/api/v1/sessions/{sid}/files/{world.file_id}/download")
    assert download.status_code == 404 and download.json() == SESSION_NOT_FOUND
    assert bob.get(f"/ui/s/{sid}/transcript").status_code == 404
    assert bob.get(f"/ui/s/{sid}/turns/{world.turn_id}/poll").status_code == 404
    # Alice's own download still works, and is her file.
    mine = world.alice.get(f"/api/v1/sessions/{sid}/files/{world.file_id}/download")
    assert mine.status_code == 200 and mine.content[:2] == b"PK"


def test_bob_cannot_use_alices_session_to_upload_or_start_a_turn(world: World) -> None:
    sid, bob = world.sid, world.bob
    before = world.tree()

    assert upload(bob, sid, xlsx_bytes(), "bobs.xlsx").status_code == 404
    started = bob.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"})
    assert started.status_code == 404
    page_upload = bob.post(
        f"/ui/s/{sid}/files", files={"file": ("b.xlsx", xlsx_bytes(), "application/octet-stream")}
    )
    assert page_upload.status_code == 404
    assert bob.post(f"/ui/s/{sid}/turns", data={"message": "hello"}).status_code == 404

    assert world.tree() == before
    state = world.alice.get(f"/api/v1/sessions/{sid}").json()
    assert [t["turn_id"] for t in state["turns"]] == [world.turn_id]
    assert [f["name"] for f in state["files"]] == ["report.xlsx"]


def test_bob_cannot_answer_alices_plan(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker="friend"),
    )
    alice, bob = rig.person(ALICE), rig.person(BOB)
    sid = new_session(alice)
    upload(alice, sid, xlsx_bytes())
    turn_id = alice.post(
        f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}
    ).json()["turn_id"]
    plan = _wait_for_plan(alice, sid, turn_id)

    for decision in ("approve", "reject"):
        answer = bob.post(
            f"/api/v1/sessions/{sid}/turns/{turn_id}/confirmation",
            json={"confirmation_id": plan["confirmation_id"], "decision": decision},
        )
        assert answer.status_code == 404 and answer.json() == SESSION_NOT_FOUND
        page = bob.post(
            f"/ui/s/{sid}/turns/{turn_id}/confirmation",
            data={"confirmation_id": plan["confirmation_id"], "decision": decision},
        )
        assert page.status_code == 404
    # Alice's plan is still waiting, untouched, and she can still approve it.
    still = alice.get(f"/api/v1/sessions/{sid}/turns/{turn_id}").json()
    assert still["status"] == "awaiting_confirmation"
    assert still["pending_confirmation"]["confirmation_id"] == plan["confirmation_id"]
    done = wait_finished(alice, sid, turn_id, decision="approve")
    assert done["outcome"] == "delivered"
    assert done["detail"]["confirmation"]["decision"] == "approve"


def _wait_for_plan(client: TestClient, sid: str, turn_id: str) -> dict[str, Any]:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        turn = client.get(f"/api/v1/sessions/{sid}/turns/{turn_id}").json()
        if turn["status"] == "awaiting_confirmation":
            return dict(turn["pending_confirmation"])
        time.sleep(0.02)
    raise AssertionError("no plan")


def test_a_stranger_with_the_session_id_and_a_pointer_cookie_gets_the_gone_page(
    world: World,
) -> None:
    bob = world.bob
    bob.cookies.set("__Host-wb_session", world.sid)

    home = bob.get("/")

    assert home.status_code == 200
    assert "no longer exists" in home.text  # the same words as for a session that was swept
    assert world.sid not in home.text and "report.xlsx" not in home.text
    assert "__Host-wb_session" in home.headers["set-cookie"]  # and the pointer is cleared


def test_the_owner_with_a_pointer_cookie_gets_their_session(world: World) -> None:
    world.alice.cookies.set("__Host-wb_session", world.sid)

    home = world.alice.get("/")

    assert world.sid in home.text and "report.xlsx" in home.text


# -- Landing after sign-in ----------------------------------------------------------------


def test_a_person_lands_in_their_most_recent_session(tmp_path: Path) -> None:
    wall = [1_900_000_000.0]
    rig = Rig(tmp_path, wall_clock=lambda: wall[0])
    alice = rig.person(ALICE)
    first = new_session(alice)
    wall[0] += 10
    second = new_session(alice)
    wall[0] += 10
    alice.get(f"/api/v1/sessions/{first}")  # using the first one makes it the latest
    bob = rig.person(BOB)
    new_session(bob)

    fresh = rig.person(ALICE)  # Alice on another computer
    home = fresh.get("/")

    assert first in home.text and second not in home.text
    assert "__Host-wb_session" in home.headers["set-cookie"]
    # Bob never lands in Alice's.
    assert first not in bob.get("/").text and second not in bob.get("/").text


def test_a_person_with_no_session_gets_a_new_one_made_by_the_page(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    bob = rig.person(BOB)
    new_session(rig.person(ALICE))

    home = bob.get("/")

    assert 'hx-post="/ui/sessions"' in home.text
    assert "report" not in home.text


def test_the_latest_session_survives_a_restart(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    alice = rig.person(ALICE)
    sid = new_session(alice)
    app = create_app(rig.settings, rig.factory, oidc_http=rig.idp.client())
    again = browser(app)
    again.headers["Cookie"] = "__Host-wb_access=" + str(alice.cookies["__Host-wb_access"])

    assert sid in again.get("/").text


# -- Broken ownership records mean nobody --------------------------------------------------------


def _owner_file(world: World) -> Path:
    return world.rig.settings.data_dir / "sessions" / session_key(world.sid) / "owner.json"


@pytest.mark.parametrize(
    "damage",
    [
        "delete",
        "empty",
        "garbage",
        "null_owner",
        "wrong_session_id",
        "extra_key",
        "symlink",
        "huge",
    ],
)
def test_an_owner_file_that_is_missing_or_wrong_means_nobody_owns_the_session(
    world: World, damage: str
) -> None:
    owner_file = _owner_file(world)
    good = json.loads(owner_file.read_text())
    world.app.state.registry._sessions.clear()
    if damage == "delete":
        owner_file.unlink()
    elif damage == "empty":
        owner_file.write_text("")
    elif damage == "garbage":
        owner_file.write_text("{not json")
    elif damage == "null_owner":
        owner_file.write_text(json.dumps({**good, "owner": None, "session_id": None}))
    elif damage == "wrong_session_id":
        owner_file.write_text(json.dumps({**good, "session_id": UNKNOWN_ID}))
    elif damage == "extra_key":
        owner_file.write_text(json.dumps({**good, "also": "alice"}))
    elif damage == "symlink":
        elsewhere = owner_file.parent / "elsewhere.json"
        elsewhere.write_text(json.dumps(good))
        owner_file.unlink()
        os.symlink(elsewhere, owner_file)
    elif damage == "huge":
        owner_file.write_text(json.dumps({**good, "owner": "x" * 10_000}))

    for client in (world.alice, world.bob, world.operator):
        assert client.get(f"/api/v1/sessions/{world.sid}").status_code == 404
        download = f"/api/v1/sessions/{world.sid}/files/{world.file_id}/download"
        assert client.get(download).status_code == 404


def test_the_owner_of_a_loaded_session_is_checked_in_memory_too(world: World) -> None:
    """Even a session already in memory is looked up by owner, not just by id."""
    registry = world.app.state.registry
    record = registry.get(world.sid, Principal.google("sub-alice", ALICE))
    assert record is not None and record.owner == "google:sub-alice"

    assert registry.get(world.sid, Principal.google("sub-bob", BOB)) is None
    # (the owner is compared whole: a prefix or a longer string is somebody else)
    assert registry.get(world.sid, Principal("google:sub-alic", "google")) is None
    assert registry.get(world.sid, Principal("google:sub-alice ", "google")) is None
    assert registry.get(world.sid, Principal("GOOGLE:SUB-ALICE", "google")) is None


def test_a_session_made_by_the_operator_is_not_anyones_but_the_operators(world: World) -> None:
    made = world.operator.post("/api/v1/sessions", json={}).json()["session_id"]

    assert world.operator.get(f"/api/v1/sessions/{made}").status_code == 200
    assert world.alice.get(f"/api/v1/sessions/{made}").status_code == 404
    assert world.bob.get(f"/api/v1/sessions/{made}").status_code == 404


def test_two_people_with_the_same_email_but_different_subjects_are_different_owners(
    tmp_path: Path,
) -> None:
    """The owner is Google's stable subject id, not the email address (which can be reassigned)."""
    rig = Rig(tmp_path)
    from tests.idp.provider import Account

    first = rig.person(ALICE)
    sid = new_session(first)
    rig.idp.accounts[ALICE] = Account(ALICE, "sub-someone-new")

    second = rig.person(ALICE)

    assert second.get(f"/api/v1/sessions/{sid}").status_code == 404
