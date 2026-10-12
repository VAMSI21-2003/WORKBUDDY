"""Unit tests for buddy.voice."""

from __future__ import annotations

import dataclasses
import logging
import re

import pytest

import buddy.voice as voice
from buddy.llm import LLMMessage
from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    ExecutionResult,
    PlanningStep,
    StepMeasurement,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.outcome import VALUES_ONLY_SENTENCE, OutcomeReport
from buddy.state import BuddyState
from buddy.think import Reasoning
from buddy.voice import (
    NullSpeaker,
    PersonalityVoice,
    SpeakingRequest,
    TemplateSpeaker,
    Voice,
)
from buddy.voice_catalog import select_phrases, style_for


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



def _factual(request: SpeakingRequest) -> str:
    """``TemplateSpeaker``'s reply in ``request``'s own mode: what ``PersonalityVoice`` gives
    for a turn that delivered nothing (the facts are the same in every style; only a
    greeting and a clarification question are worded for the mode)."""
    return TemplateSpeaker().compose(request, style=style_for(request.mode))


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
    """A single-intent turn whose capability passed: "Done —", the verifier's
    counts, and the generated file name."""
    output = "workspace/outputs/workspace-1/report_deduplicated.xlsx"
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(
            success=True, outputs=(output,), steps=(_dedupe_step(outputs=(output,)),)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=(
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    measurements=(_dedupe_measurement(),),
                ),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert reply == (
        "Done — removed 2 exact duplicate rows. The copy has 3 rows, with 0 exact duplicate rows "
        "remaining. Saved as `report_deduplicated.xlsx`. " + VALUES_ONLY_SENTENCE
    )


def test_template_speaker_success_message_covers_every_steps_output_and_finding() -> None:
    """Regression guard for the single-output/single-finding assumption:
    a turn covering two delegated steps must mention both output files
    and both steps' outcomes, not just the first of each.
    """
    first = "workspace/outputs/workspace-1/report_deduplicated.xlsx"
    second = "workspace/outputs/workspace-1/report_no_empty_rows.xlsx"
    request = _request(
        execution_result=ExecutionResult(
            success=True,
            outputs=(first, second),
            steps=(
                _dedupe_step(outputs=(first,)),
                _empty_rows_step(outputs=(second,), input_reference=first),
            ),
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    measurements=(_dedupe_measurement(),),
                ),
                StepVerification(
                    capability="spreadsheet.remove_empty_rows",
                    passed=True,
                    measurements=(_empty_rows_measurement(input_rows=3, removed=0, output_rows=3),),
                ),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "report_deduplicated.xlsx" in reply
    assert "report_no_empty_rows.xlsx" in reply
    assert "Removed 2 exact duplicate rows." in reply
    assert "There were no fully-empty rows left to remove." in reply


def test_template_speaker_states_no_count_for_a_result_without_measurements() -> None:
    """A result with no per-step breakdown or measurements (a verifier used
    directly, fakes): the reply names the file and states no count, since
    nothing measured one. The verifier's own finding strings aren't used."""
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("workspace/outputs/workspace-1/report_deduplicated.xlsx",)
        ),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("output has 3 rows, 0 exact duplicate rows remaining",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert reply == f"Saved as `report_deduplicated.xlsx`. {VALUES_ONLY_SENTENCE}"


def test_template_speaker_failure_message_covers_every_steps_finding() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("a.xlsx", "b.xlsx")),
        verification_result=VerificationResult(
            passed=False,
            confidence=1.0,
            findings=(
                "[spreadsheet.deduplicate] output has 3 rows, 1 exact duplicate row remaining",
                "[spreadsheet.remove_empty_rows] output has 4 rows, "
                "1 fully-empty row remaining",
            ),
            recommendations=(
                "re-run deduplication; duplicates remain",
                "re-run empty-row removal; empty rows remain",
            ),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "1 exact duplicate row" in reply
    assert "1 fully-empty row" in reply
    assert "re-run deduplication" in reply
    assert "re-run empty-row removal" in reply


def test_template_speaker_failure_message_includes_findings_and_recommendations() -> None:
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(
            passed=False,
            confidence=1.0,
            findings=("output has 3 rows, 1 exact duplicate row remaining",),
            recommendations=("re-run deduplication; duplicates remain",),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert "wasn't able to confirm" in reply.lower()
    assert "1 exact duplicate row" in reply
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


def _empty_rows_step(**overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "capability": "spreadsheet.remove_empty_rows",
        "skill": "excel.remove_empty_rows",
        "status": "success",
        "outputs": ("report_no_empty_rows.xlsx",),
        "input_reference": "uploads/report.xlsx",
        "permission_tier": "write",
    }
    fields.update(overrides)
    return StepResult(**fields)  # type: ignore[arg-type]


def _dedupe_measurement(
    input_rows: int | None = 5, removed: int | None = 2, output_rows: int = 3
) -> StepMeasurement:
    return StepMeasurement(
        target=DUPLICATE_ROWS_TARGET,
        output_rows=output_rows,
        remaining=0,
        input_rows=input_rows,
        removed=removed,
    )


def _empty_rows_measurement(
    input_rows: int | None = 6, removed: int | None = 1, output_rows: int = 5
) -> StepMeasurement:
    return StepMeasurement(
        target=EMPTY_ROWS_TARGET,
        output_rows=output_rows,
        remaining=0,
        input_rows=input_rows,
        removed=removed,
    )


def test_template_speaker_gives_the_reason_then_the_analysis_findings() -> None:
    request = _request(
        understanding=Understanding(intent="unclear"),
        execution_result=ExecutionResult(success=True, steps=(_analyze_step(),)),
        verification_result=VerificationResult(
            passed=True,
            confidence=1.0,
            findings=("[spreadsheet.analyze] [outliers] amount: 1 unusually high value",),
            steps=(StepVerification(capability="spreadsheet.analyze", passed=True),),
        ),
    )

    reply = TemplateSpeaker()(request)

    assert reply.startswith("I didn't read that as something I can do.")
    assert "Here's what I noticed when I looked it over: amount: 1 unusually high value." in reply
    assert "Want me to tackle any of these?" in reply
    assert reply.endswith("No changes were made to any file this turn.")


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
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
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
    # On a delivered turn the outcome comes first, then the findings (D3).
    assert reply.startswith("The duplicate-row removal ran and passed its check.")
    assert reply.index("Saved as") < reply.index("I also noticed: amount: 1 unusually high value")


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
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
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
    # A delivered turn: the findings follow the outcome, led "I also noticed" (D3).
    assert "I also noticed: " in reply
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


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_makes_no_model_call_on_a_verified_delivery(mode: str) -> None:
    """Decision D1: the reply is rendered by code in both modes; the model
    is never called."""
    provider = _FakeLLMProvider(raises=AssertionError("the model must not be called"))

    reply = PersonalityVoice(provider)(_delivered_request(mode))

    assert provider.last_messages is None
    assert "Removed 1 fully-empty row. Removed 2 exact duplicate rows." in reply


def test_personality_voice_never_returns_the_models_reply_for_a_verified_delivery() -> None:
    """Inverted: the model's text used to be the reply. Now it can't appear."""
    provider = _FakeLLMProvider(reply="  All clean, report is down to 3 rows.  ")

    reply = PersonalityVoice(provider)(_delivered_request("friend"))

    assert "All clean" not in reply
    assert provider.last_messages is None


def test_personality_voice_reply_contains_only_understanding_execution_and_verification_facts() -> (
    None
):
    """The reply is built solely from understanding/execution_result/
    verification_result — the same fields TemplateSpeaker already reads —
    never from any new data source. (It used to be the prompt; the facts
    are now asserted on the reply itself.)
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
            passed=True,
            confidence=1.0,
            findings=("output has 3 rows, 0 exact duplicate rows remaining",),
            measurements=(_dedupe_measurement(),),
        ),
    )

    reply = speaker(request)

    assert provider.last_messages is None
    assert "No changes were made" not in reply
    # The file is named as a cleaned copy of the original, never by its
    # generated name.
    assert "Your cleaned copy of report.xlsx is ready." in reply
    assert "report_deduplicated_task-1" not in reply
    assert "Done — removed 2 exact duplicate rows." in reply
    assert "0 exact duplicate rows remaining" in reply


def _clarification_turn_request(mode: str) -> SpeakingRequest:
    """The exact live-run scenario: the user asked to remove duplicates,
    Think set clarification_required=True so no spreadsheet.deduplicate
    step was planned, and only proactive spreadsheet.analyze ran (and
    passed). No output file was produced.
    """
    return _request(
        mode=mode,
        understanding=Understanding(intent="remove_duplicate_rows"),
        reasoning=Reasoning(clarification_required=True, clarification_reason="other"),
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

    reply = speaker(_clarification_turn_request(mode))

    # An action turn with no verified file: the model is never called and
    # the factual reply claims nothing. (The prompt builder these lines
    # used to check is gone: no turn calls the model.)
    assert provider.last_messages is None
    assert reply == _factual(_clarification_turn_request(mode))
    assert voice._COMPLETION_CLAIM.search(reply) is None


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

    reply = speaker(request)

    # An action turn with no verified file: the model is never called; the
    # factual reply says the step couldn't be confirmed. (The prompt
    # builder these lines used to check is gone.)
    assert provider.last_messages is None
    assert reply == _factual(request)
    assert "Done" not in reply


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
                "[spreadsheet.analyze] none of the issues I check for were found",
                "[spreadsheet.analyze] [outliers] amount: 1 unusually high value",
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
            ),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True),
                StepVerification(capability="spreadsheet.deduplicate", passed=True),
            ),
        ),
    )


@pytest.mark.parametrize(
    "verification_result",
    [
        _tagged_findings_request().verification_result,
        VerificationResult(
            passed=False,
            findings=("[spreadsheet.deduplicate] output still has 1 exact duplicate row",),
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


def test_personality_voice_is_unaffected_by_a_failing_provider() -> None:
    """There's no model call to fail: a verified delivery gets the rendered
    reply (the file as a cleaned copy of the original), not the template's
    generated file name."""
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    speaker = PersonalityVoice(provider)
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",)),
        verification_result=VerificationResult(passed=True, confidence=1.0, findings=("ok",)),
    )

    reply = speaker(request)

    assert provider.last_messages is None
    opener, closer = select_phrases(
        OutcomeReport(actions=(), delivered_file="out.xlsx"), "friend", seed=":0"
    )
    expected = " ".join(
        p
        for p in (
            opener.text,
            "Your cleaned copy of out.xlsx is ready.",
            VALUES_ONLY_SENTENCE,
            closer.text,
        )
        if p
    )
    assert reply == expected


def test_personality_voice_falls_back_to_template_speaker_on_empty_reply() -> None:
    provider = _FakeLLMProvider(reply="   ")
    speaker = PersonalityVoice(provider)
    request = _request(
        execution_result=ExecutionResult(success=False, errors=("could not read file",)),
    )

    reply = speaker(request)

    assert reply == _factual(request)


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
                "spreadsheet.deduplicate failed verification: expected 3 non-empty rows",
                "stopped: spreadsheet.deduplicate failed verification; 1 later step were "
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
                "[spreadsheet.deduplicate] output has 2 rows, 0 exact duplicate rows remaining",
                "[spreadsheet.deduplicate] expected 3 non-empty rows from its 4-row input, "
                "found 2",
                "[spreadsheet.remove_empty_rows] skipped because an earlier step failed",
            ),
            steps=(
                StepVerification(capability="spreadsheet.deduplicate", passed=False),
                StepVerification(capability="spreadsheet.remove_empty_rows", passed=False),
            ),
        ),
    )


def test_personality_voice_reply_says_where_the_chain_stopped_and_what_never_ran() -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_stopped_chain_request())

    # No verified file: the model is never called, and the factual reply
    # says where the chain stopped. (It used to check the prompt builder,
    # which is gone.)
    assert provider.last_messages is None
    assert reply == _factual(_stopped_chain_request())
    assert "I stopped at the deduplicate step, so remove empty rows never ran" in reply


def test_template_speaker_names_the_failed_step_and_what_never_ran() -> None:
    reply = TemplateSpeaker()(_stopped_chain_request())

    assert reply.startswith("I ran into a problem before I could change anything.")
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
def test_personality_voice_reply_states_no_changes_when_only_analysis_ran(mode: str) -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_b4_request(mode))

    # No verified file: the model is never called. (It used to check the
    # prompt builder, which is gone.)
    assert provider.last_messages is None
    assert reply.endswith("No changes were made to any file this turn.")
    assert "cleaned copy" not in reply


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_discards_the_live_b4_false_completion_claim(mode: str) -> None:
    """The model said "All steps completed successfully." for a turn that
    changed nothing; that reply must never reach the user."""
    provider = _FakeLLMProvider(reply=_B4_LIVE_REPLY)

    reply = PersonalityVoice(provider)(_b4_request(mode))

    assert reply == _factual(_b4_request(mode))
    assert "completed" not in reply.lower()
    assert "No changes were made to any file this turn." in reply


def test_personality_voice_uses_the_factual_reply_even_when_the_model_would_be_honest() -> None:
    """R1: an action was requested and nothing was delivered, so the model
    isn't asked at all — even a reply that would have been honest isn't
    used. The factual reply still reports what was noticed."""
    honest = "Name and amount each have one missing value. Which should I look at first?"
    provider = _FakeLLMProvider(reply=honest)

    reply = PersonalityVoice(provider)(_b4_request())

    assert provider.last_messages is None
    assert reply == _factual(_b4_request())
    assert "name: 1 missing" in reply


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

    assert reply == _factual(request)


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
                "[spreadsheet.remove_empty_rows] output has 5 rows, 0 fully-empty rows remaining",
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
                "[spreadsheet.analyze] none of the issues I check for were found",
                "[final_output] final file: output has 3 rows, 0 exact duplicate rows remaining",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=True,
                    findings=("[missing_values] name: 1 missing (16.7%)",),
                ),
                StepVerification(
                    capability="spreadsheet.remove_empty_rows",
                    passed=True,
                    measurements=(_empty_rows_measurement(),),
                ),
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    measurements=(_dedupe_measurement(),),
                ),
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=True,
                    findings=("none of the issues I check for were found",),
                    input_reference=final,
                    recheck=True,
                ),
                StepVerification(
                    capability="final_output",
                    passed=True,
                    measurements=(
                        StepMeasurement(EMPTY_ROWS_TARGET, output_rows=3, remaining=0),
                        StepMeasurement(DUPLICATE_ROWS_TARGET, output_rows=3, remaining=0),
                    ),
                ),
            ),
        ),
    )


_DELIVERED_BLOCK = (
    "Removed 1 fully-empty row. Removed 2 exact duplicate rows. The copy has 3 rows, "
    "with 0 fully-empty rows and 0 exact duplicate rows remaining."
)


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_reply_states_the_file_handed_back(mode: str) -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_delivered_request(mode))

    assert "Your cleaned copy of report.xlsx is ready." in reply
    assert "No changes were made" not in reply
    assert "deduplicated_s-task-2" not in reply  # never the generated name


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_reply_always_mentions_the_file_handed_back(mode: str) -> None:
    provider = _FakeLLMProvider(reply="Done — 3 rows left, no empties or duplicates.")

    reply = PersonalityVoice(provider)(_delivered_request(mode))

    opener, closer = select_phrases(
        voice.build_outcome_report(
            _delivered_request(mode).execution_result,
            _delivered_request(mode).verification_result,
            Understanding(intent="multi_step_request"),
            delivered_file="report.xlsx",
        ),
        mode,
        seed=":0",
    )
    expected = " ".join(
        p
        for p in (
            opener.text,
            _DELIVERED_BLOCK,
            "Your cleaned copy of report.xlsx is ready.",
            VALUES_ONLY_SENTENCE,
            "None of the issues I check for were found.",
            closer.text,
        )
        if p
    )
    assert reply == expected
    assert "3 rows left" not in reply


# -- 3. Findings describe the file the user receives --------------------------------


def test_personality_voice_reports_analysis_of_the_final_file_not_the_original() -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_delivered_request("focus"))

    assert "None of the issues I check for were found." in reply
    assert "missing" not in reply  # the original's stale finding


def test_template_speaker_reports_analysis_of_the_final_file_not_the_original() -> None:
    reply = TemplateSpeaker()(_delivered_request("focus"))

    assert "missing" not in reply
    assert "Before anything else" not in reply
    assert "I also noticed" not in reply
    assert reply == (
        f"{_DELIVERED_BLOCK} Saved as `report_no_empty_rows_s-task-1_deduplicated_s-task-2.xlsx`."
        f" {VALUES_ONLY_SENTENCE} None of the issues I check for were found."
    )


def test_voice_reports_the_originals_analysis_when_nothing_was_re_analyzed() -> None:
    """No chain completed (B4): the user's file is unchanged, so its
    analysis is still current and is reported."""
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_b4_request())

    assert "name: 1 missing (16.7%)" in reply


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
                "[spreadsheet.deduplicate] output has 2 rows, 0 exact duplicate rows remaining",
                f"[{last.capability}] {last.failure_reason}",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    findings=("output has 2 rows, 0 exact duplicate rows remaining",),
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
    assert "0 exact duplicate rows remaining" not in reply


def _declined_plan_request(mode: str = "friend") -> SpeakingRequest:
    """The user rejected the plan (or didn't answer it): the first file-changing
    step was declined, so no step changed a file and the next was skipped.
    Shaped the way Hands and Inspector record it."""
    declined = _dedupe_step(
        status="declined",
        outputs=(),
        summary="confirmation was declined",
        failure_reason="wasn't confirmed, so it didn't run",
    )
    skipped = StepResult(
        capability="spreadsheet.remove_empty_rows",
        skill="",
        status="skipped",
        summary="skipped because spreadsheet.deduplicate was not confirmed",
    )
    return _request(
        mode=mode,
        understanding=Understanding(intent="multi_step_request"),
        execution_result=ExecutionResult(
            success=False,
            errors=(
                "excel.deduplicate was not confirmed for a write-tier action; skipped",
                "stopped: spreadsheet.deduplicate was not confirmed; 1 later step were "
                "skipped and no file was handed back",
            ),
            steps=(declined, skipped),
            stopped_at="spreadsheet.deduplicate",
        ),
        verification_result=VerificationResult(
            passed=False,
            findings=(
                "[spreadsheet.deduplicate] wasn't confirmed, so it didn't run",
                "[spreadsheet.remove_empty_rows] skipped because an earlier step failed",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=False,
                    findings=("wasn't confirmed, so it didn't run",),
                    input_reference="uploads/report.xlsx",
                ),
                StepVerification(
                    capability="spreadsheet.remove_empty_rows",
                    passed=False,
                    findings=("skipped because an earlier step failed",),
                ),
            ),
        ),
    )


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_a_rejected_plan_isnt_called_a_problem_and_says_nothing_ran(mode: str) -> None:
    """PR-3: the user's "no" to a plan is an ordinary answer, not a failure."""
    request = _declined_plan_request(mode)

    reply = TemplateSpeaker()(request)

    assert "problem" not in reply
    assert reply == (
        "The deduplicate step: wasn't confirmed, so it didn't run. I stopped at the "
        "deduplicate step, so remove empty rows never ran and no file was handed back; "
        "your original file is unchanged."
    )
    _assert_no_completion_claim(reply, "deduplicate")
    assert PersonalityVoice(_FakeLLMProvider(reply="All done!"))(request) == reply


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

    assert reply == _factual(request)
    _assert_no_completion_claim(reply, stopped_step)


def test_personality_voice_reply_says_nothing_was_delivered_when_the_last_step_stopped() -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_passed_then_stopped_request(_undispatchable_report()))

    # No verified file: the model is never called. (It used to check the
    # prompt builder, which is gone.)
    assert provider.last_messages is None
    assert "cleaned copy" not in reply
    assert "no file was handed back" in reply
    assert "spreadsheet." not in reply  # the error's capability id is in plain words


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
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
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

    # A multi_step_request gets no "Done" lead word until the ledger (D4).
    assert "Done" not in reply
    assert reply.startswith("The duplicate-row removal ran and passed its check.")
    assert "Saved as `report_no_empty_rows_deduplicated.xlsx`." in reply
    assert voice.RECHECK_FAILED_SENTENCE in reply
    assert "missing" not in reply  # the original's findings no longer describe the file
    assert "wasn't able to confirm" not in reply
    assert "spreadsheet." not in reply


def test_personality_voice_reply_reports_a_failed_re_check_and_the_file() -> None:
    provider = _FakeLLMProvider(reply="ok")

    reply = PersonalityVoice(provider)(_recheck_failed_request())

    assert provider.last_messages is None
    assert voice.RECHECK_FAILED_SENTENCE in reply
    assert "Your cleaned copy of report.xlsx is ready." in reply
    assert "spreadsheet." not in reply
    assert "missing" not in reply
    assert "ok" not in reply.split()


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


# -- R1/R3: the model never describes an action turn without a verified file ---------


_R1_LEAKED_REPLIES = [
    "Duplicates are gone and your sheet is down to 3 rows.",
    "All set! I took care of the duplicate rows.",
    "Your spreadsheet now has 3 rows with the duplicates stripped out.",
]


@pytest.mark.parametrize("leaked", _R1_LEAKED_REPLIES)
@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: _passed_then_stopped_request(_undispatchable_report()),
        lambda: _passed_then_stopped_request(_declined_empty_rows()),
        _stopped_chain_request,
        _b4_request,
    ],
    ids=["stopped-dispatch", "stopped-declined", "stopped-verification", "read-only-for-action"],
)
def test_personality_voice_never_lets_the_model_describe_an_undelivered_action(
    leaked: str, request_factory
) -> None:
    """The three replies that leaked past the word list in the re-review
    (R1). The model isn't called at all, so none can reach the user."""
    provider = _FakeLLMProvider(reply=leaked)
    request = request_factory()

    reply = PersonalityVoice(provider)(request)

    assert provider.last_messages is None
    assert reply == _factual(request)
    assert leaked not in reply
    assert voice._COMPLETION_CLAIM.search(reply) is None


def _final_file_failed_request(verification_passed: bool | None) -> SpeakingRequest:
    """R3: a file was handed back and every step passed, but the final
    file failed its check (or, with ``None``, Inspector never ran)."""
    mid, final = "out/mid.xlsx", "out/final.xlsx"
    execution = ExecutionResult(
        success=True,
        outputs=(final,),
        steps=(
            _dedupe_step(outputs=(mid,), input_reference="uploads/report.xlsx"),
            StepResult(
                capability="spreadsheet.remove_empty_rows",
                skill="excel.remove_empty_rows",
                status="success",
                outputs=(final,),
                input_reference=mid,
                permission_tier="write",
            ),
        ),
    )
    verification = None
    if verification_passed is not None:
        verification = VerificationResult(
            passed=verification_passed,
            findings=(
                "[final_output] final file: output has 4 rows, 1 exact duplicate row remaining",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    input_reference="uploads/report.xlsx",
                ),
                StepVerification(
                    capability="spreadsheet.remove_empty_rows", passed=True, input_reference=mid
                ),
                StepVerification(
                    capability="final_output",
                    passed=verification_passed,
                    findings=("final file: output has 4 rows, 1 exact duplicate row remaining",),
                ),
            ),
        )
    return _request(
        understanding=Understanding(intent="multi_step_request"),
        execution_result=execution,
        verification_result=verification,
    )


@pytest.mark.parametrize("verification_passed", [False, None], ids=["failed", "missing"])
def test_personality_voice_never_lets_the_model_describe_an_unverified_delivered_file(
    verification_passed: bool | None,
) -> None:
    request = _final_file_failed_request(verification_passed)
    provider = _FakeLLMProvider(reply="All done — duplicates and empty rows removed.")

    reply = PersonalityVoice(provider)(request)

    assert provider.last_messages is None
    assert reply == _factual(request)
    assert "All done" not in reply
    assert "is ready" not in reply


def test_template_speaker_reports_an_unverified_delivered_file_honestly() -> None:
    assert TemplateSpeaker()(_final_file_failed_request(False)).startswith(
        "I wasn't able to confirm that worked."
    )
    assert TemplateSpeaker()(_final_file_failed_request(None)) == (
        "I made the changes, but wasn't able to confirm the result."
    )


@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_personality_voice_never_uses_the_models_reply_for_a_verified_delivered_turn(
    mode: str,
) -> None:
    """Inverted: the model's reply used to be the reply."""
    provider = _FakeLLMProvider(reply="Both cleanups went through; report is down to 3 rows.")

    reply = PersonalityVoice(provider)(_delivered_request(mode))

    assert provider.last_messages is None
    assert "Both cleanups went through" not in reply
    assert _DELIVERED_BLOCK in reply


# -- F1: only a verified, delivered file lets the model phrase the turn ----------
# These replace three tests that let the model phrase any turn whose intent
# was None or "unclear" (the old "plain conversation" path), plus a truth
# table marking those intents "not requested". Brain has no conversational
# intent, so None/"unclear" is what a failed or unclassifiable *action*
# request becomes; the model is no longer called for it.


@pytest.mark.parametrize(
    "execution_result",
    [
        None,
        ExecutionResult(success=True, warnings=("nothing to execute",)),
        ExecutionResult(
            success=True,
            steps=(_analyze_step(input_reference="uploads/report.xlsx", permission_tier="read"),),
        ),
    ],
    ids=["nothing-executed", "nothing-delegated", "only-proactive-analysis"],
)
@pytest.mark.parametrize("intent", [None, "unclear"])
def test_personality_voice_uses_the_factual_reply_when_the_intent_is_unclear_or_missing(
    execution_result: ExecutionResult | None, intent: str | None
) -> None:
    provider = _FakeLLMProvider(reply="Hey! Send over a file whenever you like.")
    request = _request(
        understanding=Understanding(intent=intent),
        execution_result=execution_result,
        verification_result=VerificationResult(
            passed=True, steps=(StepVerification(capability="spreadsheet.analyze", passed=True),)
        )
        if execution_result is not None and execution_result.steps
        else None,
    )

    reply = PersonalityVoice(provider)(request)

    assert provider.last_messages is None
    assert reply == _factual(request)


def _verified(passed: bool = True) -> VerificationResult:
    return VerificationResult(
        passed=passed,
        steps=(StepVerification(capability="spreadsheet.deduplicate", passed=passed),),
    )


@pytest.mark.parametrize(
    ("intent", "execution_result", "verification_result", "expected"),
    [
        # A verified file was delivered, whatever the intent.
        ("remove_duplicate_rows", "delivered", _verified(), True),
        ("unclear", "delivered", _verified(), True),
        (None, "delivered", _verified(), True),
        # Anything else: never, whatever the intent.
        ("remove_duplicate_rows", "delivered", _verified(False), False),
        ("remove_duplicate_rows", "delivered", None, False),
        ("remove_duplicate_rows", None, None, False),
        ("multi_step_request", "analysis-only", _verified(), False),
        ("unclear", None, None, False),
        (None, None, None, False),
        (None, "analysis-only", _verified(), False),
    ],
)
def test_verified_delivery(intent, execution_result, verification_result, expected) -> None:
    executions = {
        None: None,
        "delivered": ExecutionResult(
            success=True,
            outputs=("out/report_deduplicated.xlsx",),
            steps=(_dedupe_step(outputs=("out/report_deduplicated.xlsx",)),),
        ),
        "analysis-only": ExecutionResult(
            success=True,
            steps=(_analyze_step(input_reference="uploads/report.xlsx", permission_tier="read"),),
        ),
    }
    request = _request(
        understanding=Understanding(intent=intent),
        execution_result=executions[execution_result],
        verification_result=verification_result,
    )

    assert voice._verified_delivery(request) is expected


# -- R4: analysis of the original failed; the delivered file is still named ----------


def test_voice_names_the_delivered_file_when_the_initial_analysis_failed() -> None:
    final = "workspace/outputs/report_deduplicated.xlsx"
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(
            success=False,
            outputs=(final,),
            errors=("could not read it",),
            steps=(
                _analyze_step(
                    status="failure", input_reference="uploads/report.xlsx", permission_tier="read"
                ),
                _dedupe_step(outputs=(final,), input_reference="uploads/report.xlsx"),
            ),
        ),
        verification_result=VerificationResult(
            passed=True,
            findings=(
                "[spreadsheet.analyze] the analysis reported failure",
                "[spreadsheet.deduplicate] output has 3 rows, 0 exact duplicate rows remaining",
            ),
            steps=(
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=False,
                    findings=("the analysis reported failure",),
                    input_reference="uploads/report.xlsx",
                    read_only=True,
                ),
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    input_reference="uploads/report.xlsx",
                ),
            ),
        ),
    )

    reply = TemplateSpeaker()(request)
    assert "Saved as `report_deduplicated.xlsx`." in reply
    assert voice.INITIAL_ANALYSIS_FAILED_SENTENCE in reply
    assert "reported failure" not in reply  # said once, in plain words
    assert "wasn't able to confirm" not in reply

    provider = _FakeLLMProvider(reply="Dedupe went through, 3 rows left.")
    personality = PersonalityVoice(provider)(request)
    assert provider.last_messages is None  # rendered by code, no model call
    assert voice.INITIAL_ANALYSIS_FAILED_SENTENCE in personality
    assert "Your cleaned copy of report.xlsx is ready." in personality
    assert "Dedupe went through" not in personality


# -- Final review F4: the order used is stated when it couldn't be confirmed -------------


def _two_step_request(order_confirmed: bool, second_status: str = "success") -> SpeakingRequest:
    request = _delivered_request("friend")
    assert request.execution_result is not None
    steps = list(request.execution_result.steps)
    steps[2] = dataclasses.replace(steps[2], status=second_status)
    return dataclasses.replace(
        request,
        order_confirmed=order_confirmed,
        execution_result=dataclasses.replace(request.execution_result, steps=tuple(steps)),
    )


_ORDER_USED = (
    "I couldn't tell for sure from your message what order you wanted, so the steps ran "
    "in this order: remove empty rows, then deduplicate."
)


def test_template_speaker_states_the_order_used_when_it_couldnt_be_confirmed() -> None:
    reply = TemplateSpeaker()(_two_step_request(False))

    # Part of the outcome block now: after the counts, before the file.
    assert _ORDER_USED in reply
    assert reply.index(_ORDER_USED) < reply.index("Saved as")


def test_personality_voice_reply_states_the_order_used_when_it_couldnt_be_confirmed() -> None:
    """Reply-level now (it was only in the prompt): closes the backlog's
    F4-with-PersonalityVoice item."""
    reply = PersonalityVoice(_FakeLLMProvider(reply="Done, both cleanups."))(
        _two_step_request(False)
    )

    assert _ORDER_USED in reply
    assert reply.index(_ORDER_USED) < reply.index("Your cleaned copy")


@pytest.mark.parametrize(
    "request_factory",
    [
        lambda: _two_step_request(True),
        lambda: _two_step_request(False, second_status="declined"),  # only one step ran
    ],
    ids=["confirmed", "one-step-ran"],
)
def test_the_order_used_is_not_stated_otherwise(request_factory) -> None:
    request = request_factory()

    assert "what order you wanted" not in TemplateSpeaker()(request)
    assert "what order you wanted" not in PersonalityVoice()(request)


@pytest.mark.parametrize(
    ("flags", "expected"), [((), True), ((True, True), True), ((True, False), False)]
)
def test_voice_passes_on_whether_the_plans_order_was_confirmed(flags, expected) -> None:
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.planning = tuple(
        PlanningStep(id=f"step-{i}", description="x", capability="c", order_confirmed=f)
        for i, f in enumerate(flags)
    )

    assert Voice._build_request(state).order_confirmed is expected


# -- Verified outcome (PR-A): no model text can reach a delivered reply ----------------

# The review's literal model replies (docs/reviews/chaining-wip-final-review.md:
# N1 a/b/c, N2, C2, N5, the order run), plus a JSON "selection" carrying text.
_REVIEW_MODEL_REPLIES = (
    "All done — duplicates removed.",
    "Duplicates are gone and your sheet is spotless now.",
    "Got rid of those pesky dupes for you — enjoy the tidy sheet!",
    "Removed 3 exact duplicate rows — your sheet is clean.",
    "Done — duplicates and empty rows are gone from report.",
    "Both done: empty rows and duplicates are gone.",
    "Done, both cleanups are finished.",
    '{"opener": "friend.opener.seeing_double", "closer": "Removed 3 rows, all clean!"}',
)


def _n2_request(mode: str) -> SpeakingRequest:
    """N2's shape: a correct dedupe on a file with no duplicates, verified."""
    output = "workspace/outputs/report_deduplicated_s-task-1.xlsx"
    return _request(
        mode=mode,
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(
            success=True, outputs=(output,), steps=(_dedupe_step(outputs=(output,)),)
        ),
        verification_result=VerificationResult(
            passed=True,
            steps=(
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    input_reference="uploads/report.xlsx",
                    measurements=(_dedupe_measurement(input_rows=4, removed=0, output_rows=4),),
                ),
            ),
        ),
    )


@pytest.mark.parametrize("model_reply", _REVIEW_MODEL_REPLIES)
@pytest.mark.parametrize("request_factory", [_n2_request, _delivered_request], ids=["n2", "chain"])
@pytest.mark.parametrize("mode", ["friend", "focus"])
def test_the_reviews_model_replies_can_never_appear_in_a_delivered_reply(
    model_reply: str, request_factory, mode: str
) -> None:
    provider = _FakeLLMProvider(reply=model_reply)
    request = request_factory(mode)

    reply = PersonalityVoice(provider)(request)

    assert provider.last_messages is None
    assert model_reply not in reply
    assert "3 duplicate" not in reply and "Removed 3" not in reply
    assert reply == voice._verified_delivery_reply(request, file_style="plain", mode=mode)


def test_n2_reply_says_there_was_nothing_to_remove() -> None:
    assert TemplateSpeaker()(_n2_request("focus")) == (
        "Done — there were no exact duplicate rows to remove. The copy has 4 rows, "
        "with 0 exact duplicate rows remaining. Saved as `report_deduplicated_s-task-1.xlsx`. "
        + VALUES_ONLY_SENTENCE
    )


def test_delivered_reply_order_is_opener_outcome_findings_closer() -> None:
    """Decision D3. Friend mode, with an open analysis finding: no closer
    question on top of the findings' own question."""
    output = "workspace/outputs/report_deduplicated.xlsx"
    request = _request(
        mode="friend",
        session_id="s",
        understanding=Understanding(intent="remove_duplicate_rows"),
        execution_result=ExecutionResult(
            success=True,
            outputs=(output,),
            steps=(_analyze_step(permission_tier="read"), _dedupe_step(outputs=(output,))),
        ),
        verification_result=VerificationResult(
            passed=True,
            findings=("[spreadsheet.analyze] [outliers] amount: 1 unusually high value",),
            steps=(
                StepVerification(capability="spreadsheet.analyze", passed=True, read_only=True),
                StepVerification(
                    capability="spreadsheet.deduplicate",
                    passed=True,
                    measurements=(_dedupe_measurement(),),
                ),
            ),
        ),
    )

    reply = PersonalityVoice()(request)

    opener, closer = select_phrases(
        voice.build_outcome_report(
            request.execution_result,
            request.verification_result,
            Understanding(intent="remove_duplicate_rows"),
            open_findings=1,
        ),
        "friend",
        seed="s:0",
    )
    assert opener.text and closer.text == ""
    assert reply == (
        f"{opener.text} Done — removed 2 exact duplicate rows. The copy has 3 rows, "
        "with 0 exact duplicate rows remaining. Your cleaned copy of report.xlsx is ready. "
        f"{VALUES_ONLY_SENTENCE} "
        "I also noticed: amount: 1 unusually high value. Want me to tackle any of these?"
    )


def test_voice_seeds_the_phrase_choice_with_the_session_and_turn() -> None:
    state = BuddyState.new(session_id="session-9", workspace_id="w")
    state.context.record_buddy_reply("earlier reply")

    request = Voice._build_request(state)

    assert (request.session_id, request.turn_index) == ("session-9", 1)
