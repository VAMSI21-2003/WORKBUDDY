"""Unit tests for buddy.planner."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pandas as pd
import pytest

from buddy.llm import LLMMessage
from buddy.models import PlanningStep, Understanding
from buddy.planner import (
    ClaudePlanner,
    CompositePlanner,
    NullPlanner,
    Planner,
    PlanningRequest,
    ProactiveAnalysisPlanner,
    RuleBasedPlanner,
)
from buddy.state import BuddyState
from buddy.think import Reasoning
from tests import workbook_fixtures as fixtures


class _FakeLLMProvider:
    """A minimal ``LLMProvider`` stand-in that returns a canned reply."""

    def __init__(self, reply: str | None = None, *, raises: Exception | None = None) -> None:
        self._reply = reply
        self._raises = raises
        self.last_system: str | None = None
        self.last_messages: tuple[LLMMessage, ...] | None = None

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.last_system = system
        self.last_messages = messages
        if self._raises is not None:
            raise self._raises
        assert self._reply is not None
        return self._reply


def _make_state() -> BuddyState:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file("report.xlsx")
    return state


def _request(**overrides: object) -> PlanningRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(),
        "reasoning": Reasoning(),
        "uploaded_files": (),
        "active_file": None,
        "user_message": "",
    }
    fields.update(overrides)
    return PlanningRequest(**fields)  # type: ignore[arg-type]


# -- NullPlanner ----------------------------------------------------------------


def test_null_planner_produces_no_steps() -> None:
    assert NullPlanner()(_request()) == ()


# -- RuleBasedPlanner -------------------------------------------------------------


def test_rule_based_planner_produces_step_for_known_intent() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False, confidence=0.9),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    steps = RuleBasedPlanner()(request)

    assert len(steps) == 1
    assert steps[0].capability == "spreadsheet.deduplicate"
    assert "report.xlsx" in steps[0].description


def test_rule_based_planner_maps_remove_empty_rows() -> None:
    request = _request(
        understanding=Understanding(intent="remove_empty_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False, confidence=0.9),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    steps = RuleBasedPlanner()(request)

    assert [s.capability for s in steps] == ["spreadsheet.remove_empty_rows"]


def test_rule_based_planner_produces_nothing_for_unknown_intent() -> None:
    request = _request(
        understanding=Understanding(intent="unclear", confidence=0.1),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    assert RuleBasedPlanner()(request) == ()


def test_rule_based_planner_produces_nothing_when_clarification_required() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=True),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    assert RuleBasedPlanner()(request) == ()


def test_rule_based_planner_produces_nothing_without_a_file() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=(),
        active_file=None,
    )

    assert RuleBasedPlanner()(request) == ()


def test_rule_based_planner_falls_back_to_most_recent_upload() -> None:
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=("first.xlsx", "second.xlsx"),
        active_file=None,
    )

    steps = RuleBasedPlanner()(request)

    assert len(steps) == 1
    assert "second.xlsx" in steps[0].description


def test_rule_based_planner_ignores_multi_step_intent() -> None:
    """``RuleBasedPlanner`` has no entry for ``"multi_step_request"`` in
    its intent->capability mapping, so it correctly produces nothing for
    it — ``ClaudePlanner`` is the strategy that handles this intent.
    """
    request = _request(
        understanding=Understanding(intent="multi_step_request", confidence=0.9),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    assert RuleBasedPlanner()(request) == ()


# -- ClaudePlanner ------------------------------------------------------------------


def _multi_step_request(**overrides: object) -> PlanningRequest:
    fields: dict[str, object] = {
        "understanding": Understanding(intent="multi_step_request", confidence=0.9),
        "reasoning": Reasoning(clarification_required=False, objective="Do several things."),
        "uploaded_files": ("report.xlsx",),
        "active_file": "report.xlsx",
        "user_message": "check if this column exists, remove it, and give me a report and a dashboard",
    }
    fields.update(overrides)
    return _request(**fields)


def test_claude_planner_only_engages_for_multi_step_intent_with_no_llm_call() -> None:
    provider = _FakeLLMProvider()
    planner = ClaudePlanner(provider, known_capabilities=())

    steps = planner(
        _request(
            understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
            reasoning=Reasoning(clarification_required=False),
            uploaded_files=("report.xlsx",),
            active_file="report.xlsx",
        )
    )

    assert steps == ()
    assert provider.last_messages is None  # never called


def test_claude_planner_produces_nothing_when_clarification_required() -> None:
    provider = _FakeLLMProvider()
    planner = ClaudePlanner(provider, known_capabilities=())

    steps = planner(_multi_step_request(reasoning=Reasoning(clarification_required=True)))

    assert steps == ()
    assert provider.last_messages is None


def test_claude_planner_produces_nothing_without_a_target_file() -> None:
    provider = _FakeLLMProvider()
    planner = ClaudePlanner(provider, known_capabilities=())

    steps = planner(_multi_step_request(uploaded_files=(), active_file=None))

    assert steps == ()
    assert provider.last_messages is None


def test_claude_planner_parses_multiple_ordered_steps_with_dependencies() -> None:
    reply = json.dumps(
        {
            "steps": [
                {
                    "description": "Check whether the column exists",
                    "capability": "spreadsheet.column_check",
                    "depends_on_step_numbers": [],
                },
                {
                    "description": "Remove the column",
                    "capability": "spreadsheet.remove_column",
                    "depends_on_step_numbers": [1],
                },
                {
                    "description": "Generate a report",
                    "capability": "spreadsheet.report",
                    "depends_on_step_numbers": [2],
                },
            ]
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    planner = ClaudePlanner(
        provider, known_capabilities=(("spreadsheet.deduplicate", "Removes duplicate rows."),)
    )

    steps = planner(_multi_step_request())

    assert len(steps) == 3
    assert [s.capability for s in steps] == [
        "spreadsheet.column_check",
        "spreadsheet.remove_column",
        "spreadsheet.report",
    ]
    assert [s.priority for s in steps] == [0, 1, 2]
    assert steps[0].dependencies == ()
    assert steps[1].dependencies == ("step-1",)
    assert steps[2].dependencies == ("step-2",)


def test_claude_planner_never_drops_a_step_with_an_unmapped_capability() -> None:
    """Per the design's honesty requirement: a step whose capability has
    no matching real skill is still produced, not silently omitted.
    """
    reply = json.dumps(
        {
            "steps": [
                {
                    "description": "Build a dashboard",
                    "capability": "spreadsheet.dashboard",
                    "depends_on_step_numbers": [],
                }
            ]
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    planner = ClaudePlanner(
        provider, known_capabilities=(("spreadsheet.deduplicate", "Removes duplicate rows."),)
    )

    steps = planner(_multi_step_request())

    assert len(steps) == 1
    assert steps[0].capability == "spreadsheet.dashboard"


def test_claude_planner_prompt_lists_known_capabilities_and_raw_message() -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"steps": []}))
    planner = ClaudePlanner(
        provider,
        known_capabilities=(("spreadsheet.deduplicate", "Removes duplicate rows."),),
    )

    planner(_multi_step_request())

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "check if this column exists" in prompt
    assert "spreadsheet.deduplicate: Removes duplicate rows." in prompt
    assert "Target file: report.xlsx" in prompt


def test_claude_planner_prompt_never_contains_file_content() -> None:
    """The prompt is built solely from the message text, classified
    intent, Think's own conclusion, the target file, and the
    known-capabilities snapshot — never from opening the uploaded file,
    since nothing in ``PlanningRequest`` or ``ClaudePlanner`` ever reads
    it.
    """
    provider = _FakeLLMProvider(reply=json.dumps({"steps": []}))
    planner = ClaudePlanner(provider, known_capabilities=())

    planner(_multi_step_request())

    assert provider.last_messages is not None
    prompt = provider.last_messages[0].content
    assert "Alice" not in prompt  # a stand-in for "no spreadsheet cell data ever appears"


def test_claude_planner_falls_back_to_empty_on_invalid_json() -> None:
    provider = _FakeLLMProvider(reply="not json")
    planner = ClaudePlanner(provider, known_capabilities=())

    assert planner(_multi_step_request()) == ()


def test_claude_planner_falls_back_to_empty_on_unexpected_shape() -> None:
    provider = _FakeLLMProvider(reply=json.dumps({"steps": [{"description": "no capability"}]}))
    planner = ClaudePlanner(provider, known_capabilities=())

    assert planner(_multi_step_request()) == ()


def test_claude_planner_falls_back_to_empty_on_provider_failure() -> None:
    provider = _FakeLLMProvider(raises=RuntimeError("network error"))
    planner = ClaudePlanner(provider, known_capabilities=())

    assert planner(_multi_step_request()) == ()


def test_claude_planner_ignores_out_of_range_dependency_numbers() -> None:
    reply = json.dumps(
        {
            "steps": [
                {
                    "description": "Only step",
                    "capability": "spreadsheet.report",
                    "depends_on_step_numbers": [99, "not-an-int"],
                }
            ]
        }
    )
    provider = _FakeLLMProvider(reply=reply)
    planner = ClaudePlanner(provider, known_capabilities=())

    steps = planner(_multi_step_request())

    assert steps[0].dependencies == ()


def test_claude_planner_never_logs_prompt_or_reply_content(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    provider = _FakeLLMProvider(raises=RuntimeError("boom"))
    planner = ClaudePlanner(provider, known_capabilities=())

    planner(_multi_step_request())

    log_text = caplog.text
    assert "check if this column exists" not in log_text


# -- CompositePlanner ---------------------------------------------------------------


def test_composite_planner_returns_primarys_steps_when_non_empty() -> None:
    primary_steps = (
        PlanningStep(id="step-1", description="from primary", capability="cap.a"),
    )

    class _FallbackThatMustNotBeCalled:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            raise AssertionError("fallback should never be called when primary succeeds")

    composite = CompositePlanner(
        primary=lambda request: primary_steps, fallback=_FallbackThatMustNotBeCalled()
    )

    assert composite(_request()) == primary_steps


def test_composite_planner_falls_back_when_primary_is_empty() -> None:
    fallback_steps = (
        PlanningStep(id="step-1", description="from fallback", capability="cap.b"),
    )
    composite = CompositePlanner(
        primary=lambda request: (), fallback=lambda request: fallback_steps
    )

    assert composite(_request()) == fallback_steps


def test_composite_planner_wired_with_rule_based_and_claude_planner() -> None:
    """A realistic composition: RuleBasedPlanner handles the known single
    intent without any LLM call; ClaudePlanner never even gets invoked.
    """
    provider = _FakeLLMProvider()  # would raise AssertionError if .complete() were called
    composite = CompositePlanner(
        primary=RuleBasedPlanner(),
        fallback=ClaudePlanner(provider, known_capabilities=()),
    )
    request = _request(
        understanding=Understanding(intent="remove_duplicate_rows", confidence=0.9),
        reasoning=Reasoning(clarification_required=False),
        uploaded_files=("report.xlsx",),
        active_file="report.xlsx",
    )

    steps = composite(request)

    assert len(steps) == 1
    assert steps[0].capability == "spreadsheet.deduplicate"


# -- ProactiveAnalysisPlanner ---------------------------------------------------------


def test_proactive_analysis_planner_prepends_one_step_per_unanalyzed_file() -> None:
    inner_steps = (PlanningStep(id="step-1", description="from inner", capability="cap.a"),)
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=("uploads/report.xlsx",))

    steps = planner(request)

    assert len(steps) == 2
    assert steps[0].capability == "spreadsheet.analyze"
    assert "uploads/report.xlsx" in steps[0].description
    assert steps[1] == inner_steps[0]


def test_proactive_analysis_planner_produces_only_inners_steps_when_nothing_unanalyzed() -> None:
    inner_steps = (PlanningStep(id="step-1", description="from inner", capability="cap.a"),)
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=())

    steps = planner(request)

    assert steps == inner_steps


def test_proactive_analysis_planner_runs_unconditionally_even_with_no_inner_steps() -> None:
    """Analysis is independent of whatever the user's own request produces
    — even when inner produces nothing at all (e.g. an unclear message),
    the analysis step still runs. See
    docs/design/proactive-analysis-design.md Section 1.2.
    """
    planner = ProactiveAnalysisPlanner(inner=lambda request: ())
    request = _request(unanalyzed_spreadsheet_files=("uploads/report.xlsx",))

    steps = planner(request)

    assert len(steps) == 1
    assert steps[0].capability == "spreadsheet.analyze"


def test_proactive_analysis_planner_prepends_a_step_per_entry_given() -> None:
    """The wrapper itself is general over however many entries it's
    given — ``Planner._build_request`` is what actually narrows this to
    at most one in practice (see that method's own tests below).
    """
    planner = ProactiveAnalysisPlanner(inner=lambda request: ())
    request = _request(unanalyzed_spreadsheet_files=("a.xlsx", "b.xlsx"))

    steps = planner(request)

    assert len(steps) == 2
    assert [s.capability for s in steps] == ["spreadsheet.analyze", "spreadsheet.analyze"]
    assert steps[0].id != steps[1].id


# -- ProactiveAnalysisPlanner: redundant inner analysis steps -------------------------


def _inner_step(step_id: str, capability: str, *dependencies: str) -> PlanningStep:
    return PlanningStep(
        id=step_id,
        description=f"inner {capability}",
        capability=capability,
        dependencies=tuple(dependencies),
    )


def test_proactive_analysis_planner_drops_an_inner_analysis_step_it_already_added() -> None:
    """Live runs showed ClaudePlanner echoing Think's mention of analysis
    as its own step-1, so analysis ran twice in one turn."""
    inner_steps = (
        _inner_step("step-1", "spreadsheet.analyze"),
        _inner_step("step-2", "spreadsheet.deduplicate"),
    )
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=("uploads/report.xlsx",))

    steps = planner(request)

    assert [s.id for s in steps] == ["analyze-0", "step-2"]
    assert [s.capability for s in steps].count("spreadsheet.analyze") == 1
    assert steps[1] == inner_steps[1]


def test_proactive_analysis_planner_remaps_dependencies_on_a_dropped_step_to_analyze_0() -> None:
    inner_steps = (
        _inner_step("step-1", "spreadsheet.analyze"),
        _inner_step("step-2", "spreadsheet.remove_empty_rows", "step-1"),
        _inner_step("step-3", "spreadsheet.deduplicate", "step-1", "step-2"),
    )
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=("uploads/report.xlsx",))

    steps = planner(request)

    assert [s.id for s in steps] == ["analyze-0", "step-2", "step-3"]
    assert steps[1].dependencies == ("analyze-0",)
    assert steps[2].dependencies == ("analyze-0", "step-2")
    all_ids = {s.id for s in steps}
    assert all(d in all_ids for s in steps for d in s.dependencies)


def test_proactive_analysis_planner_does_not_list_analyze_0_twice_when_remapping() -> None:
    """Two dropped inner analysis steps both remap to analyze-0, which must
    appear once in the dependent step's dependencies, not twice."""
    inner_steps = (
        _inner_step("step-1", "spreadsheet.analyze"),
        _inner_step("step-2", "spreadsheet.analyze"),
        _inner_step("step-3", "spreadsheet.deduplicate", "step-1", "step-2"),
    )
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=("uploads/report.xlsx",))

    steps = planner(request)

    assert [s.id for s in steps] == ["analyze-0", "step-3"]
    assert steps[1].dependencies == ("analyze-0",)


def test_proactive_analysis_planner_keeps_a_requested_reanalysis_of_an_analyzed_file() -> None:
    """When the file was already analyzed earlier this session, nothing is
    prepended — so an inner analysis step is the user explicitly asking
    for a re-analysis, and must survive."""
    inner_steps = (_inner_step("step-1", "spreadsheet.analyze"),)
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=())

    steps = planner(request)

    assert steps == inner_steps


def test_proactive_analysis_planner_keeps_other_inner_steps_in_order_with_their_ids() -> None:
    inner_steps = (
        _inner_step("step-1", "spreadsheet.remove_empty_rows"),
        _inner_step("step-2", "spreadsheet.analyze"),
        _inner_step("step-3", "spreadsheet.deduplicate", "step-1"),
    )
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=("uploads/report.xlsx",))

    steps = planner(request)

    assert steps[1:] == (inner_steps[0], inner_steps[2])


def test_proactive_analysis_planner_moves_a_requested_reanalysis_to_the_front() -> None:
    """Analysis reads the original and sits outside the chain, so even a
    requested re-analysis never runs mid-chain."""
    inner_steps = (
        _inner_step("step-1", "spreadsheet.deduplicate"),
        _inner_step("step-2", "spreadsheet.analyze", "step-1"),
    )
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)
    request = _request(unanalyzed_spreadsheet_files=())  # nothing prepended: re-analysis kept

    steps = planner(request)

    assert [(s.id, s.capability) for s in steps] == [
        ("step-2", "spreadsheet.analyze"),
        ("step-1", "spreadsheet.deduplicate"),
    ]
    assert steps[0].dependencies == ()  # no longer runs after anything


@pytest.mark.parametrize("unanalyzed", [(), ("uploads/report.xlsx",)])
def test_proactive_analysis_planner_never_places_analysis_after_a_file_changing_step(
    unanalyzed: tuple[str, ...],
) -> None:
    inner_steps = (
        _inner_step("step-1", "spreadsheet.remove_empty_rows"),
        _inner_step("step-2", "spreadsheet.analyze"),
        _inner_step("step-3", "spreadsheet.deduplicate"),
        _inner_step("step-4", "spreadsheet.analyze"),
    )
    planner = ProactiveAnalysisPlanner(inner=lambda request: inner_steps)

    steps = planner(_request(unanalyzed_spreadsheet_files=unanalyzed))

    capabilities = [s.capability for s in steps]
    first_change = next(i for i, c in enumerate(capabilities) if c != "spreadsheet.analyze")
    assert "spreadsheet.analyze" not in capabilities[first_change:]


# -- ClaudePlanner: the user's stated order is enforced ------------------------------


_CHAIN_MESSAGE = "remove the empty rows and then remove the duplicate rows"


def _step_json(capability: str, phrase: str | None, depends_on: list[int] | None = None) -> dict:
    step: dict[str, object] = {
        "description": f"do {capability}",
        "capability": capability,
        "depends_on_step_numbers": depends_on or [],
    }
    if phrase is not None:
        step["source_phrase"] = phrase
    return step


def _plan(message: str, *steps: dict) -> tuple[PlanningStep, ...]:
    provider = _FakeLLMProvider(reply=json.dumps({"steps": list(steps)}))
    planner = ClaudePlanner(provider, known_capabilities=())
    return planner(_multi_step_request(user_message=message))


def test_claude_planner_reorders_steps_to_the_order_the_user_stated() -> None:
    """The live failure: the model listed dedupe first for a message that
    asked for empty rows first."""
    steps = _plan(
        _CHAIN_MESSAGE,
        _step_json("spreadsheet.deduplicate", "remove the duplicate rows"),
        _step_json("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    assert [(s.id, s.capability) for s in steps] == [
        ("step-1", "spreadsheet.remove_empty_rows"),
        ("step-2", "spreadsheet.deduplicate"),
    ]
    assert [s.priority for s in steps] == [0, 1]


def test_claude_planner_matches_source_phrases_case_insensitively() -> None:
    steps = _plan(
        "Remove the Empty Rows, then Remove The Duplicate Rows",
        _step_json("spreadsheet.deduplicate", "remove the duplicate rows"),
        _step_json("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    assert [s.capability for s in steps] == [
        "spreadsheet.remove_empty_rows",
        "spreadsheet.deduplicate",
    ]


def test_claude_planner_remaps_dependencies_after_reordering() -> None:
    """The model's dedupe (its step 1) is second after reordering; its
    report step depended on both, which now maps to the new ids, and the
    empty-rows step's dependency on dedupe (now later) is dropped."""
    steps = _plan(
        "remove the empty rows, then remove the duplicate rows, then give me a report",
        _step_json("spreadsheet.deduplicate", "remove the duplicate rows"),
        _step_json("spreadsheet.remove_empty_rows", "remove the empty rows", [1]),
        _step_json("spreadsheet.report", "give me a report", [1, 2]),
    )

    assert [(s.id, s.capability, s.dependencies) for s in steps] == [
        ("step-1", "spreadsheet.remove_empty_rows", ()),
        ("step-2", "spreadsheet.deduplicate", ()),
        ("step-3", "spreadsheet.report", ("step-2", "step-1")),
    ]


_DEDUPE = "spreadsheet.deduplicate"
_EMPTY = "spreadsheet.remove_empty_rows"


@pytest.mark.parametrize(
    ("message", "model_steps"),
    [
        # Each case: sorting by where the phrases appear would give a
        # *different* order than the model's — so keeping the model's order
        # proves the check declined to reorder.
        # A phrase the model paraphrased instead of quoting.
        (_CHAIN_MESSAGE, ((_DEDUPE, "drop duplicate rows"), (_EMPTY, "remove the empty rows"))),
        # A missing phrase.
        (_CHAIN_MESSAGE, ((_DEDUPE, None), (_EMPTY, "remove the empty rows"))),
        # Order-inverting wording: the model correctly put empty rows first,
        # though dedupe is mentioned first.
        (
            "remove the duplicate rows after you remove the empty rows",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        (
            "remove the duplicate rows, but first remove the empty rows",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        # Overlapping phrases.
        (
            _CHAIN_MESSAGE,
            (
                (_DEDUPE, "and then remove the duplicate rows"),
                (_EMPTY, "remove the empty rows and then"),
            ),
        ),
        # Review item M2: the model's order is right, but it quoted only
        # "rows" for the empty-rows step. "rows" first appears in "sales
        # rows", so sorting by first appearance would wrongly put empty
        # rows first. A phrase that appears more than once can't be placed.
        (
            "for the sales rows, remove duplicates, then drop the empty rows",
            ((_DEDUPE, "remove duplicates"), (_EMPTY, "rows")),
        ),
    ],
    ids=["paraphrased", "missing", "after", "late-first", "overlap", "ambiguous-phrase"],
)
def test_claude_planner_keeps_the_models_order_when_the_stated_order_cant_be_checked(
    message: str, model_steps: tuple[tuple[str, str | None], ...]
) -> None:
    steps = _plan(message, *(_step_json(cap, phrase) for cap, phrase in model_steps))

    assert [s.capability for s in steps] == [cap for cap, _ in model_steps]
    # Flagged, so the reply can say which order was used (final review F4).
    assert [s.order_confirmed for s in steps] == [False, False]


def test_claude_planner_asks_the_model_for_verbatim_source_phrases() -> None:
    from buddy.planner import _SYSTEM_PROMPT

    assert '"source_phrase"' in _SYSTEM_PROMPT
    assert "copied character-for-character" in _SYSTEM_PROMPT


# -- Planner stage -----------------------------------------------------------------


def test_planner_sets_typed_planning_field_and_mirrors_legacy_plan() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    state.reasoning = Reasoning(clarification_required=False, confidence=0.9)
    state.context.switch_active_file("report.xlsx")
    stage = Planner(planner=RuleBasedPlanner())

    stage(state)

    assert state.planning is not None
    assert len(state.planning) == 1
    assert state.planning[0].capability == "spreadsheet.deduplicate"
    assert state.plan == [state.planning[0].description]


# -- Planner._build_request: unanalyzed_spreadsheet_files resolution ------------------


def test_planner_build_request_resolves_active_file_as_unanalyzed() -> None:
    state = _make_state()  # uploads report.xlsx
    state.context.switch_active_file("report.xlsx")
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    Planner(planner=_RecordingStrategy())(state)

    assert seen[0].unanalyzed_spreadsheet_files == ("report.xlsx",)


def test_planner_build_request_falls_back_to_most_recent_upload() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file("first.xlsx")
    state.context.add_uploaded_file("second.xlsx")
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    Planner(planner=_RecordingStrategy())(state)

    assert seen[0].unanalyzed_spreadsheet_files == ("second.xlsx",)


def test_planner_build_request_omits_a_file_already_analyzed() -> None:
    state = _make_state()
    state.context.switch_active_file("report.xlsx")
    state.context.mark_analyzed("report.xlsx")
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    Planner(planner=_RecordingStrategy())(state)

    assert seen[0].unanalyzed_spreadsheet_files == ()


def test_planner_build_request_omits_a_non_spreadsheet_file() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file("report.pdf")
    state.context.switch_active_file("report.pdf")
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    Planner(planner=_RecordingStrategy())(state)

    assert seen[0].unanalyzed_spreadsheet_files == ()


def test_planner_build_request_omits_when_no_file_at_all() -> None:
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    Planner(planner=_RecordingStrategy())(state)

    assert seen[0].unanalyzed_spreadsheet_files == ()


def _unanalyzed_for(path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[str, ...]:
    monkeypatch.chdir(path.parent)
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.context.add_uploaded_file(path.name)
    state.context.switch_active_file(path.name)
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    Planner(planner=_RecordingStrategy())(state)
    return seen[0].unanalyzed_spreadsheet_files


def _workbook(path: Path, *names: str) -> Path:
    with pd.ExcelWriter(path) as writer:
        for name in names:
            pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name=name, index=False)
    return path


def test_planner_build_request_keeps_a_one_sheet_workbook_for_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = _workbook(tmp_path / "report.xlsx", "Data")

    assert _unanalyzed_for(path, monkeypatch) == ("report.xlsx",)


@pytest.mark.parametrize("names", [("Q1", "Q2"), ("Q1", "Q2", "Q3")])
def test_planner_build_request_omits_a_multi_sheet_workbook(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, names: tuple[str, ...]
) -> None:
    path = _workbook(tmp_path / "report.xlsx", *names)

    assert _unanalyzed_for(path, monkeypatch) == ()


@pytest.mark.parametrize("kind", ["missing_part", "protected", "legacy"])
def test_planner_build_request_omits_a_workbook_whose_sheets_cant_be_listed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    if kind == "missing_part":
        path = fixtures.without_sheet_part(
            _workbook(tmp_path / "book.xlsx", "Q1", "Q2"), tmp_path / "report.xlsx"
        )
    elif kind == "protected":
        path = fixtures.encrypted_package(tmp_path / "report.xlsx")
    else:
        path = fixtures.legacy_xls(tmp_path / "report.xls")

    assert _unanalyzed_for(path, monkeypatch) == ()


def test_planner_defaults_to_null_planner_when_none_configured() -> None:
    state = _make_state()
    state.understanding = Understanding(intent="remove_duplicate_rows", confidence=0.9)
    stage = Planner()

    stage(state)

    assert state.planning == ()
    assert state.plan == []


def test_planner_passes_raw_user_message_through_to_the_strategy() -> None:
    state = _make_state()
    state.user_message = "check if this column exists, remove it, and give me a report"
    seen: list[PlanningRequest] = []

    class _RecordingStrategy:
        def __call__(self, request: PlanningRequest) -> tuple[PlanningStep, ...]:
            seen.append(request)
            return ()

    stage = Planner(planner=_RecordingStrategy())
    stage(state)

    assert seen[0].user_message == "check if this column exists, remove it, and give me a report"


# -- R2: the order is read from the message only through an allowlist ---------------


@pytest.mark.parametrize(
    ("message", "model_steps"),
    [
        # The four messages from the re-review (R2). The model's order is
        # correct (empty rows first); reading positions would reverse it.
        (
            "remove the duplicate rows last, and remove the empty rows",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        (
            "remove the duplicate rows second and remove the empty rows",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        (
            "remove the duplicate rows, but do the empty rows ahead of that",
            ((_EMPTY, "the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        (
            "remove the duplicate rows; before that, remove the empty rows",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        # Plain "and" isn't a recognised forward connector either.
        (
            "remove the duplicate rows and remove the empty rows",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        # Order wording after the last step.
        (
            "remove the duplicate rows then remove the empty rows first",
            ((_EMPTY, "remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
        # Order wording inside a quoted phrase.
        (
            "remove the duplicate rows then remove the empty rows",
            ((_EMPTY, "then remove the empty rows"), (_DEDUPE, "remove the duplicate rows")),
        ),
    ],
    ids=[
        "last",
        "second",
        "ahead-of-that",
        "before-that",
        "plain-and",
        "trailing-first",
        "in-phrase",
    ],
)
def test_claude_planner_keeps_the_models_order_unless_a_forward_connector_states_it(
    message: str, model_steps: tuple[tuple[str, str], ...]
) -> None:
    steps = _plan(message, *(_step_json(cap, phrase) for cap, phrase in model_steps))

    assert [s.capability for s in steps] == [cap for cap, _ in model_steps]
    # Flagged, so the reply can say which order was used (final review F4).
    assert [s.order_confirmed for s in steps] == [False, False]


@pytest.mark.parametrize(
    ("message", "stated"),
    [
        ("remove the empty rows and then remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the duplicate rows and then remove the empty rows", [_DEDUPE, _EMPTY]),
        ("remove the empty rows, then remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows then remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows; after that, remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows followed by remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows, next remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("first remove the empty rows, then remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("please remove the empty rows and then remove the duplicate rows.", [_EMPTY, _DEDUPE]),
        # Final review F4: added to the allowlist.
        ("remove the empty rows, and after that remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows; once that's done remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows, afterwards remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows -> remove the duplicate rows", [_EMPTY, _DEDUPE]),
        ("remove the empty rows->remove the duplicate rows", [_EMPTY, _DEDUPE]),
    ],
)
@pytest.mark.parametrize("model_order", ["same", "reversed"])
def test_claude_planner_follows_the_stated_order_with_a_forward_connector(
    message: str, stated: list[str], model_order: str
) -> None:
    phrases = {_EMPTY: "remove the empty rows", _DEDUPE: "remove the duplicate rows"}
    listed = stated if model_order == "same" else list(reversed(stated))

    steps = _plan(message, *(_step_json(cap, phrases[cap]) for cap in listed))

    assert [s.capability for s in steps] == stated
    assert all(s.order_confirmed for s in steps)


@pytest.mark.parametrize(
    "message",
    [
        "remove the empty rows, remove the duplicate rows",  # bare punctuation isn't "->"
        "remove the empty rows > remove the duplicate rows",
        "remove the empty rows - remove the duplicate rows",
    ],
)
def test_claude_planner_does_not_read_other_punctuation_as_an_arrow(message: str) -> None:
    steps = _plan(
        message,
        _step_json(_DEDUPE, "remove the duplicate rows"),
        _step_json(_EMPTY, "remove the empty rows"),
    )

    assert [s.capability for s in steps] == [_DEDUPE, _EMPTY]
    assert not any(s.order_confirmed for s in steps)


def test_claude_planner_confirms_a_single_step_plan() -> None:
    steps = _plan("remove the empty rows", _step_json(_EMPTY, "not in the message"))

    assert [s.order_confirmed for s in steps] == [True]


# -- Live-run L-2: a step quoting nothing the user said doesn't run ----------------------


def test_claude_planner_drops_a_step_whose_source_phrase_is_empty(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Run 7's plan: the model added empty-row removal with source_phrase ""."""
    with caplog.at_level(logging.WARNING, logger="buddy.planner"):
        steps = _plan(
            "remove the duplicate rows and then give me a report",
            _step_json(_DEDUPE, "remove the duplicate rows"),
            _step_json(_EMPTY, "", depends_on=[1]),
            _step_json("spreadsheet.analyze", "then give me a report", depends_on=[2]),
        )

    assert [(s.id, s.capability, s.dependencies) for s in steps] == [
        ("step-1", _DEDUPE, ()),
        ("step-2", "spreadsheet.analyze", ()),  # its dependency was the dropped step
    ]
    assert any("source_phrase is empty" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("phrase", ["", "   "], ids=["empty", "whitespace"])
def test_claude_planner_drops_every_step_quoting_nothing(phrase: str) -> None:
    assert _plan("remove the empty rows", _step_json(_EMPTY, phrase)) == ()


@pytest.mark.parametrize(
    "phrase", [None, "drop the blank rows"], ids=["missing", "paraphrased"]
)
def test_claude_planner_keeps_a_step_whose_phrase_is_missing_or_paraphrased(
    phrase: str | None,
) -> None:
    steps = _plan(
        "remove the duplicate rows then remove the empty rows",
        _step_json(_DEDUPE, "remove the duplicate rows"),
        _step_json(_EMPTY, phrase),
    )

    assert [s.capability for s in steps] == [_DEDUPE, _EMPTY]
