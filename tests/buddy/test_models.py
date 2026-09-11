"""Unit tests for buddy.models.

``Understanding`` and ``Reasoning`` are already exercised incidentally by
tests/buddy/test_understand.py and tests/buddy/test_think.py — this file
focuses on the models not otherwise covered: PlanningStep,
CapabilityRequest, ExecutionResult, StepResult, VerificationResult.
"""

from __future__ import annotations

import dataclasses

import pytest

from buddy.models import (
    CapabilityRequest,
    ExecutionResult,
    PlanningStep,
    StepResult,
    VerificationResult,
)

# -- PlanningStep -------------------------------------------------------------------


def test_planning_step_requires_id_description_capability() -> None:
    step = PlanningStep(id="step-1", description="dedupe", capability="spreadsheet.deduplicate")

    assert step.priority == 0
    assert step.dependencies == ()


def test_planning_step_is_frozen() -> None:
    step = PlanningStep(id="step-1", description="dedupe", capability="spreadsheet.deduplicate")

    with pytest.raises(dataclasses.FrozenInstanceError):
        step.priority = 5  # type: ignore[misc]


def test_planning_step_equality_is_by_value() -> None:
    a = PlanningStep(id="step-1", description="dedupe", capability="spreadsheet.deduplicate")
    b = PlanningStep(id="step-1", description="dedupe", capability="spreadsheet.deduplicate")

    assert a == b


def test_planning_step_supports_multiple_dependencies() -> None:
    step = PlanningStep(
        id="step-2",
        description="summarize",
        capability="doc.summarize",
        dependencies=("step-1", "step-0"),
    )

    assert step.dependencies == ("step-1", "step-0")


# -- CapabilityRequest --------------------------------------------------------------


def test_capability_request_defaults_to_empty_parameters() -> None:
    request = CapabilityRequest(capability="spreadsheet.deduplicate", target="report.xlsx")

    assert request.parameters == {}


def test_capability_request_default_parameters_are_independent_per_instance() -> None:
    a = CapabilityRequest(capability="cap.a", target="a.xlsx")
    b = CapabilityRequest(capability="cap.b", target="b.xlsx")

    a.parameters["x"] = 1

    assert a.parameters == {"x": 1}
    assert b.parameters == {}


def test_capability_request_is_frozen_but_its_dict_field_is_still_mutable() -> None:
    """``frozen=True`` blocks reassigning the ``parameters`` attribute
    itself, but does not deep-freeze the dict object it points to — an
    easy-to-miss nuance of Python dataclasses, worth pinning down with a
    test rather than assuming.
    """
    request = CapabilityRequest(capability="spreadsheet.deduplicate", target="report.xlsx")

    with pytest.raises(dataclasses.FrozenInstanceError):
        request.parameters = {"different": "dict"}  # type: ignore[misc]

    # Mutating the existing dict in place is, by contrast, not blocked.
    request.parameters["sheet"] = "Sheet1"
    assert request.parameters == {"sheet": "Sheet1"}


# -- ExecutionResult ------------------------------------------------------------------


def test_execution_result_requires_only_success() -> None:
    result = ExecutionResult(success=True)

    assert result.outputs == ()
    assert result.warnings == ()
    assert result.errors == ()
    assert result.duration == 0.0
    assert result.steps == ()


def test_execution_result_is_frozen() -> None:
    result = ExecutionResult(success=True)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.success = False  # type: ignore[misc]


def test_execution_result_can_represent_a_failure_with_no_outputs() -> None:
    result = ExecutionResult(success=False, errors=("could not read file",))

    assert result.success is False
    assert result.outputs == ()
    assert result.errors == ("could not read file",)


def test_execution_result_empty_outputs_tuple_is_falsy() -> None:
    """Several stages (Verify, Speak) branch on
    ``not execution_result.outputs`` — confirm the empty-tuple default
    actually behaves that way rather than assuming it.
    """
    result = ExecutionResult(success=True)

    assert not result.outputs


def test_execution_result_can_carry_a_per_step_breakdown() -> None:
    step = StepResult(
        capability="spreadsheet.deduplicate",
        skill="excel.deduplicate",
        status="success",
        outputs=("out.xlsx",),
        summary="did the thing",
    )
    result = ExecutionResult(success=True, outputs=("out.xlsx",), steps=(step,))

    assert result.steps == (step,)


# -- StepResult -----------------------------------------------------------------------


def _step_result(**overrides: object) -> StepResult:
    fields: dict[str, object] = {
        "capability": "spreadsheet.deduplicate",
        "skill": "excel.deduplicate",
        "status": "success",
    }
    fields.update(overrides)
    return StepResult(**fields)  # type: ignore[arg-type]


def test_step_result_requires_capability_skill_and_status() -> None:
    step = _step_result()

    assert step.outputs == ()
    assert step.summary == ""


def test_step_result_is_frozen() -> None:
    step = _step_result()

    with pytest.raises(dataclasses.FrozenInstanceError):
        step.status = "failure"  # type: ignore[misc]


def test_step_result_equality_is_by_value() -> None:
    a = _step_result()
    b = _step_result()

    assert a == b


# -- VerificationResult ---------------------------------------------------------------


def test_verification_result_requires_only_passed() -> None:
    result = VerificationResult(passed=True)

    assert result.confidence == 0.0
    assert result.findings == ()
    assert result.recommendations == ()


def test_verification_result_is_frozen() -> None:
    result = VerificationResult(passed=True)

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.passed = False  # type: ignore[misc]


def test_verification_result_can_represent_a_pass_with_no_findings() -> None:
    result = VerificationResult(passed=True, confidence=1.0)

    assert result.findings == ()
    assert result.recommendations == ()
