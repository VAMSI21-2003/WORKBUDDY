"""Brain and Think try again once when a call fails, and log each failure without its words.

A failed attempt is a call that raised, a reply that wasn't JSON, or one with the wrong shape.
The call has no side effects, so a second try is safe. Two failures in a row give the same honest
result as one did before the retry existed.
"""

from __future__ import annotations

import json
import logging

import pytest

from buddy.brain import ClaudeInterpreter, UnderstandingRequest
from buddy.context import ConversationTurn
from buddy.llm import LLMMessage
from buddy.models import Understanding
from buddy.think import CLARIFY_FAILED, ClaudeThinker, ThinkingRequest

SECRET = "gsk_" + "A" * 24
BAD_REPLIES = {
    "raised": RuntimeError(f"429 from the provider, key {SECRET}"),
    "not json": "Sure! Here is my answer, not JSON at all: cell 4111",
    "shape": json.dumps([1, 2, 3]),
}
GOOD_BRAIN = json.dumps(
    {
        "intent": "remove_duplicate_rows",
        "confidence": 0.9,
        "requested_actions": [
            {"action": "remove_duplicate_rows", "source_phrase": "remove duplicate rows"}
        ],
    }
)
GOOD_THINK = json.dumps({"objective": "dedupe", "confidence": 0.8, "clarification_required": False})


class _Scripted:
    """Replies in call order; an exception in the list is raised."""

    def __init__(self, *replies: object) -> None:
        self._replies = list(replies)
        self.calls = 0

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.calls += 1
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return str(reply)


def _brain_request() -> UnderstandingRequest:
    return UnderstandingRequest(
        user_message="remove duplicate rows",
        observations=(),
        uploaded_files=("report.xlsx",),
        active_task=None,
        active_file=None,
        recent_conversation=(),
        turn_id="t0007",
    )


def _think_request() -> ThinkingRequest:
    turns: tuple[ConversationTurn, ...] = ()
    return ThinkingRequest(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        observations=(),
        uploaded_files=("report.xlsx",),
        active_task=None,
        recent_conversation=turns,
        user_message="remove duplicate rows",
        turn_id="t0007",
    )


@pytest.mark.parametrize("kind", list(BAD_REPLIES))
def test_think_tries_again_after_one_failure_and_uses_the_second_reading(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _Scripted(BAD_REPLIES[kind], GOOD_THINK)

    with caplog.at_level(logging.INFO, logger="buddy.think"):
        reasoning = ClaudeThinker(provider)(_think_request())

    assert provider.calls == 2
    assert reasoning.clarification_required is False and reasoning.objective == "dedupe"
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "t0007" in warning.getMessage() and "Think failed" in warning.getMessage()


@pytest.mark.parametrize("kind", list(BAD_REPLIES))
def test_two_think_failures_in_a_row_give_the_honest_failed_result(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _Scripted(BAD_REPLIES[kind], BAD_REPLIES[kind])

    with caplog.at_level(logging.INFO, logger="buddy.think"):
        reasoning = ClaudeThinker(provider)(_think_request())

    assert provider.calls == 2  # one retry, not more
    assert reasoning.clarification_required is True
    assert reasoning.clarification_reason == CLARIFY_FAILED
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2 and all("t0007" in w for w in warnings)


@pytest.mark.parametrize("kind", list(BAD_REPLIES))
def test_brain_tries_again_after_one_failure_and_uses_the_second_reading(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _Scripted(BAD_REPLIES[kind], GOOD_BRAIN)

    with caplog.at_level(logging.INFO, logger="buddy.brain"):
        understanding = ClaudeInterpreter(provider)(_brain_request())

    assert provider.calls == 2
    assert understanding.intent == "remove_duplicate_rows"
    (warning,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert "t0007" in warning.getMessage() and "Brain failed" in warning.getMessage()


@pytest.mark.parametrize("kind", list(BAD_REPLIES))
def test_two_brain_failures_in_a_row_give_no_reading(
    kind: str, caplog: pytest.LogCaptureFixture
) -> None:
    provider = _Scripted(BAD_REPLIES[kind], BAD_REPLIES[kind])

    with caplog.at_level(logging.INFO, logger="buddy.brain"):
        understanding = ClaudeInterpreter(provider)(_brain_request())

    assert provider.calls == 2
    assert understanding.intent is None and understanding.confidence == 0.0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 2 and all("t0007" in w for w in warnings)


@pytest.mark.parametrize("stage", ["buddy.think", "buddy.brain"])
def test_a_failure_is_logged_without_the_reply_the_message_or_a_key(
    stage: str, caplog: pytest.LogCaptureFixture
) -> None:
    """The WARNING names the turn and the kind of failure; nothing the model or the provider
    said is in it (the reply could echo the user's message; an exception could carry a key)."""
    bad = [BAD_REPLIES["raised"], BAD_REPLIES["not json"]]
    with caplog.at_level(logging.DEBUG, logger=stage):
        if stage == "buddy.think":
            ClaudeThinker(_Scripted(*bad))(_think_request())
        else:
            ClaudeInterpreter(_Scripted(*bad))(_brain_request())

    text = " ".join(
        r.getMessage() + (r.exc_text or "") for r in caplog.records if r.levelno >= logging.WARNING
    )
    assert "t0007" in text
    for forbidden in (SECRET, "4111", "Sure!", "remove duplicate rows", "429 from"):
        assert forbidden not in text
    # No level carries a traceback: at DEBUG it would hold the exception's message.
    assert all(r.exc_info is None for r in caplog.records)
    assert SECRET not in caplog.text and "429 from" not in caplog.text


def test_the_retry_does_not_widen_what_is_sent() -> None:
    """The second attempt sends exactly what the first did (nothing about the failure)."""
    seen: list[tuple[str, tuple[LLMMessage, ...]]] = []

    class Recording(_Scripted):
        def complete(
            self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
        ) -> str:
            seen.append((system, messages))
            return super().complete(system=system, messages=messages, max_tokens=max_tokens)

    ClaudeThinker(Recording(BAD_REPLIES["not json"], GOOD_THINK))(_think_request())

    assert len(seen) == 2 and seen[0] == seen[1]
