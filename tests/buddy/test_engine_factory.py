"""End-to-end test for the "remove duplicate rows" vertical slice.

Runs the full cognitive cycle built by ``build_engine`` against a real
temporary ``.xlsx`` file with known duplicate rows, using a fake
``LLMProvider`` so the test never makes a real network call or needs a
real API key. This is the test that proves the whole slice works
together — Eyes through Voice — not just each stage in isolation.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from buddy.engine_factory import build_engine
from buddy.llm import LLMMessage
from buddy.state import BuddyState


class _QueuedLLMProvider:
    """An ``LLMProvider`` stand-in that returns canned replies in call order.

    In this pipeline, Brain always calls the provider once and then
    Think calls it once — in that fixed order — so a simple queue is
    sufficient to fake both without needing to inspect which stage is
    calling.
    """

    def __init__(self, replies: list[str]) -> None:
        self._replies = list(replies)
        self.calls: list[tuple[str, tuple[LLMMessage, ...]]] = []

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.calls.append((system, messages))
        if not self._replies:
            raise AssertionError("no more canned LLM replies configured for this test")
        return self._replies.pop(0)


def _write_fixture_with_duplicates(path: Path) -> None:
    """Write a real .xlsx file with 5 rows, 2 of which are exact
    duplicates, leaving 3 distinct rows after deduplication.
    """
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", "Alice", "Carol", "Bob"],
            "amount": [10, 20, 10, 30, 20],
        }
    ).to_excel(path, index=False)


def test_full_slice_removes_duplicate_rows_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates(tmp_path / input_path)

    understand_reply = json.dumps(
        {
            "intent": "remove_duplicate_rows",
            "confidence": 0.95,
            "referenced_files": [str(input_path)],
            "referenced_work_items": [],
            "missing_information": [],
            "ambiguity_flags": [],
            "notes": "",
        }
    )
    think_reply = json.dumps(
        {
            "objective": "Remove duplicate rows from the uploaded spreadsheet.",
            "summary": "The request is clear and a file is available.",
            "assumptions": [],
            "constraints": [],
            "risks": [],
            "recommended_strategy": "Use the Excel skill to deduplicate rows.",
            "clarification_required": False,
            "confidence": 0.9,
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove duplicate rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    # Both LLM calls actually happened, in the expected order — Brain
    # then Think — proving the real Brain/Think stages ran, not stubs.
    assert len(provider.calls) == 2

    # Hands actually ran the real ExcelDeduplicateSkill and produced a
    # real output file.
    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert len(state.execution_result.outputs) == 1
    output_path = Path(state.execution_result.outputs[0])
    assert output_path.exists()

    # Inspector's own independent judgment passed.
    assert state.verification_result is not None
    assert state.verification_result.passed is True

    # Independent re-check in the TEST itself — not trusting the app's own
    # Inspector stage or the skill's self-report — that the output file
    # genuinely has zero duplicate rows.
    written = pd.read_excel(output_path)
    assert len(written) == 3
    assert int(written.duplicated().sum()) == 0

    # Voice produced a sensible reply describing what happened.
    assert state.reply
    assert output_path.name in state.reply

    # The original upload was never modified.
    original = pd.read_excel(tmp_path / input_path)
    assert len(original) == 5


def test_full_slice_reports_clarification_needed_for_an_unclear_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When Claude classifies the request as unclear, nothing should be
    executed, and the reply should say so rather than silently doing
    nothing or crashing.
    """
    monkeypatch.chdir(tmp_path)

    understand_reply = json.dumps(
        {
            "intent": "unclear",
            "confidence": 0.2,
            "missing_information": ["no file was clearly referenced"],
        }
    )
    think_reply = json.dumps(
        {
            "objective": "",
            "summary": "The request is too ambiguous to act on.",
            "clarification_required": True,
            "confidence": 0.2,
            "risks": ["intent is unclear"],
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "do something with my stuff"

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    assert state.execution_result is not None
    assert state.execution_result.success is True  # nothing to execute is not a failure
    assert state.execution_result.outputs == ()
    assert state.verification_result is not None
    assert state.verification_result.passed is False
    assert state.reply


def test_full_slice_decomposes_a_compound_request_into_multiple_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A compound request should flow through RuleBasedPlanner (which
    produces nothing for an unknown single intent), fall back to
    ClaudePlanner via CompositePlanner, and land multiple ordered steps
    in state.planning — a 3rd canned LLM reply, for Planner's own call.
    The existing single-step test above needs no such 3rd reply, since
    RuleBasedPlanner handles "remove_duplicate_rows" without ever
    invoking the fallback.
    """
    monkeypatch.chdir(tmp_path)

    understand_reply = json.dumps(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "missing_information": [],
            "ambiguity_flags": [],
            "notes": "",
        }
    )
    think_reply = json.dumps(
        {
            "objective": "Check, remove, and report on a column.",
            "summary": "The request names three distinct actions but is clear.",
            "clarification_required": False,
            "confidence": 0.85,
        }
    )
    plan_reply = json.dumps(
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
    provider = _QueuedLLMProvider([understand_reply, think_reply, plan_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "check if this column exists, remove it, and give me a report"
    state.context.add_uploaded_file("uploads/report.xlsx")
    state.context.switch_active_file("uploads/report.xlsx")

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    # All three LLM calls happened, in order — Brain, Think, then Planner's
    # own ClaudePlanner call (reached only after RuleBasedPlanner produced
    # nothing for the unrecognized single-intent mapping).
    assert len(provider.calls) == 3

    assert state.planning is not None
    assert len(state.planning) == 3
    assert [s.capability for s in state.planning] == [
        "spreadsheet.column_check",
        "spreadsheet.remove_column",
        "spreadsheet.report",
    ]
    assert state.planning[1].dependencies == ("step-1",)

    # None of these capabilities have a registered skill, so Hands
    # surfaces per-item errors cleanly rather than crashing — exactly the
    # mechanism this design relies on (Step 1 of the design doc).
    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert len(state.execution_result.errors) == 3
