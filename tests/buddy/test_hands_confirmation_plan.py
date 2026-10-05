"""Hands tells a plan-aware confirmation provider the whole plan before any step runs.

Backend design "Decisions (2026-09-30)" item 2 and "Decisions (2026-10-01)"
item 9 (review C5): a ``CapabilityRequest`` carries no permission tier, so
the plan a user approves must be resolved through the Dispatcher, by Hands,
before the first step. These tests check that the plan Hands announces is
the plan it then runs, and that a step resolving differently at run time is
refused rather than approved unseen.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime

from backend.confirmation import APPROVE, WebConfirmationProvider
from buddy.hands import ConfirmationStep, Hands, NullConfirmationProvider
from buddy.models import DECLINED_STATUS, SKIPPED_STATUS, CapabilityRequest
from buddy.state import BuddyState
from core.dispatcher import Dispatcher
from skills.base import PermissionTier, Skill, SkillResult, SkillStatus, SkillTask
from skills.registry import SkillRegistry

TARGET = "uploads/report.xlsx"
ANALYZE = "spreadsheet.analyze"
DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
WRITE = PermissionTier.WRITE


class _Skill(Skill):
    """Writes ``<input>.<name>.xlsx``; records what it ran on, in a shared event log."""

    def __init__(
        self,
        name: str,
        capability: str,
        tier: PermissionTier,
        events: list[tuple[str, ...]],
        *,
        accepts: str | None = None,
    ) -> None:
        self._name = name
        self._capability = capability
        self._tier = tier
        self._events = events
        self._accepts = accepts

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def supported_inputs(self) -> tuple[str, ...]:
        return (self._capability,)

    @property
    def permission_tier(self) -> PermissionTier:
        return self._tier

    def can_handle(self, task: SkillTask) -> bool:
        return self._accepts is None or task.input_references[0] == self._accepts

    def execute(self, task: SkillTask) -> SkillResult:
        self._events.append(("execute", self._name, task.input_references[0]))
        output = task.input_references[0].removesuffix(".xlsx") + f".{self._name}.xlsx"
        return SkillResult(status=SkillStatus.SUCCESS, outputs=[output])

    def verify(self, task: SkillTask, result: SkillResult) -> bool:
        return result.status is SkillStatus.SUCCESS


class _RecordingProvider:
    """A plan-aware provider that approves, logging every call in the shared event log."""

    def __init__(self, events: list[tuple[str, ...]]) -> None:
        self._events = events
        self.plan: tuple[ConfirmationStep, ...] | None = None

    def expect_plan(self, steps: tuple[ConfirmationStep, ...]) -> None:
        self._events.append(("plan",))
        self.plan = steps

    def confirm(self, task: SkillTask, tier: PermissionTier) -> bool:
        self._events.append(("confirm", task.task_id, tier.value))
        return True


def _state(*capabilities: str) -> BuddyState:
    state = BuddyState.new(session_id="s1", workspace_id="w1")
    state.delegation = tuple(CapabilityRequest(capability=c, target=TARGET) for c in capabilities)
    return state


def _dispatcher(*skills: Skill) -> Dispatcher:
    registry = SkillRegistry()
    for skill in skills:
        registry.register(skill)
    return Dispatcher(registry)


def _three_skills(events: list[tuple[str, ...]]) -> Dispatcher:
    return _dispatcher(
        _Skill("analyze", ANALYZE, PermissionTier.READ, events),
        _Skill("dedupe", DEDUPE, WRITE, events),
        _Skill("empty", EMPTY, WRITE, events),
    )


def test_hands_announces_every_confirming_step_before_any_step_runs() -> None:
    events: list[tuple[str, ...]] = []
    provider = _RecordingProvider(events)
    hands = Hands(dispatcher=_three_skills(events), confirmation_provider=provider)
    state = _state(ANALYZE, DEDUPE, EMPTY)

    hands(state)

    assert events[0] == ("plan",)  # before the read-only analysis, before anything
    assert provider.plan == (
        ConfirmationStep("s1-task-1", DEDUPE, WRITE, TARGET),
        ConfirmationStep("s1-task-2", EMPTY, WRITE, TARGET),
    )
    # The steps Hands then asks about are exactly the plan's, in its order.
    asked = [(e[1], e[2]) for e in events if e[0] == "confirm"]
    assert asked == [(s.task_id, s.tier.value) for s in provider.plan]


def test_each_later_step_reads_the_previous_steps_output_as_the_plan_text_says() -> None:
    """The "Then … from the result of step k-1" wording matches the wiring."""
    events: list[tuple[str, ...]] = []
    provider = _RecordingProvider(events)
    hands = Hands(dispatcher=_three_skills(events), confirmation_provider=provider)
    state = _state(ANALYZE, DEDUPE, EMPTY)

    hands(state)

    assert state.execution_result is not None
    chain = [s for s in state.execution_result.steps if s.permission_tier == "write"]
    assert [s.capability for s in chain] == [
        DEDUPE,
        EMPTY,
    ]
    assert chain[0].input_reference == TARGET
    assert chain[1].input_reference == chain[0].outputs[0]


def test_a_plan_with_only_read_steps_asks_nothing() -> None:
    events: list[tuple[str, ...]] = []
    provider = _RecordingProvider(events)
    hands = Hands(dispatcher=_three_skills(events), confirmation_provider=provider)

    hands(_state(ANALYZE))

    assert provider.plan == ()
    assert not [e for e in events if e[0] == "confirm"]


def test_a_step_the_dispatcher_cant_route_ends_the_plan() -> None:
    """Hands stops the chain at an unroutable step, so nothing after it is listed."""
    events: list[tuple[str, ...]] = []
    provider = _RecordingProvider(events)
    hands = Hands(dispatcher=_three_skills(events), confirmation_provider=provider)

    hands(_state(DEDUPE, "spreadsheet.sort", EMPTY))

    assert provider.plan is not None
    assert [s.capability for s in provider.plan] == [DEDUPE]


def test_nothing_is_announced_when_nothing_was_delegated() -> None:
    events: list[tuple[str, ...]] = []
    provider = _RecordingProvider(events)

    Hands(dispatcher=_three_skills(events), confirmation_provider=provider)(_state())

    assert provider.plan is None and events == []


def test_a_provider_without_expect_plan_is_asked_per_step_as_before() -> None:
    events: list[tuple[str, ...]] = []
    provider = NullConfirmationProvider()
    hands = Hands(dispatcher=_three_skills(events), confirmation_provider=provider)
    state = _state(DEDUPE, EMPTY)

    hands(state)

    assert state.execution_result is not None
    assert len(state.execution_result.outputs) == 1
    assert [e[1] for e in events if e[0] == "execute"] == ["dedupe", "empty"]


class _Approver:
    """Answers the provider's published plan with ``approve`` from another thread."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.provider: WebConfirmationProvider | None = None
        self.published: list[object] = []

    def on_publish(self, plan: object) -> bool:
        self.published.append(plan)
        provider = self.provider
        assert provider is not None
        threading.Timer(
            0.05, provider.answer, args=(plan.confirmation_id, APPROVE)  # type: ignore[attr-defined]
        ).start()
        return True

    def on_settle(self, outcome: object) -> None:
        pass


def test_a_step_that_resolves_to_another_tier_at_run_time_is_refused_not_run() -> None:
    """The planner/executor mismatch guard, through real Hands and the real provider.

    Step 2's Write-tier skill accepts only the original file, so for the plan
    (which resolves every step on the delegated target) it's Write tier. At
    run time step 2 reads step 1's output, and the Dispatcher selects an
    External-Effect skill instead. That step was never shown to the user:
    it's refused, the chain stops, and nothing is handed back.
    """
    events: list[tuple[str, ...]] = []
    approver = _Approver()
    provider = WebConfirmationProvider(
        lock=approver.lock,
        timeout_s=5,
        clock=lambda: datetime.now(UTC),
        on_publish=approver.on_publish,  # type: ignore[arg-type]
        on_settle=approver.on_settle,  # type: ignore[arg-type]
    )
    approver.provider = provider
    dispatcher = _dispatcher(
        _Skill("dedupe", DEDUPE, WRITE, events),
        _Skill("picky", EMPTY, WRITE, events, accepts=TARGET),
        _Skill("anything", EMPTY, PermissionTier.EXTERNAL_EFFECT, events),
    )
    state = _state(DEDUPE, EMPTY)

    Hands(dispatcher=dispatcher, confirmation_provider=provider)(state)

    assert len(approver.published) == 1  # the plan (both Write tier) was approved once
    assert [e[1] for e in events if e[0] == "execute"] == ["dedupe"]  # never "anything"
    result = state.execution_result
    assert result is not None
    assert [s.status for s in result.steps] == ["success", DECLINED_STATUS]
    assert result.steps[1].permission_tier == PermissionTier.EXTERNAL_EFFECT.value
    assert result.stopped_at == EMPTY
    assert result.outputs == ()  # nothing handed back
    assert SKIPPED_STATUS not in [s.status for s in result.steps]
