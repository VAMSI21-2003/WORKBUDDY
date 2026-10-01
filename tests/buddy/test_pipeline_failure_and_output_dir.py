"""R6 and F5 through the real pipeline (``docs/design/backend-api-design.md``
Sections 3.4, 4.2 and 5.3; PR-1).

R6: a stage that raises still ends in a reply, and that reply claims nothing.
F5: two turns in one session write into different folders, so the second
can't overwrite a file handed back by the first.

Every LLM call is faked; the skills, Hands, Inspector and Voice are real.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pandas as pd
import pytest

from buddy.engine_factory import build_engine
from buddy.inspector import Inspector, SpreadsheetDedupeVerifier
from buddy.models import ExecutionResult, Understanding, VerificationResult
from buddy.state import BuddyState, StageFailure
from buddy.voice import (
    _COMPLETION_CLAIM,
    PersonalityVoice,
    SpeakingRequest,
    TemplateSpeaker,
    Voice,
    _verified_delivery,
)
from buddy.voice_catalog import (
    FAILURE_STAGE_LABEL_DEFAULT,
    FAILURE_STAGE_LABELS,
    FALLBACK_INTEGRITY_FAILURE,
    FALLBACK_PIPELINE_ERROR,
    failure_reply,
)
from core.engine import CognitiveCycle
from skills.base import PermissionTier, SkillTask
from skills.excel.dedupe import ExcelDeduplicateSkill
from tests.buddy.test_engine_factory import (
    _chain_replies,
    _QueuedLLMProvider,
    _single_intent_replies,
    _write_fixture_with_duplicates_and_an_empty_row,
)

_SECRET = "secret cell value 4111"
_SPEAKERS = ["template", "friend", "focus"]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _failure_replies() -> list[str]:
    return [
        FALLBACK_PIPELINE_ERROR,
        FALLBACK_INTEGRITY_FAILURE,
        *(failure_reply(stage) for stage in FAILURE_STAGE_LABELS),
    ]


# -- The fixed failure replies --------------------------------------------------


@pytest.mark.parametrize("text", _failure_replies())
def test_no_failure_reply_claims_any_outcome(text: str) -> None:
    assert _COMPLETION_CLAIM.search(text) is None
    assert re.search(r"\d", text) is None
    assert "{" not in text


def test_every_stage_but_voice_has_a_failure_label() -> None:
    """Voice raising is the API's last-resort layer, not a Voice reply."""
    slots = set(CognitiveCycle.__dataclass_fields__)

    assert set(FAILURE_STAGE_LABELS) == slots - {"voice"}


@pytest.mark.parametrize("stage", [None, "", "voice", "something_new"])
def test_an_unknown_stage_gets_the_generic_failure_reply(stage: str | None) -> None:
    assert failure_reply(stage) == FALLBACK_PIPELINE_ERROR
    assert FAILURE_STAGE_LABEL_DEFAULT in FALLBACK_PIPELINE_ERROR


def test_the_failure_replies_say_nothing_is_confirmed_or_handed_back() -> None:
    for stage in FAILURE_STAGE_LABELS:
        reply = failure_reply(stage)
        assert FAILURE_STAGE_LABELS[stage] in reply
        assert "can't confirm" in reply
        assert "haven't handed back any file" in reply
    assert "held back every file" in FALLBACK_INTEGRITY_FAILURE


# -- Voice and the speakers, given a failure --------------------------------------


def _verified_looking_request(failure: StageFailure | None) -> SpeakingRequest:
    """Results that, without a failure, would count as a verified delivery."""
    return SpeakingRequest(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(passed=True),
        mode="friend",
        failure=failure,
    )


def test_verified_delivery_is_false_whenever_a_stage_failed() -> None:
    assert _verified_delivery(_verified_looking_request(None)) is True  # control

    failure = StageFailure(stage="inspector", error_type="RuntimeError")
    assert _verified_delivery(_verified_looking_request(failure)) is False


@pytest.mark.parametrize("speaker", [TemplateSpeaker(), PersonalityVoice()])
def test_a_speaker_called_directly_with_a_failure_gives_the_failure_reply(speaker) -> None:  # type: ignore[no-untyped-def]
    failure = StageFailure(stage="inspector", error_type="RuntimeError")

    reply = speaker(_verified_looking_request(failure))

    assert reply == failure_reply("inspector")


class _SpySpeaker:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, request: SpeakingRequest) -> str:
        self.calls += 1
        return "Done — everything worked."


def test_voice_gives_the_failure_reply_without_calling_its_speaker() -> None:
    speaker = _SpySpeaker()
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.execution_result = ExecutionResult(success=True, outputs=("out.xlsx",))
    state.verification_result = VerificationResult(passed=True)
    state.failure = StageFailure(stage="hands", error_type="ValueError")

    Voice(speaker=speaker)(state)

    assert speaker.calls == 0
    assert state.reply == failure_reply("hands")


def test_voice_still_calls_its_speaker_when_nothing_failed() -> None:
    speaker = _SpySpeaker()
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    Voice(speaker=speaker)(state)

    assert speaker.calls == 1


# -- R6 end to end: a stage raises after a real output exists --------------------


def _start(tmp_path: Path, speaker_kind: str) -> tuple[BuddyState, Path, str]:
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates_and_an_empty_row(tmp_path / input_path)
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove the duplicate rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    if speaker_kind != "template":
        state.context.set_mode(speaker_kind)
    return state, tmp_path / input_path, _sha256(tmp_path / input_path)


def _speaker(kind: str) -> PersonalityVoice | None:
    return None if kind == "template" else PersonalityVoice()


def _assert_claims_nothing(state: BuddyState, stage: str) -> None:
    assert state.reply == failure_reply(stage)
    assert _COMPLETION_CLAIM.search(state.reply) is None
    assert _SECRET not in state.reply
    assert "4111" not in state.reply
    assert _verified_delivery(Voice._build_request(state)) is False
    for output in state.execution_result.outputs if state.execution_result else ():
        assert Path(output).name not in state.reply


def _raise_secret(*args: object, **kwargs: object) -> object:
    raise RuntimeError(_SECRET)


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_inspector_raising_after_a_real_output_yields_a_reply_that_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """The design's required case (Section 4.2): Hands produced and verified
    a real file, then Inspector raised. Nothing may be claimed."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Inspector, "__call__", _raise_secret)
    state, original, original_hash = _start(tmp_path, speaker_kind)
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))

    build_engine(llm_provider=provider, speaker=_speaker(speaker_kind)).run(state)

    assert state.failure == StageFailure(stage="inspector", error_type="RuntimeError")
    # A real, correct output exists on disk: this is the dangerous case.
    assert state.execution_result is not None
    assert state.execution_result.success is True
    output = Path(state.execution_result.outputs[0])
    assert output.is_file()
    assert int(pd.read_excel(output).duplicated().sum()) == 0
    assert state.verification_result is None
    _assert_claims_nothing(state, "inspector")
    assert _sha256(original) == original_hash


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_a_skill_raising_inside_hands_yields_a_reply_that_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ExcelDeduplicateSkill, "execute", _raise_secret)
    state, original, original_hash = _start(tmp_path, speaker_kind)
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))

    build_engine(llm_provider=provider, speaker=_speaker(speaker_kind)).run(state)

    assert state.failure == StageFailure(stage="hands", error_type="RuntimeError")
    assert state.execution_result is None  # Hands never recorded a result
    _assert_claims_nothing(state, "hands")
    assert _sha256(original) == original_hash


def test_a_verifier_raising_inside_the_chain_yields_a_reply_that_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Backlog R6's own example. The dedupe verifier gates the chain step in
    Hands, so that is where it raises; the skill's file is already written."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(SpreadsheetDedupeVerifier, "__call__", _raise_secret)
    state, original, original_hash = _start(tmp_path, "friend")
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))

    build_engine(llm_provider=provider, speaker=PersonalityVoice()).run(state)

    assert state.failure == StageFailure(stage="hands", error_type="RuntimeError")
    written = list((tmp_path / "workspace" / "outputs").rglob("*.xlsx"))
    assert len(written) == 1  # an intermediate file exists, and isn't mentioned
    assert written[0].name not in (state.reply or "")
    _assert_claims_nothing(state, "hands")
    assert _sha256(original) == original_hash


# -- build_engine's confirmation_provider ------------------------------------------


class _Declines:
    def __init__(self) -> None:
        self.calls: list[tuple[str, PermissionTier]] = []

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        self.calls.append((task.description, tier))
        return False


def test_build_engine_uses_the_given_confirmation_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state, original, original_hash = _start(tmp_path, "friend")
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))
    confirmations = _Declines()

    build_engine(
        llm_provider=provider, speaker=PersonalityVoice(), confirmation_provider=confirmations
    ).run(state)

    assert [tier for _, tier in confirmations.calls] == [PermissionTier.WRITE]
    assert "spreadsheet.deduplicate" in confirmations.calls[0][0]
    assert state.failure is None
    assert state.execution_result is not None
    assert state.execution_result.outputs == ()
    assert state.execution_result.stopped_at == "spreadsheet.deduplicate"
    assert not (tmp_path / "workspace" / "outputs").exists()  # nothing was written
    assert state.reply is not None
    assert _COMPLETION_CLAIM.search(state.reply) is None
    assert _sha256(original) == original_hash


# -- F5 end to end: two turns in one session ---------------------------------------


def _turn(state: BuddyState, message: str, replies: list[str], turn_dir: Path) -> tuple[str, ...]:
    state.reset_transient()
    state.user_message = message
    turn_dir.mkdir(parents=True, exist_ok=False)
    state.turn_output_dir = str(turn_dir)
    build_engine(llm_provider=_QueuedLLMProvider(replies), speaker=PersonalityVoice()).run(state)
    assert state.failure is None
    assert state.verification_result is not None and state.verification_result.passed
    assert state.execution_result is not None
    return state.execution_result.outputs


def test_turns_of_the_same_kind_write_to_different_folders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Turn 1 runs proactive analysis first, so dedupe is task 1; later turns
    skip it (the file is already analyzed), so dedupe is task 0 in turns 2
    and 3. Without F5, turn 3 writes ``report_deduplicated_session-1-task-0
    .xlsx`` into the same folder as turn 2 and overwrites its delivered file."""
    monkeypatch.chdir(tmp_path)
    state, original, original_hash = _start(tmp_path, "friend")
    outputs_root = tmp_path / "data" / "outputs" / "session-1"
    replies = _single_intent_replies("remove_duplicate_rows")

    paths: list[Path] = []
    hashes: list[str] = []
    for number in (1, 2, 3):
        turn_dir = outputs_root / f"t{number:04d}"
        outputs = _turn(state, "remove the duplicate rows", list(replies), turn_dir)
        assert len(outputs) == 1
        paths.append(Path(outputs[0]))
        hashes.append(_sha256(paths[-1]))

    assert paths[1].name == paths[2].name  # same step, same task id
    assert [p.parent for p in paths] == [outputs_root / f"t{n:04d}" for n in (1, 2, 3)]
    assert [_sha256(p) for p in paths] == hashes  # no earlier delivery was touched
    assert not (tmp_path / "workspace").exists()  # nothing went to the legacy folder
    assert _sha256(original) == original_hash


def test_a_chain_turn_then_a_single_step_turn_keep_every_file_in_its_own_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    state, original, original_hash = _start(tmp_path, "focus")
    outputs_root = tmp_path / "data" / "outputs" / "session-1"
    chain = _chain_replies(
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
    )

    first = _turn(
        state,
        "remove the empty rows and then remove the duplicate rows",
        chain,
        outputs_root / "t0001",
    )
    first_hash = _sha256(Path(first[0]))
    second = _turn(
        state,
        "remove the duplicate rows",
        _single_intent_replies("remove_duplicate_rows"),
        outputs_root / "t0002",
    )

    turn_one = {p.name for p in (outputs_root / "t0001").iterdir()}
    turn_two = {p.name for p in (outputs_root / "t0002").iterdir()}
    assert len(turn_one) == 2  # the intermediate and the final file
    assert Path(first[0]).name in turn_one
    assert {Path(second[0]).name} == turn_two
    assert _sha256(Path(first[0])) == first_hash
    final = pd.read_excel(first[0])
    assert int(final.duplicated().sum()) == 0
    assert int(final.isna().all(axis=1).sum()) == 0
    assert _sha256(original) == original_hash
