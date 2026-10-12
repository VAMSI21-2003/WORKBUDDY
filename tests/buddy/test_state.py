"""Unit tests for buddy.state."""

from __future__ import annotations

import copy
import dataclasses
from datetime import UTC, datetime

import pytest

from buddy.context import Context
from buddy.models import (
    FILE_KIND_COPY,
    CapabilityRequest,
    ExecutionResult,
    FileChoice,
    FileUsed,
    PlanningStep,
    StepMeasurement,
    StepResult,
    StepVerification,
    Understanding,
    VerificationResult,
)
from buddy.state import BuddyState, StageFailure
from buddy.think import Reasoning


def _make_state() -> BuddyState:
    return BuddyState.new(session_id="session-1", workspace_id="workspace-1")


def test_new_state_has_no_delegation_by_default() -> None:
    state = _make_state()

    assert state.delegation is None


def test_delegation_can_be_set_and_read_back() -> None:
    state = _make_state()
    requests = (
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )

    state.delegation = requests

    assert state.delegation == requests


def test_reset_transient_clears_delegation() -> None:
    state = _make_state()
    state.delegation = (
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )

    state.reset_transient()

    assert state.delegation is None


def test_reset_transient_preserves_context_but_clears_delegation() -> None:
    state = _make_state()
    state.context.add_uploaded_file("uploads/report.xlsx")
    state.delegation = (
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )

    state.reset_transient()

    # Context (session-level) survives a reset — only per-cycle fields clear.
    assert state.uploaded_files == ["uploads/report.xlsx"]
    assert state.delegation is None


def test_new_state_has_none_for_all_typed_stage_fields() -> None:
    state = _make_state()

    assert state.understanding is None
    assert state.reasoning is None
    assert state.planning is None
    assert state.delegation is None
    assert state.execution_result is None
    assert state.verification_result is None


# -- failure and turn_output_dir (R6, F5) -------------------------------------


def test_new_state_has_no_failure_and_no_turn_output_dir() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    assert state.failure is None
    assert state.turn_output_dir is None


def test_reset_transient_clears_failure_and_turn_output_dir() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.failure = StageFailure(stage="inspector", error_type="RuntimeError")
    state.turn_output_dir = "/data/outputs/session-1/t0001"

    state.reset_transient()

    assert state.failure is None
    assert state.turn_output_dir is None


def test_stage_failure_is_immutable() -> None:
    failure = StageFailure(stage="hands", error_type="ValueError")

    with pytest.raises(dataclasses.FrozenInstanceError):
        failure.stage = "voice"  # type: ignore[misc]


def _fully_populated_state() -> BuddyState:
    """A state with every field, and every part of its Context, set to a
    non-default value, mutable containers included."""
    context = Context(
        active_workspace="workspace-1",
        current_session="session-1",
        clock=lambda: datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
    )
    context.add_uploaded_file("uploads/report.xlsx")
    context.mark_analyzed("uploads/report.xlsx")
    context.switch_active_file("uploads/report.xlsx", document="doc", sheet="Sheet1")
    context.switch_active_task("task-1")
    context.set_current_topic("cleanup")
    context.set_mode("focus")
    context.set_metadata("nested", {"list": [1, 2]})
    context.record_user_message("remove duplicate rows")
    context.record_buddy_reply("an earlier reply")
    context.record_execution("ran dedupe")

    measurement = StepMeasurement(
        target="duplicate_rows", output_rows=3, remaining=0, input_rows=5, removed=2
    )
    state = BuddyState(context=context)
    state.user_message = "remove duplicate rows"
    state.observations = ["an observation"]
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    state.intent = "remove_duplicate_rows"
    state.confidence = 0.9
    state.reasoning = Reasoning(objective="dedupe", confidence=0.8)
    state.reasoning_trace = ["a reasoning step"]
    state.planning = (PlanningStep(id="1", description="dedupe", capability="c"),)
    state.plan = ["dedupe"]
    state.delegation = (
        CapabilityRequest(capability="c", target="uploads/report.xlsx", parameters={"k": [1]}),
    )
    state.tasks = [{"task_id": "task-1", "nested": {"a": [1]}}]
    state.active_task = {"task_id": "task-1"}
    state.execution_result = ExecutionResult(
        success=True,
        outputs=("out.xlsx",),
        steps=(StepResult(capability="c", skill="s", status="success", outputs=("out.xlsx",)),),
    )
    state.outputs = [{"task_id": "task-1", "outputs": ["out.xlsx"]}]
    state.verification_result = VerificationResult(
        passed=True,
        steps=(StepVerification(capability="c", passed=True, measurements=(measurement,)),),
        measurements=(measurement,),
    )
    state.reply = "a reply"
    state.failure = StageFailure(stage="inspector", error_type="RuntimeError")
    state.turn_output_dir = "/data/outputs/session-1/t0001"
    state.file_choice = FileChoice(original="/data/uploads/a.xlsx", copy="/data/outputs/b.xlsx")
    state.file_used = FileUsed(path="/data/outputs/b.xlsx", kind=FILE_KIND_COPY, copy_name="b.xlsx")
    state.file_refusal = "the workbook has 2 sheets, and only single-sheet workbooks are supported"
    state.turn_id = "t0001"
    state.pending_request = "remove duplicate rows"
    return state


def test_every_buddy_state_field_is_set_by_the_fully_populated_fixture() -> None:
    """Guards the copy test below: a new field must be added to the fixture."""
    state = _fully_populated_state()
    default = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    unset = [
        f.name
        for f in dataclasses.fields(BuddyState)
        if f.name != "context" and getattr(state, f.name) == getattr(default, f.name)
    ]
    assert unset == []


def test_a_deep_copy_of_a_fully_populated_state_is_equal_and_independent() -> None:
    """The backend runs each turn on a deep copy and commits it only if the
    turn finishes (backend-api-design.md Section 3.3). That needs a copy
    sharing no mutable part with the original."""
    original = _fully_populated_state()
    before = copy.deepcopy(original)

    copied = copy.deepcopy(original)

    for f in dataclasses.fields(BuddyState):
        assert getattr(copied, f.name) == getattr(original, f.name), f.name
    assert copied.context.recent_conversation() == original.context.recent_conversation()
    assert copied.context.recent_execution() == original.context.recent_execution()
    assert copied.context.analyzed_files == original.context.analyzed_files

    # Mutate every mutable part of the copy, through the same paths the
    # pipeline uses.
    copied.reset_transient()
    copied.context.add_uploaded_file("uploads/other.xlsx")
    copied.context.mark_analyzed("uploads/other.xlsx")
    copied.context.switch_active_file("uploads/other.xlsx")
    copied.context.set_metadata("new", 1)
    copied.context.metadata["nested"]["list"].append(3)
    copied.context.record_user_message("a second message")
    copied.context.record_execution("ran something else")

    for f in dataclasses.fields(BuddyState):
        if f.name != "context":
            assert getattr(original, f.name) == getattr(before, f.name), f.name
    assert original.context == before.context
    assert original.context.metadata == {"nested": {"list": [1, 2]}}
    assert original.context.recent_conversation() == before.context.recent_conversation()
    assert original.context.recent_execution() == before.context.recent_execution()
    assert original.context.analyzed_files == frozenset({"uploads/report.xlsx"})
