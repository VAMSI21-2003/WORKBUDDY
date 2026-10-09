"""Unit tests for buddy.hands."""

from __future__ import annotations

import pytest

from buddy.hands import ConfirmationProvider, Hands, NullConfirmationProvider
from buddy.inspector import VerificationRequest
from buddy.models import CapabilityRequest, ExecutionResult, VerificationResult
from buddy.state import BuddyState
from buddy.text import count_noun
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


def test_hands_records_a_failed_step_result_when_dispatch_fails() -> None:
    """A request that never reached a Skill is still recorded as a failed
    step, so Inspector sees the step that stopped the chain."""
    state = _make_state_with_delegation(
        CapabilityRequest(capability="some.other.capability", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(_FakeSkill(capability="spreadsheet.deduplicate")))

    stage(state)

    assert state.execution_result is not None
    (step,) = state.execution_result.steps
    assert (step.capability, step.skill, step.status) == ("some.other.capability", "", "failure")
    assert step.failure_reason
    assert "some.other" not in step.failure_reason  # plain words, no capability id


def test_hands_records_a_declined_step_result_when_confirmation_is_declined() -> None:
    skill = _FakeSkill(permission_tier=PermissionTier.WRITE)
    confirmation = _FakeConfirmationProvider(approve=False)
    state = _make_state_with_delegation(
        CapabilityRequest(capability="spreadsheet.deduplicate", target="uploads/report.xlsx")
    )
    stage = Hands(dispatcher=_dispatcher_with(skill), confirmation_provider=confirmation)

    stage(state)

    assert state.execution_result is not None
    (step,) = state.execution_result.steps
    assert (step.capability, step.status, step.permission_tier) == (
        "spreadsheet.deduplicate",
        "declined",
        "write",
    )
    assert step.failure_reason


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


# -- Chaining: each file-changing step reads the previous step's output ---------


class _NamedSkill(_FakeSkill):
    """A ``_FakeSkill`` with its own name, so several can share one registry."""

    def __init__(self, name: str, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self._name = name

    @property
    def name(self) -> str:
        return self._name


class _FakeStepVerifier:
    """Fails any step whose capability is in ``failing``; records requests."""

    def __init__(self, *failing: str) -> None:
        self._failing = set(failing)
        self.requests: list[VerificationRequest] = []

    def __call__(self, request: VerificationRequest) -> VerificationResult:
        self.requests.append(request)
        assert request.execution_result is not None
        capability = request.execution_result.steps[0].capability
        if capability in self._failing:
            return VerificationResult(passed=False, findings=("2 rows missing",))
        return VerificationResult(passed=True)


_ORIGINAL = "uploads/report.xlsx"


def _chain_dispatcher(*skills: Skill) -> Dispatcher:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return Dispatcher(registry)


def _requests(*capabilities: str) -> BuddyState:
    return _make_state_with_delegation(
        *(CapabilityRequest(capability=c, target=_ORIGINAL) for c in capabilities)
    )


def _step_a() -> _NamedSkill:
    return _NamedSkill("skill.a", capability="cap.a", outputs=["out/a.xlsx"])


def _step_b() -> _NamedSkill:
    return _NamedSkill("skill.b", capability="cap.b", outputs=["out/b.xlsx"])


def _reader(**overrides: object) -> _NamedSkill:
    fields: dict[str, object] = {
        "capability": "cap.read",
        "outputs": [],
        "permission_tier": PermissionTier.READ,
    }
    fields.update(overrides)
    return _NamedSkill("skill.read", **fields)


def test_hands_feeds_each_chain_step_the_previous_steps_output() -> None:
    a, b = _step_a(), _step_b()
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b))(state)

    assert a.received_tasks[0].input_references == [_ORIGINAL]
    assert b.received_tasks[0].input_references == ["out/a.xlsx"]
    assert state.execution_result is not None
    steps = state.execution_result.steps
    assert [s.input_reference for s in steps] == [_ORIGINAL, "out/a.xlsx"]
    assert steps[0].outputs == ("out/a.xlsx",)  # the intermediate stays on its step


def test_hands_hands_back_only_the_final_file() -> None:
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(_step_a(), _step_b()))(state)

    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert state.execution_result.outputs == ("out/b.xlsx",)
    assert state.execution_result.stopped_at is None


def test_hands_keeps_read_tier_steps_outside_the_chain_on_the_original() -> None:
    """A READ-tier step (proactive analysis) reads the original even when
    it comes after a chain step, and never feeds the next step."""
    a, b, reader = _step_a(), _step_b(), _reader()
    state = _requests("cap.read", "cap.a", "cap.read", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b, reader))(state)

    # Both planned reads saw the original; after the chain completed, the
    # read-only check ran once more — on the final file.
    assert [t.input_references for t in reader.received_tasks] == [
        [_ORIGINAL],
        [_ORIGINAL],
        ["out/b.xlsx"],
    ]
    assert b.received_tasks[0].input_references == ["out/a.xlsx"]
    assert state.execution_result is not None
    assert state.execution_result.outputs == ("out/b.xlsx",)


@pytest.mark.parametrize(
    "outputs",
    [["out/chart.png"], [], ["out/a1.xlsx", "out/a2.xlsx"]],
    ids=["different-type", "none", "two"],
)
def test_hands_stops_the_chain_when_a_steps_output_cant_feed_the_next_step(
    outputs: list[str],
) -> None:
    """Continuing from the old input would silently drop this step's change
    from the final file, so the chain stops with a clear error instead."""
    a = _NamedSkill("skill.a", capability="cap.a", outputs=outputs)
    b = _step_b()
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b))(state)

    _assert_stopped_at_first_step(state, b, "'s output couldn't be carried forward")
    assert state.execution_result is not None
    failed = state.execution_result.steps[0]
    assert (failed.capability, failed.status) == ("cap.a", "failure")
    assert "couldn't be carried forward" in failed.failure_reason
    errors = state.execution_result.errors
    assert any(e.startswith(f"cap.a produced {count_noun(len(outputs), 'file')}") for e in errors)


def test_hands_stops_when_the_last_chain_steps_output_cant_be_handed_back() -> None:
    """Even for the last step: handing back the previous step's file would
    claim a change that isn't in it."""
    b = _NamedSkill("skill.b", capability="cap.b", outputs=["out/b.xlsx", "out/b.png"])
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(_step_a(), b))(state)

    result = state.execution_result
    assert result is not None
    assert result.success is False
    assert result.stopped_at == "cap.b"
    assert result.outputs == ()


# -- Final review F3: an .xls input with an .xlsx output carries the chain -------------

_XLS_ORIGINAL = "uploads/report.xls"


def _xls_requests(*capabilities: str) -> BuddyState:
    return _make_state_with_delegation(
        *(CapabilityRequest(capability=c, target=_XLS_ORIGINAL) for c in capabilities)
    )


class _SuffixSkill(_NamedSkill):
    """A ``_NamedSkill`` that only handles inputs ending in ``suffixes``."""

    def __init__(self, name: str, suffixes: tuple[str, ...], **kwargs: object) -> None:
        super().__init__(name, **kwargs)
        self._suffixes = suffixes

    def can_handle(self, task: SkillTask) -> bool:
        return task.input_references[0].endswith(self._suffixes)


def test_hands_hands_back_an_xlsx_written_from_an_xls_input() -> None:
    a = _SuffixSkill("skill.a", (".xlsx", ".xls"), capability="cap.a", outputs=["out/a.xlsx"])
    state = _xls_requests("cap.a")

    Hands(dispatcher=_chain_dispatcher(a))(state)

    result = state.execution_result
    assert result is not None
    assert (result.success, result.stopped_at, result.outputs) == (True, None, ("out/a.xlsx",))


def test_hands_chains_an_xls_input_through_an_xlsx_output() -> None:
    a = _SuffixSkill("skill.a", (".xlsx", ".xls"), capability="cap.a", outputs=["out/a.xlsx"])
    b = _SuffixSkill("skill.b", (".xlsx", ".xls"), capability="cap.b", outputs=["out/b.xlsx"])
    state = _xls_requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b))(state)

    assert b.received_tasks[0].input_references == ["out/a.xlsx"]
    result = state.execution_result
    assert result is not None
    assert (result.success, result.stopped_at, result.outputs) == (True, None, ("out/b.xlsx",))


def test_hands_stops_the_chain_when_the_next_skill_cant_read_the_output() -> None:
    a = _SuffixSkill("skill.a", (".xls",), capability="cap.a", outputs=["out/a.xlsx"])
    b = _SuffixSkill("skill.b", (".xls",), capability="cap.b", outputs=["out/b.xls"])
    state = _xls_requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b))(state)

    _assert_stopped_at_first_step(state, b, "'s output couldn't be carried forward")
    assert state.execution_result is not None
    assert state.execution_result.steps[0].failure_reason == (
        "produced a .xlsx file the next step can't read, so its result couldn't be carried forward"
    )


def test_hands_leaves_a_next_step_with_no_skill_to_stop_the_chain_itself() -> None:
    """The step with no skill is still what stops the chain. Since PR-3's
    review M1, the chain can't finish, so its earlier step never starts."""
    a = _step_a()
    state = _requests("cap.a", "cap.missing")

    Hands(dispatcher=_chain_dispatcher(a))(state)

    result = state.execution_result
    assert result is not None
    assert result.stopped_at == "cap.missing"
    assert [(s.capability, s.status) for s in result.steps] == [
        ("cap.a", "skipped"),
        ("cap.missing", "failure"),
    ]
    assert a.received_tasks == []


def _assert_stopped_at_first_step(state: BuddyState, b: _NamedSkill, reason: str) -> None:
    # "reason" follows the capability directly, e.g. "cap.a did not complete"
    # or "cap.a's output couldn't be carried forward".
    assert b.received_tasks == []  # the second step never ran
    result = state.execution_result
    assert result is not None
    assert result.success is False
    assert result.outputs == ()  # nothing handed back
    assert result.stopped_at == "cap.a"
    assert (result.steps[-1].capability, result.steps[-1].status) == ("cap.b", "skipped")
    sep = "" if reason.startswith("'") else " "
    assert result.errors[-1] == (
        f"stopped: cap.a{sep}{reason}; 1 later step was skipped and no file was handed back"
    )
    # The step that stopped the chain is always recorded, so Inspector sees it.
    assert result.steps[0].capability == "cap.a"


def test_hands_stops_the_chain_when_a_step_reports_failure() -> None:
    a = _NamedSkill(
        "skill.a", capability="cap.a", status=SkillStatus.FAILURE, outputs=[], errors=["boom"]
    )
    b = _step_b()
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b))(state)

    _assert_stopped_at_first_step(state, b, "did not complete")
    assert state.execution_result is not None
    assert "boom" in state.execution_result.errors


def test_hands_records_a_failing_chain_steps_own_errors_as_its_failure_reason() -> None:
    """Backlog 1a: the Skill's reason (e.g. a refused multi-sheet workbook)
    is what Inspector relays, instead of "did not produce an output file"."""
    a = _NamedSkill(
        "skill.a",
        capability="cap.a",
        status=SkillStatus.FAILURE,
        outputs=[],
        errors=["the workbook has 2 sheets", "second reason"],
    )
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, _step_b()))(state)

    assert state.execution_result is not None
    assert state.execution_result.steps[0].failure_reason == (
        "the workbook has 2 sheets; second reason"
    )


def test_hands_leaves_the_failure_reason_empty_without_skill_errors_or_off_the_chain() -> None:
    silent = _NamedSkill("skill.a", capability="cap.a", status=SkillStatus.FAILURE, outputs=[])
    reader = _reader(status=SkillStatus.FAILURE, errors=["analysis failed"])
    state = _requests("cap.read", "cap.a")

    Hands(dispatcher=_chain_dispatcher(reader, silent))(state)

    assert state.execution_result is not None
    assert [s.failure_reason for s in state.execution_result.steps] == ["", ""]


def test_hands_stops_the_chain_when_a_step_fails_its_well_formedness_check() -> None:
    a = _NamedSkill("skill.a", capability="cap.a", outputs=["out/a.xlsx"], verify_result=False)
    b = _step_b()
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b))(state)

    _assert_stopped_at_first_step(state, b, "produced a malformed result")


def test_hands_stops_the_chain_when_confirmation_is_declined() -> None:
    a, b = _step_a(), _step_b()
    state = _requests("cap.a", "cap.b")

    Hands(
        dispatcher=_chain_dispatcher(a, b),
        confirmation_provider=_FakeConfirmationProvider(approve=False),
    )(state)

    assert a.received_tasks == []
    _assert_stopped_at_first_step(state, b, "was not confirmed")
    assert state.execution_result is not None
    assert state.execution_result.steps[0].status == "declined"


def test_hands_stops_the_chain_when_a_step_cant_be_dispatched() -> None:
    b = _step_b()
    state = _requests("cap.a", "cap.b")  # nothing registered for cap.a

    Hands(dispatcher=_chain_dispatcher(b))(state)

    _assert_stopped_at_first_step(state, b, "could not be run")
    assert state.execution_result is not None
    assert state.execution_result.steps[0].status == "failure"


def test_hands_records_the_failing_last_step_when_the_chain_cant_finish() -> None:
    """The B1 shape: the last step can't be dispatched. The last step must
    still be recorded, or Inspector only sees steps that didn't fail. Since
    PR-3's review M1, step 1 doesn't run either: the chain can't finish."""
    a = _step_a()
    state = _requests("cap.a", "cap.b")  # nothing registered for cap.b

    Hands(dispatcher=_chain_dispatcher(a))(state)

    result = state.execution_result
    assert result is not None
    assert result.outputs == ()
    assert result.stopped_at == "cap.b"
    assert [(s.capability, s.status) for s in result.steps] == [
        ("cap.a", "skipped"),
        ("cap.b", "failure"),
    ]
    assert a.received_tasks == []
    assert result.errors[-1] == (
        "stopped: cap.b could not be run; 1 earlier step wasn't started "
        "and no file was handed back"
    )


def test_hands_records_a_declined_last_step_after_an_earlier_step_passed() -> None:
    class _DeclineB:
        def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
            return "cap.b" not in task.description

    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(_step_a(), _step_b()), confirmation_provider=_DeclineB())(
        state
    )

    result = state.execution_result
    assert result is not None
    assert result.outputs == ()
    assert result.stopped_at == "cap.b"
    assert [(s.capability, s.status) for s in result.steps] == [
        ("cap.a", "success"),
        ("cap.b", "declined"),
    ]


def test_hands_stops_the_chain_when_a_step_fails_verification() -> None:
    a, b = _step_a(), _step_b()
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(a, b), step_verifier=_FakeStepVerifier("cap.a"))(state)

    _assert_stopped_at_first_step(state, b, "failed verification")
    assert state.execution_result is not None
    assert "cap.a failed verification: 2 rows missing" in state.execution_result.errors


def test_hands_verifies_each_chain_step_against_its_own_input_before_continuing() -> None:
    verifier = _FakeStepVerifier()
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(_step_a(), _step_b()), step_verifier=verifier)(state)

    checked = []
    for request in verifier.requests:
        assert request.execution_result is not None
        checked.append(request.execution_result.steps[0])
    assert [(s.capability, s.input_reference) for s in checked] == [
        ("cap.a", _ORIGINAL),
        ("cap.b", "out/a.xlsx"),
    ]
    assert state.execution_result is not None
    assert state.execution_result.outputs == ("out/b.xlsx",)


def test_hands_does_not_verify_read_tier_steps_in_the_loop() -> None:
    verifier = _FakeStepVerifier("cap.read")
    state = _requests("cap.read")

    Hands(dispatcher=_chain_dispatcher(_reader()), step_verifier=verifier)(state)

    assert verifier.requests == []


def test_hands_does_not_stop_the_chain_when_a_read_tier_step_fails() -> None:
    reader = _reader(status=SkillStatus.FAILURE, errors=["analysis failed"])
    a, b = _step_a(), _step_b()
    state = _requests("cap.read", "cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(reader, a, b))(state)

    assert b.received_tasks[0].input_references == ["out/a.xlsx"]
    result = state.execution_result
    assert result is not None
    assert result.stopped_at is None
    assert result.outputs == ("out/b.xlsx",)
    assert "analysis failed" in result.errors  # still reported, just not a chain stop


def test_hands_does_not_re_check_a_final_file_when_the_chain_stopped() -> None:
    reader = _reader()
    a = _NamedSkill(
        "skill.a", capability="cap.a", status=SkillStatus.FAILURE, outputs=[], errors=["boom"]
    )
    state = _requests("cap.read", "cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(reader, a, _step_b()))(state)

    assert [t.input_references for t in reader.received_tasks] == [[_ORIGINAL]]  # no re-check


class _FailsOnSecondRun(_NamedSkill):
    """A READ-tier skill that succeeds on the original, then fails."""

    def execute(self, task: SkillTask) -> SkillResult:
        result = super().execute(task)
        if len(self.received_tasks) > 1:
            return SkillResult(status=SkillStatus.FAILURE, errors=["could not read it"])
        return result


def test_hands_records_a_failed_final_file_re_check_as_a_warning_not_an_error() -> None:
    """The re-check is outside the chain: its failure never costs the user
    the file they asked for."""
    reader = _FailsOnSecondRun(
        "skill.read", capability="cap.read", outputs=[], permission_tier=PermissionTier.READ
    )
    state = _requests("cap.read", "cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(reader, _step_a(), _step_b()))(state)

    result = state.execution_result
    assert result is not None
    assert result.success is True
    assert result.errors == ()
    assert result.outputs == ("out/b.xlsx",)
    assert result.warnings == ("could not re-check the final file with cap.read",)
    recheck = result.steps[-1]
    assert (recheck.capability, recheck.status, recheck.input_reference, recheck.recheck) == (
        "cap.read",
        "failure",
        "out/b.xlsx",
        True,
    )
    assert not any(s.recheck for s in result.steps[:-1])


# -- Per-turn output folder (F5) and the stopped-chain assumption (R6) --------


def test_hands_copies_the_turns_output_dir_onto_every_task() -> None:
    a, b, reader = _step_a(), _step_b(), _reader()
    state = _requests("cap.read", "cap.a", "cap.b")
    state.turn_output_dir = "/data/outputs/session-1/t0003"

    Hands(dispatcher=_chain_dispatcher(a, b, reader))(state)

    tasks = [*reader.received_tasks, *a.received_tasks, *b.received_tasks]
    assert len(tasks) == 4  # the read ran on the original and on the final file
    assert {t.output_dir for t in tasks} == {"/data/outputs/session-1/t0003"}


def test_hands_leaves_output_dir_unset_when_the_turn_has_none() -> None:
    a = _step_a()
    state = _requests("cap.a")

    Hands(dispatcher=_chain_dispatcher(a))(state)

    assert a.received_tasks[0].output_dir is None


class _DeclineCapB:
    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        return "cap.b" not in task.description


@pytest.mark.parametrize(
    "stop",
    ["not-dispatchable", "declined", "skill-failure", "malformed", "failed-verification"],
)
def test_a_chain_stopped_after_an_earlier_step_passed_hands_back_no_file(stop: str) -> None:
    """backend-api-design.md Section 5.3 assumes ``ExecutionResult.outputs``
    is empty whenever a chain stops ("then nothing is handed back"). The
    first step produced a real intermediate file each time; none of it may
    be listed as an output."""
    a = _step_a()
    b_fields: dict[str, object] = {"capability": "cap.b", "outputs": ["out/b.xlsx"]}
    if stop == "skill-failure":
        b_fields["status"] = SkillStatus.FAILURE
    if stop == "malformed":
        b_fields["verify_result"] = False
    skills: list[Skill] = [a]
    if stop != "not-dispatchable":
        skills.append(_NamedSkill("skill.b", **b_fields))
    kwargs: dict[str, object] = {}
    if stop == "declined":
        kwargs["confirmation_provider"] = _DeclineCapB()
    if stop == "failed-verification":
        kwargs["step_verifier"] = _FakeStepVerifier("cap.b")
    state = _requests("cap.a", "cap.b")

    Hands(dispatcher=_chain_dispatcher(*skills), **kwargs)(state)  # type: ignore[arg-type]

    result = state.execution_result
    assert result is not None
    if stop == "not-dispatchable":
        # Known before anything ran (PR-3 review M1): step 1 never starts.
        assert result.steps[0].status == "skipped" and a.received_tasks == []
    else:
        assert result.steps[0].outputs == ("out/a.xlsx",)  # the intermediate existed
    assert result.stopped_at == "cap.b"
    assert result.outputs == ()
