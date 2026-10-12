"""A reply the backend API made ordinary (``docs/reviews/backend-api-core-review.md``).

M1: a turn in which nothing ran at all gets one fixed reply, not the
"No verifier has been configured" wording.

(M2, "I made this copy from your original upload", was replaced by the follow-up turns:
``tests/buddy/test_follow_up_files.py``.)

Every speaker is real; every LLM call is faked.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from buddy.engine_factory import build_engine
from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.state import BuddyState
from buddy.voice import (
    _COMPLETION_CLAIM,
    PersonalityVoice,
    SpeakingRequest,
    TemplateSpeaker,
)
from buddy.voice_catalog import NO_FILE_SENTENCE
from tests.buddy.test_engine_factory import _QueuedLLMProvider, _single_intent_replies

_SPEAKERS = ["template", "friend", "focus"]


def _speaker(kind: str) -> object:
    return TemplateSpeaker() if kind == "template" else PersonalityVoice()


def test_the_new_constants_claim_nothing() -> None:
    assert _COMPLETION_CLAIM.search(NO_FILE_SENTENCE) is None
    for text in (NO_FILE_SENTENCE,):
        assert "{" not in text and not any(c.isdigit() for c in text)


# -- M1 ------------------------------------------------------------------------------


@pytest.mark.parametrize("kind", _SPEAKERS)
def test_a_turn_where_nothing_ran_gets_the_fixed_reply(kind: str) -> None:
    request = SpeakingRequest(
        understanding=Understanding(intent="unclear"),
        execution_result=ExecutionResult(success=True, warnings=("nothing to execute",)),
        verification_result=VerificationResult(passed=False, findings=("x",)),
        mode="focus" if kind == "focus" else "friend",
        has_file=False,
    )

    reply = _speaker(kind)(request)  # type: ignore[operator]

    assert reply.startswith(NO_FILE_SENTENCE)
    assert "verifier" not in reply.lower() and "confirm that worked" not in reply


@pytest.mark.parametrize("kind", _SPEAKERS)
def test_a_turn_with_no_upload_through_the_real_pipeline(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.user_message = "remove duplicate rows"
    if kind != "template":
        state.context.set_mode(kind)
    speaker = None if kind == "template" else PersonalityVoice()

    build_engine(llm_provider=provider, speaker=speaker).run(state)

    assert state.execution_result is not None and not state.execution_result.steps
    assert (state.reply or "").startswith(NO_FILE_SENTENCE)


@pytest.mark.parametrize("kind", _SPEAKERS)
def test_a_turn_where_a_step_ran_keeps_its_own_reply(kind: str) -> None:
    """Only a turn with no step, output or error gets the fixed reply."""
    request = SpeakingRequest(
        understanding=Understanding(intent="unclear"),
        execution_result=ExecutionResult(success=False, errors=("could not be run",)),
        verification_result=VerificationResult(passed=False, findings=("could not be run",)),
        mode="friend",
    )

    assert not _speaker(kind)(request).startswith(NO_FILE_SENTENCE)  # type: ignore[operator]
