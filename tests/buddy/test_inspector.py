"""Unit tests for buddy.inspector."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pandas as pd

from buddy.inspector import (
    CompositeVerifier,
    Inspector,
    NullVerifier,
    ResidualMeasurer,
    SpreadsheetAnalysisVerifier,
    SpreadsheetDedupeVerifier,
    SpreadsheetEmptyRowsVerifier,
    VerificationRequest,
)
from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    LEDGER_FROM_BRAIN,
    ExecutionResult,
    RequestedAction,
    StepMeasurement,
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
    assert result.findings == ("none of the issues I check for were found",)


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
            StepVerification(
                capability="spreadsheet.analyze",
                passed=True,
                confidence=1.0,
                input_reference="report.xlsx",
            ),
            StepVerification(capability="spreadsheet.deduplicate", passed=False, confidence=1.0),
        ),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" in state.context.analyzed_files


def test_inspector_does_not_mark_the_original_analyzed_from_a_re_check_of_the_final_file() -> (
    None
):
    """L5: analysis of the original failed; only the re-check of the final
    file passed. That's a check of a different file, so the original stays
    unanalyzed and is analyzed again next turn."""
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=True,
        steps=(
            StepVerification(
                capability="spreadsheet.analyze", passed=False, input_reference="report.xlsx"
            ),
            StepVerification(
                capability="spreadsheet.analyze",
                passed=True,
                input_reference="out/final.xlsx",
                recheck=True,
            ),
        ),
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" not in state.context.analyzed_files


def test_inspector_does_not_mark_analyzed_from_a_verdict_with_no_input_reference() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.switch_active_file("report.xlsx")
    result = VerificationResult(
        passed=True, steps=(StepVerification(capability="spreadsheet.analyze", passed=True),)
    )
    Inspector(verifier=_FakeVerifier(result))(state)

    assert "report.xlsx" not in state.context.analyzed_files


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


# -- Each step against its own input (chaining) -----------------------------------


def _xlsx(path: Path, rows: dict[str, list[object]]) -> str:
    pd.DataFrame(rows).to_excel(path, index=False)
    return str(path)


def _step_request(
    capability: str, output: str, input_reference: str = ""
) -> VerificationRequest:
    step = StepResult(
        capability=capability,
        skill="some.skill",
        status="success",
        outputs=(output,),
        input_reference=input_reference,
    )
    return _request(execution_result=ExecutionResult(success=True, outputs=(output,), steps=(step,)))


_WITH_DUPES = {"name": ["Alice", "Bob", "Alice", "Carol"], "amount": [10, 20, 10, 30]}


def test_dedupe_verifier_passes_an_output_consistent_with_its_input(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", _WITH_DUPES)
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob", "Carol"], "amount": [10, 20, 30]})

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is True
    assert result.findings == ("output has 3 row(s), 0 duplicate row(s) remaining",)


def test_dedupe_verifier_fails_an_output_that_removed_a_row_it_should_have_kept(
    tmp_path: Path,
) -> None:
    """No duplicates left — an output-only check passes — but Carol, who
    was never a duplicate, is gone too."""
    source = _xlsx(tmp_path / "in.xlsx", _WITH_DUPES)
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob"], "amount": [10, 20]})

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is False
    assert "expected 3 non-empty row(s) from its 4-row input, found 2" in result.findings[1]
    assert result.recommendations


def test_dedupe_verifier_fails_an_output_with_a_row_not_in_its_input(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", _WITH_DUPES)
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob", "Dave"], "amount": [10, 20, 40]})

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is False
    assert "1 output row(s) don't appear in its input" in result.findings[1]


def test_dedupe_verifier_checks_only_the_output_when_it_has_no_input(tmp_path: Path) -> None:
    """The final-file check has no single input; only the postcondition applies."""
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob"], "amount": [10, 20]})

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output))

    assert result.passed is True


def test_dedupe_verifier_tolerates_losing_a_fully_empty_row(tmp_path: Path) -> None:
    """An .xlsx can't hold a trailing empty row, so one that ends up last
    after deduplication disappears on write — not the step's fault."""
    source = _xlsx(tmp_path / "in.xlsx", {"name": ["Alice", None, "Alice"], "amount": [10, None, 10]})
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice"], "amount": [10]})

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is True


def test_empty_rows_verifier_passes_an_output_consistent_with_its_input(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", {"name": ["Alice", None, "Bob"], "amount": [10, None, 20]})
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob"], "amount": [10, 20]})

    result = SpreadsheetEmptyRowsVerifier()(
        _step_request("spreadsheet.remove_empty_rows", output, source)
    )

    assert result.passed is True


def test_empty_rows_verifier_fails_an_output_that_removed_a_non_empty_row(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", {"name": ["Alice", None, "Bob"], "amount": [10, None, 20]})
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice"], "amount": [10]})

    result = SpreadsheetEmptyRowsVerifier()(
        _step_request("spreadsheet.remove_empty_rows", output, source)
    )

    assert result.passed is False
    assert "expected 2 non-empty row(s)" in result.findings[1]


def test_analysis_verifier_reads_the_steps_own_input_over_the_active_file(
    tmp_path: Path,
) -> None:
    analyzed = _xlsx(tmp_path / "analyzed.xlsx", {"amount": [10, 20, 30, 40, 50]})
    request = _analysis_request("does-not-exist.xlsx", _analysis_step(input_reference=analyzed))

    result = SpreadsheetAnalysisVerifier()(request)

    assert result.passed is True
    assert result.findings == ("none of the issues I check for were found",)


# -- CompositeVerifier: skipped steps and the final file --------------------------


def test_composite_verifier_checks_the_final_file_against_every_chained_steps_claim(
    tmp_path: Path,
) -> None:
    source = _xlsx(
        tmp_path / "in.xlsx",
        {"name": ["Alice", "Bob", None, "Alice"], "amount": [10, 20, None, 10]},
    )
    first_out = _xlsx(tmp_path / "a.xlsx", {"name": ["Alice", "Bob", None], "amount": [10, 20, None]})
    final = _xlsx(tmp_path / "b.xlsx", {"name": ["Alice", "Bob"], "amount": [10, 20]})
    steps = (
        StepResult(
            capability="spreadsheet.deduplicate",
            skill="excel.deduplicate",
            status="success",
            outputs=(first_out,),
            input_reference=source,
        ),
        StepResult(
            capability="spreadsheet.remove_empty_rows",
            skill="excel.remove_empty_rows",
            status="success",
            outputs=(final,),
            input_reference=first_out,
        ),
    )
    composite = CompositeVerifier(
        {
            "spreadsheet.deduplicate": SpreadsheetDedupeVerifier(),
            "spreadsheet.remove_empty_rows": SpreadsheetEmptyRowsVerifier(),
        }
    )

    result = composite(
        _request(execution_result=ExecutionResult(success=True, outputs=(final,), steps=steps))
    )

    assert result.passed is True
    assert [s.capability for s in result.steps] == [
        "spreadsheet.deduplicate",
        "spreadsheet.remove_empty_rows",
        "final_output",
    ]
    assert result.steps[-1].findings == (
        "final file: output has 2 row(s), 0 duplicate row(s) remaining",
        "final file: output has 2 row(s), 0 fully-empty row(s) remaining",
    )


def test_composite_verifier_fails_a_final_file_that_breaks_an_earlier_steps_claim(
    tmp_path: Path,
) -> None:
    """Each step passed its own check, but the second step (a stand-in for
    e.g. a future whitespace-trimming skill) made two rows identical, so
    the file the user receives has a duplicate again."""
    source = _xlsx(tmp_path / "in.xlsx", {"name": ["Alice", "Alice "], "amount": [10, 10]})
    first_out = _xlsx(tmp_path / "a.xlsx", {"name": ["Alice", "Alice "], "amount": [10, 10]})
    final = _xlsx(tmp_path / "b.xlsx", {"name": ["Alice", "Alice"], "amount": [10, 10]})
    steps = (
        StepResult(
            capability="spreadsheet.deduplicate",
            skill="excel.deduplicate",
            status="success",
            outputs=(first_out,),
            input_reference=source,
        ),
        StepResult(
            capability="spreadsheet.trim",
            skill="excel.trim",
            status="success",
            outputs=(final,),
            input_reference=first_out,
        ),
    )
    composite = CompositeVerifier(
        {
            "spreadsheet.deduplicate": SpreadsheetDedupeVerifier(),
            "spreadsheet.trim": _FakeVerifier(VerificationResult(passed=True, confidence=1.0)),
        }
    )

    result = composite(
        _request(execution_result=ExecutionResult(success=True, outputs=(final,), steps=steps))
    )

    assert [(s.capability, s.passed) for s in result.steps] == [
        ("spreadsheet.deduplicate", True),
        ("spreadsheet.trim", True),
        ("final_output", False),
    ]
    assert result.passed is False
    assert (
        "[final_output] final file: output has 2 row(s), 1 duplicate row(s) remaining"
        in result.findings
    )


def test_composite_verifier_skips_the_final_check_when_steps_did_not_chain() -> None:
    """Two steps that each read the original (the pre-chaining shape) have
    no single final file to check."""
    verifier = _FakeVerifier(VerificationResult(passed=True))
    steps = (
        StepResult(capability="cap.a", skill="a", status="success", outputs=("a.xlsx",)),
        StepResult(capability="cap.b", skill="b", status="success", outputs=("b.xlsx",)),
    )
    composite = CompositeVerifier({"cap.a": verifier, "cap.b": verifier})

    result = composite(
        _request(execution_result=ExecutionResult(success=True, outputs=("b.xlsx",), steps=steps))
    )

    assert [s.capability for s in result.steps] == ["cap.a", "cap.b"]


def test_composite_verifier_reports_a_skipped_step_without_verifying_it() -> None:
    verifier = _FakeVerifier(VerificationResult(passed=True))
    steps = (
        StepResult(capability="cap.a", skill="a", status="failure"),
        StepResult(capability="cap.b", skill="", status="skipped", summary="skipped because x"),
    )
    composite = CompositeVerifier({"cap.a": verifier, "cap.b": verifier})

    result = composite(
        _request(execution_result=ExecutionResult(success=False, steps=steps, stopped_at="cap.a"))
    )

    assert len(verifier.received_requests) == 1  # only cap.a was actually verified
    skipped = result.steps[1]
    assert (skipped.capability, skipped.passed) == ("cap.b", False)
    assert skipped.findings == ("skipped because an earlier step failed",)
    assert result.passed is False


# -- Steps Hands failed itself, and re-checks of the final file -------------------


def test_composite_verifier_relays_a_steps_failure_reason_without_verifying_it() -> None:
    """A step no Skill could run (or that wasn't confirmed) has no result to
    check: Inspector fails it with Hands' plain-words reason."""
    verifier = _FakeVerifier(VerificationResult(passed=True, findings=("ok",)))
    steps = (
        StepResult(capability="cap.a", skill="a", status="success", outputs=("a.xlsx",)),
        StepResult(
            capability="cap.b",
            skill="",
            status="failure",
            failure_reason="couldn't be run because no available skill can do it",
        ),
    )
    composite = CompositeVerifier({"cap.a": verifier, "cap.b": verifier})

    result = composite(
        _request(execution_result=ExecutionResult(success=False, steps=steps, stopped_at="cap.b"))
    )

    assert len(verifier.received_requests) == 1  # only cap.a
    assert result.passed is False
    assert (result.steps[1].capability, result.steps[1].passed) == ("cap.b", False)
    assert result.steps[1].findings == ("couldn't be run because no available skill can do it",)


def test_composite_verifier_does_not_fail_the_turn_on_a_failed_re_check() -> None:
    """M1: the chain's steps and the final file passed; only the re-check of
    the final file failed. The delivered file is still verified."""
    passing = _FakeVerifier(VerificationResult(passed=True, confidence=1.0, findings=("ok",)))
    failing = _FakeVerifier(
        VerificationResult(
            passed=False, confidence=1.0, findings=("the analysis reported failure",)
        )
    )
    steps = (
        StepResult(
            capability="cap.a",
            skill="a",
            status="success",
            outputs=("final.xlsx",),
            input_reference="orig.xlsx",
        ),
        StepResult(
            capability="cap.read",
            skill="r",
            status="failure",
            input_reference="final.xlsx",
            recheck=True,
        ),
    )
    composite = CompositeVerifier({"cap.a": passing, "cap.read": failing})

    result = composite(
        _request(
            execution_result=ExecutionResult(success=True, outputs=("final.xlsx",), steps=steps)
        )
    )

    assert result.passed is True
    recheck = result.steps[1]
    assert (recheck.passed, recheck.recheck, recheck.input_reference) == (
        False,
        True,
        "final.xlsx",
    )
    assert recheck.findings == ("the final file couldn't be re-checked after the changes",)


def test_composite_verifier_records_each_steps_input_reference() -> None:
    verifier = _FakeVerifier(VerificationResult(passed=True))
    step = StepResult(capability="cap.a", skill="a", status="success", input_reference="in.xlsx")

    result = CompositeVerifier({"cap.a": verifier})(
        _request(execution_result=ExecutionResult(success=True, steps=(step,)))
    )

    assert result.steps[0].input_reference == "in.xlsx"



def test_composite_verifier_does_not_fail_the_turn_on_a_failed_read_only_step() -> None:
    """R4: a READ-tier step on the original (proactive analysis) is outside
    the chain. Its failure is recorded but doesn't fail the turn."""
    passing = _FakeVerifier(VerificationResult(passed=True, confidence=1.0))
    failing = _FakeVerifier(VerificationResult(passed=False, confidence=1.0, findings=("x",)))
    steps = (
        StepResult(
            capability="cap.read",
            skill="r",
            status="failure",
            input_reference="orig.xlsx",
            permission_tier="read",
        ),
        StepResult(
            capability="cap.a",
            skill="a",
            status="success",
            outputs=("final.xlsx",),
            input_reference="orig.xlsx",
            permission_tier="write",
        ),
    )

    result = CompositeVerifier({"cap.a": passing, "cap.read": failing})(
        _request(
            execution_result=ExecutionResult(success=False, outputs=("final.xlsx",), steps=steps)
        )
    )

    assert result.passed is True
    assert (result.steps[0].passed, result.steps[0].read_only) == (False, True)


def test_composite_verifier_still_fails_a_turn_whose_only_step_is_a_failed_read() -> None:
    """When the read-only step is all there was (e.g. the user asked for an
    analysis), its failure is the turn's failure."""
    failing = _FakeVerifier(VerificationResult(passed=False, confidence=1.0, findings=("x",)))
    step = StepResult(
        capability="cap.read", skill="r", status="failure", permission_tier="read"
    )

    result = CompositeVerifier({"cap.read": failing})(
        _request(execution_result=ExecutionResult(success=False, steps=(step,)))
    )

    assert result.passed is False


# -- Measurements: counts the verifiers measured themselves (verified-outcome 2.1) ------

_SIX_ROWS = {
    # 2 exact duplicates (Alice, Bob) and 1 fully-empty row, mid-file.
    "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
    "amount": [10, 20, None, 10, 30, 20],
}


def test_dedupe_verifier_measures_what_it_confirmed(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", _SIX_ROWS)
    output = _xlsx(
        tmp_path / "out.xlsx",
        pd.read_excel(source).drop_duplicates().to_dict(orient="list"),
    )

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is True
    original, written = pd.read_excel(source), pd.read_excel(output)
    assert result.measurements == (
        StepMeasurement(
            target=DUPLICATE_ROWS_TARGET,
            output_rows=len(written),
            remaining=int(written.duplicated().sum()),
            input_rows=len(original),
            removed=int(original.dropna(how="all").duplicated().sum()),
        ),
    )
    assert (result.measurements[0].input_rows, result.measurements[0].removed) == (6, 2)
    assert (result.measurements[0].output_rows, result.measurements[0].remaining) == (4, 0)
    # The finding strings are unchanged.
    assert result.findings == ("output has 4 row(s), 0 duplicate row(s) remaining",)


def test_dedupe_verifier_measures_zero_removed_on_a_file_with_no_duplicates(
    tmp_path: Path,
) -> None:
    """N2's data: nothing to remove is measured as 0, never a guess."""
    rows = {"name": ["Alice", "Bob", "Carol"], "amount": [10, 20, 30]}
    source = _xlsx(tmp_path / "in.xlsx", rows)
    output = _xlsx(tmp_path / "out.xlsx", rows)

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is True
    assert result.measurements == (
        StepMeasurement(DUPLICATE_ROWS_TARGET, output_rows=3, remaining=0, input_rows=3, removed=0),
    )


def _dedupe_like_the_skill(source: str, tmp_path: Path, name: str = "out.xlsx") -> str:
    """What ``ExcelDeduplicateSkill`` writes: ``drop_duplicates`` (identical blank
    rows included), through a real .xlsx round trip."""
    return _xlsx(tmp_path / name, pd.read_excel(source).drop_duplicates().to_dict("list"))


def test_dedupe_verifier_counts_repeated_blank_rows_it_removed(tmp_path: Path) -> None:
    """H1, scenario BT: no duplicate data rows, but two identical blank rows.
    Dedupe removed one of them, so ``removed`` is 1, not 0."""
    source = _xlsx(
        tmp_path / "in.xlsx",
        {"name": ["Alice", None, "Bob", None, "Carol"], "amount": [1, None, 2, None, 3]},
    )
    output = _dedupe_like_the_skill(source, tmp_path)

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    original, written = pd.read_excel(source), pd.read_excel(output)
    assert (len(original), len(written)) == (5, 4)
    assert result.passed is True
    assert result.measurements == (
        StepMeasurement(
            DUPLICATE_ROWS_TARGET,
            output_rows=4,
            remaining=0,
            input_rows=5,
            removed=len(original) - len(written),
            removed_blank=1,
        ),
    )


def test_dedupe_verifier_counts_data_and_blank_duplicates_together(tmp_path: Path) -> None:
    """H1, scenario X-two-empty: 1 duplicate data row and 2 identical blank rows."""
    source = _xlsx(
        tmp_path / "in.xlsx",
        {
            "name": ["Alice", None, "Bob", "Alice", None, "Carol"],
            "amount": [1, None, 2, 1, None, 3],
        },
    )
    output = _dedupe_like_the_skill(source, tmp_path)

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    original, written = pd.read_excel(source), pd.read_excel(output)
    assert result.passed is True
    (measurement,) = result.measurements
    assert (measurement.input_rows, measurement.output_rows) == (6, 4)
    assert measurement.removed == len(original) - len(written) == 2
    assert (measurement.removed_blank, measurement.removed_data) == (1, 1)


def test_dedupe_verifier_gives_no_count_when_the_xlsx_round_trip_dropped_a_blank(
    tmp_path: Path,
) -> None:
    """The row the step kept last is blank, and the .xlsx round trip drops a
    trailing blank row. Rows in minus rows out (2) is then not what the step
    removed as duplicates (1), so the verifier still passes the file but
    confirms no count."""
    source = _xlsx(tmp_path / "in.xlsx", {"name": ["Alice", None, "Alice"], "amount": [1, None, 1]})
    output = _dedupe_like_the_skill(source, tmp_path)
    assert len(pd.read_excel(source)) - len(pd.read_excel(output)) == 2  # the round trip's doing

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is True
    (measurement,) = result.measurements
    assert (measurement.removed, measurement.removed_blank) == (None, 0)
    assert (measurement.input_rows, measurement.output_rows) == (3, 1)


def test_dedupe_verifier_gives_no_count_when_a_step_dropped_every_blank_row(
    tmp_path: Path,
) -> None:
    """A step that dropped both of two identical blank rows removed more than
    dedupe should have. The file passes the existing check (blank rows are left
    out of it), but the count isn't confirmed."""
    source = _xlsx(
        tmp_path / "in.xlsx",
        {"name": ["Alice", None, "Bob", None, "Carol"], "amount": [1, None, 2, None, 3]},
    )
    output = _xlsx(
        tmp_path / "out.xlsx",
        pd.read_excel(source).dropna(how="all").to_dict("list"),
    )

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output, source))

    assert result.passed is True
    assert result.measurements[0].removed is None


def test_empty_rows_verifier_measures_what_it_confirmed(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", _SIX_ROWS)
    output = _xlsx(tmp_path / "out.xlsx", pd.read_excel(source).dropna(how="all").to_dict("list"))

    result = SpreadsheetEmptyRowsVerifier()(
        _step_request("spreadsheet.remove_empty_rows", output, source)
    )

    assert result.passed is True
    original = pd.read_excel(source)
    assert result.measurements == (
        StepMeasurement(
            EMPTY_ROWS_TARGET,
            output_rows=5,
            remaining=0,
            input_rows=len(original),
            removed=int(original.isna().all(axis=1).sum()),
        ),
    )
    assert result.measurements[0].removed == 1


def test_a_failing_verifier_records_no_measurement(tmp_path: Path) -> None:
    source = _xlsx(tmp_path / "in.xlsx", _WITH_DUPES)
    dropped_a_row = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob"], "amount": [10, 20]})
    still_dupes = _xlsx(tmp_path / "dupes.xlsx", _WITH_DUPES)

    wrong_rows = SpreadsheetDedupeVerifier()(
        _step_request("spreadsheet.deduplicate", dropped_a_row, source)
    )
    dupes_left = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", still_dupes))
    empty_left = SpreadsheetEmptyRowsVerifier()(
        _step_request("spreadsheet.remove_empty_rows", _xlsx(tmp_path / "e.xlsx", _SIX_ROWS))
    )

    for result in (wrong_rows, dupes_left, empty_left):
        assert result.passed is False
        assert result.measurements == ()


def test_a_verifier_with_no_input_measures_only_the_output(tmp_path: Path) -> None:
    output = _xlsx(tmp_path / "out.xlsx", {"name": ["Alice", "Bob"], "amount": [10, 20]})

    result = SpreadsheetDedupeVerifier()(_step_request("spreadsheet.deduplicate", output))

    assert result.measurements == (
        StepMeasurement(DUPLICATE_ROWS_TARGET, output_rows=2, remaining=0),
    )


def test_composite_verifier_carries_each_steps_measurements_and_the_final_files(
    tmp_path: Path,
) -> None:
    source = _xlsx(tmp_path / "in.xlsx", _SIX_ROWS)
    first_out = _xlsx(tmp_path / "a.xlsx", pd.read_excel(source).drop_duplicates().to_dict("list"))
    final = _xlsx(tmp_path / "b.xlsx", pd.read_excel(first_out).dropna(how="all").to_dict("list"))
    steps = (
        StepResult(
            capability="spreadsheet.deduplicate",
            skill="excel.deduplicate",
            status="success",
            outputs=(first_out,),
            input_reference=source,
        ),
        StepResult(
            capability="spreadsheet.remove_empty_rows",
            skill="excel.remove_empty_rows",
            status="success",
            outputs=(final,),
            input_reference=first_out,
        ),
    )
    composite = CompositeVerifier(
        {
            "spreadsheet.deduplicate": SpreadsheetDedupeVerifier(),
            "spreadsheet.remove_empty_rows": SpreadsheetEmptyRowsVerifier(),
        }
    )

    result = composite(
        _request(execution_result=ExecutionResult(success=True, outputs=(final,), steps=steps))
    )

    assert result.passed is True
    dedupe, empty_rows, final_check = result.steps
    assert dedupe.measurements == (
        StepMeasurement(DUPLICATE_ROWS_TARGET, output_rows=4, remaining=0, input_rows=6, removed=2),
    )
    assert empty_rows.measurements == (
        StepMeasurement(EMPTY_ROWS_TARGET, output_rows=3, remaining=0, input_rows=4, removed=1),
    )
    # The final-file check reads only the final file: no "removed".
    written = pd.read_excel(final)
    assert final_check.measurements == (
        StepMeasurement(DUPLICATE_ROWS_TARGET, output_rows=len(written), remaining=0),
        StepMeasurement(EMPTY_ROWS_TARGET, output_rows=len(written), remaining=0),
    )
    # The aggregate carries them per step, not at the top level.
    assert result.measurements == ()


# -- ResidualMeasurer (verified-outcome design, Section 2.5) ------------------------------

_RESIDUAL_ROWS = {  # 5 rows: 2 exact duplicates (one pair plus a repeat), 1 fully-empty
    "name": ["Alice", "Bob", None, "Alice", "Bob"],
    "amount": [10, 20, None, 10, 20],
}


def _delivered(tmp_path: Path, rows: dict[str, list[object]] = _RESIDUAL_ROWS) -> str:
    path = tmp_path / "delivered.xlsx"
    pd.DataFrame(rows).to_excel(path, index=False)
    return str(path)


def _ledger(*actions: str) -> Understanding:
    return Understanding(
        intent="multi_step_request",
        requested_actions=tuple(RequestedAction(a, a, LEDGER_FROM_BRAIN, False) for a in actions),
    )


def _measure(
    understanding: Understanding,
    execution_result: ExecutionResult | None,
    inner: VerificationResult | None = None,
) -> VerificationResult:
    inner_result = inner if inner is not None else VerificationResult(passed=True, findings=("x",))
    return ResidualMeasurer(_FakeVerifier(inner_result))(
        _request(understanding=understanding, execution_result=execution_result)
    )


def test_residual_measurer_counts_each_requested_target_in_the_delivered_file(
    tmp_path: Path,
) -> None:
    delivered = _delivered(tmp_path)
    inner = VerificationResult(passed=True, confidence=0.9, findings=("kept",))

    result = _measure(
        _ledger("remove_empty_rows", "remove_duplicate_rows", "unsupported"),
        ExecutionResult(success=True, outputs=(delivered,)),
        inner,
    )

    fresh = pd.read_excel(delivered)
    assert result.residuals == (
        StepMeasurement(EMPTY_ROWS_TARGET, len(fresh), int(fresh.isna().all(axis=1).sum())),
        StepMeasurement(DUPLICATE_ROWS_TARGET, len(fresh), int(fresh.duplicated().sum())),
    )
    assert [(m.remaining, m.removed, m.input_rows) for m in result.residuals] == [
        (1, None, None),
        (2, None, None),
    ]
    # The verdict itself is the inner verifier's, unchanged.
    assert replace(result, residuals=()) == inner


def test_residual_measurer_uses_the_intent_when_brain_listed_nothing(tmp_path: Path) -> None:
    result = _measure(
        Understanding(intent="remove_empty_rows"),
        ExecutionResult(success=True, outputs=(_delivered(tmp_path),)),
    )

    assert [m.target for m in result.residuals] == [EMPTY_ROWS_TARGET]


def test_residual_measurer_measures_nothing_without_a_ledger(tmp_path: Path) -> None:
    result = _measure(
        Understanding(intent="multi_step_request"),
        ExecutionResult(success=True, outputs=(_delivered(tmp_path),)),
    )

    assert result.residuals == ()


def test_residual_measurer_never_measures_an_unverified_or_undelivered_turn(
    tmp_path: Path,
) -> None:
    delivered = _delivered(tmp_path)
    ledger = _ledger("remove_empty_rows")
    failed = VerificationResult(passed=False, findings=("no",))

    assert _measure(ledger, ExecutionResult(success=True, outputs=(delivered,)), failed) == failed
    assert _measure(ledger, ExecutionResult(success=True)).residuals == ()
    assert _measure(ledger, None).residuals == ()
    stopped = ExecutionResult(success=False, outputs=(delivered,), stopped_at="x")
    assert _measure(ledger, stopped).residuals == ()


def test_residual_measurer_claims_nothing_when_the_file_cant_be_read(tmp_path: Path) -> None:
    unreadable = tmp_path / "broken.xlsx"
    unreadable.write_text("not a workbook")
    inner = VerificationResult(passed=True, findings=("kept",))

    delivered = ExecutionResult(success=True, outputs=(str(unreadable),))

    result = _measure(_ledger("remove_empty_rows"), delivered, inner)

    assert result == inner  # no residuals, verdict unchanged


def test_residual_measurer_only_reads_the_delivered_file(tmp_path: Path) -> None:
    """Rule 2: the original is never opened, let alone written."""
    original = tmp_path / "original.xlsx"
    pd.DataFrame(_RESIDUAL_ROWS).to_excel(original, index=False)
    before = original.read_bytes()
    step = StepResult(
        capability="spreadsheet.deduplicate",
        skill="s",
        status="success",
        outputs=(_delivered(tmp_path, {"name": ["Alice"], "amount": [1]}),),
        input_reference=str(original),
        permission_tier="write",
    )

    result = _measure(
        _ledger("remove_duplicate_rows"),
        ExecutionResult(success=True, outputs=step.outputs, steps=(step,)),
    )

    assert result.residuals == (StepMeasurement(DUPLICATE_ROWS_TARGET, 1, 0),)
    assert original.read_bytes() == before
