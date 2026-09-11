"""Unit tests for buddy.delegate."""

from __future__ import annotations

from buddy.delegate import Delegate, DelegationRequest, MechanicalDelegator, NullDelegator
from buddy.models import CapabilityRequest, PlanningStep
from buddy.state import BuddyState


def _make_state() -> BuddyState:
    return BuddyState.new(session_id="session-1", workspace_id="workspace-1")


def _request(**overrides: object) -> DelegationRequest:
    fields: dict[str, object] = {
        "planning": (),
        "uploaded_files": (),
        "active_file": None,
    }
    fields.update(overrides)
    return DelegationRequest(**fields)  # type: ignore[arg-type]


def _step(capability: str = "spreadsheet.deduplicate") -> PlanningStep:
    return PlanningStep(id="step-1", description="dedupe", capability=capability)


# -- NullDelegator ----------------------------------------------------------------


def test_null_delegator_produces_no_capability_requests() -> None:
    assert NullDelegator()(_request()) == ()


# -- MechanicalDelegator -----------------------------------------------------------


def test_mechanical_delegator_produces_one_request_per_step() -> None:
    request = _request(planning=(_step(),), active_file="report.xlsx")

    requests = MechanicalDelegator()(request)

    assert requests == (
        CapabilityRequest(
            capability="spreadsheet.deduplicate", target="report.xlsx", parameters={}
        ),
    )


def test_mechanical_delegator_falls_back_to_most_recent_upload() -> None:
    request = _request(
        planning=(_step(),), uploaded_files=("a.xlsx", "b.xlsx"), active_file=None
    )

    requests = MechanicalDelegator()(request)

    assert requests[0].target == "b.xlsx"


def test_mechanical_delegator_produces_nothing_without_a_target_file() -> None:
    request = _request(planning=(_step(),), uploaded_files=(), active_file=None)

    assert MechanicalDelegator()(request) == ()


def test_mechanical_delegator_never_references_a_skill_by_name() -> None:
    """The capability string passes through verbatim from the
    ``PlanningStep`` — ``MechanicalDelegator`` has no knowledge of
    ``skills.excel`` or any other concrete Skill.
    """
    request = _request(planning=(_step("some.other.capability"),), active_file="report.xlsx")

    requests = MechanicalDelegator()(request)

    assert requests[0].capability == "some.other.capability"


# -- Delegate stage -----------------------------------------------------------------


def test_delegate_sets_typed_delegation_field_and_mirrors_legacy_tasks() -> None:
    state = _make_state()
    state.planning = (_step(),)
    state.context.switch_active_file("report.xlsx")
    stage = Delegate(delegator=MechanicalDelegator())

    stage(state)

    assert state.delegation == (
        CapabilityRequest(
            capability="spreadsheet.deduplicate", target="report.xlsx", parameters={}
        ),
    )
    assert state.tasks == [
        {"capability": "spreadsheet.deduplicate", "target": "report.xlsx", "parameters": {}}
    ]


def test_delegate_defaults_to_null_delegator_when_none_configured() -> None:
    state = _make_state()
    stage = Delegate()

    stage(state)

    assert state.delegation == ()
    assert state.tasks == []
