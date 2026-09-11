"""Unit tests for core.engine."""

from __future__ import annotations

import pytest

from buddy.state import BuddyState
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


def test_engine_propagates_stage_exceptions() -> None:
    def failing_stage(state: BuddyState) -> BuddyState:
        raise RuntimeError("boom")

    def passthrough(state: BuddyState) -> BuddyState:
        return state

    cycle = CognitiveCycle(
        eyes=failing_stage,
        brain=passthrough,
        think=passthrough,
        planner=passthrough,
        delegate=passthrough,
        hands=passthrough,
        inspector=passthrough,
        voice=passthrough,
    )
    engine = Engine(cycle)
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")

    with pytest.raises(RuntimeError, match="boom"):
        engine.run(state)
