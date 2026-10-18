"""Brain and Think try again once when a call fails, and log each failure without its words.

A failed attempt is a call that raised, a reply that wasn't JSON, or one with the wrong shape.
The call has no side effects, so a second try is safe. Two failures in a row give the same honest
result as one did before the retry existed.

The second try comes after a pause of about a second (with jitter), and a call that raised is
tried again only when the error was a 429, a timeout or a 5xx: any other error would fail the
same way, so a retry would only use up the provider's patience and the turn's deadline.
"""

from __future__ import annotations

import json
import logging

import anthropic
import groq
import httpx
import pytest

from buddy.brain import ClaudeInterpreter, UnderstandingRequest
from buddy.context import ConversationTurn
from buddy.llm import (
    RETRY_JITTER,
    RETRY_PAUSE_S,
    LLMMessage,
    is_transient_failure,
    retry_pause_seconds,
)
from buddy.models import Understanding
from buddy.think import CLARIFY_FAILED, ClaudeThinker, ThinkingRequest

SECRET = "gsk_" + "A" * 24
_REQUEST = httpx.Request("POST", "https://provider.test/v1/messages")


def _status_error(status: int, message: str = "provider said no") -> groq.APIStatusError:
    """What the groq SDK raises for an HTTP error status (anthropic's has the same shape)."""
    response = httpx.Response(status, request=_REQUEST)
    return groq.APIStatusError(message, response=response, body=None)


BAD_REPLIES = {
    "raised": _status_error(429, f"429 from the provider, key {SECRET}"),
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


def _no_sleep(seconds: float) -> None:
    """A stand-in for ``time.sleep`` in tests that don't look at the wait."""


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
        reasoning = ClaudeThinker(provider, sleep=_no_sleep)(_think_request())

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
        reasoning = ClaudeThinker(provider, sleep=_no_sleep)(_think_request())

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
        understanding = ClaudeInterpreter(provider, sleep=_no_sleep)(_brain_request())

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
        understanding = ClaudeInterpreter(provider, sleep=_no_sleep)(_brain_request())

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
            ClaudeThinker(_Scripted(*bad), sleep=_no_sleep)(_think_request())
        else:
            ClaudeInterpreter(_Scripted(*bad), sleep=_no_sleep)(_brain_request())

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

    ClaudeThinker(Recording(BAD_REPLIES["not json"], GOOD_THINK), sleep=_no_sleep)(
        _think_request()
    )

    assert len(seen) == 2 and seen[0] == seen[1]


# -- the wait and the kinds of error ---------------------------------------------------------------


class _Pauses:
    """Records the waits a stage asks for instead of sleeping."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _run(stage: str, provider: _Scripted, pauses: _Pauses) -> None:
    if stage == "think":
        ClaudeThinker(provider, sleep=pauses)(_think_request())
    else:
        ClaudeInterpreter(provider, sleep=pauses)(_brain_request())


GOOD = {"think": GOOD_THINK, "brain": GOOD_BRAIN}

TRANSIENT = {
    "429": _status_error(429),
    "500": _status_error(500),
    "503": _status_error(503),
    "529": _status_error(529),
    "groq timeout": groq.APITimeoutError(request=_REQUEST),
    "anthropic timeout": anthropic.APITimeoutError(request=_REQUEST),
    "httpx timeout": httpx.ReadTimeout("slow", request=_REQUEST),
    "builtin timeout": TimeoutError("slow"),
}
NOT_TRANSIENT = {
    "400": _status_error(400),
    "401": _status_error(401),
    "403": _status_error(403),
    "404": _status_error(404),
    "422": _status_error(422),
    "499": _status_error(499),
    "connection": groq.APIConnectionError(request=_REQUEST),
    "plain": RuntimeError("429 in the text only"),
    "value": ValueError("bad"),
}


@pytest.mark.parametrize("name", list(TRANSIENT))
def test_a_429_a_timeout_or_a_5xx_is_worth_another_try(name: str) -> None:
    assert is_transient_failure(TRANSIENT[name])


@pytest.mark.parametrize("name", list(NOT_TRANSIENT))
def test_any_other_error_is_not(name: str) -> None:
    """Read from the type and the status, never from the message ("429" in the text isn't one)."""
    assert not is_transient_failure(NOT_TRANSIENT[name])


@pytest.mark.parametrize("stage", ["think", "brain"])
@pytest.mark.parametrize("name", list(TRANSIENT))
def test_a_transient_error_is_retried_once_after_a_pause(stage: str, name: str) -> None:
    provider, pauses = _Scripted(TRANSIENT[name], GOOD[stage]), _Pauses()

    _run(stage, provider, pauses)

    assert provider.calls == 2
    (wait,) = pauses.waits
    assert RETRY_PAUSE_S * (1 - RETRY_JITTER) <= wait <= RETRY_PAUSE_S * (1 + RETRY_JITTER)


@pytest.mark.parametrize("stage", ["think", "brain"])
@pytest.mark.parametrize("name", list(NOT_TRANSIENT))
def test_an_error_that_a_second_try_cannot_fix_is_not_retried(stage: str, name: str) -> None:
    provider, pauses = _Scripted(NOT_TRANSIENT[name], GOOD[stage]), _Pauses()

    _run(stage, provider, pauses)

    assert provider.calls == 1 and pauses.waits == []


@pytest.mark.parametrize("stage", ["think", "brain"])
def test_two_transient_errors_wait_once_not_after_the_last_try(stage: str) -> None:
    provider, pauses = _Scripted(_status_error(429), _status_error(429)), _Pauses()

    _run(stage, provider, pauses)

    assert provider.calls == 2 and len(pauses.waits) == 1


@pytest.mark.parametrize("stage", ["think", "brain"])
@pytest.mark.parametrize("kind", ["not json", "shape"])
def test_a_misshapen_reply_is_tried_again_after_the_same_pause(stage: str, kind: str) -> None:
    provider, pauses = _Scripted(BAD_REPLIES[kind], GOOD[stage]), _Pauses()

    _run(stage, provider, pauses)

    assert provider.calls == 2 and len(pauses.waits) == 1


def test_the_pause_is_about_a_second_and_moves_with_the_draw() -> None:
    assert retry_pause_seconds(0.5) == pytest.approx(RETRY_PAUSE_S)
    assert retry_pause_seconds(0.0) == pytest.approx(RETRY_PAUSE_S * (1 - RETRY_JITTER))
    assert retry_pause_seconds(0.999999) == pytest.approx(RETRY_PAUSE_S * (1 + RETRY_JITTER))
    assert 0.5 <= RETRY_PAUSE_S - RETRY_PAUSE_S * RETRY_JITTER and RETRY_PAUSE_S <= 1.5
    draws = {retry_pause_seconds() for _ in range(50)}
    assert len(draws) > 1, "the default draw is random"


@pytest.mark.parametrize("stage", ["think", "brain"])
def test_the_default_wait_is_a_real_sleep(
    stage: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing is passed in production, so the stages sleep through ``time.sleep``."""
    slept: list[float] = []
    monkeypatch.setattr("time.sleep", slept.append)
    provider = _Scripted(_status_error(429), GOOD[stage])

    if stage == "think":
        ClaudeThinker(provider)(_think_request())
    else:
        ClaudeInterpreter(provider)(_brain_request())

    assert len(slept) == 1 and 0.5 < slept[0] < 1.5
