"""Two replies the backend API made ordinary (``docs/reviews/backend-api-core-review.md``).

M1: a turn in which nothing ran at all gets one fixed reply, not the
"No verifier has been configured" wording.
M2: a delivered reply says the copy was made from the original upload when
the session already handed back a file in an earlier turn.

Every speaker is real; every LLM call is faked.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
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
from buddy.voice_catalog import FROM_ORIGINAL_SENTENCE, NO_FILE_SENTENCE
from tests.buddy.test_engine_factory import _QueuedLLMProvider, _single_intent_replies

_SPEAKERS = ["template", "friend", "focus"]


def _speaker(kind: str) -> object:
    return TemplateSpeaker() if kind == "template" else PersonalityVoice()


def test_the_new_constants_claim_nothing() -> None:
    assert _COMPLETION_CLAIM.search(NO_FILE_SENTENCE) is None
    assert _COMPLETION_CLAIM.search(FROM_ORIGINAL_SENTENCE) is None
    for text in (NO_FILE_SENTENCE, FROM_ORIGINAL_SENTENCE):
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


# -- M2 ------------------------------------------------------------------------------


def _delivered_state(tmp_path: Path, kind: str, earlier: int) -> BuddyState:
    upload = tmp_path / "report.xlsx"
    pd.DataFrame({"name": ["Alice", "Bob", "Alice"], "amount": [1, 2, 1]}).to_excel(
        upload, index=False
    )
    out = tmp_path / f"out{earlier}"
    out.mkdir()
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.context.add_uploaded_file(str(upload))
    state.context.switch_active_file(str(upload))
    state.user_message = "remove duplicate rows"
    state.turn_output_dir = str(out)
    state.earlier_deliveries = earlier
    if kind != "template":
        state.context.set_mode(kind)
    speaker = None if kind == "template" else PersonalityVoice()
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))
    build_engine(llm_provider=provider, speaker=speaker).run(state)
    assert state.execution_result is not None and state.execution_result.outputs
    return state


@pytest.mark.parametrize("kind", _SPEAKERS)
def test_a_delivered_reply_says_it_came_from_the_original_after_an_earlier_delivery(
    kind: str, tmp_path: Path
) -> None:
    first = _delivered_state(tmp_path, kind, earlier=0)
    later = _delivered_state(tmp_path, kind, earlier=1)

    assert FROM_ORIGINAL_SENTENCE not in (first.reply or "")
    assert FROM_ORIGINAL_SENTENCE in (later.reply or "")
    # It follows the outcome block, and the rest of the reply is unchanged.
    assert (later.reply or "").replace(FROM_ORIGINAL_SENTENCE + " ", "").replace(
        " " + FROM_ORIGINAL_SENTENCE, ""
    ) == first.reply


@pytest.mark.parametrize("kind", _SPEAKERS)
def test_an_undelivered_reply_never_gets_the_from_original_sentence(kind: str) -> None:
    request = SpeakingRequest(
        understanding=Understanding(intent="unclear"),
        execution_result=ExecutionResult(success=True, warnings=("nothing to execute",)),
        verification_result=VerificationResult(passed=False),
        mode="friend",
        earlier_deliveries=3,
    )

    assert FROM_ORIGINAL_SENTENCE not in _speaker(kind)(request)  # type: ignore[operator]


def test_reset_transient_clears_earlier_deliveries() -> None:
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.earlier_deliveries = 2

    state.reset_transient()

    assert state.earlier_deliveries == 0
