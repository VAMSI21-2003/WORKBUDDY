"""Unit tests for core.dispatcher."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from core.dispatcher import (
    Dispatcher,
    ExecutionRequest,
    NoCapableSkillError,
    SkillUnavailableError,
    select_first_available,
)
from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.registry import SkillRegistry


class _FakeSkill(Skill):
    """A configurable fake Skill for exercising Dispatcher's routing logic."""

    def __init__(
        self,
        name: str,
        *,
        capabilities: tuple[str, ...] = ("spreadsheet.deduplicate",),
        can_handle_fn: Callable[[SkillTask], bool] | None = None,
    ) -> None:
        self._name = name
        self._capabilities = capabilities
        self._can_handle_fn = can_handle_fn if can_handle_fn is not None else (lambda task: True)
        self.execute_calls: list[SkillTask] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return f"fake skill {self._name}"

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        return self._capabilities

    @property
    def permission_tier(self) -> PermissionTier:
        return PermissionTier.READ

    def can_handle(self, task: SkillTask) -> bool:
        return self._can_handle_fn(task)

    def execute(self, task: SkillTask) -> SkillResult:
        self.execute_calls.append(task)
        return SkillResult(status=SkillStatus.SUCCESS)

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        return True


def _task(task_id: str = "task-1") -> SkillTask:
    return SkillTask(
        task_id=task_id, session_id="session-1", workspace_id="workspace-1", description="d"
    )


# -- No capable skill registered at all ------------------------------------------------


def test_dispatch_raises_no_capable_skill_error_when_capability_is_unregistered() -> None:
    """Confirms the documented failure mode (docs/WORKBUDDY_ARCHITECTURE.md
    Section 11: "Raises clear, typed errors when no capable Skill
    exists"): a typed error, never a silent no-op that returns nothing.
    """
    registry = SkillRegistry()
    dispatcher = Dispatcher(registry)

    with pytest.raises(NoCapableSkillError):
        dispatcher.dispatch(_task(), input_type="spreadsheet.deduplicate")


def test_dispatch_raises_no_capable_skill_error_when_registry_is_empty() -> None:
    dispatcher = Dispatcher(SkillRegistry())

    with pytest.raises(NoCapableSkillError):
        dispatcher.dispatch(_task(), input_type="anything")


# -- can_handle() is respected, not just the capability string ------------------------


def test_dispatch_does_not_select_a_skill_whose_can_handle_returns_false() -> None:
    """The capability string matching (via ``supported_inputs``) is only
    step one — Dispatcher must also confirm the specific skill can
    actually handle *this* task via ``can_handle()``, per
    core/dispatcher.py's own ``select()`` docstring ("not just capable of
    the general category of work"). It must not dispatch to a skill just
    because the capability string matched.
    """
    registry = SkillRegistry()
    unwilling_skill = _FakeSkill("skill.unwilling", can_handle_fn=lambda task: False)
    registry.register(unwilling_skill)
    dispatcher = Dispatcher(registry)

    with pytest.raises(NoCapableSkillError):
        dispatcher.dispatch(_task(), input_type="spreadsheet.deduplicate")

    assert unwilling_skill.execute_calls == []


def test_dispatch_skips_an_unwilling_skill_in_favor_of_a_capable_one() -> None:
    registry = SkillRegistry()
    unwilling_skill = _FakeSkill("skill.unwilling", can_handle_fn=lambda task: False)
    capable_skill = _FakeSkill("skill.capable", can_handle_fn=lambda task: True)
    registry.register(unwilling_skill)
    registry.register(capable_skill)
    dispatcher = Dispatcher(registry)

    execution_request = dispatcher.dispatch(_task(), input_type="spreadsheet.deduplicate")

    assert execution_request.skill is capable_skill


# -- Selection strategy: deterministic, not accidental --------------------------------


def test_default_selection_strategy_is_registration_order_deterministically() -> None:
    """docs/WORKBUDDY_ARCHITECTURE.md Sections 11 and 18 require the
    Skill-selection policy to be a *swappable strategy*, not logic
    hard-coded into the Dispatcher — but neither section mandates a
    specific default algorithm; Section 18 explicitly lists priority
    ordering only as a *future* policy, not the current one. The actual
    default is documented directly on ``select_first_available``'s own
    docstring: "choose the first capable skill found." This test pins
    that down as genuine, deterministic behavior driven by registration
    order — not an accident of dict iteration or set ordering — so if it
    is ever right by coincidence rather than by design, this test would
    still catch a regression.
    """
    registry = SkillRegistry()
    first_registered = _FakeSkill("skill.first")
    second_registered = _FakeSkill("skill.second")
    registry.register(first_registered)
    registry.register(second_registered)
    dispatcher = Dispatcher(registry)

    execution_request = dispatcher.dispatch(_task(), input_type="spreadsheet.deduplicate")

    assert execution_request.skill is first_registered


def test_default_selection_strategy_is_stable_across_repeated_dispatches() -> None:
    """The same input always yields the same selection — not a random or
    unstable choice among equally-capable candidates.
    """
    registry = SkillRegistry()
    registry.register(_FakeSkill("skill.a"))
    registry.register(_FakeSkill("skill.b"))
    dispatcher = Dispatcher(registry)

    results = [
        dispatcher.dispatch(_task(f"task-{i}"), input_type="spreadsheet.deduplicate").skill.name
        for i in range(10)
    ]

    assert results == ["skill.a"] * 10


def test_selection_strategy_is_swappable() -> None:
    """Concretely confirms Section 18's extensibility claim: a different
    selection policy can be supplied without changing the Dispatcher's
    interface, and it is genuinely consulted rather than ignored (i.e.
    the Dispatcher doesn't quietly fall back to registration order
    regardless of what strategy was injected).
    """
    registry = SkillRegistry()
    first_registered = _FakeSkill("skill.first")
    second_registered = _FakeSkill("skill.second")
    registry.register(first_registered)
    registry.register(second_registered)

    def prefer_last(candidates: tuple[Skill, ...], task: SkillTask) -> Skill:
        return candidates[-1]

    dispatcher = Dispatcher(registry, selection_strategy=prefer_last)

    execution_request = dispatcher.dispatch(_task(), input_type="spreadsheet.deduplicate")

    assert execution_request.skill is second_registered


def test_select_first_available_returns_the_first_candidate() -> None:
    """Direct unit test of the strategy function itself, independent of
    the Dispatcher/Registry plumbing around it.
    """
    a = _FakeSkill("a")
    b = _FakeSkill("b")

    assert select_first_available((a, b), _task()) is a


# -- locate_by_capability / select -----------------------------------------------------


def test_locate_by_capability_returns_all_declaring_skills_regardless_of_can_handle() -> None:
    """``locate_by_capability`` is the raw Registry lookup by capability
    string — it does not itself filter by ``can_handle()``; that
    filtering happens one level up, in ``select()``. Confirmed here so
    the two layers of "is this skill usable" aren't conflated.
    """
    registry = SkillRegistry()
    unwilling_skill = _FakeSkill("skill.unwilling", can_handle_fn=lambda task: False)
    registry.register(unwilling_skill)
    dispatcher = Dispatcher(registry)

    assert dispatcher.locate_by_capability("spreadsheet.deduplicate") == (unwilling_skill,)


def test_select_raises_when_capability_unregistered() -> None:
    dispatcher = Dispatcher(SkillRegistry())

    with pytest.raises(NoCapableSkillError):
        dispatcher.select(_task(), "spreadsheet.deduplicate")


# -- dispatch_to (explicit skill selection) --------------------------------------------


def test_dispatch_to_returns_execution_request_for_a_capable_named_skill() -> None:
    registry = SkillRegistry()
    skill = _FakeSkill("skill.a")
    registry.register(skill)
    dispatcher = Dispatcher(registry)

    execution_request = dispatcher.dispatch_to("skill.a", _task())

    assert execution_request == ExecutionRequest(task=_task(), skill=skill)


def test_dispatch_to_raises_for_unregistered_skill_name() -> None:
    dispatcher = Dispatcher(SkillRegistry())

    with pytest.raises(SkillUnavailableError):
        dispatcher.dispatch_to("does-not-exist", _task())


def test_dispatch_to_raises_when_the_named_skill_cannot_handle_the_task() -> None:
    """Explicit selection by name still respects ``can_handle()`` — naming
    a skill directly is not a bypass of the capability contract.
    """
    registry = SkillRegistry()
    unwilling_skill = _FakeSkill("skill.unwilling", can_handle_fn=lambda task: False)
    registry.register(unwilling_skill)
    dispatcher = Dispatcher(registry)

    with pytest.raises(SkillUnavailableError):
        dispatcher.dispatch_to("skill.unwilling", _task())
