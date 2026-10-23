"""A reply in several messages, through the API, on disk, after a restart, and with only Groq.

The real pipeline with the app's own speaker (``NaturalVoice``); only the models are faked.
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from buddy.llm import GroqProvider
from tests.backend.factories import PipelineFactory, QueuedProvider, Raising, single
from tests.backend.helpers import new_session, start_turn, upload, wait_finished, xlsx_bytes
from tests.buddy.writer_fakes import FactEchoWriter, echo_answer


def _delivered_turn(client: TestClient) -> tuple[str, dict[str, Any]]:
    session_id = new_session(client, "friend")
    assert upload(client, session_id, xlsx_bytes()).status_code == 201
    started = start_turn(client, session_id, "remove duplicate rows")
    assert started.status_code == 202, started.text
    assert started.json()["messages"] is None
    return session_id, wait_finished(client, session_id, started.json()["turn_id"])


def test_a_written_reply_is_its_messages_and_the_reply_is_them_one_per_line(
    settings: Settings,
) -> None:
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows")), speaker="natural", writer=FactEchoWriter()
    )
    client = TestClient(create_app(settings, factory))
    session_id, turn = _delivered_turn(client)

    assert turn["outcome"] == "delivered"
    assert len(turn["messages"]) == 3
    assert turn["reply"] == "\n".join(turn["messages"])
    assert turn["messages"][0] == "Removed 2 exact duplicate rows."
    summary = client.get(f"/api/v1/sessions/{session_id}").json()["turns"][0]
    assert summary["messages"] == turn["messages"]

    # On disk, and read back by a new server on the same data.
    restarted = TestClient(create_app(settings, PipelineFactory(QueuedProvider())))
    again = restarted.get(f"/api/v1/sessions/{session_id}/turns/{turn['turn_id']}").json()
    assert again["messages"] == turn["messages"] and again["reply"] == turn["reply"]


def test_a_one_message_reply_is_saved_as_before_and_read_as_one_message(
    settings: Settings,
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker="friend")
    client = TestClient(create_app(settings, factory))
    session_id, turn = _delivered_turn(client)

    assert turn["messages"] == [turn["reply"]]
    (record,) = settings.sessions_dir.rglob(f"turns/{turn['turn_id']}.json")
    assert "messages" not in json.loads(record.read_text(encoding="utf-8"))


def test_a_failed_stage_reply_is_one_message_and_the_writer_isnt_asked(
    settings: Settings,
) -> None:
    writer = FactEchoWriter()
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows")),
        speaker="natural",
        writer=writer,
        wrap=lambda cycle: dataclasses.replace(
            cycle, inspector=Raising(cycle.inspector, after=False)
        ),
    )
    client = TestClient(create_app(settings, factory))
    _, turn = _delivered_turn(client)

    assert turn["outcome"] == "failed"
    assert turn["messages"] == [turn["reply"]]
    assert writer.calls == []


def _route(prompt_system: str, messages: Any) -> str:
    """Groq, faked: Brain, Think and the planner by their prompts; the writer echoes facts."""
    if "intent classifier" in prompt_system:
        return json.dumps(
            {
                "intent": "remove_duplicate_rows",
                "confidence": 0.9,
                "requested_actions": [
                    {"action": "remove_duplicate_rows", "source_phrase": "remove duplicate rows"}
                ],
            }
        )
    if "reasoning stage" in prompt_system:
        return json.dumps(
            {"objective": "o", "summary": "s", "clarification_required": False, "confidence": 0.9}
        )
    if prompt_system.startswith("You are Buddy"):
        return echo_answer(messages[0].content)
    raise AssertionError("an unexpected model call")


def test_the_app_works_with_only_the_groq_key_and_groq_then_writes_too(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("GEMINI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROQ_API_KEY", "gsk_placeholderTESTONLY")
    systems: list[str] = []

    def complete(self: Any, *, system: str, messages: Any, max_tokens: int = 1024) -> str:
        systems.append(system)
        return _route(system, messages)

    monkeypatch.setattr(GroqProvider, "complete", complete)
    client = TestClient(create_app(settings))  # the production engine factory

    assert client.get("/healthz").json() == {"ok": True, "llm_configured": True}
    _, turn = _delivered_turn(client)
    assert turn["outcome"] == "delivered"
    assert len(turn["messages"]) == 3
    assert any(system.startswith("You are Buddy") for system in systems)  # Groq wrote it


def test_follow_ups_on_the_copy_are_written_and_checked_too(settings: Settings) -> None:
    """The second turn works on the copy and names it; the third finds nothing left to remove.
    Both drafts (the facts' own sentences) pass the check: no template reply."""
    replies = (
        single("remove_duplicate_rows")
        + single("remove_empty_rows")
        + single("remove_duplicate_rows")
    )
    writer = FactEchoWriter()
    factory = PipelineFactory(QueuedProvider(replies), speaker="natural", writer=writer)
    client = TestClient(create_app(settings, factory))
    session_id = new_session(client, "friend")
    assert upload(client, session_id, xlsx_bytes()).status_code == 201
    turns = []
    for message in ("remove duplicate rows", "remove empty rows", "remove duplicate rows"):
        started = start_turn(client, session_id, message)
        turns.append(wait_finished(client, session_id, started.json()["turn_id"]))

    assert [t["outcome"] for t in turns] == ["delivered", "delivered", "not_delivered"]
    assert "the latest copy I handed back" in turns[1]["messages"][1]
    assert "already has no exact duplicate rows" in turns[2]["messages"][0]
    assert len(turns[2]["messages"]) == 2  # written, not the one-message template reply
    assert len(writer.calls) == 3
