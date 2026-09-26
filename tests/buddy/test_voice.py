"""Unit tests for buddy.voice."""

from __future__ import annotations

import logging

import pytest

import buddy.voice as voice
from buddy.llm import LLMMessage
from buddy.models import (
    ExecutionResult,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.state import BuddyState
from buddy.voice import (
    FOCUS_MODE_PROMPT,
    FRIEND_MODE_PROMPT,
    NullSpeaker,
    PersonalityVoice,
    SpeakingRequest,
    TemplateSpeaker,
    Voice,
)


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


def _request(**overrides: object) -> SpeakingRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "execution_result": None,
        "verification_result": None,
        "mode": "friend",
    }
    fields.update(overrides)
    return SpeakingRequest(**fields)  # type: ignore[arg-type]


# -- NullSpeaker ------------------------------------------------------------------


def test_null_speaker_returns_honest_fallback() -> None:
    reply = NullSpeaker()(_request())

    assert "no speaker has been configured" in reply.lower()


# -- TemplateSpeaker ----------------------------------------------------------------
# TemplateSpeaker is untouched by the personality-mode addition — PersonalityVoice
# is an addition, not a replacement, and TemplateSpeaker simply ignores
# request.mode. These tests confirm it still works exactly as before.


def test_template_speaker_success_message_mentions_output_file_and_finding() -> None:
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("workspace/outputs/workspace-1/report_deduplicated.xlsx",)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("output has 3 row(s), 0 duplicate row(s) remaining",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "Done" in reply
    assert "report_deduplicated.xlsx" in reply
    assert "0 duplicate row(s)" in reply


def test_template_speaker_success_message_covers_every_steps_output_and_finding() -> None:
    """Regression guard for the single-output/single-finding assumption:
    a turn covering two delegated steps must mention both output files
    and both findings, not just the first of each.
    """
    request = _request(
        execution_result=ExecutionResult(
            success=True,
            outputs=(
                "workspace/outputs/workspace-1/report_deduplicated.xlsx",
                "workspace/outputs/workspace-1/report_no_empty_rows.xlsx",
            ),
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
                "[spreadsheet.remove_empty_rows] output has 3 row(s), "
                "0 fully-empty row(s) remaining",
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "Done" in reply
    assert "report_deduplicated.xlsx" in reply
    assert "report_no_empty_rows.xlsx" in reply
    assert "0 duplicate row(s)" in reply
    assert "0 fully-empty row(s)" in reply


def test_template_speaker_failure_message_covers_every_steps_finding() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("a.xlsx", "b.xlsx")),
        verification_result=VerificationResult(
            passed=False,
            confidence=1.0,
            findings=(
                "[spreadsheet.deduplicate] output has 3 row(s), 1 duplicate row(s) remaining",
                "[spreadsheet.remove_empty_rows] output has 4 row(s), "
                "1 fully-empty row(s) remaining",
            ),
            recommendations=(
                "re-run deduplication; duplicates remain",
                "re-run empty-row removal; empty rows remain",
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "1 duplicate row(s)" in reply
    assert "1 fully-empty row(s)" in reply
    assert "re-run deduplication" in reply
    assert "re-run empty-row removal" in reply


def test_template_speaker_failure_message_includes_findings_and_recommendations() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(
            passed=False,
            confidence=1.0,
            findings=("output has 3 row(s), 1 duplicate row(s) remaining",),
            recommendations=("re-run deduplication; duplicates remain",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "wasn't able to confirm" in reply.lower()
    assert "1 duplicate row(s)" in reply
    assert "re-run deduplication" in reply


def test_template_speaker_describes_execution_failure_when_verification_never_ran() -> None:
    request = _request(
        execution_result=ExecutionResult(success=False, errors=("could not read file",)),
        verification_result=None,
    )

    reply = TemplateSpeaker()(request)

    assert "could not read file" in reply


def test_template_speaker_handles_nothing_executed() -> None:
    request = _request(execution_result=None, verification_result=None)

    reply = TemplateSpeaker()(request)

    assert reply


def test_template_speaker_ignores_mode() -> None:
    """Confirms TemplateSpeaker produces identical output regardless of
    ``request.mode`` — it has no personality logic to select between.
    """
    base_kwargs = dict(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(passed=True, confidence=1.0, findings=("ok",)),
    )
    friend_reply = TemplateSpeaker()(_request(mode="friend", **base_kwargs))
    focus_reply = TemplateSpeaker()(_request(mode="focus", **base_kwargs))

    assert friend_reply == focus_reply


# -- TemplateSpeaker: proactive-analysis preface (Section 5.3) ------------------


def _analyze_step(**overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "capability": "spreadsheet.analyze",
        "skill": "excel.analyze",
        "status": "success",
    }
    fields.update(overrides)
    return StepResult(**fields)  # type: ignore[arg-type]


def test_template_speaker_leads_with_analysis_findings_before_the_ordinary_message() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, steps=(_analyze_step(),)),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("[spreadsheet.analyze] [outliers] amount: 1 unusually high value",),
            steps=(StepVerification(capability="spreadsheet.analyze", passed=True),),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert reply.startswith("Before anything else, here's what I noticed:")
    assert "amount: 1 unusually high value" in reply
    assert "Want me to tackle any of these?" in reply


def test_template_speaker_analysis_preface_still_reports_other_steps_outcome() -> None:
    """The trailing message still covers whatever the user's own request
    produced this same turn — the analysis preface is additive, not a
    replacement for the ordinary success/failure report.
    """
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("report_deduplicated.xlsx",), steps=(_analyze_step(),)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] [outliers] amount: 1 unusually high value",
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
            ),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True),
                StepVerification(capability="spreadsheet.deduplicate", passed=True),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "amount: 1 unusually high value" in reply
    assert "report_deduplicated.xlsx" in reply
    assert "0 duplicate row(s) remaining" in reply


def test_template_speaker_skips_the_preface_sentence_when_no_findings_are_open() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, steps=(_analyze_step(),)),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(),
            steps=(StepVerification(capability="spreadsheet.analyze", passed=True),),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "Before anything else" not in reply


def test_template_speaker_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step() -> (
    None
):
    """The specific rule from docs/design/proactive-analysis-design.md
    Section 5.3: spreadsheet.deduplicate is exact-match only
    (ExcelDeduplicateSkill.execute() calls drop_duplicates()) and never
    resolves a fuzzy near-duplicate finding, no matter how related the
    two capabilities sound. A near-duplicate finding must still appear as
    an open item even when a spreadsheet.deduplicate step ran and passed
    in the very same turn.
    """
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("report_deduplicated.xlsx",), steps=(_analyze_step(),)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] [near_duplicates] Alice Johnson and Alice Johnston "
                "might be the same (self-reported, not independently re-checked)",
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
            ),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True),
                # spreadsheet.deduplicate ran AND passed in this same turn --
                # the exact condition that must NOT be enough to fold the
                # near-duplicate finding away.
                StepVerification(capability="spreadsheet.deduplicate", passed=True),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "Alice Johnson and Alice Johnston" in reply
    assert "Before anything else, here's what I noticed" in reply
    assert "Want me to tackle any of these?" in reply


def test_template_speaker_does_fold_a_finding_once_a_real_matching_capability_passes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The mirror-image proof: the fold mechanism itself does work
    correctly once a real capability is registered as resolving a given
    finding category — it's specifically "no capability resolves
    near-duplicates today" that keeps that one open, not a blanket
    refusal to ever fold anything.
    """
    monkeypatch.setattr(
        voice, "_ANALYSIS_FINDING_FIX_CAPABILITY", {"outliers": "spreadsheet.fix_outliers"}
    )
    request = _request(
        execution_result=ExecutionResult(success=True, steps=(_analyze_step(),)),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("[spreadsheet.analyze] [outliers] amount: 1 unusually high value",),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True),
                StepVerification(capability="spreadsheet.fix_outliers", passed=True),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "amount: 1 unusually high value" not in reply
    assert "Before anything else" not in reply


# -- PersonalityVoice ---------------------------------------------------------------


def test_personality_voice_uses_friend_mode_prompt() -> None:
    provider = _FakeLLMProvider(reply="Nice, all cleaned up!")
    speaker = PersonalityVoice(provider)

    speaker(_request(mode="friend"))

    assert provider.last_system == FRIEND_MODE_PROMPT


def test_personality_voice_uses_focus_mode_prompt() -> None:
    provider = _FakeLLMProvider(reply="Done. No duplicates remain.")
    speaker = PersonalityVoice(provider)

    speaker(_request(mode="focus"))

    assert provider.last_system == FOCUS_MODE_PROMPT


def test_personality_voice_returns_the_models_reply_verbatim_when_successful() -> None:
    provider = _FakeLLMProvider(reply="  All clean, nothing left to do here.  ")
    speaker = PersonalityVoice(provider)

    reply = speaker(_request())

    assert reply == "All clean, nothing left to do here."


def test_personality_voice_prompt_contains_only_understanding_execution_and_verification_facts() -> (
    None
):
    """The prompt must be built solely from understanding/execution_result/
    verification_result — the same fields TemplateSpeaker already reads —
    never from any new data source.
    """
    provider = _FakeLLMProvider(reply="ok")
    speaker = PersonalityVoice(provider)
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(success=True, outputs=("uploads/report_deduped.xlsx",)),
        verification_result=VerificationResult(
            passed=True, confidence=1.0, findings=("output has 3 row(s), 0 duplicate row(s) remaining",)
        ),
    )

    speaker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "remove_duplicate_rows" in prompt
    assert "Execution succeeded: True" in prompt
    assert "report_deduped.xlsx" in prompt
    assert "Verification passed: True" in prompt
    assert "0 duplicate row(s) remaining" in prompt


def test_personality_voice_falls_back_to_template_speaker_on_provider_failure() -> None:
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    speaker = PersonalityVoice(provider)
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(passed=True, confidence=1.0, findings=("ok",)),
    )

    reply = speaker(request)

    assert reply == TemplateSpeaker()(request)


def test_personality_voice_falls_back_to_template_speaker_on_empty_reply() -> None:
    provider = _FakeLLMProvider(reply="   ")
    speaker = PersonalityVoice(provider)
    request = _request(
        execution_result=ExecutionResult(success=False, errors=("could not read file",)),
    )

    reply = speaker(request)

    assert reply == TemplateSpeaker()(request)


def test_personality_voice_never_logs_prompt_or_reply_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    provider = _FakeLLMProvider(reply="a sensitive-looking reply about the file")
    speaker = PersonalityVoice(provider)
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("secret_report.xlsx",)),
    )

    speaker(request)

    log_text = caplog.text
    assert "a sensitive-looking reply about the file" not in log_text
    assert "secret_report.xlsx" not in log_text


# -- Voice stage --------------------------------------------------------------------


def test_voice_stage_sets_reply() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    stage = Voice(speaker=NullSpeaker())

    stage(state)

    assert state.reply is not None
    assert "no speaker has been configured" in state.reply.lower()


def test_voice_stage_sources_mode_from_context() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.set_mode("focus")
    seen: list[SpeakingRequest] = []

    class _RecordingSpeaker:
        def __call__(self, request: SpeakingRequest) -> str:
            seen.append(request)
            return "ok"

    stage = Voice(speaker=_RecordingSpeaker())
    stage(state)

    assert seen[0].mode == "focus"


def test_voice_stage_defaults_to_friend_mode() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    seen: list[SpeakingRequest] = []

    class _RecordingSpeaker:
        def __call__(self, request: SpeakingRequest) -> str:
            seen.append(request)
            return "ok"

    stage = Voice(speaker=_RecordingSpeaker())
    stage(state)

    assert seen[0].mode == "friend"
