"""Unit tests for core.engine."""

from __future__ import annotations

import logging

import pytest

from buddy.state import BuddyState, StageFailure
from core.engine import CognitiveCycle, Engine


def _make_stage(name: str, calls: list[str]):
    def stage(state: BuddyState) -> BuddyState:
        calls.append(name)
        return state

    return stage


def test_cognitive_cycle_has_exactly_the_eight_documented_stage_slots() -> None:
    fields = {f.name for f in CognitiveCycle.__dataclass_fields__.values()}

    assert fields == {
        "eyes",
        "brain",
        "think",
        "planner",
        "delegate",
        "hands",
        "inspector",
        "voice",
    }


def test_cognitive_cycle_has_no_separate_dispatch_stage() -> None:
    """Per docs/WORKBUDDY_ARCHITECTURE.md Sections 4/5/11, the Dispatcher
    is not a pipeline stage — it never reads or writes BuddyState, and
    Hands is its only caller. There must be no ``dispatch`` slot here.
    """
    assert "dispatch" not in CognitiveCycle.__dataclass_fields__


def test_engine_runs_all_eight_stages_in_documented_order() -> None:
    calls: list[str] = []
    cycle = CognitiveCycle(
        eyes=_make_stage("eyes", calls),
        brain=_make_stage("brain", calls),
        think=_make_stage("think", calls),
        planner=_make_stage("planner", calls),
        delegate=_make_stage("delegate", calls),
        hands=_make_stage("hands", calls),
        inspector=_make_stage("inspector", calls),
        voice=_make_stage("voice", calls),
    )
    engine = Engine(cycle)
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    engine.run(state)

    assert calls == [
        "eyes",
        "brain",
        "think",
        "planner",
        "delegate",
        "hands",
        "inspector",
        "voice",
    ]


_STAGES = ("eyes", "brain", "think", "planner", "delegate", "hands", "inspector", "voice")


def _cycle(calls: list[str], **overrides) -> CognitiveCycle:  # type: ignore[no-untyped-def]
    stages = {name: _make_stage(name, calls) for name in _STAGES}
    stages.update(overrides)
    return CognitiveCycle(**stages)


def _raises(message: str = "boom: secret cell value 4111"):  # type: ignore[no-untyped-def]
    def stage(state: BuddyState) -> BuddyState:
        raise RuntimeError(message)

    return stage


@pytest.mark.parametrize("failing", [s for s in _STAGES if s != "voice"])
def test_a_stage_that_raises_sets_failure_skips_the_rest_and_still_runs_voice(
    failing: str,
) -> None:
    """R6 (docs/design/backend-api-design.md Section 4.2). This replaces the
    old contract, in which the Engine re-raised and the turn had no reply."""
    calls: list[str] = []
    engine = Engine(_cycle(calls, **{failing: _raises()}))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    returned = engine.run(state)

    assert returned is state
    assert state.failure == StageFailure(stage=failing, error_type="RuntimeError")
    before = list(_STAGES[: _STAGES.index(failing)])
    assert calls == before + ["voice"]


def test_a_failure_never_puts_the_exception_message_on_the_state() -> None:
    calls: list[str] = []
    engine = Engine(_cycle(calls, inspector=_raises("boom: secret cell value 4111")))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    engine.run(state)

    assert "secret" not in repr(state.failure)
    assert "4111" not in repr(state)


def test_the_failure_is_logged_with_its_traceback(caplog: pytest.LogCaptureFixture) -> None:
    engine = Engine(_cycle([], hands=_raises()))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    with caplog.at_level(logging.ERROR, logger="core.engine"):
        engine.run(state)

    assert any(record.exc_info is not None for record in caplog.records)


def test_voice_raising_still_propagates() -> None:
    """With no stage left to reply, the caller (the API's last-resort layer)
    handles it."""
    engine = Engine(_cycle([], voice=_raises("voice broke")))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    with pytest.raises(RuntimeError, match="voice broke"):
        engine.run(state)


def test_voice_raising_after_an_earlier_failure_still_propagates() -> None:
    engine = Engine(_cycle([], hands=_raises(), voice=_raises("voice broke")))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    with pytest.raises(RuntimeError, match="voice broke"):
        engine.run(state)
    assert state.failure == StageFailure(stage="hands", error_type="RuntimeError")


def test_a_stage_raising_a_non_runtime_error_records_its_type() -> None:
    def raises_key_error(state: BuddyState) -> BuddyState:
        raise KeyError("column")

    engine = Engine(_cycle([], inspector=raises_key_error))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    engine.run(state)

    assert state.failure == StageFailure(stage="inspector", error_type="KeyError")


def test_named_stages_match_the_cycle_slots_in_order() -> None:
    cycle = _cycle([])

    assert [name for name, _ in cycle.named_stages()] == list(_STAGES)
    assert [stage for _, stage in cycle.named_stages()] == list(cycle.stages())
