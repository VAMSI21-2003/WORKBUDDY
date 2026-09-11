"""Unit tests for buddy.hands."""

from __future__ import annotations

from buddy.hands import Hands
from buddy.models import CapabilityRequest, ExecutionResult
from buddy.state import BuddyState
from core.dispatcher import Dispatcher
from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.registry import SkillRegistry

_DEFAULT_OUTPUTS = ["workspace/outputs/workspace-1/report_deduplicated.xlsx"]


class _FakeSkill(Skill):
    """A minimal Skill stand-in that records the tasks it was given."""

    def __init__(
        self,
        *,
        capability: str = "spreadsheet.deduplicate",
        status: SkillStatus = SkillStatus.SUCCESS,
        outputs: list[str] | None = None,
        errors: list[str] | None = None,
    ) -> None:
        self._capability = capability
        self._status = status
        self.outputs = outputs if outputs is not None else list(_DEFAULT_OUTPUTS)
        self._errors = errors if errors is not None else []
        self.received_tasks: list[SkillTask] = []

    @property
    def name(self) -> str:
        return "fake.skill"

    @property
    def description(self) -> str:
        return "A fake skill for testing Hands."

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        return (self._capability,)

    @property
    def permission_tier(self) -> PermissionTier:
        return PermissionTier.WRITE

    def can_handle(self, task: SkillTask) -> bool:
        return True

    def execute(self, task: SkillTask) -> SkillResult:
        self.received_tasks.append(task)
        return SkillResult(
            status=self._status,
            outputs=list(self.outputs),
            summary="did the fake thing",
            errors=list(self._errors),
        )

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        return result.status is SkillStatus.SUCCESS


def _make_state_with_delegation(*capability_requests: CapabilityRequest) -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.delegation = tuple(capability_requests)
    return state


def _dispatcher_with(skill: Skill) -> Dispatcher:
    registry = SkillRegistry()
    registry.register(skill)
    return Dispatcher(registry)


# -- no-op path ---------------------------------------------------------------


def test_hands_no_ops_when_nothing_delegated() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    stage = Hands(dispatcher=_dispatcher_with(_FakeSkill()))

    stage(state)

    assert state.execution_result == ExecutionResult(success=True, warnings=("nothing to execute",))
    assert state.outputs == []
    assert state.active_task is None


# -- successful dispatch + execution -------------------------------------------


def test_hands_dispatches_and_invokes_the_matching_skill() -> None:
    skill = _FakeSkill()
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert len(skill.received_tasks) == 1
    task = skill.received_tasks[0]
    assert task.input_references == ["uploads/report.xlsx"]
    assert task.permission is PermissionTier.WRITE
    assert task.session_id == "session-1"
    assert task.workspace_id == "workspace-1"

    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert state.execution_result.outputs == tuple(_DEFAULT_OUTPUTS)
    assert state.active_task is None  # cleared once execution finishes


def test_hands_mirrors_raw_results_into_legacy_outputs() -> None:
    skill = _FakeSkill()
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert len(state.outputs) == 1
    assert state.outputs[0]["skill"] == "fake.skill"
    assert state.outputs[0]["status"] == "success"


# -- failure paths ----------------------------------------------------------------


def test_hands_records_failure_when_no_skill_can_handle_the_capability() -> None:
    state = _make_state_with_delegation(
        CapabilityRequest(capability="some.other.capability", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(_FakeSkill(capability="spreadsheet.deduplicate")))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert state.execution_result.errors


def test_hands_records_failure_when_skill_reports_failure() -> None:
    skill = _FakeSkill(status=SkillStatus.FAILURE, outputs=[], errors=["could not read file"])
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert "could not read file" in state.execution_result.errors
