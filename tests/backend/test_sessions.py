"""Sessions: create, read, limits from the enforcing settings, ids and isolation."""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.sessions import session_key
from backend.settings import Settings


@pytest.fixture
def client(settings: Settings) -> TestClient:
    return TestClient(create_app(settings, lambda provider: None))  # type: ignore[arg-type,return-value]


def test_create_session_returns_201_with_limits_and_no_retention(
    client: TestClient, settings: Settings
) -> None:
    response = client.post("/api/v1/sessions", json={"mode": "focus"})

    assert response.status_code == 201
    body = response.json()
    assert body["session_id"].startswith("s_")
    assert body["mode"] == "focus"
    datetime.fromisoformat(body["created_at"])
    assert body["limits"] == {
        "max_upload_bytes": settings.max_upload_bytes,
        "accepted_extensions": [".xlsx"],
        "max_message_chars": settings.max_message_chars,
    }
    assert body["retention_hours"] is None


def test_create_session_without_a_body_defaults_to_friend(client: TestClient) -> None:
    response = client.post("/api/v1/sessions")

    assert response.status_code == 201
    assert response.json()["mode"] == "friend"


@pytest.mark.parametrize("body", [{"mode": "chatty"}, {"mode": "focus", "extra": 1}, {"mode": 3}])
def test_create_session_rejects_an_invalid_body(client: TestClient, body: object) -> None:
    response = client.post("/api/v1/sessions", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_limits_follow_the_settings_object(tmp_path) -> None:  # type: ignore[no-untyped-def]
    custom = Settings(data_dir=tmp_path, max_upload_bytes=1234, max_message_chars=55)
    client = TestClient(create_app(custom, lambda provider: None))  # type: ignore[arg-type,return-value]

    session_id = client.post("/api/v1/sessions").json()["session_id"]
    for body in (
        client.post("/api/v1/sessions").json(),
        client.get(f"/api/v1/sessions/{session_id}").json(),
    ):
        assert body["limits"]["max_upload_bytes"] == 1234
        assert body["limits"]["max_message_chars"] == 55
        assert body["limits"]["accepted_extensions"] == list(custom.accepted_extensions)


def test_get_session_returns_the_whole_empty_session(client: TestClient) -> None:
    session_id = client.post("/api/v1/sessions", json={"mode": "focus"}).json()["session_id"]

    response = client.get(f"/api/v1/sessions/{session_id}")

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "session_id": session_id,
        "mode": "focus",
        "busy": False,
        "active_file_id": None,
        "files": [],
        "turns": [],
        "pending_confirmation": None,
        "limits": body["limits"],
        "retention_hours": None,
    }


def test_session_ids_are_128_random_bits_and_distinct(client: TestClient) -> None:
    ids = {client.post("/api/v1/sessions").json()["session_id"] for _ in range(20)}

    assert len(ids) == 20
    for session_id in ids:
        assert len(session_id) == len("s_") + 22  # token_urlsafe(16)


@pytest.mark.parametrize(
    "bad_id",
    ["s_doesnotexist", "..", "%2e%2e", "a" * 65, "s_a b", "s_<x>", "s_é"],
)
def test_an_unknown_or_malformed_session_is_404(client: TestClient, bad_id: str) -> None:
    response = client.get(f"/api/v1/sessions/{bad_id}")

    assert response.status_code == 404
    assert response.json()["error"]["code"] in {"session_not_found", "not_found"}


def test_a_traversal_attempt_never_reaches_a_session(client: TestClient) -> None:
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    for path in (
        f"/api/v1/sessions/{session_id}/../{session_id}",
        f"/api/v1/sessions/..%2F{session_id}",
        f"/api/v1/sessions/{session_id}%2F..",
    ):
        response = client.get(path)
        assert response.status_code in (200, 404)
        if response.status_code == 200:  # the client normalised the path itself
            assert response.json()["session_id"] == session_id


def test_the_session_id_is_never_written_to_disk(client: TestClient, settings: Settings) -> None:
    session_id = client.post("/api/v1/sessions").json()["session_id"]

    names = [p.name for p in settings.data_dir.rglob("*")]
    contents = b"".join(p.read_bytes() for p in settings.data_dir.rglob("*") if p.is_file())
    assert session_id not in "".join(names)
    assert session_id.encode() not in contents


def test_the_session_key_is_not_the_id() -> None:
    key = session_key("s_example")

    assert len(key) == 32
    assert "example" not in key
    assert key == session_key("s_example")
    assert key != session_key("s_examplf")
