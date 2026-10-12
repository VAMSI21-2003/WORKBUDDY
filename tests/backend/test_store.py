"""Persistence: atomic writes, restore after a restart, interrupted-turn repair, bad records.

Design Sections 3.1, 3.2, 7.2 (``SessionStore``), 7.3 item 12 and 7.5 run 8;
item 7 of "What a PR-2 author must do" and review M2.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.sessions import session_key
from backend.settings import Settings
from backend.store import atomic_write_json
from buddy.context import Context
from buddy.voice_catalog import FALLBACK_PIPELINE_ERROR
from tests.backend.factories import BlockingFactory, PipelineFactory, QueuedProvider, single
from tests.backend.helpers import new_session, start_turn, upload, wait_finished, xlsx_bytes


def _client(settings: Settings, factory: Any = None) -> TestClient:
    return TestClient(create_app(settings, factory or PipelineFactory()))


def _session_json(settings: Settings, session_id: str) -> Path:
    return settings.sessions_dir / session_key(session_id) / "session.json"


def _delivered_session(settings: Settings) -> tuple[str, dict[str, Any], PipelineFactory]:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    client = _client(settings, factory)
    session_id = new_session(client, "focus")
    upload(client, session_id, xlsx_bytes())
    turn_id = start_turn(client, session_id, "remove duplicate rows").json()["turn_id"]
    return session_id, wait_finished(client, session_id, turn_id), factory


# -- Atomic writes ----------------------------------------------------------------------


def test_atomic_write_replaces_the_file_and_leaves_no_temp(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": 1})
    atomic_write_json(path, {"a": 2})

    assert json.loads(path.read_text()) == {"a": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


def test_a_write_that_fails_halfway_keeps_the_old_file(tmp_path: Path) -> None:
    path = tmp_path / "x.json"
    atomic_write_json(path, {"a": 1})

    with pytest.raises(TypeError):
        atomic_write_json(path, {"a": object()})

    assert json.loads(path.read_text()) == {"a": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["x.json"]


# -- Restore after a restart (design 7.3 item 12) ----------------------------------------------


def test_a_new_app_on_the_same_data_root_restores_everything(settings: Settings) -> None:
    session_id, turn, factory = _delivered_session(settings)
    provider = QueuedProvider(single("remove_empty_rows"))
    restarted = _client(settings, PipelineFactory(provider))

    state = restarted.get(f"/api/v1/sessions/{session_id}").json()
    assert state["mode"] == "focus" and state["busy"] is False
    assert len(state["files"]) == 1
    (summary,) = state["turns"]
    assert summary["reply"] == turn["reply"] and summary["outcome"] == "delivered"
    again = restarted.get(f"/api/v1/sessions/{session_id}/turns/{turn['turn_id']}").json()
    assert again == turn
    download = restarted.get(turn["delivered_files"][0]["download_url"])
    assert download.status_code == 200
    assert download.content == Path(factory.runs[0].outputs[0]).read_bytes()

    second_id = start_turn(restarted, session_id, "now remove the empty rows too").json()["turn_id"]
    second = wait_finished(restarted, session_id, second_id)
    assert second_id == "t0002"
    assert second["outcome"] == "delivered"
    brain_prompt = provider.calls[0][1][-1].content
    assert turn["reply"] in brain_prompt  # the restored conversation reaches the model


def test_restored_context_keeps_analyzed_files(settings: Settings) -> None:
    # A turn that hands nothing back (a declined plan) leaves the upload analysed; one that
    # delivers a file does not (``backend.turns._restore_upload_focus``).
    client = _client(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    session_id = new_session(client, "focus")
    upload(client, session_id, xlsx_bytes())
    turn_id = start_turn(client, session_id, "remove duplicate rows").json()["turn_id"]
    declined = wait_finished(client, session_id, turn_id, decision="reject")
    assert declined["outcome"] == "not_delivered"
    data = json.loads(_session_json(settings, session_id).read_text())
    assert data["context"]["analyzed_files"]  # the first turn analyzed the upload

    restarted = _client(settings)
    restarted.get(f"/api/v1/sessions/{session_id}")
    (record,) = restarted.app.state.registry._sessions.values()  # type: ignore[attr-defined]
    assert record.state.context.analyzed_files == frozenset(data["context"]["analyzed_files"])


def test_the_session_id_reaches_the_disk_only_in_owner_json(settings: Settings) -> None:
    session_id, _, _ = _delivered_session(settings)

    for path in settings.data_dir.rglob("*"):
        assert session_id not in str(path)
        if path.is_file() and path.name != "owner.json":
            assert session_id.encode() not in path.read_bytes()


# -- Interrupted turns (design 7.5 run 8) -------------------------------------------------------


def test_a_turn_interrupted_by_a_restart_is_reported_failed_not_missing(
    settings: Settings,
) -> None:
    blocking = BlockingFactory()
    before = _client(settings, blocking)
    session_id = new_session(before)
    upload(before, session_id, xlsx_bytes())
    turn_id = start_turn(before, session_id, "remove duplicate rows").json()["turn_id"]
    assert blocking.started.wait(5)
    stored = json.loads(
        (_session_json(settings, session_id).parent / "turns" / f"{turn_id}.json").read_text()
    )
    assert stored["status"] == "running"

    # The first app's worker stays blocked: a killed process writes nothing more.
    restarted = _client(settings)
    turn = restarted.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()

    assert turn["status"] == "finished"
    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"
    assert turn["delivered_files"] == [] and turn["detail"] is None
    state = restarted.get(f"/api/v1/sessions/{session_id}").json()
    assert state["busy"] is False
    # The repair was written back: a third start sees it finished too.
    third = _client(settings).get(f"/api/v1/sessions/{session_id}/turns/{turn_id}").json()
    assert third == turn
    (record,) = restarted.app.state.registry._sessions.values()  # type: ignore[attr-defined]
    conversation = [(t.role.value, t.message) for t in record.state.context.recent_conversation()]
    assert conversation[-2:] == [
        ("user", "remove duplicate rows"),
        ("buddy", FALLBACK_PIPELINE_ERROR),
    ]
    before.app.state.registry._store = None  # type: ignore[attr-defined]
    blocking.release.set()
    assert blocking.finished.wait(5)


def test_a_turn_record_missing_from_the_session_list_is_still_found(settings: Settings) -> None:
    session_id, turn, _ = _delivered_session(settings)
    path = _session_json(settings, session_id)
    data = json.loads(path.read_text())
    data["turn_ids"] = []
    data["next_seq"] = 1
    path.write_text(json.dumps(data))

    restarted = _client(settings, PipelineFactory(QueuedProvider(single("remove_empty_rows"))))
    state = restarted.get(f"/api/v1/sessions/{session_id}").json()
    assert [t["turn_id"] for t in state["turns"]] == [turn["turn_id"]]
    next_turn = start_turn(restarted, session_id, "remove the empty rows")
    assert next_turn.status_code == 202
    assert next_turn.json()["turn_id"] == "t0002"
    wait_finished(restarted, session_id, "t0002")


def test_a_leftover_turn_folder_is_never_reused(settings: Settings) -> None:
    session_id, _, _ = _delivered_session(settings)
    (settings.outputs_dir / session_key(session_id) / "t0007").mkdir()

    restarted = _client(settings, PipelineFactory(QueuedProvider(single("remove_empty_rows"))))
    response = start_turn(restarted, session_id, "remove the empty rows")

    assert response.json()["turn_id"] == "t0008"
    wait_finished(restarted, session_id, "t0008")


# -- Bad records: a clean 404, never a crash ------------------------------------------------


def _mutate(data: dict[str, Any], change: str) -> None:
    context = data["context"]
    first_upload = context["uploaded_files"][0]
    if change == "not_json":
        raise AssertionError  # handled by the caller
    changes: dict[str, Any] = {
        "version_bool": lambda: data.update(version=True),
        "context_version_float": lambda: context.update(version=1.0),
        "uploaded_files_string": lambda: context.update(uploaded_files="abc"),
        "uploaded_files_dict": lambda: context.update(uploaded_files={"p": 1}),
        "active_file_elsewhere": lambda: context.update(active_file="../../etc/passwd"),
        "active_file_number": lambda: context.update(active_file=5),
        "analyzed_elsewhere": lambda: context.update(analyzed_files=["/etc/passwd"]),
        "message_number": lambda: context["conversation_history"][0].update(message=123),
        "metadata_list": lambda: context.update(metadata=[1, 2]),
        "created_at_number": lambda: context.update(created_at=0),
        "created_at_naive": lambda: context.update(created_at="2026-10-03T10:00:00"),
        "history_naive": lambda: context["conversation_history"][0].update(
            timestamp="2026-10-03T10:00:00"
        ),
        "bad_role": lambda: context["conversation_history"][0].update(role="admin"),
        "bad_mode": lambda: context.update(mode="evil"),
        "missing_key": lambda: data.pop("files"),
        "extra_key": lambda: data.update(extra=1),
        "file_name_traversal": lambda: data["files"][0].update(name="../../x.xlsx"),
        "file_id_traversal": lambda: data["files"][0].update(file_id="../f"),
        "sha_not_hex": lambda: data["files"][0].update(sha256="z" * 64),
        "workspace_mismatch": lambda: context.update(active_workspace="someone-else"),
        "uploads_disagree": lambda: context.update(uploaded_files=[first_upload, first_upload]),
        "next_seq_string": lambda: data.update(next_seq="2"),
        "turn_ids_traversal": lambda: data.update(turn_ids=["../../x"]),
    }
    changes[change]()


_CHANGES = [
    "version_bool", "context_version_float", "uploaded_files_string", "uploaded_files_dict",
    "active_file_elsewhere", "active_file_number", "analyzed_elsewhere", "message_number",
    "metadata_list", "created_at_number", "created_at_naive", "history_naive", "bad_role",
    "bad_mode", "missing_key", "extra_key", "file_name_traversal", "file_id_traversal",
    "sha_not_hex", "workspace_mismatch", "uploads_disagree", "next_seq_string",
    "turn_ids_traversal",
]


@pytest.mark.parametrize("change", _CHANGES)
def test_a_corrupt_session_record_is_a_clean_404(
    settings: Settings, change: str, caplog: pytest.LogCaptureFixture
) -> None:
    session_id, _, _ = _delivered_session(settings)
    path = _session_json(settings, session_id)
    data = json.loads(path.read_text())
    _mutate(data, change)
    path.write_text(json.dumps(data))
    caplog.set_level(logging.ERROR, logger="backend.store")

    response = _client(settings).get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "session_not_found"
    assert "couldn't be restored" in caplog.text
    assert "/etc/passwd" not in caplog.text and "Alice" not in caplog.text


@pytest.mark.parametrize("content", [b"{not json", b"", b"[]", b"\xff\xfe", b"null"])
def test_an_unreadable_session_record_is_a_clean_404(settings: Settings, content: bytes) -> None:
    session_id, _, _ = _delivered_session(settings)
    _session_json(settings, session_id).write_bytes(content)

    assert _client(settings).get(f"/api/v1/sessions/{session_id}").status_code == 404


@pytest.mark.parametrize(
    "change",
    [
        {"status": "finished", "reply": None},
        {"status": "weird"},
        {"outcome": "delivered", "delivered_files": []},
        {"seq": 9},
        {"reply_source": "model"},
        {"finished_at": "yesterday"},
        {"detail": {"intent": 5}},
        {"detail": {"intent": None, "clarification_required": None, "stopped_at": None,
                    "steps": [{"capability": "x"}]}},
        {"delivered_files": [{"file_id": "d_t0001_1", "name": "../../uploads/x", "size": 1,
                              "sha256": "0" * 64, "delivered_at": "2026-10-03T10:00:00+00:00"}]},
    ],
)
def test_a_corrupt_turn_record_is_a_clean_404(settings: Settings, change: dict[str, Any]) -> None:
    session_id, turn, _ = _delivered_session(settings)
    path = _session_json(settings, session_id).parent / "turns" / f"{turn['turn_id']}.json"
    data = json.loads(path.read_text())
    data.update(change)
    path.write_text(json.dumps(data))

    assert _client(settings).get(f"/api/v1/sessions/{session_id}").status_code == 404


@pytest.mark.parametrize("error", [TypeError, AttributeError, RecursionError, OSError])
def test_any_exception_from_context_from_dict_is_caught(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, error: type[Exception]
) -> None:
    session_id, _, _ = _delivered_session(settings)

    def explode(*args: Any, **kwargs: Any) -> Any:
        raise error("boom")

    monkeypatch.setattr(Context, "from_dict", classmethod(explode))

    assert _client(settings).get(f"/api/v1/sessions/{session_id}").status_code == 404


def test_a_session_that_was_never_stored_is_404(settings: Settings) -> None:
    assert _client(settings).get("/api/v1/sessions/s_neverexisted").status_code == 404


# -- A write that fails rolls the change back ---------------------------------------------------


def test_a_failed_write_at_turn_start_creates_no_turn(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings, PipelineFactory(QueuedProvider(single("remove_empty_rows"))))
    session_id = new_session(client)
    upload(client, session_id, xlsx_bytes())

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("backend.store.SessionStore.save_turn", disk_full)
    response = start_turn(client, session_id, "remove the empty rows")

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["turns"] == [] and state["busy"] is False
    assert client.app.state.runner.running == 0  # type: ignore[attr-defined]
    assert not list((settings.outputs_dir / session_key(session_id)).glob("t*"))


def test_a_failed_write_at_upload_keeps_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = _client(settings)
    session_id = new_session(client)

    def disk_full(*args: Any, **kwargs: Any) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("backend.store.SessionStore.save_session", disk_full)
    response = upload(client, session_id, xlsx_bytes())

    assert response.status_code == 500
    monkeypatch.undo()
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["files"] == [] and state["active_file_id"] is None
    assert not [p for p in settings.uploads_dir.rglob("*") if p.is_file()]


# -- The request waiting on "which file?" (conversation repair) ----------------------------------


def _pending_session(settings: Settings) -> str:
    session_id, _, _ = _delivered_session(settings)
    return session_id


def _rewrite(settings: Settings, session_id: str, change: Any) -> None:
    path = _session_json(settings, session_id)
    data = json.loads(path.read_text())
    change(data)
    path.write_text(json.dumps(data))


def test_a_session_saved_before_pending_requests_existed_still_opens_with_none(
    settings: Settings,
) -> None:
    session_id = _pending_session(settings)
    _rewrite(settings, session_id, lambda data: data.pop("pending_request"))

    restarted = _client(settings)

    assert restarted.get(f"/api/v1/sessions/{session_id}").status_code == 200
    (record,) = restarted.app.state.registry._sessions.values()  # type: ignore[attr-defined]
    assert record.pending is None


def test_the_session_record_always_says_whether_a_request_is_waiting(settings: Settings) -> None:
    session_id = _pending_session(settings)

    data = json.loads(_session_json(settings, session_id).read_text())

    assert "pending_request" in data and data["pending_request"] is None


def test_a_waiting_request_is_saved_and_restored(settings: Settings) -> None:
    session_id = _pending_session(settings)
    _rewrite(
        settings,
        session_id,
        lambda data: data.update(
            pending_request={"message": "remove duplicate rows in x.xlsx", "turn_id": "t0001"}
        ),
    )

    restarted = _client(settings)
    restarted.get(f"/api/v1/sessions/{session_id}")
    (record,) = restarted.app.state.registry._sessions.values()  # type: ignore[attr-defined]

    assert record.pending is not None
    assert (record.pending.message, record.pending.turn_id) == (
        "remove duplicate rows in x.xlsx",
        "t0001",
    )
    # ...and writing the session back keeps it.
    restarted.app.state.registry.save_session(record)  # type: ignore[attr-defined]
    saved = json.loads(_session_json(settings, session_id).read_text())["pending_request"]
    assert saved == {"message": "remove duplicate rows in x.xlsx", "turn_id": "t0001"}


@pytest.mark.parametrize(
    "bad",
    [
        "a string",
        ["list"],
        {"message": "x"},
        {"message": "x", "turn_id": "t0001", "extra": 1},
        {"message": "", "turn_id": "t0001"},
        {"message": 5, "turn_id": "t0001"},
        {"message": "x" * 20_001, "turn_id": "t0001"},
        {"message": "x", "turn_id": "t0099"},
        {"message": "x", "turn_id": "../../t0001"},
        {"message": "x", "turn_id": 1},
    ],
)
def test_a_corrupt_waiting_request_is_a_clean_404(settings: Settings, bad: Any) -> None:
    session_id = _pending_session(settings)
    _rewrite(settings, session_id, lambda data: data.update(pending_request=bad))

    restarted = _client(settings)

    assert restarted.get(f"/api/v1/sessions/{session_id}").status_code == 404
