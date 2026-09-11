"""Unit tests for buddy.think."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from buddy.context import ConversationRole, ConversationTurn
from buddy.llm import LLMMessage
from buddy.models import Understanding
from buddy.state import BuddyState
from buddy.think import ClaudeThinker, NullThinker, Reasoning, Think, ThinkingRequest


class _FakeLLMProvider:
    """A minimal ``LLMProvider`` stand-in that returns a canned reply."""

    def __init__(self, reply: str | None = None, *, raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises
        self.last_system: str | None = None
        self.last_messages: tuple[LLMMessage, ...] | None = None

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.last_system = system
        self.last_messages = messages
        if self._raises is not None:
            raise self._raises
        assert self._reply is not None
        return self._reply


def _make_state() -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file("report.xlsx")
    return state


def _empty_request(**overrides: object) -> ThinkingRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "observations": (),
        "uploaded_files": (),
        "active_task": None,
        "recent_conversation": (),
    }
    fields.update(overrides)
    return ThinkingRequest(**fields)  # type: ignore[arg-type]


# -- Think stage: dead-field fix + repointed read ----------------------------


def test_think_sets_typed_reasoning_field() -> None:
    state = _make_state()
    reasoning = Reasoning(objective="dedupe the file", confidence=0.8)

    class _FixedThinker:
        def __call__(self, request: ThinkingRequest) -> Reasoning:
            return reasoning

    stage = Think(thinker=_FixedThinker())
    stage(state)

    assert state.reasoning == reasoning


def test_think_no_longer_writes_metadata_reasoning_result() -> None:
    state = _make_state()
    stage = Think(thinker=NullThinker())

    stage(state)

    assert "reasoning_result" not in state.metadata
    assert state.reasoning is not None  # NullThinker still returns a real Reasoning


def test_think_reads_understanding_from_typed_field_not_metadata() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    # Deliberately do NOT populate state.metadata["understanding"] — if Think
    # still read from metadata, this would regress to an empty Understanding.
    seen: list[Understanding] = []

    class _RecordingThinker:
        def __call__(self, request: ThinkingRequest) -> Reasoning:
            seen.append(request.understanding)
            return Reasoning()

    stage = Think(thinker=_RecordingThinker())
    stage(state)

    assert seen == [state.understanding]


def test_think_falls_back_to_empty_understanding_when_none_set() -> None:
    state = _make_state()
    seen: list[Understanding] = []

    class _RecordingThinker:
        def __call__(self, request: ThinkingRequest) -> Reasoning:
            seen.append(request.understanding)
            return Reasoning()

    stage = Think(thinker=_RecordingThinker())
    stage(state)

    assert seen == [Understanding()]


# -- ClaudeThinker -------------------------------------------------------------


def test_claude_thinker_parses_valid_json_reply() -> None:
    reply = json.dumps(
        {
            "objective": "Remove duplicate rows from report.xlsx",
            "summary": "The request is clear and a file is available.",
            "assumptions": ["duplicates means exact full-row duplicates"],
            "constraints": [],
            "risks": [],
            "recommended_strategy": "Use the Excel skill to deduplicate rows.",
            "clarification_required": False,
            "confidence": 0.9,
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    thinker = ClaudeThinker(provider)
    request = _empty_request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        uploaded_files=("report.xlsx",),
    )

    reasoning = thinker(request)

    assert reasoning.objective == "Remove duplicate rows from report.xlsx"
    assert reasoning.clarification_required is False
    assert reasoning.confidence == pytest.approx(0.9)


def test_claude_thinker_prompt_contains_only_intent_and_file_references() -> None:
    """The prompt must be built solely from the classified ``Understanding``
    and file references — never from opening the uploaded file — since
    neither ``Understanding`` nor ``ThinkingRequest`` has a field capable of
    carrying cell or row data in the first place. An exact match proves
    nothing extra (e.g. file content) was added.
    """
    provider = _FakeLLMProvider(
        reply=json.dumps({"objective": "", "confidence": 0.0, "clarification_required": True})
    )
    thinker = ClaudeThinker(provider)
    request = _empty_request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        uploaded_files=("report.xlsx",),
    )

    thinker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert prompt == (
        "Classified intent: 'remove_duplicate_rows' (confidence=0.90)\n"
        "Uploaded files (names only): report.xlsx"
    )


def test_claude_thinker_prompt_includes_recent_conversation_text_only() -> None:
    provider = _FakeLLMProvider(
        reply=json.dumps({"objective": "", "confidence": 0.0, "clarification_required": True})
    )
    thinker = ClaudeThinker(provider)
    request = _empty_request(
        recent_conversation=(
            ConversationTurn(
                role=ConversationRole.USER,
                message="remove duplicate rows",
                timestamp=datetime.now(timezone.utc),
            ),
        ),
    )

    thinker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "Recent conversation:" in prompt
    assert "user: remove duplicate rows" in prompt


def test_claude_thinker_falls_back_on_invalid_json() -> None:
    provider = _FakeLLMProvider(reply="not json")
    thinker = ClaudeThinker(provider)

    reasoning = thinker(_empty_request())

    assert reasoning.clarification_required is True
    assert reasoning.confidence == 0.0
    assert reasoning.risks


def test_claude_thinker_falls_back_on_unexpected_shape() -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"confidence": "not-a-number"}))
    thinker = ClaudeThinker(provider)

    reasoning = thinker(_empty_request())

    assert reasoning.clarification_required is True
    assert reasoning.confidence == 0.0


def test_claude_thinker_falls_back_on_provider_failure() -> None:
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    thinker = ClaudeThinker(provider)

    reasoning = thinker(_empty_request())

    assert reasoning.clarification_required is True
    assert reasoning.confidence == 0.0


def test_claude_thinker_wired_into_think_stage() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    reply = json.dumps(
        {"objective": "dedupe", "confidence": 0.8, "clarification_required": False}
    )
    provider = _FakeLLMProvider(reply=reply)
    stage = Think(thinker=ClaudeThinker(provider))

    stage(state)

    assert state.reasoning is not None
    assert state.reasoning.objective == "dedupe"
