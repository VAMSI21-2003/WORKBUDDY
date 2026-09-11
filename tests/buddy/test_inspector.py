"""Unit tests for buddy.inspector."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from buddy.inspector import (
    CompositeVerifier,
    Inspector,
    NullVerifier,
    SpreadsheetDedupeVerifier,
    VerificationRequest,
)
from buddy.models import ExecutionResult, StepResult, Understanding, VerificationResult
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
