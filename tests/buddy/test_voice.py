"""Unit tests for buddy.voice."""

from __future__ import annotations

import dataclasses
import logging
import re

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


def _dedupe_step(**overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "capability": "spreadsheet.deduplicate",
        "skill": "excel.deduplicate",
        "status": "success",
        "outputs": ("report_deduplicated.xlsx",),
        "input_reference": "uploads/report.xlsx",
        "permission_tier": "write",
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
            success=True,
            outputs=("report_deduplicated.xlsx",),
            steps=(_analyze_step(), _dedupe_step()),
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
            success=True,
            outputs=("report_deduplicated.xlsx",),
            steps=(_analyze_step(), _dedupe_step()),
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


def test_personality_voice_returns_the_models_reply_stripped_when_it_claims_nothing() -> None:
    """Nothing was delivered, but the reply makes no completion claim, so
    it's used as-is (only surrounding whitespace removed)."""
    provider = _FakeLLMProvider(reply="  Here's where things stand.  ")
    speaker = PersonalityVoice(provider)

    reply = speaker(_request())

    assert reply == "Here's where things stand."


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
        execution_result=ExecutionResult(
            success=True,
            outputs=("workspace/outputs/report_deduplicated_task-1.xlsx",),
            steps=(
                _dedupe_step(outputs=("workspace/outputs/report_deduplicated_task-1.xlsx",)),
            ),
        ),
        verification_result=VerificationResult(
            passed=True, confidence=1.0, findings=("output has 3 row(s), 0 duplicate row(s) remaining",)
        ),
    )

    speaker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "remove duplicate rows" in prompt
    assert "deduplicate (passed)" in prompt
    assert "NOT performed" not in prompt
    assert "No changes were made" not in prompt
    assert "Execution succeeded: True" in prompt
    # The file is named as a cleaned copy of the original, never by its
    # generated name.
    assert "a cleaned copy of report.xlsx" in prompt
    assert "report_deduplicated_task-1" not in prompt
    assert "Verification passed: True" in prompt
    assert "0 duplicate row(s) remaining" in prompt


def _clarification_turn_request(mode: str) -> SpeakingRequest:
    """The exact live-run scenario: the user asked to remove duplicates,
    Think set clarification_required=True so no spreadsheet.deduplicate
    step was planned, and only proactive spreadsheet.analyze ran (and
    passed). No output file was produced.
    """
    return _request(
        mode=mode,
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(success=True, steps=(_analyze_step(),)),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("[spreadsheet.analyze] [outliers] amount: 1 unusually high value",),
            steps=(StepVerification(capability="spreadsheet.analyze", passed=True),),
        ),
    )


def test_rule_based_planner_queues_no_dedupe_step_when_clarification_is_required() -> None:
    """Upstream half of the scenario: confirms clarification_required=True
    really does leave the dedupe capability unplanned, so the Voice
    request below reflects what the pipeline actually produces.
    """
    from buddy.planner import PlanningRequest, RuleBasedPlanner
    from buddy.think import Reasoning

    steps = RuleBasedPlanner()(
        PlanningRequest(
            understanding=Understanding(intent="remove_duplicate_rows"),
            reasoning=Reasoning(clarification_required=True),
            uploaded_files=("report.xlsx",),
            active_file="report.xlsx",
            user_message="remove the duplicates",
        )
    )

    assert steps == ()


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_does_not_present_an_unrun_intent_as_done(mode: str) -> None:
    """Regression: the classified intent used to reach the model
    unconditionally ("classified as: remove_duplicate_rows") alongside
    "Execution succeeded: True", and the model replied "duplicates
    removed" for a turn where no dedupe step ran at all.
    """
    provider = _FakeLLMProvider(reply="ok")
    speaker = PersonalityVoice(provider)

    speaker(_clarification_turn_request(mode))

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "(deduplicate) was NOT performed this turn" in prompt
    assert "Do not say or imply that it was done." in prompt
    assert "Steps that actually ran this turn: analyze (passed)" in prompt
    assert "deduplicate (passed)" not in prompt
    assert "classified as" not in prompt


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_reply_for_unrun_dedupe_does_not_claim_duplicates_removed(
    mode: str,
) -> None:
    """The reply itself, on the deterministic path (model unavailable):
    nothing in it may claim duplicates were removed or a file was saved.
    """
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    speaker = PersonalityVoice(provider)

    reply = speaker(_clarification_turn_request(mode)).lower()

    assert "duplicate" not in reply
    assert "removed" not in reply
    assert "saved as" not in reply


def test_personality_voice_flags_a_dedupe_step_that_ran_but_failed_verification() -> None:
    provider = _FakeLLMProvider(reply="ok")
    speaker = PersonalityVoice(provider)
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(
            success=True,
            steps=(
                StepResult(
                    capability="spreadsheet.deduplicate",
                    skill="excel.deduplicate",
                    status="success",
                ),
            ),
        ),
        verification_result=VerificationResult(
            passed=False,
            steps=(StepVerification(capability="spreadsheet.deduplicate", passed=False),),
        ),
    )

    speaker(request)

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "deduplicate (did not pass)" in prompt
    assert "(deduplicate) was NOT performed this turn" in prompt


# -- Internal capability labels never reach the user ------------------------------

_CAPABILITY_TAG = re.compile(r"\[[\w.]+\]")


def _tagged_findings_request(mode: str = "friend") -> SpeakingRequest:
    """Mirrors the live focus-mode run whose reply leaked
    "[spreadsheet.analyze] nothing notable; [spreadsheet.deduplicate] ...".
    """
    return _request(
        mode=mode,
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(
            success=True,
            outputs=("workspace/outputs/report_deduplicated.xlsx",),
            steps=(
                _analyze_step(),
                StepResult(
                    capability="spreadsheet.deduplicate",
                    skill="excel.deduplicate",
                    status="success",
                ),
            ),
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] nothing notable found",
                "[spreadsheet.analyze] [outliers] amount: 1 unusually high value",
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
            ),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True),
                StepVerification(capability="spreadsheet.deduplicate", passed=True),
            ),
        ),
    )


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_prompt_carries_no_capability_tags_or_ids(mode: str) -> None:
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_tagged_findings_request(mode))

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert _CAPABILITY_TAG.search(prompt) is None
    assert "spreadsheet." not in prompt
    # The facts themselves still arrive, just without their labels -- and
    # analysis findings stay distinguishable as unprompted observations.
    assert "Noticed on your own, unprompted: nothing notable found; amount: 1 unusually high value" in prompt
    assert "Verification findings: output has 3 row(s), 0 duplicate row(s) remaining" in prompt


@pytest.mark.parametrize("prompt", [FRIEND_MODE_PROMPT, FOCUS_MODE_PROMPT])
def test_mode_prompts_forbid_repeating_internal_labels(prompt: str) -> None:
    assert "Never repeat internal labels" in prompt


@pytest.mark.parametrize(
    "verification_result",
    [
        _tagged_findings_request().verification_result,
        VerificationResult(
            passed=False,
            findings=("[spreadsheet.deduplicate] output still has 1 duplicate row(s)",),
            recommendations=("re-run deduplication",),
        ),
    ],
    ids=["passed", "failed"],
)
def test_rendered_reply_contains_no_capability_tags(
    verification_result: VerificationResult,
) -> None:
    """The model is instructed not to repeat labels, but that's advisory --
    the deterministic reply (TemplateSpeaker, and PersonalityVoice's
    fallback to it) must strip them itself.
    """
    request = dataclasses.replace(
        _tagged_findings_request(), verification_result=verification_result
    )

    template_reply = TemplateSpeaker()(request)
    fallback_reply = PersonalityVoice(_FakeLLMProvider(raises=RuntimeError("down")))(request)

    for reply in (template_reply, fallback_reply):
        assert _CAPABILITY_TAG.search(reply) is None, reply
        assert "spreadsheet." not in reply


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


# -- A chain that stopped: say which step failed, in plain words ------------------


def _stopped_chain_request(mode: str = "focus") -> SpeakingRequest:
    return _request(
        mode=mode,
        execution_result=ExecutionResult(
            success=False,
            errors=(
                "spreadsheet.deduplicate failed verification: expected 3 non-empty row(s)",
                "stopped: spreadsheet.deduplicate failed verification; 1 later step(s) were "
                "skipped and no file was handed back",
            ),
            steps=(
                StepResult(
                    capability="spreadsheet.deduplicate",
                    skill="excel.deduplicate",
                    status="success",
                    outputs=("out/a.xlsx",),
                ),
                StepResult(
                    capability="spreadsheet.remove_empty_rows",
                    skill="",
                    status="skipped",
                    summary="skipped because spreadsheet.deduplicate failed verification",
                ),
            ),
            stopped_at="spreadsheet.deduplicate",
        ),
        verification_result=VerificationResult(
            passed=False,
            findings=(
                "[spreadsheet.deduplicate] output has 2 row(s), 0 duplicate row(s) remaining",
                "[spreadsheet.deduplicate] expected 3 non-empty row(s) from its 4-row input, "
                "found 2",
                "[spreadsheet.remove_empty_rows] skipped because an earlier step failed",
            ),
            steps=(
                StepVerification(capability="spreadsheet.deduplicate", passed=False),
                StepVerification(capability="spreadsheet.remove_empty_rows", passed=False),
            ),
        ),
    )


def test_personality_voice_prompt_says_where_the_chain_stopped_and_what_never_ran() -> None:
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_stopped_chain_request())

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "Steps that actually ran this turn: deduplicate (did not pass)" in prompt
    assert "Steps that never ran, because an earlier step failed: remove empty rows" in prompt
    assert (
        "Work stopped at this failed step, so no file was handed back and the original is "
        "unchanged: deduplicate"
    ) in prompt


def test_template_speaker_names_the_failed_step_and_what_never_ran() -> None:
    reply = TemplateSpeaker()(_stopped_chain_request())

    assert reply.startswith("I ran into a problem partway through.")
    assert "Done" not in reply
    assert reply.endswith(
        "I stopped at the deduplicate step, so remove empty rows never ran and no file was "
        "handed back; your original file is unchanged."
    )
    assert _CAPABILITY_TAG.search(reply) is None
    assert "spreadsheet." not in reply


def test_template_speaker_adds_no_stop_sentence_when_the_chain_completed() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out/final.xlsx",)),
        verification_result=VerificationResult(passed=True, findings=("ok",)),
    )

    assert "stopped" not in TemplateSpeaker()(request)


# -- 1. No false completion claims -------------------------------------------------


def _b4_request(mode: str = "focus") -> SpeakingRequest:
    """Live run B4 exactly: a multi-step request, Think asked for
    clarification, so only read-only analysis ran (on the original) and it
    passed. No file was changed or handed back."""
    return _request(
        mode=mode,
        understanding=Understanding(intent="multi_step_request"),
        execution_result=ExecutionResult(
            success=True,
            steps=(_analyze_step(input_reference="uploads/report.xlsx", permission_tier="read"),),
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] [missing_values] name: 1 missing (16.7%)",
                "[spreadsheet.analyze] [missing_values] amount: 1 missing (16.7%)",
            ),
            steps=(StepVerification(capability="spreadsheet.analyze", passed=True),),
        ),
    )


_B4_LIVE_REPLY = (
    "All steps completed successfully. name missing 1 (16.7%); amount missing 1 (16.7%). "
    "Which to prioritize?"
)


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_prompt_states_no_changes_when_only_analysis_ran(mode: str) -> None:
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_b4_request(mode))

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert prompt.startswith("No changes were made to any file this turn.")
    assert "File handed back" not in prompt


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_discards_the_live_b4_false_completion_claim(mode: str) -> None:
    """The model said "All steps completed successfully." for a turn that
    changed nothing; that reply must never reach the user."""
    provider = _FakeLLMProvider(reply=_B4_LIVE_REPLY)

    reply = PersonalityVoice(provider)(_b4_request(mode))

    assert reply == TemplateSpeaker()(_b4_request(mode))
    assert "completed" not in reply.lower()
    assert "No changes were made to any file this turn." in reply


def test_personality_voice_keeps_an_honest_reply_when_nothing_was_changed() -> None:
    honest = "Name and amount each have one missing value. Which should I look at first?"
    provider = _FakeLLMProvider(reply=honest)

    assert PersonalityVoice(provider)(_b4_request()) == honest


@pytest.mark.parametrize(
    "claim",
    [
        "Done.",
        "Duplicates removed.",
        "All cleaned up!",
        "Deduped and saved.",
        "Your file is ready.",
        "Finished — 3 rows left.",
    ],
)
def test_personality_voice_discards_any_completion_claim_when_the_chain_stopped(
    claim: str,
) -> None:
    request = _stopped_chain_request()
    provider = _FakeLLMProvider(reply=claim)

    reply = PersonalityVoice(provider)(request)

    assert reply == TemplateSpeaker()(request)


def test_template_speaker_never_says_done_when_only_analysis_ran() -> None:
    reply = TemplateSpeaker()(_b4_request())

    assert "Done" not in reply
    assert reply.endswith("No changes were made to any file this turn.")
    assert "name: 1 missing" in reply  # what was noticed is still reported


# -- 2. A produced file is always mentioned ----------------------------------------


def _delivered_request(mode: str) -> SpeakingRequest:
    first_out = "workspace/outputs/report_no_empty_rows_s-task-1.xlsx"
    final = "workspace/outputs/report_no_empty_rows_s-task-1_deduplicated_s-task-2.xlsx"
    return _request(
        mode=mode,
        understanding=Understanding(intent="multi_step_request"),
        execution_result=ExecutionResult(
            success=True,
            outputs=(final,),
            steps=(
                _analyze_step(input_reference="uploads/report.xlsx", permission_tier="read"),
                StepResult(
                    capability="spreadsheet.remove_empty_rows",
                    skill="excel.remove_empty_rows",
                    status="success",
                    outputs=(first_out,),
                    input_reference="uploads/report.xlsx",
                    permission_tier="write",
                ),
                _dedupe_step(outputs=(final,), input_reference=first_out),
                _analyze_step(input_reference=final, permission_tier="read", recheck=True),
            ),
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] [missing_values] name: 1 missing (16.7%)",
                "[spreadsheet.remove_empty_rows] output has 5 row(s), 0 fully-empty row(s) remaining",
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
                "[spreadsheet.analyze] nothing notable found",
                "[final_output] final file: output has 3 row(s), 0 duplicate row(s) remaining",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=True,
                    findings=("[missing_values] name: 1 missing (16.7%)",),
                ),
                StepVerification(capability="spreadsheet.remove_empty_rows", passed=True),
                StepVerification(capability="spreadsheet.deduplicate", passed=True),
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=True,
                    findings=("nothing notable found",),
                    input_reference=final,
                    recheck=True,
                ),
                StepVerification(capability="final_output", passed=True),
            ),
        ),
    )


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_prompt_states_the_file_handed_back(mode: str) -> None:
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_delivered_request(mode))

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert (
        "File handed back to the user (mention it in your reply): a cleaned copy of "
        "report.xlsx, with every change above applied"
    ) in prompt
    assert "No changes were made" not in prompt
    assert "deduplicated_s-task-2" not in prompt  # never the generated name


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_reply_always_mentions_the_file_handed_back(mode: str) -> None:
    provider = _FakeLLMProvider(reply="Done — 3 rows left, no empties or duplicates.")

    reply = PersonalityVoice(provider)(_delivered_request(mode))

    assert reply == (
        "Done — 3 rows left, no empties or duplicates. "
        "Your cleaned copy of report.xlsx is ready."
    )


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_leaves_a_reply_that_already_names_the_file(mode: str) -> None:
    named = "Your report is cleaned up: 3 rows, no empties or duplicates."
    provider = _FakeLLMProvider(reply=named)

    assert PersonalityVoice(provider)(_delivered_request(mode)) == named


# -- 3. Findings describe the file the user receives --------------------------------


def test_personality_voice_reports_analysis_of_the_final_file_not_the_original() -> None:
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_delivered_request("focus"))

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert (
        "Noticed on your own, unprompted (in the cleaned file handed back, after the changes): "
        "nothing notable found"
    ) in prompt
    assert "missing" not in prompt  # the original's stale finding


def test_template_speaker_reports_analysis_of_the_final_file_not_the_original() -> None:
    reply = TemplateSpeaker()(_delivered_request("focus"))

    assert "missing" not in reply
    assert "Before anything else" not in reply
    assert "nothing notable found" in reply


def test_voice_reports_the_originals_analysis_when_nothing_was_re_analyzed() -> None:
    """No chain completed (B4): the user's file is unchanged, so its
    analysis is still current and is reported."""
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_b4_request())

    assert provider.last_messages is not None
    assert "name: 1 missing (16.7%)" in provider.last_messages[0].content


# -- B1: a chain that stopped after an earlier step passed never says "Done" -------


def _passed_then_stopped_request(last: StepResult, mode: str = "friend") -> SpeakingRequest:
    """Dedupe passed (and Inspector confirmed it); the last step then failed
    without running — the exact shape of review item B1. Shaped the way
    Hands and Inspector now record it: the failing step has a StepResult
    with a failure_reason, and Inspector fails it with that reason."""
    first = _dedupe_step(
        outputs=("out/dedupe.xlsx",),
        input_reference="uploads/report.xlsx",
        permission_tier="write",
    )
    return _request(
        mode=mode,
        understanding=Understanding(intent="multi_step_request"),
        execution_result=ExecutionResult(
            success=False,
            errors=(f"No registered skill declares support for '{last.capability}'.",),
            steps=(first, last),
            stopped_at=last.capability,
        ),
        verification_result=VerificationResult(
            passed=False,
            confidence=1.0,
            findings=(
                "[spreadsheet.deduplicate] output has 2 row(s), 0 duplicate row(s) remaining",
                f"[{last.capability}] {last.failure_reason}",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    findings=("output has 2 row(s), 0 duplicate row(s) remaining",),
                    input_reference="uploads/report.xlsx",
                ),
                StepVerification(
                    capability=last.capability,
                    passed=False,
                    findings=(last.failure_reason,),
                    input_reference="out/dedupe.xlsx",
                ),
            ),
        ),
    )


def _undispatchable_report() -> StepResult:
    return StepResult(
        capability="spreadsheet.report",
        skill="",
        status="failure",
        input_reference="out/dedupe.xlsx",
        failure_reason="couldn't be run because no available skill can do it",
    )


def _declined_empty_rows() -> StepResult:
    return StepResult(
        capability="spreadsheet.remove_empty_rows",
        skill="excel.remove_empty_rows",
        status="declined",
        input_reference="out/dedupe.xlsx",
        permission_tier="write",
        failure_reason="wasn't confirmed, so it didn't run",
    )


def _assert_no_completion_claim(reply: str, stopped_step: str) -> None:
    assert voice._COMPLETION_CLAIM.search(reply) is None, reply
    assert "Done" not in reply
    assert f"I stopped at the {stopped_step} step" in reply
    assert "no file was handed back; your original file is unchanged." in reply
    assert "spreadsheet." not in reply


@pytest.mark.parametrize(
    ("last", "stopped_step", "reason"),
    [
        (_undispatchable_report(), "report", "couldn't be run"),
        (_declined_empty_rows(), "remove empty rows", "wasn't confirmed"),
    ],
    ids=["dispatch-failure", "declined"],
)
def test_template_speaker_never_says_done_when_the_last_step_stopped_the_chain(
    last: StepResult, stopped_step: str, reason: str
) -> None:
    reply = TemplateSpeaker()(_passed_then_stopped_request(last))

    assert reply.startswith("I ran into a problem partway through.")
    assert reason in reply
    _assert_no_completion_claim(reply, stopped_step)
    # The earlier step's findings describe a file that was never handed back.
    assert "0 duplicate row(s) remaining" not in reply


@pytest.mark.parametrize(
    ("last", "stopped_step"),
    [(_undispatchable_report(), "report"), (_declined_empty_rows(), "remove empty rows")],
    ids=["dispatch-failure", "declined"],
)
@pytest.mark.parametrize(
    "provider",
    [
        _FakeLLMProvider(reply="All done — duplicates removed."),
        _FakeLLMProvider(reply="   "),
        _FakeLLMProvider(raises=RuntimeError("boom")),
    ],
    ids=["completion-claim", "empty-reply", "model-failure"],
)
def test_personality_voice_fallback_never_says_done_when_the_last_step_stopped_the_chain(
    last: StepResult, stopped_step: str, provider: _FakeLLMProvider
) -> None:
    request = _passed_then_stopped_request(last)

    reply = PersonalityVoice(provider)(request)

    assert reply == TemplateSpeaker()(request)
    _assert_no_completion_claim(reply, stopped_step)


def test_personality_voice_prompt_says_nothing_was_delivered_when_the_last_step_stopped() -> None:
    provider = _FakeLLMProvider(reply="ok")

    PersonalityVoice(provider)(_passed_then_stopped_request(_undispatchable_report()))

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "File handed back" not in prompt
    assert "report (did not pass)" in prompt
    assert "spreadsheet." not in prompt  # the error's capability id is in plain words


# -- M1: a failed re-check of the final file is reported, not a failure ------------


def _recheck_failed_request(mode: str = "friend") -> SpeakingRequest:
    final = "workspace/outputs/report_no_empty_rows_deduplicated.xlsx"
    return _request(
        mode=mode,
        understanding=Understanding(intent="multi_step_request"),
        execution_result=ExecutionResult(
            success=True,
            outputs=(final,),
            warnings=("could not re-check the final file with spreadsheet.analyze",),
            steps=(
                _analyze_step(input_reference="uploads/report.xlsx", permission_tier="read"),
                _dedupe_step(
                    outputs=(final,),
                    input_reference="uploads/report.xlsx",
                    permission_tier="write",
                ),
                StepResult(
                    capability="spreadsheet.analyze",
                    skill="excel.analyze",
                    status="failure",
                    input_reference=final,
                    permission_tier="read",
                    recheck=True,
                ),
            ),
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.analyze] [missing_values] name: 1 missing (16.7%)",
                "[spreadsheet.deduplicate] output has 3 row(s), 0 duplicate row(s) remaining",
                "[spreadsheet.analyze] the final file couldn't be re-checked after the changes",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=True,
                    findings=("[missing_values] name: 1 missing (16.7%)",),
                    input_reference="uploads/report.xlsx",
                ),
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    input_reference="uploads/report.xlsx",
                ),
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=False,
                    findings=("the final file couldn't be re-checked after the changes",),
                    input_reference=final,
                    recheck=True,
                ),
            ),
        ),
    )


def test_template_speaker_reports_a_failed_re_check_and_still_names_the_file() -> None:
    reply = TemplateSpeaker()(_recheck_failed_request())

    assert reply.startswith("Done")
    assert "Saved as `report_no_empty_rows_deduplicated.xlsx`." in reply
    assert voice.RECHECK_FAILED_SENTENCE in reply
    assert "missing" not in reply  # the original's findings no longer describe the file
    assert "wasn't able to confirm" not in reply
    assert "spreadsheet." not in reply


def test_personality_voice_prompt_reports_a_failed_re_check_and_the_file() -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_recheck_failed_request())

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "could not be re-checked for other problems" in prompt
    assert "a cleaned copy of report.xlsx" in prompt
    assert "analyze re-check of the final file (did not pass)" in prompt
    assert "spreadsheet." not in prompt
    assert "missing" not in prompt
    assert reply == "ok Your cleaned copy of report.xlsx is ready."


# -- No raw capability id anywhere in a reply -----------------------------------------


def test_plain_words_replaces_capability_ids_anywhere_but_leaves_file_names() -> None:
    assert voice._strip_finding_tags(
        "[spreadsheet.analyze] spreadsheet.analyze reported failure on 'spreadsheet.report'"
    ) == "analyze reported failure on 'report'"
    assert voice._plain_words("saved as spreadsheet.xlsx") == "saved as spreadsheet.xlsx"


def test_template_speaker_scrubs_a_capability_id_inside_a_finding() -> None:
    request = _request(
        execution_result=ExecutionResult(
            success=True,
            outputs=("out/final.xlsx",),
            steps=(_dedupe_step(outputs=("out/final.xlsx",)),),
        ),
        verification_result=VerificationResult(
            passed=False,
            findings=("[spreadsheet.deduplicate] spreadsheet.deduplicate left 1 row behind",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "spreadsheet." not in reply
    assert "deduplicate left 1 row behind" in reply.lower()


# -- L2: no verification, only read-only steps -------------------------------------


def test_template_speaker_never_claims_work_without_verification_when_only_reads_ran() -> None:
    request = _request(
        execution_result=ExecutionResult(
            success=True,
            steps=(_analyze_step(input_reference="uploads/report.xlsx", permission_tier="read"),),
        ),
        verification_result=None,
    )

    reply = TemplateSpeaker()(request)

    assert "I did the work" not in reply
    assert voice._COMPLETION_CLAIM.search(reply) is None
    assert reply.endswith("No changes were made to any file this turn.")


# -- One rule for "passed" ----------------------------------------------------------


def test_a_step_inspector_never_checked_does_not_count_as_passed() -> None:
    """Strict semantics: passed means the Skill succeeded AND Inspector has a
    passing verdict for that step. No verdict for it is not a pass."""
    step = _dedupe_step(outputs=("out/a.xlsx",), permission_tier="write")
    unchecked = VerificationResult(
        passed=True, steps=(StepVerification(capability="spreadsheet.analyze", passed=True),)
    )
    confirmed = VerificationResult(
        passed=True, steps=(StepVerification(capability="spreadsheet.deduplicate", passed=True),)
    )

    assert voice._step_passed(step, unchecked) is False
    assert voice._step_passed(step, confirmed) is True
    assert voice._step_passed(step, None) is False
    execution = ExecutionResult(success=True, outputs=("out/a.xlsx",), steps=(step,))
    # The prompt label and the no-changes rule now agree.
    assert voice._action_passed_verification(execution, unchecked) is False
    assert voice._action_passed_verification(execution, confirmed) is True


def test_the_original_analysis_and_its_re_check_are_judged_separately() -> None:
    original = _analyze_step(input_reference="uploads/report.xlsx", permission_tier="read")
    recheck = _analyze_step(input_reference="out/final.xlsx", permission_tier="read", recheck=True)
    verification = VerificationResult(
        passed=True,
        steps=(
            StepVerification(
                capability="spreadsheet.analyze", passed=True, input_reference="uploads/report.xlsx"
            ),
            StepVerification(
                capability="spreadsheet.analyze",
                passed=False,
                input_reference="out/final.xlsx",
                recheck=True,
            ),
        ),
    )

    assert voice._step_passed(original, verification) is True
    assert voice._step_passed(recheck, verification) is False
