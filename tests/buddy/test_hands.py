"""Unit tests for buddy.hands."""

from __future__ import annotations

from buddy.hands import ConfirmationProvider, Hands, NullConfirmationProvider
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
        permission_tier: PermissionTier = PermissionTier.WRITE,
        verify_result: bool | None = None,
    ) -> None:
        self._capability = capability
        self._status = status
        self.outputs = outputs if outputs is not None else list(_DEFAULT_OUTPUTS)
        self._errors = errors if errors is not None else []
        self._permission_tier = permission_tier
        # None means "use the real, status-derived rule below"; a bool
        # forces verify() to that exact answer regardless of status, for
        # tests that need to isolate the well-formedness check itself.
        self._verify_result = verify_result
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
        return self._permission_tier

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
        if self._verify_result is not None:
            return self._verify_result
        return result.status is SkillStatus.SUCCESS


class _FakeConfirmationProvider:
    """A ``ConfirmationProvider`` stand-in that records every call it gets."""

    def __init__(self, *, approve: bool) -> None:
        self._approve = approve
        self.calls: list[tuple[SkillTask, PermissionTier]] = []

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        self.calls.append((task, tier))
        return self._approve


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


# -- permission tier is read from the skill, not hardcoded ---------------------


def test_hands_reads_the_selected_skills_own_declared_permission_tier() -> None:
    """Regression guard: the task's permission must come from whichever
    Skill the Dispatcher actually selected, not a value Hands assumes up
    front. A READ-tier skill proves this, since the old, hardcoded
    behavior would have produced WRITE for every task regardless.
    """
    skill = _FakeSkill(permission_tier=PermissionTier.READ)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert len(skill.received_tasks) == 1
    assert skill.received_tasks[0].permission is PermissionTier.READ


# -- confirmation gating for Write / External-Effect tiers ----------------------


def test_null_confirmation_provider_approves_unconditionally() -> None:
    task = SkillTask(task_id="t-1", session_id="s-1", workspace_id="w-1", description="d")
    provider: ConfirmationProvider = NullConfirmationProvider()

    assert provider.confirm(task, PermissionTier.EXTERNAL_EFFECT) is True


def test_hands_defaults_to_null_confirmation_provider_and_auto_approves() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.EXTERNAL_EFFECT)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))  # no confirmation_provider passed

    stage(state)

    assert len(skill.received_tasks) == 1
    assert state.execution_result is not None
    assert state.execution_result.success is True


def test_hands_does_not_ask_for_confirmation_on_a_read_tier_task() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.READ)
    confirmation = _FakeConfirmationProvider(approve=True)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill), confirmation_provider=confirmation)

    stage(state)

    assert confirmation.calls == []
    assert len(skill.received_tasks) == 1


def test_hands_asks_for_confirmation_on_a_write_tier_task_and_executes_when_approved() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.WRITE)
    confirmation = _FakeConfirmationProvider(approve=True)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill), confirmation_provider=confirmation)

    stage(state)

    assert len(confirmation.calls) == 1
    assert confirmation.calls[0][1] is PermissionTier.WRITE
    assert len(skill.received_tasks) == 1
    assert state.execution_result is not None
    assert state.execution_result.success is True


def test_hands_asks_for_confirmation_on_an_external_effect_tier_task() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.EXTERNAL_EFFECT)
    confirmation = _FakeConfirmationProvider(approve=True)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill), confirmation_provider=confirmation)

    stage(state)

    assert len(confirmation.calls) == 1
    assert confirmation.calls[0][1] is PermissionTier.EXTERNAL_EFFECT


def test_hands_skips_a_write_tier_task_cleanly_when_confirmation_is_declined() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.WRITE)
    confirmation = _FakeConfirmationProvider(approve=False)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill), confirmation_provider=confirmation)

    stage(state)

    assert skill.received_tasks == []  # the skill itself was never invoked
    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert state.execution_result.outputs == ()
    assert state.execution_result.errors
    assert state.active_task is None  # still cleared, same as any other turn


# -- Skill.verify() as a fail-fast check before Inspector ever runs -------------


def test_hands_discards_a_result_that_fails_the_skills_own_verify_check() -> None:
    skill = _FakeSkill(
        status=SkillStatus.SUCCESS,
        outputs=["workspace/outputs/workspace-1/suspicious.xlsx"],
        verify_result=False,
    )
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is False
    # The self-reported output must not survive to reach Inspector.
    assert state.execution_result.outputs == ()
    assert any("well-formedness" in error for error in state.execution_result.errors)


def test_hands_prefers_the_skills_own_structured_errors_over_a_generic_verify_message() -> None:
    """When a skill already reports FAILURE, its own specific error should
    still surface — even though this fake's ``verify()`` also happens to
    return False for any non-SUCCESS status, matching the real
    ``ExcelDeduplicateSkill.verify()``'s rule. The generic "failed its own
    well-formedness check" message must not replace or hide it.
    """
    skill = _FakeSkill(
        status=SkillStatus.FAILURE,
        outputs=[],
        errors=["could not read file"],
        verify_result=False,
    )
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert "could not read file" in state.execution_result.errors
    assert not any("well-formedness" in error for error in state.execution_result.errors)


def test_hands_keeps_a_successful_well_formed_result() -> None:
    skill = _FakeSkill(status=SkillStatus.SUCCESS, verify_result=True)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.success is True


# -- ExecutionResult.steps: the per-capability breakdown Verify/Voice need -------


def test_hands_records_one_step_result_per_successful_capability_request() -> None:
    skill = _FakeSkill(capability="spreadsheet.deduplicate")
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert len(state.execution_result.steps) == 1
    step = state.execution_result.steps[0]
    assert step.capability == "spreadsheet.deduplicate"
    assert step.skill == "fake.skill"
    assert step.status == "success"
    assert step.outputs == tuple(_DEFAULT_OUTPUTS)


def test_hands_records_a_failed_step_result_when_verify_check_fails() -> None:
    skill = _FakeSkill(status=SkillStatus.SUCCESS, verify_result=False)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    assert len(state.execution_result.steps) == 1
    step = state.execution_result.steps[0]
    assert step.capability == "spreadsheet.deduplicate"
    assert step.status == "failure"
    assert step.outputs == ()


def test_hands_records_no_step_result_when_dispatch_fails() -> None:
    """A request that never reached a Skill has nothing to report as a
    ``StepResult`` — it's represented purely by an ``errors`` entry.
    """
    state = _make_state_with_delegation(
        CapabilityRequest(capability="some.other.capability", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(_FakeSkill(capability="spreadsheet.deduplicate")))

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.steps == ()


def test_hands_records_no_step_result_when_confirmation_is_declined() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.WRITE)
    confirmation = _FakeConfirmationProvider(approve=False)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill), confirmation_provider=confirmation)

    stage(state)

    assert state.execution_result is not None
    assert state.execution_result.steps == ()


def test_hands_records_one_step_result_per_step_across_multiple_delegated_requests() -> None:
    skill = _FakeSkill(capability="spreadsheet.deduplicate")
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx"),
    )
    stage = Hands(dispatcher=_dispatcher_with(skill))

    stage(state)

    assert state.execution_result is not None
    steps = state.execution_result.steps
    assert len(steps) == 2
    assert all(step.capability == "spreadsheet.deduplicate" for step in steps)
