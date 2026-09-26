"""Unit tests for buddy.inspector."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from buddy.inspector import (
    CompositeVerifier,
    Inspector,
    NullVerifier,
    SpreadsheetAnalysisVerifier,
    SpreadsheetDedupeVerifier,
    VerificationRequest,
)
from buddy.models import (
    ExecutionResult,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.state import BuddyState


def _request(**overrides: object) -> VerificationRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "execution_result": None,
    }
    fields.update(overrides)
    return VerificationRequest(**fields)  # type: ignore[arg-type]


class _FakeVerifier:
    """A ``Verifier`` stand-in returning a canned result and recording
    every request it was actually called with.
    """

    def __init__(self, result: VerificationResult) -> None:
        self._result = result
        self.received_requests: list[VerificationRequest] = []

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        self.received_requests.append(request)
        return self._result


# -- NullVerifier ---------------------------------------------------------------


def test_null_verifier_never_claims_success() -> None:
    result = NullVerifier()(_request())

    assert result.passed is False
    assert result.confidence == 0.0


# -- SpreadsheetDedupeVerifier: independent re-read ------------------------------


def test_verifier_passes_when_output_file_has_no_duplicates(tmp_path: Path) -> None:
    output_path = tmp_path / "report_deduplicated.xlsx"
    pd.DataFrame({"name": ["Alice", "Bob", "Carol"]}).to_excel(output_path, index=False)
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(output_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is True
    assert "0 duplicate row(s)" in result.findings[0]


def test_verifier_fails_when_output_file_still_has_duplicates(tmp_path: Path) -> None:
    output_path = tmp_path / "still_has_dupes.xlsx"
    pd.DataFrame({"name": ["Alice", "Alice", "Bob"]}).to_excel(output_path, index=False)
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(output_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False
    assert "1 duplicate row(s)" in result.findings[0]
    assert result.recommendations


def test_verifier_recomputes_independently_rather_than_trusting_anything(tmp_path: Path) -> None:
    """``SpreadsheetDedupeVerifier`` itself only ever reads
    ``execution_result.success``/``.outputs`` — never a Skill's own
    ``status``, ``summary``, or ``metadata`` (``ExecutionResult.steps``
    exists for ``CompositeVerifier``'s per-capability dispatch, not for a
    single Verifier to consult about what a Skill claimed). So there is
    nothing for this verifier to "trust" about what the skill said; it
    must derive its answer purely from re-reading the file.
    """
    output_path = tmp_path / "actually_has_dupes.xlsx"
    pd.DataFrame({"name": ["Alice", "Alice"]}).to_excel(output_path, index=False)
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(output_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False


def test_verifier_fails_when_execution_did_not_succeed() -> None:
    request = _request(execution_result=ExecutionResult(success=False, errors=("boom",)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False
    assert result.findings


def test_verifier_fails_when_no_execution_result_at_all() -> None:
    result = SpreadsheetDedupeVerifier()(_request(execution_result=None))

    assert result.passed is False


def test_verifier_fails_when_output_file_is_missing(tmp_path: Path) -> None:
    missing_path = tmp_path / "does-not-exist.xlsx"
    request = _request(execution_result=ExecutionResult(success=True, outputs=(str(missing_path),)))

    result = SpreadsheetDedupeVerifier()(request)

    assert result.passed is False
    assert result.findings


# -- SpreadsheetAnalysisVerifier: fully re-derived vs. self-reported -------------


def _analysis_step(**overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "capability": "spreadsheet.analyze",
        "skill": "excel.analyze",
        "status": "success",
    }
    fields.update(overrides)
    return StepResult(**fields)  # type: ignore[arg-type]


def _analysis_request(active_file: str | None, step: StepResult) -> VerificationRequest:
    return _request(
        execution_result=ExecutionResult(success=True, steps=(step,)),
        active_file=active_file,
    )


def test_spreadsheet_analysis_verifier_fails_when_no_execution_result_at_all() -> None:
    request = _request(execution_result=None, active_file="report.xlsx")

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is False
    assert "no analysis step to verify" in result.findings[0]


def test_spreadsheet_analysis_verifier_fails_when_the_step_itself_reported_failure() -> None:
    request = _analysis_request("report.xlsx", _analysis_step(status="failure"))

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is False
    assert "reported failure" in result.findings[0]


def test_spreadsheet_analysis_verifier_fails_when_no_active_file_is_available() -> None:
    request = _analysis_request(None, _analysis_step())

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is False
    assert "no active file" in result.findings[0]


def test_spreadsheet_analysis_verifier_fails_when_active_file_cannot_be_read() -> None:
    request = _analysis_request("does-not-exist.xlsx", _analysis_step())

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is False
    assert "could not independently read" in result.findings[0]


def test_spreadsheet_analysis_verifier_reports_nothing_notable_for_a_clean_file(
    tmp_path: Path,
) -> None:
    clean_path = tmp_path / "clean.xlsx"
    pd.DataFrame({"amount": [10, 20, 30, 40, 50]}).to_excel(clean_path, index=False)
    request = _analysis_request(str(clean_path), _analysis_step())

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert result.confidence == 1.0
    assert result.findings == ("nothing notable found",)


def test_spreadsheet_analysis_verifier_independently_recomputes_missing_values(
    tmp_path: Path,
) -> None:
    """Recomputed from the ORIGINAL upload, never from anything the Skill
    claimed — mirrors the same "deliberately blind" discipline
    SpreadsheetDedupeVerifier already applies (see that class's own test
    above), just against the original file instead of an output file.
    """
    input_path = tmp_path / "messy.xlsx"
    pd.DataFrame({"name": ["Alice", None, "Carol", "Dave"]}).to_excel(input_path, index=False)
    # The Skill's own self-report is irrelevant and deliberately not read —
    # a fabricated, wrong summary must not change the verdict.
    step = _analysis_step(summary="I found nothing at all, trust me.")
    request = _analysis_request(str(input_path), step)

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert any(f.startswith("[missing_values]") and "name" in f for f in result.findings)


def test_spreadsheet_analysis_verifier_independently_recomputes_outliers(tmp_path: Path) -> None:
    input_path = tmp_path / "outliers.xlsx"
    pd.DataFrame({"amount": [10, 12, 11, 13, 9, 999999]}).to_excel(input_path, index=False)
    request = _analysis_request(str(input_path), _analysis_step())

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert any(f.startswith("[outliers]") and "amount" in f for f in result.findings)


def test_spreadsheet_analysis_verifier_independently_recomputes_date_format_consistency(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "dates.xlsx"
    pd.DataFrame(
        {"signup_date": ["2024-01-01", "01/02/2024", "2024-01-03", "not a date"]}
    ).to_excel(input_path, index=False)
    request = _analysis_request(str(input_path), _analysis_step())

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert any(f.startswith("[date_format]") and "signup_date" in f for f in result.findings)


def test_spreadsheet_analysis_verifier_independently_recomputes_grouped_key_conflicts(
    tmp_path: Path,
) -> None:
    input_path = tmp_path / "conflicts.xlsx"
    pd.DataFrame(
        {
            "email": ["a@x.com", "a@x.com", "b@x.com"],
            "phone": ["111", "222", "333"],
        }
    ).to_excel(input_path, index=False)
    request = _analysis_request(str(input_path), _analysis_step())

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert any(f.startswith("[grouped_key_conflict]") and "email" in f for f in result.findings)


def test_spreadsheet_analysis_verifier_passes_near_duplicates_through_as_self_reported(
    tmp_path: Path,
) -> None:
    """Near-duplicate findings are the one category NOT independently
    re-run (Section 5.2 of docs/design/proactive-analysis-design.md) —
    relayed verbatim from the Skill's own self-report, at a capped
    confidence, explicitly labeled as such.
    """
    clean_path = tmp_path / "clean.xlsx"
    pd.DataFrame({"amount": [10, 20, 30, 40]}).to_excel(clean_path, index=False)
    step = _analysis_step(
        self_reported_findings=("Alice Johnson and Alice Johnston might be the same person",)
    )
    request = _analysis_request(str(clean_path), step)

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert result.confidence == 0.7  # lower than a fully-independent 1.0
    assert any(
        f.startswith("[near_duplicates]")
        and "Alice Johnson and Alice Johnston" in f
        and "self-reported, not independently re-checked" in f
        for f in result.findings
    )


# -- CompositeVerifier: per-capability dispatch and aggregation -----------------


def test_composite_verifier_dispatches_to_the_registered_verifier_for_the_capability() -> None:
    dedupe_verifier = _FakeVerifier(
        VerificationResult(passed=True, confidence=1.0, findings=("dedupe ok",))
    )
    composite = CompositeVerifier({"spreadsheet.deduplicate": dedupe_verifier})
    step = StepResult(
        capability="spreadsheet.deduplicate",
        skill="excel.deduplicate",
        status="success",
        outputs=("out.xlsx",),
    )
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",), steps=(step,))
    )

    result = composite(request)

    assert len(dedupe_verifier.received_requests) == 1
    assert result.passed is True
    assert "[spreadsheet.deduplicate] dedupe ok" in result.findings


def test_composite_verifier_scopes_each_verifiers_request_to_just_its_own_step() -> None:
    """The request a registered Verifier receives must reflect only that
    step's own outputs — not the whole turn's.
    """
    verifier = _FakeVerifier(VerificationResult(passed=True))
    composite = CompositeVerifier({"spreadsheet.deduplicate": verifier})
    step_a = StepResult(
        capability="spreadsheet.deduplicate",
        skill="excel.deduplicate",
        status="success",
        outputs=("a.xlsx",),
    )
    step_b = StepResult(
        capability="spreadsheet.remove_empty_rows",
        skill="excel.remove_empty_rows",
        status="success",
        outputs=("b.xlsx",),
    )
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("a.xlsx", "b.xlsx"), steps=(step_a, step_b)
        )
    )

    composite(request)

    assert len(verifier.received_requests) == 1
    scoped = verifier.received_requests[0].execution_result
    assert scoped is not None
    assert scoped.outputs == ("a.xlsx",)


def test_composite_verifier_falls_back_for_a_capability_with_no_registered_verifier() -> None:
    fallback = _FakeVerifier(VerificationResult(passed=False, findings=("not checked",)))
    composite = CompositeVerifier({}, fallback=fallback)
    step = StepResult(
        capability="spreadsheet.unknown_capability",
        skill="some.skill",
        status="success",
        outputs=("out.xlsx",),
    )
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",), steps=(step,))
    )

    result = composite(request)

    assert len(fallback.received_requests) == 1
    assert result.passed is False


def test_composite_verifier_defaults_its_fallback_to_null_verifier() -> None:
    composite = CompositeVerifier({})
    step = StepResult(
        capability="spreadsheet.unknown_capability",
        skill="some.skill",
        status="success",
        outputs=("out.xlsx",),
    )
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=("out.xlsx",), steps=(step,))
    )

    result = composite(request)

    assert result.passed is False
    assert "no verifier has been configured" in result.findings[0]


def test_composite_verifier_falls_back_when_execution_result_has_no_steps_at_all() -> None:
    fallback = _FakeVerifier(VerificationResult(passed=False, findings=("nothing to verify",)))
    composite = CompositeVerifier({}, fallback=fallback)
    request = _request(
        execution_result=ExecutionResult(success=True, warnings=("nothing to execute",))
    )

    result = composite(request)

    assert len(fallback.received_requests) == 1
    assert result.passed is False


def test_composite_verifier_falls_back_when_execution_result_is_none() -> None:
    fallback = _FakeVerifier(VerificationResult(passed=False))
    composite = CompositeVerifier({}, fallback=fallback)

    result = composite(_request(execution_result=None))

    assert len(fallback.received_requests) == 1
    assert result.passed is False


def test_composite_verifier_passes_only_when_every_step_passes() -> None:
    passing = _FakeVerifier(
        VerificationResult(passed=True, confidence=1.0, findings=("dedupe ok",))
    )
    failing = _FakeVerifier(
        VerificationResult(passed=False, confidence=1.0, findings=("still has empty rows",))
    )
    composite = CompositeVerifier(
        {
            "spreadsheet.deduplicate": passing,
            "spreadsheet.remove_empty_rows": failing,
        }
    )
    step_a = StepResult(
        capability="spreadsheet.deduplicate",
        skill="excel.deduplicate",
        status="success",
        outputs=("a.xlsx",),
    )
    step_b = StepResult(
        capability="spreadsheet.remove_empty_rows",
        skill="excel.remove_empty_rows",
        status="success",
        outputs=("b.xlsx",),
    )
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("a.xlsx", "b.xlsx"), steps=(step_a, step_b)
        )
    )

    result = composite(request)

    assert result.passed is False
    assert "[spreadsheet.deduplicate] dedupe ok" in result.findings
    assert "[spreadsheet.remove_empty_rows] still has empty rows" in result.findings


def test_composite_verifier_confidence_is_the_minimum_across_steps() -> None:
    high = _FakeVerifier(VerificationResult(passed=True, confidence=1.0))
    low = _FakeVerifier(VerificationResult(passed=True, confidence=0.4))
    composite = CompositeVerifier({"cap.a": high, "cap.b": low})
    step_a = StepResult(capability="cap.a", skill="skill.a", status="success", outputs=("a.xlsx",))
    step_b = StepResult(capability="cap.b", skill="skill.b", status="success", outputs=("b.xlsx",))
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("a.xlsx", "b.xlsx"), steps=(step_a, step_b)
        )
    )

    result = composite(request)

    assert result.confidence == 0.4


def test_composite_verifier_concatenates_recommendations_across_steps() -> None:
    a = _FakeVerifier(VerificationResult(passed=False, recommendations=("fix a",)))
    b = _FakeVerifier(VerificationResult(passed=False, recommendations=("fix b",)))
    composite = CompositeVerifier({"cap.a": a, "cap.b": b})
    step_a = StepResult(capability="cap.a", skill="skill.a", status="success", outputs=("a.xlsx",))
    step_b = StepResult(capability="cap.b", skill="skill.b", status="success", outputs=("b.xlsx",))
    request = _request(
        execution_result=ExecutionResult(
            success=True, outputs=("a.xlsx", "b.xlsx"), steps=(step_a, step_b)
        )
    )

    result = composite(request)

    assert result.recommendations == ("fix a", "fix b")


def test_composite_verifier_passes_active_file_through_to_each_scoped_request() -> None:
    verifier = _FakeVerifier(VerificationResult(passed=True))
    composite = CompositeVerifier({"spreadsheet.analyze": verifier})
    step = StepResult(capability="spreadsheet.analyze", skill="excel.analyze", status="success")
    request = _request(
        execution_result=ExecutionResult(success=True, steps=(step,)),
        active_file="uploads/report.xlsx",
    )

    composite(request)

    assert len(verifier.received_requests) == 1
    assert verifier.received_requests[0].active_file == "uploads/report.xlsx"


def test_composite_verifier_builds_a_per_step_verification_breakdown() -> None:
    passing = _FakeVerifier(
        VerificationResult(passed=True, confidence=1.0, findings=("dedupe ok",))
    )
    failing = _FakeVerifier(
        VerificationResult(passed=False, confidence=0.8, findings=("still has empty rows",))
    )
    composite = CompositeVerifier(
        {"spreadsheet.deduplicate": passing, "spreadsheet.remove_empty_rows": failing}
    )
    step_a = StepResult(
        capability="spreadsheet.deduplicate", skill="excel.deduplicate", status="success"
    )
    step_b = StepResult(
        capability="spreadsheet.remove_empty_rows",
        skill="excel.remove_empty_rows",
        status="success",
    )
    request = _request(execution_result=ExecutionResult(success=True, steps=(step_a, step_b)))

    result = composite(request)

    assert len(result.steps) == 2
    assert result.steps[0] == StepVerification(
        capability="spreadsheet.deduplicate", passed=True, confidence=1.0, findings=("dedupe ok",)
    )
    assert result.steps[1] == StepVerification(
        capability="spreadsheet.remove_empty_rows",
        passed=False,
        confidence=0.8,
        findings=("still has empty rows",),
    )


def test_composite_verifier_marks_scoped_execution_as_failed_when_step_status_is_failure() -> None:
    step = StepResult(
        capability="spreadsheet.deduplicate", skill="excel.deduplicate", status="failure"
    )
    composite = CompositeVerifier({"spreadsheet.deduplicate": SpreadsheetDedupeVerifier()})
    request = _request(
        execution_result=ExecutionResult(success=False, errors=("boom",), steps=(step,))
    )

    result = composite(request)

    assert result.passed is False


def test_composite_verifier_reuses_spreadsheet_dedupe_verifier_unchanged(tmp_path: Path) -> None:
    """End-to-end proof that a real, pre-existing single-output Verifier
    (``SpreadsheetDedupeVerifier``) works through ``CompositeVerifier``'s
    per-step scoping with no changes to ``SpreadsheetDedupeVerifier`` itself.
    """
    output_path = tmp_path / "report_deduplicated.xlsx"
    pd.DataFrame({"name": ["Alice", "Bob", "Carol"]}).to_excel(output_path, index=False)
    composite = CompositeVerifier({"spreadsheet.deduplicate": SpreadsheetDedupeVerifier()})
    step = StepResult(
        capability="spreadsheet.deduplicate",
        skill="excel.deduplicate",
        status="success",
        outputs=(str(output_path),),
    )
    request = _request(
        execution_result=ExecutionResult(success=True, outputs=(str(output_path),), steps=(step,))
    )

    result = composite(request)

    assert result.passed is True
    assert any("0 duplicate row(s)" in finding for finding in result.findings)


# -- Inspector stage -----------------------------------------------------------------


def test_inspector_stage_sets_typed_verification_result_field() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    stage = Inspector(verifier=NullVerifier())

    stage(state)

    assert state.verification_result is not None
    assert state.verification_result.passed is False


def test_inspector_build_request_includes_active_file() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("uploads/report.xlsx")
    seen: list[VerificationRequest] = []

    class _RecordingVerifier:
        def __call__(self, request: VerificationRequest) -> VerificationResult:
            seen.append(request)
            return VerificationResult(passed=False)

    Inspector(verifier=_RecordingVerifier())(state)

    assert seen[0].active_file == "uploads/report.xlsx"


# -- Inspector: marking a file analyzed from the PER-STEP verdict ----------------


def test_inspector_marks_analyzed_from_the_steps_own_verdict_even_when_the_aggregate_failed() -> (
    None
):
    """Regression guard for Section 1.2/5.5 of
    docs/design/proactive-analysis-design.md: marking must read the
    per-step verdict in VerificationResult.steps, not the aggregate
    ``passed`` — a different capability failing in the same turn must
    never stop a genuinely-passed spreadsheet.analyze step from being
    marked analyzed.
    """
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=False,  # the turn overall failed — some OTHER step failed
        confidence=0.5,
        steps=(
            StepVerification(capability="spreadsheet.analyze", passed=True, confidence=1.0),
            StepVerification(capability="spreadsheet.deduplicate", passed=False, confidence=1.0),
        ),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" in state.context.analyzed_files


def test_inspector_does_not_mark_analyzed_from_an_aggregate_pass_when_the_step_itself_failed() -> (
    None
):
    """The mirror image of the guard above: an aggregate ``passed=True``
    must never be misread as spreadsheet.analyze itself having passed.
    """
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=True,
        confidence=1.0,
        steps=(StepVerification(capability="spreadsheet.analyze", passed=False, confidence=1.0),),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" not in state.context.analyzed_files


def test_inspector_does_not_mark_analyzed_when_there_is_no_active_file() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    result = VerificationResult(
        passed=True, steps=(StepVerification(capability="spreadsheet.analyze", passed=True),)
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert state.context.analyzed_files == frozenset()


def test_inspector_does_not_mark_analyzed_when_no_analyze_step_ran_at_all() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=True,
        steps=(StepVerification(capability="spreadsheet.deduplicate", passed=True),),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" not in state.context.analyzed_files
