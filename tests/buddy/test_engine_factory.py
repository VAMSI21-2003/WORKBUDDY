"""End-to-end test for the "remove duplicate rows" vertical slice.

Runs the full cognitive cycle built by ``build_engine`` against a real
temporary ``.xlsx`` file with known duplicate rows, using a fake
``LLMProvider`` so the test never makes a real network call or needs a
real API key. This is the test that proves the whole slice works
together — Eyes through Voice — not just each stage in isolation.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from buddy.engine_factory import build_engine
from buddy.llm import LLMMessage
from buddy.outcome import NO_SKILL_UNSURE_SENTENCE
from buddy.state import BuddyState
from buddy.text import count_noun as _n
from buddy.voice import (
    _COMPLETION_CLAIM,
    RECHECK_FAILED_SENTENCE,
    PersonalityVoice,
    Speaker,
    SpeakingRequest,
    TemplateSpeaker,
)
from buddy.voice_catalog import style_for


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

    ``ProactiveAnalysisPlanner`` also prepends a ``spreadsheet.analyze``
    step unconditionally, ahead of these three — see
    docs/design/proactive-analysis-design.md — so a real fixture is
    written at the upload path for it to genuinely succeed against,
    keeping this test's real point (three *unmapped* capabilities produce
    three clean per-item errors) undiluted by an unrelated file-not-found
    error from the new step.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    pd.DataFrame({"name": ["Alice", "Bob"], "amount": [10, 20]}).to_excel(
        tmp_path / "uploads" / "report.xlsx", index=False
    )

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
    assert len(state.planning) == 4
    assert [s.capability for s in state.planning] == [
        "spreadsheet.analyze",
        "spreadsheet.column_check",
        "spreadsheet.remove_column",
        "spreadsheet.report",
    ]
    assert state.planning[2].dependencies == ("step-1",)

    # None of the three *requested* capabilities have a registered skill.
    # Steps now form a chain, so the first one that can't run stops it:
    # the other two are recorded as skipped (never attempted), the error
    # names the step that stopped the chain, and no file is handed back.
    # The prepended spreadsheet.analyze step is outside the chain — it
    # has a real skill and a real fixture, so it still succeeds.
    assert state.execution_result is not None
    assert state.execution_result.success is False
    assert state.execution_result.outputs == ()
    errors = state.execution_result.errors
    assert len(errors) == 2
    assert "spreadsheet.column_check" in errors[0]
    assert errors[1].startswith("stopped: spreadsheet.column_check could not be run")
    # The step that stopped the chain is recorded as failed, so Inspector
    # sees it (review item B1).
    assert [(s.capability, s.status) for s in state.execution_result.steps] == [
        ("spreadsheet.analyze", "success"),
        ("spreadsheet.column_check", "failure"),
        ("spreadsheet.remove_column", "skipped"),
        ("spreadsheet.report", "skipped"),
    ]


def _write_fixture_with_duplicates_and_an_empty_row(path: Path) -> None:
    """Write a real .xlsx file with both an exact duplicate row and a
    fully-empty row, so both registered skills each have real work to do
    — and a chained plan's final file must have *neither* (Hands feeds
    each step the previous step's output; see buddy.hands.Hands).

    The empty row sits before the final row, not after it: a trailing
    all-empty row has no following row to anchor it, so openpyxl's
    write/read round trip silently drops it rather than preserving it as
    a blank row (the same reason tests/skills/excel/test_remove_empty_rows.py's
    own fixture places its empty row mid-file rather than last).
    """
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", None, "Alice"],
            "amount": [10, 20, None, 10],
        }
    ).to_excel(path, index=False, engine="openpyxl")  # engine given so a .xls name works too


def test_full_slice_runs_two_real_skills_and_aggregates_verification_and_reply(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """End-to-end proof that CompositeVerifier's per-capability dispatch
    and TemplateSpeaker's multi-step reply actually work together, not
    just in isolation: two *real* registered skills
    (ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill) both run against
    the same real file, each is independently verified by its own real
    Verifier, and the final reply covers both outcomes.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates_and_an_empty_row(tmp_path / input_path)

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
            "objective": "Clean up the spreadsheet.",
            "summary": "The request names two distinct cleanup actions but is clear.",
            "clarification_required": False,
            "confidence": 0.85,
        }
    )
    plan_reply = json.dumps(
        {
            "steps": [
                {
                    "description": "Remove duplicate rows",
                    "capability": "spreadsheet.deduplicate",
                    "depends_on_step_numbers": [],
                },
                {
                    "description": "Remove empty rows",
                    "capability": "spreadsheet.remove_empty_rows",
                    "depends_on_step_numbers": [],
                },
            ]
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply, plan_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove duplicate rows and remove empty rows from this file"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    # All three steps reached a real, registered skill and succeeded —
    # ProactiveAnalysisPlanner's prepended spreadsheet.analyze step,
    # alongside the two requested capabilities.
    assert state.execution_result is not None
    assert state.execution_result.success is True
    assert len(state.execution_result.steps) == 4  # incl. the final-file re-check
    capabilities = {step.capability for step in state.execution_result.steps}
    assert capabilities == {
        "spreadsheet.analyze",
        "spreadsheet.deduplicate",
        "spreadsheet.remove_empty_rows",
    }
    # The two requested steps chained: remove_empty_rows (step 2) read
    # deduplicate's (step 1) output, and only its output — the final file
    # — is handed back. spreadsheet.analyze writes no file and read the
    # original (Section 5.2/5.4 of docs/design/proactive-analysis-design.md).
    # After the chain, analysis re-ran read-only on the final file, so the
    # reply's findings describe the file the user actually receives.
    analyze, dedupe, empty_rows, final_analysis = state.execution_result.steps
    assert analyze.input_reference == str(input_path)
    assert dedupe.input_reference == str(input_path)
    assert empty_rows.input_reference == dedupe.outputs[0]
    assert state.execution_result.outputs == empty_rows.outputs
    assert final_analysis.capability == "spreadsheet.analyze"
    assert final_analysis.input_reference == empty_rows.outputs[0]

    # CompositeVerifier dispatched each step to its own real Verifier, then
    # checked the final file against both steps' claims, and aggregated it
    # all into a single passed verdict.
    assert state.verification_result is not None
    assert state.verification_result.passed is True
    assert [s.capability for s in state.verification_result.steps] == [
        "spreadsheet.analyze",
        "spreadsheet.deduplicate",
        "spreadsheet.remove_empty_rows",
        "spreadsheet.analyze",
        "final_output",
    ]
    assert any(
        f.startswith("[spreadsheet.deduplicate]") for f in state.verification_result.findings
    )
    assert any(
        f.startswith("[spreadsheet.remove_empty_rows]")
        for f in state.verification_result.findings
    )
    # The fixture has a missing value in both "name" and "amount" (the
    # all-empty row) — SpreadsheetAnalysisVerifier independently recomputes
    # this straight from the original upload, not from anything the Skill
    # claimed.
    assert any(
        f.startswith("[spreadsheet.analyze] [missing_values]")
        for f in state.verification_result.findings
    )

    # Independent re-check, in the test itself, of both real output files —
    # not trusting the app's own Inspector stage or either skill's
    # self-report. Each output is checked against the specific claim its
    # own capability makes, not an either-or guess.
    for step in state.execution_result.steps:
        if step.capability == "spreadsheet.analyze":
            assert step.outputs == ()
            continue
        assert len(step.outputs) == 1
        written = pd.read_excel(step.outputs[0])
        if step.capability == "spreadsheet.deduplicate":
            assert int(written.duplicated().sum()) == 0
        elif step.capability == "spreadsheet.remove_empty_rows":
            assert int(written.isna().all(axis=1).sum()) == 0
        else:
            raise AssertionError(f"unexpected capability in step results: {step.capability!r}")

    # ...and the one file the user receives has neither problem left.
    final = pd.read_excel(state.execution_result.outputs[0])
    assert int(final.duplicated().sum()) == 0
    assert int(final.isna().all(axis=1).sum()) == 0
    assert len(final) == 2  # Alice, Bob

    # The original's missing values (the empty row) are stale: the chain
    # removed that row, and analysis re-run on the final file found nothing.
    # Inspector still records the original's findings, but the reply
    # reports the file the user actually receives — and names it.
    assert state.reply is not None
    assert "Before anything else, here's what I noticed" not in state.reply
    assert "missing" not in state.reply.lower()
    # Now its own sentence after the outcome, so it starts with a capital.
    assert "None of the issues I check for were found." in state.reply
    output_names = {Path(p).name for p in state.execution_result.outputs}
    for name in output_names:
        assert name in state.reply

    # The turn's target file was marked analyzed, so a later turn on the
    # same file won't re-queue spreadsheet.analyze.
    assert str(input_path) in state.context.analyzed_files

    # The original upload was never modified.
    original = pd.read_excel(tmp_path / input_path)
    assert len(original) == 4


# The same (name, phone) rows validated by the real spike
# (scripts/spike_tools_test.py): true duplicates (spelling variant, same
# phone) score real match_probability ~0.21-0.28; name collisions (same
# or near-identical name, different phone) score ~0.0000 -- both well
# separated by NearDuplicateAnalyzer's threshold (0.15).
_NEAR_DUPLICATE_AND_COLLISION_ROWS = [
    ("Alice Johnson", "555-0101"),
    ("Alice Johnston", "555-0101"),  # true duplicate of row 0 (same phone)
    ("Robert Smith", "555-0202"),
    ("Bob Smith", "555-0202"),  # true duplicate of row 2 (same phone)
    ("Carol Davis", "555-0303"),
    ("Carol Davies", "555-0303"),  # true duplicate of row 4 (same phone)
    ("Alice Johnson", "555-0199"),  # name collision with row 0 (different phone)
    ("Robert Smith", "555-0288"),  # name collision with row 2 (different phone)
    ("Carol Davis", "555-0377"),  # name collision with row 4 (different phone)
    ("Frank Nguyen", "555-0505"),
    ("Grace Kim", "555-0606"),
    ("Henry Patel", "555-0707"),
    ("Irene Chao", "555-0808"),
    ("Jack Turner", "555-0909"),
    ("Karen Wu", "555-1010"),
    ("Liam O'Brien", "555-1111"),
    ("Maria Garcia", "555-1212"),
    ("Noah Lee", "555-1313"),
    ("Olivia Brown", "555-1414"),
    ("Peter Chen", "555-1515"),
    ("Quinn Adams", "555-1616"),
    ("Rachel Kim", "555-1717"),
    ("Sam Park", "555-1818"),
    ("Tina Ali", "555-1919"),
    ("Alice Johnson", "555-0101"),  # EXACT duplicate of row 0 -- real work for dedupe
]


def test_full_slice_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real, end-to-end proof of the fold-in rule
    (docs/design/proactive-analysis-design.md Section 5.3): a real
    Splink-detected near-duplicate finding must still be reported as an
    open item even though a real ``spreadsheet.deduplicate`` step ran and
    passed in the exact same turn — proving the rule holds through the
    whole pipeline (real analyzer, real Verifier, real Voice), not just at
    ``TemplateSpeaker``'s own unit level (see
    tests/buddy/test_voice.py::test_template_speaker_does_not_fold_a_near_duplicate_finding_into_an_exact_dedupe_step
    for that narrower proof).
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    pd.DataFrame(_NEAR_DUPLICATE_AND_COLLISION_ROWS, columns=["name", "phone"]).to_excel(
        tmp_path / input_path, index=False
    )

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

    # Both a real spreadsheet.analyze step (Splink included) and a real
    # spreadsheet.deduplicate step ran, and both passed.
    assert state.execution_result is not None
    assert state.execution_result.success is True
    capabilities = {step.capability for step in state.execution_result.steps}
    assert capabilities == {"spreadsheet.analyze", "spreadsheet.deduplicate"}

    assert state.verification_result is not None
    dedupe_verdict = next(
        sv for sv in state.verification_result.steps if sv.capability == "spreadsheet.deduplicate"
    )
    assert dedupe_verdict.passed is True

    # A real near-duplicate finding was actually produced.
    near_duplicate_findings = [
        f
        for f in state.verification_result.findings
        if f.startswith("[spreadsheet.analyze] [near_duplicates]")
    ]
    assert len(near_duplicate_findings) > 0

    # ...and it survived into the final reply as an OPEN item -- never
    # folded away just because spreadsheet.deduplicate (exact-match only)
    # also ran and passed in the very same turn.
    # On a delivered turn the findings follow the outcome (decision D3).
    assert state.reply is not None
    assert "I also noticed: " in state.reply
    assert state.reply.index("Saved as") < state.reply.index("I also noticed: ")
    assert "Want me to tackle any of these?" in state.reply


def test_full_slice_runs_analysis_once_when_the_llm_planner_also_plans_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reproduces the live c2 run: ClaudePlanner echoed Think's mention of
    analysis back as its own step-1, so spreadsheet.analyze ran twice.
    ProactiveAnalysisPlanner must drop the inner copy (remapping any
    dependency on it to analyze-0) so analysis runs exactly once, while the
    requested steps still run.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates_and_an_empty_row(tmp_path / input_path)

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
            "objective": "Clean up the spreadsheet.",
            "summary": "Buddy will run spreadsheet.analyze, then remove empty and duplicate rows.",
            "clarification_required": False,
            "confidence": 0.85,
        }
    )
    plan_reply = json.dumps(
        {
            "steps": [
                {
                    "description": "Analyze the spreadsheet for data quality issues",
                    "capability": "spreadsheet.analyze",
                    "depends_on_step_numbers": [],
                },
                {
                    "description": "Remove empty rows",
                    "capability": "spreadsheet.remove_empty_rows",
                    "depends_on_step_numbers": [1],
                },
                {
                    "description": "Remove duplicate rows",
                    "capability": "spreadsheet.deduplicate",
                    "depends_on_step_numbers": [1, 2],
                },
            ]
        }
    )
    provider = _QueuedLLMProvider([understand_reply, think_reply, plan_reply])

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove the empty rows and then remove the duplicate rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))

    engine = build_engine(llm_provider=provider)
    engine.run(state)

    assert state.planning is not None
    assert [(s.id, s.capability) for s in state.planning] == [
        ("analyze-0", "spreadsheet.analyze"),
        ("step-2", "spreadsheet.remove_empty_rows"),
        ("step-3", "spreadsheet.deduplicate"),
    ]
    assert state.planning[1].dependencies == ("analyze-0",)
    assert state.planning[2].dependencies == ("analyze-0", "step-2")

    assert state.execution_result is not None
    assert state.execution_result.success is True
    # Analysis ran once on the original (the planner's duplicate was
    # dropped), plus the single read-only re-check of the final file.
    executed = [(s.capability, s.input_reference) for s in state.execution_result.steps]
    final_file = state.execution_result.outputs[0]
    assert executed == [
        ("spreadsheet.analyze", str(input_path)),
        ("spreadsheet.remove_empty_rows", str(input_path)),
        ("spreadsheet.deduplicate", executed[2][1]),
        ("spreadsheet.analyze", final_file),
    ]
    assert executed[2][1] != str(input_path)  # dedupe read the chained file


# -- Chaining, end to end ------------------------------------------------------------


def _chain_replies(*planned: tuple[str, str]) -> list[str]:
    """Brain, Think, and ClaudePlanner replies for a two-step cleanup, with
    the planner's steps in the given ``(capability, source_phrase)`` order."""
    understand = json.dumps(
        {
            "intent": "multi_step_request",
            "confidence": 0.9,
            "missing_information": [],
            "ambiguity_flags": [],
            "notes": "",
        }
    )
    think = json.dumps(
        {
            "objective": "Clean up the spreadsheet.",
            "summary": "Two cleanup steps, clear enough to proceed.",
            "clarification_required": False,
            "confidence": 0.85,
        }
    )
    plan = json.dumps(
        {
            "steps": [
                {
                    "description": f"do {capability}",
                    "capability": capability,
                    "depends_on_step_numbers": [],
                    "source_phrase": phrase,
                }
                for capability, phrase in planned
            ]
        }
    )
    return [understand, think, plan]


def _run_chain(
    tmp_path: Path,
    message: str,
    replies: list[str],
    speaker: Speaker | None = None,
    file_name: str = "report.xlsx",
) -> tuple[BuddyState, Path, bytes]:
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / file_name
    _write_fixture_with_duplicates_and_an_empty_row(tmp_path / input_path)
    original_bytes = (tmp_path / input_path).read_bytes()

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = message
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    build_engine(llm_provider=_QueuedLLMProvider(replies), speaker=speaker).run(state)
    return state, tmp_path / input_path, original_bytes


def test_full_slice_chains_steps_in_the_users_order_into_one_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planner lists dedupe first (as it did in 2 of 5 live runs), but
    the user asked for empty rows first: the stated order wins, each step
    reads the previous one's output, and one file comes back with both
    changes."""
    monkeypatch.chdir(tmp_path)
    message = "remove the empty rows and then remove the duplicate rows"
    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    state, original_path, original_bytes = _run_chain(tmp_path, message, replies)

    assert state.planning is not None
    assert [s.capability for s in state.planning] == [
        "spreadsheet.analyze",
        "spreadsheet.remove_empty_rows",
        "spreadsheet.deduplicate",
    ]

    result = state.execution_result
    assert result is not None
    assert result.success is True
    analyze, empty_rows, dedupe, final_analysis = result.steps
    assert final_analysis.input_reference == result.outputs[0]
    upload = str(Path("uploads") / "report.xlsx")
    assert analyze.input_reference == upload  # analysis read the original
    assert empty_rows.input_reference == upload
    assert dedupe.input_reference == empty_rows.outputs[0]  # chained
    assert result.outputs == dedupe.outputs  # one file handed back

    final = pd.read_excel(result.outputs[0])
    assert int(final.duplicated().sum()) == 0
    assert int(final.isna().all(axis=1).sum()) == 0
    assert final["name"].tolist() == ["Alice", "Bob"]

    assert original_path.read_bytes() == original_bytes  # untouched

    assert state.verification_result is not None
    assert state.verification_result.passed is True
    assert state.verification_result.steps[-1].capability == "final_output"
    assert state.verification_result.steps[-1].passed is True


def test_full_slice_stops_the_chain_when_a_step_fails_verification(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Dedupe (first) silently drops one row it should have kept. It leaves
    no duplicates, so an output-only check would pass — but checked against
    its own input it fails, so the chain stops: empty-row removal never
    runs, nothing is handed back, and the reply says which step failed."""
    from skills.excel.dedupe import ExcelDeduplicateSkill

    real_execute = ExcelDeduplicateSkill.execute

    def drops_an_extra_row(self, task):  # type: ignore[no-untyped-def]
        result = real_execute(self, task)
        output = result.outputs[0]
        pd.read_excel(output).iloc[:-1].to_excel(output, index=False)
        return result

    monkeypatch.setattr(ExcelDeduplicateSkill, "execute", drops_an_extra_row)
    monkeypatch.chdir(tmp_path)
    message = "remove the duplicate rows and then remove the empty rows"
    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    state, original_path, original_bytes = _run_chain(tmp_path, message, replies)

    result = state.execution_result
    assert result is not None
    assert result.success is False
    assert result.stopped_at == "spreadsheet.deduplicate"
    assert result.outputs == ()
    assert [(s.capability, s.status) for s in result.steps] == [
        ("spreadsheet.analyze", "success"),
        ("spreadsheet.deduplicate", "success"),
        ("spreadsheet.remove_empty_rows", "skipped"),
    ]
    assert any(e.startswith("spreadsheet.deduplicate failed verification") for e in result.errors)

    assert original_path.read_bytes() == original_bytes  # untouched

    # Inspector (the final word) agrees: dedupe failed, empty rows never ran.
    assert state.verification_result is not None
    assert state.verification_result.passed is False
    verdicts = {s.capability: s.passed for s in state.verification_result.steps}
    assert verdicts["spreadsheet.deduplicate"] is False
    assert "final_output" not in verdicts

    assert state.reply is not None
    assert (
        "I stopped at the deduplicate step, so remove empty rows never ran and no file was "
        "handed back; your original file is unchanged." in state.reply
    )
    assert "spreadsheet." not in state.reply


# -- Review item B1, end to end: the last step can't run after an earlier one passed --


_B1_MESSAGE = "remove the duplicate rows and then build me a dashboard"


def _b1_replies() -> list[str]:
    return _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.dashboard", "build me a dashboard"),
    )


class _RecordingPersonalityVoice(PersonalityVoice):
    """A ``PersonalityVoice`` that also records the request it spoke for."""

    def __init__(self, reply: str) -> None:
        super().__init__(_QueuedLLMProvider([reply]))
        self.requests: list[SpeakingRequest] = []

    def __call__(self, request: SpeakingRequest) -> str:
        self.requests.append(request)
        return super().__call__(request)


def test_full_slice_never_says_done_when_the_last_step_cant_be_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact scenario from the review: the dashboard step has no skill.
    Nothing is handed back, Inspector sees the failing step, and the reply
    never claims completion. Since PR-3's review M1 the chain, which can't
    finish, doesn't start: dedupe never runs."""
    monkeypatch.chdir(tmp_path)

    state, original_path, original_bytes = _run_chain(tmp_path, _B1_MESSAGE, _b1_replies())

    result = state.execution_result
    assert result is not None
    assert result.outputs == ()
    assert result.stopped_at == "spreadsheet.dashboard"
    assert [(s.capability, s.status) for s in result.steps] == [
        ("spreadsheet.analyze", "success"),
        ("spreadsheet.deduplicate", "skipped"),
        ("spreadsheet.dashboard", "failure"),
    ]
    assert original_path.read_bytes() == original_bytes  # untouched

    verification = state.verification_result
    assert verification is not None
    assert verification.passed is False
    verdicts = {v.capability: v.passed for v in verification.steps}
    assert verdicts["spreadsheet.dashboard"] is False

    reply = state.reply
    assert reply is not None
    assert "Done" not in reply
    assert _COMPLETION_CLAIM.search(reply) is None, reply
    assert (
        "I stopped at the dashboard step, so deduplicate never ran and no file was handed back"
        in reply
    )
    assert "spreadsheet." not in reply


@pytest.mark.parametrize(
    "model_reply", ["All done — duplicates removed.", "Your report is ready and saved."]
)
def test_full_slice_personality_voice_falls_back_honestly_when_the_last_step_cant_be_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_reply: str
) -> None:
    monkeypatch.chdir(tmp_path)
    speaker = _RecordingPersonalityVoice(model_reply)

    state, _, _ = _run_chain(tmp_path, _B1_MESSAGE, _b1_replies(), speaker=speaker)

    assert state.reply == TemplateSpeaker()(speaker.requests[0])
    assert "Done" not in state.reply
    assert _COMPLETION_CLAIM.search(state.reply) is None


# -- Review item M1, end to end: the re-check of the final file fails ------------------


def test_full_slice_reports_a_failed_re_check_without_failing_the_delivered_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analysis succeeds on the original, then fails only when re-run on the
    final file. The file was delivered and verified: the turn passes, the
    reply names the file, and says in plain words it couldn't be re-checked."""
    from skills.base import SkillResult, SkillStatus
    from skills.excel.analyze import SpreadsheetAnalysisSkill

    real_execute = SpreadsheetAnalysisSkill.execute
    calls: list[str] = []

    def fails_on_second_call(self, task):  # type: ignore[no-untyped-def]
        calls.append(task.input_references[0])
        if len(calls) > 1:
            return SkillResult(status=SkillStatus.FAILURE, errors=["could not read it"])
        return real_execute(self, task)

    monkeypatch.setattr(SpreadsheetAnalysisSkill, "execute", fails_on_second_call)
    monkeypatch.chdir(tmp_path)
    message = "remove the empty rows and then remove the duplicate rows"
    replies = _chain_replies(
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
    )

    state, original_path, original_bytes = _run_chain(tmp_path, message, replies)

    result = state.execution_result
    assert result is not None
    assert len(calls) == 2  # the original, then the final file
    assert result.success is True
    assert len(result.outputs) == 1
    assert result.steps[-1].recheck is True
    assert original_path.read_bytes() == original_bytes

    verification = state.verification_result
    assert verification is not None
    assert verification.passed is True
    recheck = verification.steps[-2]  # the final_output check comes last
    assert (recheck.capability, recheck.recheck, recheck.passed) == (
        "spreadsheet.analyze",
        True,
        False,
    )
    # The original's own analysis passed, so it is still marked analyzed.
    assert str(Path("uploads") / "report.xlsx") in state.context.analyzed_files

    reply = state.reply
    assert reply is not None
    # PR-B (review H1): this Brain reply has no requested_actions, so the ledger
    # comes from the mention detector alone. A mention never confirms a request,
    # so both removals are stated but the reply doesn't claim "Done".
    assert reply.startswith("Removed 1 fully-empty row. Removed 1 exact duplicate row.")
    assert "Done" not in reply
    assert RECHECK_FAILED_SENTENCE in reply
    assert f"Saved as `{Path(result.outputs[0]).name}`." in reply
    assert "wasn't able to confirm" not in reply
    assert "spreadsheet." not in reply
    assert "[" not in reply


def test_full_slice_personality_voice_mentions_the_file_when_the_re_check_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from skills.base import SkillResult, SkillStatus
    from skills.excel.analyze import SpreadsheetAnalysisSkill

    real_execute = SpreadsheetAnalysisSkill.execute
    calls: list[str] = []

    def fails_on_second_call(self, task):  # type: ignore[no-untyped-def]
        calls.append(task.input_references[0])
        if len(calls) > 1:
            return SkillResult(status=SkillStatus.FAILURE, errors=["could not read it"])
        return real_execute(self, task)

    monkeypatch.setattr(SpreadsheetAnalysisSkill, "execute", fails_on_second_call)
    monkeypatch.chdir(tmp_path)
    speaker = _RecordingPersonalityVoice("Both cleanups went through.")
    replies = _chain_replies(
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
    )

    state, _, _ = _run_chain(
        tmp_path, "remove the empty rows and then remove the duplicate rows", replies, speaker
    )

    # No model call: the reply is rendered by code, and the model's text
    # never appears.
    assert speaker._provider.calls == []  # type: ignore[union-attr]
    assert "Both cleanups went through." not in state.reply
    assert "Your cleaned copy of report.xlsx is ready." in state.reply
    assert RECHECK_FAILED_SENTENCE in state.reply
    assert "spreadsheet." not in state.reply



# -- Re-review R4, end to end: analysis of the original fails ---------------------------


def test_full_slice_names_the_delivered_file_when_the_initial_analysis_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Analysis fails on the original (its first call) and passes on the
    re-check. The chain completed and was verified, so the turn passes, the
    reply names the delivered file, and says the initial analysis couldn't
    run. The original isn't marked analyzed."""
    from skills.base import SkillResult, SkillStatus
    from skills.excel.analyze import SpreadsheetAnalysisSkill

    real_execute = SpreadsheetAnalysisSkill.execute
    calls: list[str] = []

    def fails_on_first_call(self, task):  # type: ignore[no-untyped-def]
        calls.append(task.input_references[0])
        if len(calls) == 1:
            return SkillResult(status=SkillStatus.FAILURE, errors=["could not read it"])
        return real_execute(self, task)

    monkeypatch.setattr(SpreadsheetAnalysisSkill, "execute", fails_on_first_call)
    monkeypatch.chdir(tmp_path)
    replies = _chain_replies(
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
    )

    state, original_path, original_bytes = _run_chain(
        tmp_path, "remove the empty rows and then remove the duplicate rows", replies
    )

    result = state.execution_result
    assert result is not None
    assert len(result.outputs) == 1
    assert original_path.read_bytes() == original_bytes
    verification = state.verification_result
    assert verification is not None
    assert verification.passed is True
    assert (verification.steps[0].capability, verification.steps[0].passed) == (
        "spreadsheet.analyze",
        False,
    )
    assert state.context.analyzed_files == frozenset()  # L5: only a passing analysis marks it

    reply = state.reply
    assert reply is not None
    # PR-B (review H1): this Brain reply has no requested_actions, so the ledger
    # comes from the mention detector alone. A mention never confirms a request,
    # so both removals are stated but the reply doesn't claim "Done".
    assert reply.startswith("Removed 1 fully-empty row. Removed 1 exact duplicate row.")
    assert "Done" not in reply
    assert f"Saved as `{Path(result.outputs[0]).name}`." in reply
    assert "The initial analysis of your original file couldn't run." in reply
    assert "wasn't able to confirm" not in reply
    assert "spreadsheet." not in reply


@pytest.mark.parametrize(
    "leaked",
    [
        "Duplicates are gone and your sheet is down to 3 rows.",
        "All set! I took care of the duplicate rows.",
        "Your spreadsheet now has 3 rows with the duplicates stripped out.",
    ],
)
def test_full_slice_personality_voice_never_describes_a_three_step_chain_stopped_midway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, leaked: str
) -> None:
    """R1, end to end: the re-review's three-step chain that stops at its
    middle step. The model is never asked; the factual reply is used."""
    monkeypatch.chdir(tmp_path)
    speaker = _RecordingPersonalityVoice(leaked)
    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.dashboard", "build me a dashboard"),
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    state, original_path, original_bytes = _run_chain(
        tmp_path,
        "remove the duplicate rows, then build me a dashboard, then remove the empty rows",
        replies,
        speaker,
    )

    assert state.execution_result is not None
    assert state.execution_result.stopped_at == "spreadsheet.dashboard"
    assert speaker._provider.calls == []  # type: ignore[attr-defined]
    assert state.reply == TemplateSpeaker()(speaker.requests[0])
    assert leaked not in state.reply
    # The chain can't finish, so none of it runs (PR-3 review M1).
    assert (
        "I stopped at the dashboard step, so deduplicate, remove empty rows never ran"
        in state.reply
    )
    assert original_path.read_bytes() == original_bytes


# -- Final review F1: an action request Brain can't classify ---------------------------


class _TooManyRequests(Exception):
    """What a provider's SDK raises for HTTP 429: the only kind of error worth a second try."""

    status_code = 429


class _BrainFailsLLMProvider(_QueuedLLMProvider):
    """Raises on the first two calls (Brain's attempt and its one retry), then answers from the
    queue."""

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        if len(self.calls) < 2:
            self.calls.append((system, messages))
            raise _TooManyRequests("429 Too Many Requests")
        return super().complete(system=system, messages=messages, max_tokens=max_tokens)


_F1_THINK_REPLY = json.dumps(
    {
        "objective": "Work out what the user wants.",
        "summary": "The request couldn't be classified.",
        "clarification_required": True,
        "confidence": 0.3,
    }
)


def _brain_reply(intent: str) -> str:
    return json.dumps({"intent": intent, "confidence": 0.4})


@pytest.mark.parametrize(
    "model_reply",
    [
        "All done — duplicates removed.",
        "Duplicates are gone and your sheet is spotless now.",
        "Got rid of those pesky dupes for you — enjoy the tidy sheet!",
    ],
)
@pytest.mark.parametrize(
    ("brain", "expected_intent"),
    [
        # Scenario A: Brain says "unclear", or invents an intent (now recorded as "unclear").
        (_brain_reply("unclear"), "unclear"),
        (_brain_reply("deduplicate"), "unclear"),
        (_brain_reply("Remove_Duplicate_Rows"), "unclear"),
        (_brain_reply("remove_duplicates"), "unclear"),
        # Scenario A2: the Brain call itself fails (e.g. a 429).
        (None, None),
        # Scenario A3: Brain's reply isn't JSON.
        ("sure! remove_duplicate_rows", None),
    ],
    ids=["A-unclear", "A-invented", "A-wrong-case", "A-near-miss", "A2-brain-fails", "A3-non-json"],
)
def test_full_slice_personality_voice_never_phrases_an_unclassified_action_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    brain: str | None,
    expected_intent: str | None,
    model_reply: str,
) -> None:
    """F1: nothing is delivered. Whatever Brain made of the request, the model is
    never asked to phrase the turn, so none of its replies (including the two that
    avoid the word list) reaches the user. Think asked for clarification, so the
    reply is its fixed question and no analysis was spent."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("buddy.brain._sleep", lambda seconds: None)  # the pause before a retry
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    _write_fixture_with_duplicates_and_an_empty_row(tmp_path / input_path)
    original_bytes = (tmp_path / input_path).read_bytes()
    provider = (
        _BrainFailsLLMProvider([_F1_THINK_REPLY])
        if brain is None
        # A reply that isn't a reading is asked for again once (Brain's one retry), so it is
        # given twice.
        else _QueuedLLMProvider(
            [brain, *([brain] if expected_intent is None else []), _F1_THINK_REPLY]
        )
    )
    speaker = _RecordingPersonalityVoice(model_reply)

    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove the duplicate rows please"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    build_engine(llm_provider=provider, speaker=speaker).run(state)

    assert state.understanding is not None
    assert state.understanding.intent == expected_intent
    result = state.execution_result
    assert result is not None
    assert result.outputs == ()
    assert [s.capability for s in result.steps] == []
    assert (tmp_path / input_path).read_bytes() == original_bytes

    assert speaker._provider.calls == []  # type: ignore[attr-defined]
    request = speaker.requests[0]
    assert state.reply == TemplateSpeaker().compose(request, style=style_for(request.mode))
    assert model_reply not in state.reply
    assert state.reply.endswith("?")
    assert _COMPLETION_CLAIM.search(state.reply) is None, state.reply


# -- Final review F3: .xls uploads -------------------------------------------------------
# xlrd isn't a dependency, so these .xls files hold workbook (xlsx) data, which
# pandas reads by content. The skills write .xlsx whatever the input.


def test_full_slice_deduplicates_an_xls_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    understand = json.dumps({"intent": "remove_duplicate_rows", "confidence": 0.95})
    think = json.dumps(
        {"objective": "Remove duplicate rows.", "clarification_required": False, "confidence": 0.9}
    )

    state, original_path, original_bytes = _run_chain(
        tmp_path, "remove duplicate rows", [understand, think], file_name="report.xls"
    )

    result = state.execution_result
    assert result is not None
    assert result.stopped_at is None
    assert len(result.outputs) == 1
    assert result.outputs[0].endswith(".xlsx")
    assert int(pd.read_excel(result.outputs[0]).duplicated().sum()) == 0
    assert state.verification_result is not None
    assert state.verification_result.passed is True
    assert state.reply is not None
    assert "Done" in state.reply
    assert original_path.read_bytes() == original_bytes


def test_full_slice_chains_two_steps_from_an_xls_upload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    state, original_path, original_bytes = _run_chain(
        tmp_path,
        "remove the duplicate rows and then remove the empty rows",
        replies,
        file_name="report.xls",
    )

    result = state.execution_result
    assert result is not None
    assert result.stopped_at is None
    _, dedupe, empty_rows, _ = result.steps
    assert dedupe.input_reference == str(Path("uploads") / "report.xls")
    assert dedupe.outputs[0].endswith(".xlsx")
    assert empty_rows.input_reference == dedupe.outputs[0]  # chained across the type change
    assert result.outputs == empty_rows.outputs
    final = pd.read_excel(result.outputs[0])
    assert final["name"].tolist() == ["Alice", "Bob"]
    assert state.verification_result is not None
    assert state.verification_result.passed is True
    assert original_path.read_bytes() == original_bytes


# -- Final review F4 (scenario D): the order the user stated ------------------------------
# The planner lists the steps reversed (empty rows first); every message asks
# for duplicates first.

_DEDUPE_FIRST = ["spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"]
_EMPTY_ROWS_FIRST = list(reversed(_DEDUPE_FIRST))
_ORDER_SENTENCE = "so the steps ran in this order: "


def _reversed_plan(dedupe_phrase: str, empty_rows_phrase: str) -> list[str]:
    return _chain_replies(
        ("spreadsheet.remove_empty_rows", empty_rows_phrase),
        ("spreadsheet.deduplicate", dedupe_phrase),
    )


def _action_order(state: BuddyState) -> list[str]:
    assert state.execution_result is not None
    return [s.capability for s in state.execution_result.steps if s.permission_tier == "write"]


@pytest.mark.parametrize(
    "message",
    [
        "remove duplicates then remove empty rows",
        "remove duplicates. Then remove empty rows",
        "remove duplicates, and after that remove empty rows",
        "remove duplicates; once that's done remove empty rows",
        "remove duplicates; once that’s done remove empty rows",
        "remove duplicates, afterwards remove empty rows",
        "Remove duplicates -> remove empty rows",
    ],
)
def test_full_slice_runs_the_steps_in_a_confirmed_stated_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, message: str
) -> None:
    monkeypatch.chdir(tmp_path)

    state, _, _ = _run_chain(
        tmp_path, message, _reversed_plan("remove duplicates", "remove empty rows")
    )

    assert _action_order(state) == _DEDUPE_FIRST
    assert state.reply is not None
    assert _ORDER_SENTENCE not in state.reply


@pytest.mark.parametrize(
    ("message", "dedupe_phrase", "empty_rows_phrase"),
    [
        (
            "Before you remove empty rows, remove duplicates",
            "remove duplicates",
            "remove empty rows",
        ),
        ("remove duplicates and remove empty rows", "remove duplicates", "remove empty rows"),
        (
            "remove duplicates then remove empty rows",
            "get rid of duplicate rows",  # paraphrased: can't be located
            "drop the blank rows",
        ),
    ],
    ids=["before-you", "and", "paraphrased-source-phrases"],
)
@pytest.mark.parametrize("use_personality_voice", [False, True], ids=["template", "personality"])
def test_full_slice_states_the_order_used_when_it_couldnt_be_confirmed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    dedupe_phrase: str,
    empty_rows_phrase: str,
    use_personality_voice: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    speaker = _RecordingPersonalityVoice("Both cleanups went through.")
    expected = (
        "I couldn't tell for sure from your message what order you wanted, so the steps ran "
        "in this order: remove empty rows, then deduplicate."
    )

    state, _, _ = _run_chain(
        tmp_path,
        message,
        _reversed_plan(dedupe_phrase, empty_rows_phrase),
        speaker if use_personality_voice else None,
    )

    assert _action_order(state) == _EMPTY_ROWS_FIRST  # the model's order ran
    assert state.verification_result is not None
    assert state.verification_result.passed is True
    # The order sentence is part of the rendered outcome block, for both
    # speakers, before the file sentence. No model is called.
    assert state.reply is not None
    assert expected in state.reply
    file_sentence = "Your cleaned copy" if use_personality_voice else "Saved as"
    assert state.reply.index(expected) < state.reply.index(file_sentence)
    assert speaker._provider.calls == []  # type: ignore[union-attr]


# -- Live-run L-2, end to end: run 7's plan ---------------------------------------------


def test_full_slice_does_not_run_a_step_the_user_never_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The planner added empty-row removal with source_phrase "". Only the
    dedupe the user asked for runs. (This fixture's empty row ends up last
    after dedupe, where the xlsx round trip drops it, so the check is on
    the steps that ran, not on the empty row.)"""
    monkeypatch.chdir(tmp_path)
    replies = _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.remove_empty_rows", ""),
        ("spreadsheet.analyze", "then give me a report"),
    )

    state, original_path, original_bytes = _run_chain(
        tmp_path, "remove the duplicate rows and then give me a report", replies
    )

    assert _action_order(state) == ["spreadsheet.deduplicate"]
    result = state.execution_result
    assert result is not None
    assert result.stopped_at is None
    assert "spreadsheet.remove_empty_rows" not in [s.capability for s in result.steps]
    assert int(pd.read_excel(result.outputs[0]).duplicated().sum()) == 0
    assert original_path.read_bytes() == original_bytes


# -- Verified outcome (PR-A): the reply's facts come from code, end to end ---------------

# The review's literal Voice-model replies (N1 a/b/c, N2, C2, N5). Each is queued as
# the reply a Voice model call would get; none may reach the user.
_REVIEW_MODEL_REPLIES = (
    "All done — duplicates removed.",
    "Duplicates are gone and your sheet is spotless now.",
    "Got rid of those pesky dupes for you — enjoy the tidy sheet!",
    "Removed 3 exact duplicate rows — your sheet is clean.",
    "Done — duplicates and empty rows are gone from report.",
    "Both done: empty rows and duplicates are gone.",
)


def _single_intent_replies(intent: str) -> list[str]:
    understand = json.dumps(
        {"intent": intent, "confidence": 0.95, "missing_information": [], "notes": ""}
    )
    think = json.dumps(
        {
            "objective": "Clean up the spreadsheet.",
            "summary": "Clear enough to proceed.",
            "clarification_required": False,
            "confidence": 0.9,
        }
    )
    return [understand, think]


def _run_single(
    tmp_path: Path,
    rows: dict[str, list[object]],
    replies: list[str],
    speaker_kind: str = "template",
) -> tuple[BuddyState, _QueuedLLMProvider, Path, bytes]:
    """One single-intent turn. ``speaker_kind``: ``"template"`` for the
    default ``TemplateSpeaker``, or a mode for ``PersonalityVoice``."""
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    pd.DataFrame(rows).to_excel(tmp_path / input_path, index=False)
    original_bytes = (tmp_path / input_path).read_bytes()
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove the duplicate rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    speaker = None
    if speaker_kind != "template":
        state.context.set_mode(speaker_kind)
        speaker = PersonalityVoice()
    provider = _QueuedLLMProvider(replies)
    build_engine(llm_provider=provider, speaker=speaker).run(state)
    return state, provider, tmp_path / input_path, original_bytes


def _numbers_outside_file_names(reply: str, *names: str) -> set[int]:
    for name in names:
        reply = reply.replace(name, "")
    return {int(n) for n in re.findall(r"\d+", reply)}


_NO_DUPLICATES = {"name": ["Alice", "Bob", "Carol", "Dana"], "amount": [10, 20, 30, 40]}


@pytest.mark.parametrize("model_reply", _REVIEW_MODEL_REPLIES)
@pytest.mark.parametrize("speaker_kind", ["template", "friend", "focus"])
def test_n2_end_to_end_says_there_was_nothing_to_remove(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, model_reply: str, speaker_kind: str
) -> None:
    """N2: a correct dedupe on a file with no duplicates. The reply's count
    comes from the verifier; no model text, and no "3", can appear."""
    monkeypatch.chdir(tmp_path)
    replies = [*_single_intent_replies("remove_duplicate_rows"), model_reply]

    state, provider, original_path, original_bytes = _run_single(
        tmp_path, _NO_DUPLICATES, replies, speaker_kind
    )

    # No Voice model call: the queued model reply was never consumed.
    assert provider._replies == [model_reply]
    result = state.execution_result
    assert result is not None and len(result.outputs) == 1
    delivered = pd.read_excel(result.outputs[0])
    original = pd.read_excel(original_path)
    assert int(original.duplicated().sum()) == 0

    reply = state.reply
    assert reply is not None
    assert model_reply not in reply
    assert "Done — there were no exact duplicate rows to remove." in reply
    assert (
        f"The copy has {_n(len(delivered), 'row')}, with "
        f"{_n(int(delivered.duplicated().sum()), 'exact duplicate row')} remaining."
    ) in reply
    output_name = Path(result.outputs[0]).name
    # Every number in the reply is one the independent read confirms.
    assert _numbers_outside_file_names(reply, output_name) <= {len(delivered), 0}
    if speaker_kind == "template":
        assert f"Saved as `{output_name}`." in reply
    else:
        assert "Your cleaned copy of report.xlsx is ready." in reply
    assert original_path.read_bytes() == original_bytes  # rule 2


@pytest.mark.parametrize("speaker_kind", ["template", "friend", "focus"])
def test_a_single_step_verified_turn_still_says_done_and_names_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """Regression: a single-intent turn whose capability passed keeps
    "Done —", with the verifier's counts, and names the file."""
    monkeypatch.chdir(tmp_path)
    rows = {"name": ["Alice", "Bob", "Alice", "Carol", "Bob"], "amount": [10, 20, 10, 30, 20]}
    replies = _single_intent_replies("remove_duplicate_rows")

    state, _, original_path, original_bytes = _run_single(tmp_path, rows, replies, speaker_kind)

    result = state.execution_result
    assert result is not None
    delivered, original = pd.read_excel(result.outputs[0]), pd.read_excel(original_path)
    removed = len(original) - len(delivered)
    assert removed == int(original.duplicated().sum()) == 2
    reply = state.reply
    assert reply is not None
    block = (
        f"Done — removed {_n(removed, 'exact duplicate row')}. "
        f"The copy has {_n(len(delivered), 'row')}, "
        f"with {_n(int(delivered.duplicated().sum()), 'exact duplicate row')} remaining."
    )
    if speaker_kind == "template":
        assert reply.startswith(block)
        assert f"Saved as `{Path(result.outputs[0]).name}`." in reply
    else:
        assert block in reply
        assert "Your cleaned copy of report.xlsx is ready." in reply
        if speaker_kind == "focus":
            assert reply.startswith(block)  # Focus mode has no opener
    assert original_path.read_bytes() == original_bytes


def test_a_dropped_second_action_never_yields_a_nothing_to_remove_opener(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression (PR-A review): "remove duplicates then remove empty rows",
    but the planner returns only dedupe, and the file has no duplicates and one
    empty row. Dedupe measures removed=0, and the delivered file still has the
    empty row the user asked to remove. "Nothing to remove" would be false, so
    friend mode must never say it (PR-A has no ledger of what was requested)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    pd.DataFrame(
        {
            "name": ["Alice", "Bob", None, "Carol", "Dana"],
            "amount": [10, 20, None, 30, 40],
        }
    ).to_excel(tmp_path / input_path, index=False, engine="openpyxl")
    original_bytes = (tmp_path / input_path).read_bytes()
    original = pd.read_excel(tmp_path / input_path)
    assert int(original.duplicated().sum()) == 0
    assert int(original.isna().all(axis=1).sum()) == 1

    replies = _chain_replies(("spreadsheet.deduplicate", "remove duplicates"))
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove duplicates then remove empty rows"
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    state.context.set_mode("friend")
    build_engine(llm_provider=_QueuedLLMProvider(replies), speaker=PersonalityVoice()).run(state)

    assert _action_order(state) == ["spreadsheet.deduplicate"]  # empty rows never ran
    result = state.execution_result
    assert result is not None and len(result.outputs) == 1
    delivered = pd.read_excel(result.outputs[0])
    # The user's second request is still unmet in the delivered file.
    assert int(delivered.isna().all(axis=1).sum()) == 1

    reply = state.reply
    assert reply is not None
    assert "nothing to remove" not in reply.lower()
    output_name = Path(result.outputs[0]).name
    # Every count in the outcome block is one a pandas read of the delivered file confirms.
    outcome, _, findings = reply.partition("I also noticed:")
    assert _numbers_outside_file_names(outcome, output_name) <= {
        len(delivered),
        int(delivered.duplicated().sum()),
        int(delivered.isna().all(axis=1).sum()),  # PR-B: the residual empty row
        0,
    }
    # PR-B: the unrun request is stated, with what's still in the file. Brain's
    # reply has no requested_actions, so only the mention detector has "empty
    # rows": no "Partly done" (decision D2), and no "Done".
    assert (
        "Your message mentions empty rows, but I didn't run the empty-row removal." in reply
    )
    assert "It still has 1 fully-empty row." in reply
    assert "Done —" not in reply
    # The analysis finding's counts (the empty row's missing cells) match pandas too.
    missing = {int(n) for n in delivered.isna().sum()}
    percent = {round(100 * m / len(delivered)) for m in missing}
    assert {int(n) for n in re.findall(r"\d+", findings)} <= missing | percent | {0}  # "20.0%"
    assert f"The copy has {_n(len(delivered), 'row')}" in reply
    assert f"{_n(int(delivered.duplicated().sum()), 'exact duplicate row')} remaining" in reply
    assert (tmp_path / input_path).read_bytes() == original_bytes  # rule 2


# -- H1: dedupe also removes identical blank rows, so "removed" must count them ------------

_BT_ROWS = {"name": ["Alice", None, "Bob", None, "Carol"], "amount": [1, None, 2, None, 3]}
_X_TWO_EMPTY_ROWS = {
    "name": ["Alice", None, "Bob", "Alice", None, "Carol"],
    "amount": [1, None, 2, 1, None, 3],
}


def _run_rows(
    tmp_path: Path, rows: dict[str, list[object]], speaker_kind: str, chain: bool
) -> tuple[BuddyState, Path, bytes]:
    """One turn on ``rows``: a single dedupe, or the chain "remove duplicates,
    then remove empty rows". ``speaker_kind``: ``"template"``, ``"friend"`` or ``"focus"``."""
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    pd.DataFrame(rows).to_excel(tmp_path / input_path, index=False)
    original_bytes = (tmp_path / input_path).read_bytes()
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = (
        "remove duplicates then remove empty rows" if chain else "remove the duplicate rows"
    )
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    speaker = None
    if speaker_kind != "template":
        state.context.set_mode(speaker_kind)
        speaker = PersonalityVoice()
    replies = (
        _chain_replies(
            ("spreadsheet.deduplicate", "remove duplicates"),
            ("spreadsheet.remove_empty_rows", "remove empty rows"),
        )
        if chain
        else _single_intent_replies("remove_duplicate_rows")
    )
    build_engine(llm_provider=_QueuedLLMProvider(replies), speaker=speaker).run(state)
    return state, tmp_path / input_path, original_bytes


@pytest.mark.parametrize("speaker_kind", ["template", "friend", "focus"])
@pytest.mark.parametrize("chain", [False, True], ids=["single-step", "chain"])
@pytest.mark.parametrize(
    "rows", [_BT_ROWS, _X_TWO_EMPTY_ROWS], ids=["BT-alice-blank-bob-blank-carol", "X-two-empty"]
)
def test_h1_removed_counts_repeated_blank_rows_and_every_number_matches_pandas(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    rows: dict[str, list[object]],
    chain: bool,
    speaker_kind: str,
) -> None:
    """H1: dedupe removes all but one of identical blank rows. The reply's
    "Removed N" is what pandas shows the step removed (rows in minus rows out),
    and it says how many of those were repeated blank rows."""
    monkeypatch.chdir(tmp_path)

    state, original_path, original_bytes = _run_rows(tmp_path, rows, speaker_kind, chain)

    result = state.execution_result
    assert result is not None and len(result.outputs) == 1
    original, delivered = pd.read_excel(original_path), pd.read_excel(result.outputs[0])
    rows_in, rows_out = len(original), len(delivered)
    blank_in = int(original.isna().all(axis=1).sum())
    duplicates_in = int(original.duplicated().sum())  # blank repeats included
    assert (blank_in, duplicates_in) == (2, 2 if rows is _X_TWO_EMPTY_ROWS else 1)
    reply = state.reply
    assert reply is not None
    outcome = reply.partition("I also noticed:")[0]

    # The rendered "Removed N ..." sentences add up to rows in minus rows out.
    removed_sentences = re.findall(r"[Rr]emoved (\d+) ([a-z -]+? rows?)", outcome)
    assert sum(int(n) for n, _ in removed_sentences) == rows_in - rows_out
    duplicate_sentence = re.search(
        r"(?:Done — )?[Rr]emoved (\d+) exact duplicate rows?"
        r"(?:, including (\d+) repeated blank rows?)?\.",
        outcome,
    )
    assert duplicate_sentence is not None, reply
    # Dedupe's own count: every duplicate row of the input, blank repeats included.
    assert int(duplicate_sentence[1]) == duplicates_in
    # ... of which the repeated blank rows are the input's blank rows beyond the one kept.
    assert int(duplicate_sentence[2]) == blank_in - 1 == 1
    if chain:
        # The chain's second step removes the one blank row dedupe kept.
        assert removed_sentences[1] == ("1", "fully-empty row")
        assert (rows_in, rows_out) == (len(rows["name"]), len(rows["name"]) - duplicates_in - 1)
        # PR-B (review H1): Brain gave no reading, so the mention detector alone
        # has both actions and never confirms a request: no "Done".
        assert "Done" not in reply
        expected = (
            f"The copy has {_n(rows_out, 'row')}, with 0 exact duplicate rows and 0 fully-empty"
        )
        assert expected in reply
    else:
        assert len(removed_sentences) == 1
        assert rows_out == rows_in - duplicates_in
        assert reply.count("Done — removed") == 1
        assert (
            f"The copy has {_n(rows_out, 'row')}, with 0 exact duplicate rows remaining." in reply
        )
    assert int(delivered.duplicated().sum()) == 0
    assert int(delivered.isna().all(axis=1).sum()) == (0 if chain else 1)

    # Every number in the outcome block (file names aside) is one pandas confirms.
    output_name = Path(result.outputs[0]).name
    assert _numbers_outside_file_names(outcome, output_name) <= {
        rows_in - rows_out,
        duplicates_in,
        blank_in - 1,
        1,  # the chain's empty-row step
        rows_out,
        0,
    }
    # The analysis finding's counts (the leftover blank row) match pandas too.
    if "I also noticed:" in reply:
        findings = reply.partition("I also noticed:")[2]
        missing = {int(n) for n in delivered.isna().sum()}
        percent = {round(100 * m / rows_out) for m in missing}
        assert {int(n) for n in re.findall(r"\d+", findings)} <= missing | percent | {0}
    if speaker_kind == "template":
        assert f"Saved as `{Path(result.outputs[0]).name}`." in reply
    else:
        assert "Your cleaned copy of report.xlsx is ready." in reply
    # The old false sentence and the old undercount can't come back.
    assert "no exact duplicate rows to remove" not in reply
    # M1: a closer implying the request is finished appears only when the
    # outcome block says every requested action is done.
    for closer in ("Anything else", "What's next"):
        assert closer not in reply or "Done — " in reply
    assert original_path.read_bytes() == original_bytes  # rule 2


@pytest.mark.parametrize("speaker_kind", ["template", "friend", "focus"])
def test_h1_an_unconfirmable_count_renders_the_no_count_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """The step keeps a blank row last, and the .xlsx round trip drops it: rows in
    minus rows out (2) isn't what dedupe removed (1). The verifier can't confirm
    the count, so the reply states no number for the removal."""
    monkeypatch.chdir(tmp_path)
    rows = {"name": ["Alice", None, "Alice"], "amount": [1, None, 1]}

    state, original_path, original_bytes = _run_rows(tmp_path, rows, speaker_kind, chain=False)

    result = state.execution_result
    assert result is not None and len(result.outputs) == 1
    original, delivered = pd.read_excel(original_path), pd.read_excel(result.outputs[0])
    assert (len(original), len(delivered)) == (3, 1)
    reply = state.reply
    assert reply is not None
    assert "the duplicate-row removal ran and passed its check." in reply
    assert "Removed" not in reply and "removed" not in reply
    assert "no exact duplicate rows to remove" not in reply
    assert (
        f"The copy has {_n(len(delivered), 'row')}, with 0 exact duplicate rows remaining." in reply
    )
    assert original_path.read_bytes() == original_bytes  # rule 2


# -- Verified outcome (PR-B): the requested-actions ledger, end to end ---------------------
# The review's default fixture: 6 rows, 2 exact duplicates, and 1 fully-empty row
# mid-file that dedupe doesn't remove. Every scenario runs through build_engine with
# real skills and a fake LLM; the review's Voice-model replies are queued after
# Brain/Think/Planner, so a Voice model call would consume one (it never does).

_REVIEW_ROWS = {
    "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
    "amount": [10, 20, None, 10, 30, 20],
}

# The review's false phrases (N1 a/b/c, N2, C2, N5): none may reach any reply.
_FALSE_PHRASES = (
    "duplicates removed",
    "Duplicates are gone",
    "pesky dupes",
    "Removed 3",
    "empty rows are gone",
    "Both done",
    "spotless",
    "sorted",
)

_THINK_PROCEEDS = json.dumps(
    {
        "objective": "Clean up the spreadsheet.",
        "summary": "Clear enough to proceed.",
        "clarification_required": False,
        "confidence": 0.9,
    }
)


def _brain_ledger_reply(intent: str, *requested: tuple[str, str] | None) -> str:
    """Brain's reply; ``requested`` is its ``requested_actions`` as ``(action, phrase)``,
    or a single ``None`` for a reply without the field (older replies)."""
    reply: dict[str, object] = {"intent": intent, "confidence": 0.9}
    if requested != (None,):
        reply["requested_actions"] = [
            {"action": action, "source_phrase": phrase}
            for action, phrase in requested  # type: ignore[misc]
        ]
    return json.dumps(reply)


def _plan_reply(*planned: tuple[str, str]) -> str:
    return json.dumps(
        {
            "steps": [
                {
                    "description": f"do {capability}",
                    "capability": capability,
                    "depends_on_step_numbers": [],
                    "source_phrase": phrase,
                }
                for capability, phrase in planned
            ]
        }
    )


def _run_ledger_turn(
    tmp_path: Path,
    message: str,
    replies: list[str],
    speaker_kind: str,
    model_reply: str,
    session_id: str = "session-1",
    second_sheet: bool = False,
) -> tuple[BuddyState, _QueuedLLMProvider, Path, bytes]:
    """One turn on the review's fixture. ``speaker_kind``: ``"template"``, or a mode
    for ``PersonalityVoice`` (given the same provider, so any Voice call would show).
    ``second_sheet`` adds a second sheet with duplicates of its own."""
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
    if second_sheet:
        with pd.ExcelWriter(tmp_path / input_path) as writer:
            pd.DataFrame(_REVIEW_ROWS).to_excel(writer, sheet_name="Q1", index=False)
            pd.DataFrame(_REVIEW_ROWS).to_excel(writer, sheet_name="Q2", index=False)
    else:
        pd.DataFrame(_REVIEW_ROWS).to_excel(tmp_path / input_path, index=False)
    original_bytes = (tmp_path / input_path).read_bytes()
    state = BuddyState.new(session_id=session_id, workspace_id="workspace-1")
    state.user_message = message
    state.context.add_uploaded_file(str(input_path))
    state.context.switch_active_file(str(input_path))
    provider = _QueuedLLMProvider([*replies, model_reply])
    speaker = None
    if speaker_kind != "template":
        state.context.set_mode(speaker_kind)
        speaker = PersonalityVoice(provider)
    build_engine(llm_provider=provider, speaker=speaker).run(state)
    return state, provider, tmp_path / input_path, original_bytes


def _counts(path: Path | str) -> tuple[int, int, int]:
    """(rows, exact duplicate rows, fully-empty rows), by a fresh pandas read."""
    df = pd.read_excel(path)
    return len(df), int(df.duplicated().sum()), int(df.isna().all(axis=1).sum())


def _check_delivered_reply(
    state: BuddyState,
    provider: _QueuedLLMProvider,
    model_reply: str,
    original_path: Path,
    original_bytes: bytes,
    speaker_kind: str,
    expected_block: str,
    allowed_numbers: set[int],
) -> str:
    """The checks every delivered PR-B scenario shares. Returns the reply."""
    result = state.execution_result
    assert result is not None and len(result.outputs) == 1
    assert state.verification_result is not None and state.verification_result.passed
    reply = state.reply
    assert reply is not None
    # 1. No model text: the queued Voice reply was never consumed or shown.
    assert provider._replies == [model_reply]
    assert model_reply not in reply
    # 2. None of the review's false phrases.
    for phrase in _FALSE_PHRASES:
        assert phrase.lower() not in reply.lower(), (phrase, reply)
    # 3. The code-rendered block, with every count from pandas.
    assert expected_block in reply, reply
    output_name = Path(result.outputs[0]).name
    outcome = reply.partition("I also noticed:")[0]
    assert _numbers_outside_file_names(outcome, output_name) <= allowed_numbers, reply
    # 4. No bare "Done" when anything requested wasn't done.
    assert re.search(r"(?<!Partly )[Dd]one —", reply) is None, reply
    # ... and no closer that presupposes the request is finished.
    for closer in ("Anything else", "What's next"):
        assert closer not in reply
    if speaker_kind == "template":
        assert f"Saved as `{output_name}`." in reply
    else:
        assert "Your cleaned copy of report.xlsx is ready." in reply
    if speaker_kind == "focus":
        assert reply.startswith(expected_block.split(" ", 1)[0])  # no opener
    # 5. Rule 2: the original is byte-identical.
    assert original_path.read_bytes() == original_bytes
    return reply


_SPEAKERS = ["template", "friend", "focus"]


@pytest.mark.parametrize("model_reply", _REVIEW_MODEL_REPLIES[:3])
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    "brain",
    [
        # Worst case: Brain's ledger makes the same error as its intent.
        _brain_ledger_reply(
            "remove_empty_rows", ("remove_empty_rows", "remove the duplicate rows please")
        ),
        _brain_ledger_reply("remove_empty_rows", None),  # no requested_actions at all
    ],
    ids=["ledger-wrong-too", "no-ledger-field"],
)
def test_n1_misclassified_dedupe_request_says_what_ran_and_what_didnt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, brain: str, speaker_kind: str, model_reply: str
) -> None:
    """N1: "remove the duplicate rows please", Brain reads remove_empty_rows and
    Think proceeds. Empty-row removal runs; the mention detector adds dedupe as
    not read as a request. D2: no "Partly done", and no "Done"."""
    monkeypatch.chdir(tmp_path)

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove the duplicate rows please", [brain, _THINK_PROCEEDS], speaker_kind,
        model_reply,
    )

    assert state.understanding is not None
    assert state.understanding.intent == "remove_empty_rows"
    assert _action_order(state) == ["spreadsheet.remove_empty_rows"]
    assert state.execution_result is not None
    rows_in, _, empty_in = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    assert (rows_in - rows_out, empty_in, empty_out, dups_out) == (1, 1, 0, 2)
    block = (
        f"Removed {_n(rows_in - rows_out, 'fully-empty row')}. Your message mentions duplicate "
        "rows, but I didn't run the duplicate-row removal. I read your request as: remove empty "
        f"rows. The copy has {_n(rows_out, 'row')}, "
        f"with {_n(empty_out, 'fully-empty row')} remaining. "
        f"It still has {_n(dups_out, 'exact duplicate row')}."
    )
    reply = _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, empty_out, dups_out},
    )
    assert "Partly done" not in reply


@pytest.mark.parametrize(
    "speaker_kind", _SPEAKERS, ids=["C1-template", "C2-friend", "C2-focus"]
)
def test_c1_c2_a_step_the_planner_dropped_is_stated_as_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """C1/C2: "remove duplicates then remove empty rows", Brain lists both, the
    planner returns only dedupe. C2's model reply can't reach the user."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "multi_step_request",
        ("remove_duplicate_rows", "remove duplicates"),
        ("remove_empty_rows", "remove empty rows"),
    )
    plan = _plan_reply(("spreadsheet.deduplicate", "remove duplicates"))
    model_reply = "Done — duplicates and empty rows are gone from report."

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove duplicates then remove empty rows", [brain, _THINK_PROCEEDS, plan],
        speaker_kind, model_reply,
    )

    assert _action_order(state) == ["spreadsheet.deduplicate"]
    assert state.execution_result is not None
    rows_in, dups_in, _ = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    assert (rows_in - rows_out, dups_in, dups_out, empty_out) == (2, 2, 0, 1)
    block = (
        f"Partly done — removed {_n(rows_in - rows_out, 'exact duplicate row')}. I didn't run the "
        "empty-row removal, though I read it as part of your request. The copy has "
        f"{_n(rows_out, 'row')}, with {_n(dups_out, 'exact duplicate row')} remaining. "
        f"It still has {_n(empty_out, 'fully-empty row')}."
    )
    reply = _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, dups_out, empty_out},
    )
    if speaker_kind == "friend":
        # Any friend opener must be true of a partly-done turn.
        opener = reply.partition(" Partly done —")[0]
        assert opener in {
            "Here's where things landed.",
            "Okay, here's the rundown.",
            "That sheet was seeing double.",
            "Heads-up: only part of this went through.",
        }


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    "brain",
    [
        _brain_ledger_reply(
            "remove_duplicate_rows", ("remove_duplicate_rows", "remove duplicates")
        ),
        _brain_ledger_reply("remove_duplicate_rows", None),
    ],
    ids=["ledger", "no-ledger-field"],
)
def test_c3_a_compound_request_brain_read_as_one_action(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, brain: str, speaker_kind: str
) -> None:
    """C3: "remove duplicates and also delete the empty rows", Brain reads
    remove_duplicate_rows. The mention detector has the empty rows (D2)."""
    monkeypatch.chdir(tmp_path)
    model_reply = "Done — duplicates and empty rows are gone from report."

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove duplicates and also delete the empty rows", [brain, _THINK_PROCEEDS],
        speaker_kind, model_reply,
    )

    assert _action_order(state) == ["spreadsheet.deduplicate"]
    assert state.execution_result is not None
    rows_in, _, _ = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    assert (rows_in - rows_out, empty_out) == (2, 1)
    block = (
        f"Removed {_n(rows_in - rows_out, 'exact duplicate row')}. Your message mentions empty "
        f"rows, "
        "but I didn't run the empty-row removal. I read your request as: remove duplicate rows. "
        f"The copy has {_n(rows_out, 'row')}, "
        f"with {_n(dups_out, 'exact duplicate row')} remaining. It still has "
        f"{_n(empty_out, 'fully-empty row')}."
    )
    _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, dups_out, empty_out},
    )


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_n5_unrequested_work_is_stated_as_such_and_the_request_as_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """N5: "remove empty rows"; Brain says multi_step_request but lists only empty
    rows; the planner turns the request into analysis + dedupe."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "multi_step_request", ("remove_empty_rows", "remove empty rows")
    )
    plan = _plan_reply(
        ("spreadsheet.analyze", "remove empty rows"),
        ("spreadsheet.deduplicate", "remove empty rows"),
    )
    model_reply = "Both done: empty rows and duplicates are gone."

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove empty rows", [brain, _THINK_PROCEEDS, plan], speaker_kind, model_reply
    )

    assert _action_order(state) == ["spreadsheet.deduplicate"]
    assert state.execution_result is not None
    rows_in, _, _ = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    assert (rows_in - rows_out, empty_out) == (2, 1)
    block = (
        f"I removed {_n(rows_in - rows_out, 'exact duplicate row')}, "
        "though I didn't read that as part of "
        "your request. I didn't run the empty-row removal, though I read it as part of your "
        f"request. The copy has {_n(rows_out, 'row')}, "
        f"with {_n(dups_out, 'exact duplicate row')} remaining. "
        f"It still has {_n(empty_out, 'fully-empty row')}."
    )
    reply = _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, dups_out, empty_out},
    )
    assert "Partly done" not in reply  # nothing requested was done
    assert "seeing double" not in reply  # no opener celebrates unrequested work


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_n5_without_a_brain_ledger_claims_nothing_about_the_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """N5 with an older Brain reply (no requested_actions): there's no reading to
    call the dedupe unrequested, so the reply says only what ran and what the
    message mentions that didn't."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply("multi_step_request", None)
    plan = _plan_reply(("spreadsheet.deduplicate", "remove empty rows"))
    model_reply = "Both done: empty rows and duplicates are gone."

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove empty rows", [brain, _THINK_PROCEEDS, plan], speaker_kind, model_reply
    )

    assert state.execution_result is not None
    rows_in, _, _ = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    block = (
        f"Removed {_n(rows_in - rows_out, 'exact duplicate row')}. Your message mentions empty "
        f"rows, "
        "but I didn't run the empty-row removal. The copy has "
        f"{_n(rows_out, 'row')}, with {_n(dups_out, 'exact duplicate row')} remaining. "
        f"It still has {_n(empty_out, 'fully-empty row')}."
    )
    reply = _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, dups_out, empty_out},
    )
    assert "didn't read that" not in reply and "I read your request as" not in reply


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    ("phrase", "expected_line"),
    [
        ("sort by amount", "I can't do this part yet: “sort by amount”."),
        # The model paraphrased: its words are never quoted as the user's.
        ("sort the rows by amount", "I read part of your request as something I can't do yet."),
    ],
    ids=["users-words", "paraphrased"],
)
def test_dedupe_and_sort_says_the_sort_part_cant_be_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phrase: str,
    expected_line: str,
    speaker_kind: str,
) -> None:
    """Review M2's "dedupe and sort": Brain reads remove_duplicate_rows and lists
    the sort as unsupported. No sort step exists, and the reply never says one ran."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_duplicate_rows",
        ("remove_duplicate_rows", "remove duplicates"),
        ("sort_rows", phrase),  # outside the vocabulary: recorded as unsupported
    )
    model_reply = "All done — duplicates removed."

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove duplicates and sort by amount", [brain, _THINK_PROCEEDS],
        speaker_kind, model_reply,
    )

    assert state.execution_result is not None
    rows_in, _, _ = _counts(original_path)
    rows_out, dups_out, _ = _counts(state.execution_result.outputs[0])
    block = (
        f"Partly done — removed {_n(rows_in - rows_out, 'exact duplicate row')}. "
        f"{expected_line} The copy "
        f"has {_n(rows_out, 'row')}, with {_n(dups_out, 'exact duplicate row')} remaining."
    )
    _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, dups_out},
    )


def test_dedupe_and_sort_as_a_chain_hands_nothing_back_and_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same message read as multi_step_request: the planner adds a sort step
    that has no skill, so the chain can't finish. Since PR-3's review M1 it
    doesn't start: dedupe never runs, and the reply says so."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "multi_step_request",
        ("remove_duplicate_rows", "remove duplicates"),
        ("unsupported", "sort by amount"),
    )
    plan = _plan_reply(
        ("spreadsheet.deduplicate", "remove duplicates"), ("spreadsheet.sort", "sort by amount")
    )

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove duplicates and sort by amount", [brain, _THINK_PROCEEDS, plan],
        "friend", "All done — duplicates removed.",
    )

    assert state.execution_result is not None
    assert state.execution_result.outputs == ()
    assert ("spreadsheet.deduplicate", "skipped") in [
        (step.capability, step.status) for step in state.execution_result.steps
    ]
    assert state.reply is not None
    assert (
        "I stopped at the sort step, so deduplicate never ran and no file was handed back"
        in state.reply
    )
    assert _COMPLETION_CLAIM.search(state.reply) is None, state.reply
    assert original_path.read_bytes() == original_bytes


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_remove_the_empty_rows_but_keep_the_duplicates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """A mention-detector false positive (design 3.2): "duplicates" is mentioned
    but not requested. The added sentence is true, and nothing is removed that
    the user said to keep."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_empty_rows", ("remove_empty_rows", "remove the empty rows")
    )
    model_reply = "All done — duplicates removed."

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove the empty rows but keep the duplicates", [brain, _THINK_PROCEEDS],
        speaker_kind, model_reply,
    )

    assert _action_order(state) == ["spreadsheet.remove_empty_rows"]
    assert state.execution_result is not None
    rows_in, dups_in, _ = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    assert dups_out == dups_in == 2  # the duplicates were kept
    block = (
        f"Removed {_n(rows_in - rows_out, 'fully-empty row')}. Your message mentions duplicate "
        "rows, but I didn't run the duplicate-row removal. I read your request as: remove empty "
        f"rows. The copy has {_n(rows_out, 'row')}, "
        f"with {_n(empty_out, 'fully-empty row')} remaining. "
        f"It still has {_n(dups_out, 'exact duplicate row')}."
    )
    reply = _check_delivered_reply(
        state, provider, model_reply, original_path, original_bytes, speaker_kind, block,
        {rows_in - rows_out, rows_out, empty_out, dups_out},
    )
    assert "Partly done" not in reply


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_a_fully_done_chain_says_done_and_may_close(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """Regression: both requested actions ran and passed, so "Done —" leads,
    the file is named, and there's nothing left for a residual to report."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "multi_step_request",
        ("remove_duplicate_rows", "remove the duplicate rows"),
        ("remove_empty_rows", "remove the empty rows"),
    )
    plan = _plan_reply(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
    )

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, "remove the duplicate rows and then remove the empty rows",
        [brain, _THINK_PROCEEDS, plan], speaker_kind, "Both done!",
    )

    assert state.execution_result is not None
    rows_in, _, _ = _counts(original_path)
    rows_out, dups_out, empty_out = _counts(state.execution_result.outputs[0])
    assert (rows_out, dups_out, empty_out) == (3, 0, 0)
    reply = state.reply
    assert reply is not None
    assert provider._replies == ["Both done!"]
    assert (
        f"Done — removed 2 exact duplicate rows. Removed 1 fully-empty row. The copy has "
        f"{_n(rows_out, 'row')}, with {_n(dups_out, 'exact duplicate row')} and "
        f"{_n(empty_out, 'fully-empty row')} remaining."
    ) in reply
    assert "still has" not in reply and "didn't run" not in reply
    assert original_path.read_bytes() == original_bytes


# -- Verified outcome (PR-B fixes): review H1, H2 and M1, end to end -----------------------

_MODEL_REPLY = "All done — duplicates and empty rows are gone."
_COMPLETION_CLOSERS = ("Anything else", "What's next")
_SEEDS = ["s1", "s2", "s3", "s4", "s5", "s6"]


def _delivered_turn(
    tmp_path: Path,
    message: str,
    replies: list[str],
    speaker_kind: str = "template",
    session_id: str = "session-1",
) -> tuple[str, Path]:
    """One delivered turn; checks the parts every fix scenario shares. Returns the reply
    and the delivered file."""
    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, message, replies, speaker_kind, _MODEL_REPLY, session_id
    )
    result = state.execution_result
    assert result is not None and len(result.outputs) == 1
    assert state.verification_result is not None and state.verification_result.passed
    reply = state.reply
    assert reply is not None
    assert provider._replies == [_MODEL_REPLY]  # the Voice model was never called
    assert _MODEL_REPLY not in reply
    assert original_path.read_bytes() == original_bytes  # rule 2
    return reply, Path(result.outputs[0])


def _no_completion_claim(reply: str) -> None:
    assert "Done" not in reply and "done —" not in reply, reply
    for closer in _COMPLETION_CLOSERS:
        assert closer not in reply, reply
    assert "only part of this went through" not in reply, reply


_H1_CHAIN_PLAN = _plan_reply(
    ("spreadsheet.deduplicate", "remove duplicates"),
    ("spreadsheet.remove_empty_rows", "remove the empty rows"),
)


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_c2_a_step_the_user_forbade_is_unrequested_and_the_reply_never_says_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str, session_id: str
) -> None:
    """Review H1, c2: Brain reads only remove_empty_rows; the planner also ran dedupe.
    The mention detector matches "remove duplicates" inside a negation, so it can't make
    that step requested: it is stated as not read as a request, and nothing leads "Done"."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "multi_step_request", ("remove_empty_rows", "remove the empty rows")
    )

    reply, delivered = _delivered_turn(
        tmp_path, "don't remove duplicates, just remove the empty rows",
        [brain, _THINK_PROCEEDS, _H1_CHAIN_PLAN], speaker_kind, session_id,
    )

    assert _counts(delivered) == (3, 0, 0)
    assert (
        "I also removed 2 exact duplicate rows, though I didn't read that as part of your request."
    ) in reply
    assert "Removed 1 fully-empty row." in reply
    assert "sheet was seeing double" not in reply  # no opener praising the forbidden step
    _no_completion_claim(reply)


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    ("message", "duplicate_phrase"),
    [
        ("remove the empty rows but keep the duplicates", "the duplicates"),  # c3
        ("keep the duplicates, remove the empty rows", "the duplicates"),  # c7
    ],
    ids=["c3", "c7"],
)
def test_c3_c7_with_no_reading_from_brain_a_mention_never_says_done(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    message: str,
    duplicate_phrase: str,
    speaker_kind: str,
    session_id: str,
) -> None:
    """Review H1, c3 and c7: a multi_step_request reply with no requested_actions, and
    a planner that also ran dedupe. Both steps are in the ledger only through the mention
    detector, which can't confirm a request: the removals are stated, nothing more."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply("multi_step_request", None)
    empty, dedupe = (
        ("spreadsheet.remove_empty_rows", "remove the empty rows"),
        ("spreadsheet.deduplicate", duplicate_phrase),
    )
    plan = (
        _plan_reply(empty, dedupe) if message.startswith("remove") else _plan_reply(dedupe, empty)
    )

    reply, delivered = _delivered_turn(
        tmp_path, message, [brain, _THINK_PROCEEDS, plan], speaker_kind, session_id
    )

    assert _counts(delivered) == (3, 0, 0)
    assert "emoved 2 exact duplicate rows" in reply and "emoved 1 fully-empty row" in reply
    _no_completion_claim(reply)


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_c6_nothing_requested_was_done_so_the_reply_says_neither_done_nor_partly_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str, session_id: str
) -> None:
    """Review H1, c6: Brain reads only remove_empty_rows and the planner ran only dedupe.
    The dedupe is unrequested (the mention doesn't count) and the empty-row removal was
    read as requested but not run. Nothing requested was done, so no "Partly done"."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "multi_step_request", ("remove_empty_rows", "remove the empty rows")
    )
    plan = _plan_reply(("spreadsheet.deduplicate", "remove duplicates"))

    reply, delivered = _delivered_turn(
        tmp_path, "don't remove duplicates, just remove the empty rows",
        [brain, _THINK_PROCEEDS, plan], speaker_kind, session_id,
    )

    assert _counts(delivered) == (4, 0, 1)
    assert (
        "I removed 2 exact duplicate rows, though I didn't read that as part of your request."
    ) in reply
    assert "I didn't run the empty-row removal, though I read it as part of your request." in reply
    assert "It still has 1 fully-empty row." in reply
    assert "Partly done" not in reply
    assert "sheet was seeing double" not in reply
    _no_completion_claim(reply)


_DEDUPE_ONLY_BLOCK = (
    "Done — removed 2 exact duplicate rows. The copy has 4 rows, with 0 exact duplicate rows "
    "remaining."
)


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    "action",
    ["remove_duplicates", "Remove_Duplicate_Rows", "remove_duplicate_rows ", "deduplicate"],
)
def test_k_a_near_miss_action_name_for_a_done_action_is_never_cant_do_yet(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    action: str,
    speaker_kind: str,
    session_id: str,
) -> None:
    """Review H2, k-remove_duplicates: the dedupe ran and passed, and Brain's
    requested_actions spelled its name slightly off. Casing and whitespace are
    normalised; a different name is "unsupported" but names an action that was
    done, so it says nothing. Either way: "Done", never "I can't do this part yet"."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_duplicate_rows", (action, "remove the duplicate rows")
    )

    reply, delivered = _delivered_turn(
        tmp_path, "remove the duplicate rows please", [brain, _THINK_PROCEEDS],
        speaker_kind, session_id,
    )

    assert _counts(delivered) == (4, 0, 1)
    assert _DEDUPE_ONLY_BLOCK in reply, reply
    assert "can't do" not in reply and "Partly done" not in reply, reply
    assert "only part of this went through" not in reply


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_a1_a_whole_message_phrase_is_never_quoted_as_something_buddy_cant_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """Review H2, a1: Brain's unsupported entry quotes the whole message, which names the
    dedupe that ran. It can't be quoted ("I can't do … remove duplicates and sort by
    amount") but the sort part is real, so the generic sentence stays."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_duplicate_rows",
        ("remove_duplicate_rows", "remove duplicates"),
        ("unsupported", "remove duplicates and sort by amount"),
    )

    reply, delivered = _delivered_turn(
        tmp_path, "remove duplicates and sort by amount", [brain, _THINK_PROCEEDS],
        speaker_kind,
    )

    assert _counts(delivered) == (4, 0, 1)
    assert (
        "Partly done — removed 2 exact duplicate rows. "
        "I read part of your request as something I can't do yet. "
        "The copy has 4 rows, with 0 exact duplicate rows remaining."
    ) in reply, reply
    assert "can't do this part yet" not in reply
    assert "sort by amount”" not in reply


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_a4_an_unsupported_entry_naming_only_a_done_action_is_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """Review H2, a4: Brain labels "remove duplicates" itself as unsupported, though the
    dedupe ran. Nothing is left over once the done action's words are removed."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply("remove_duplicate_rows", ("unsupported", "remove duplicates"))

    reply, _ = _delivered_turn(
        tmp_path, "remove duplicates and sort by amount", [brain, _THINK_PROCEEDS],
        speaker_kind,
    )

    assert "can't do" not in reply and "Partly done" not in reply, reply
    assert "emoved 2 exact duplicate rows." in reply


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_b5_keep_the_duplicates_is_not_something_buddy_cant_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """Review H2, b5: Brain lists "keep the duplicates" as unsupported. The duplicates
    were kept, so "I can't do this part yet" would be wrong, and so would "Partly done"."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_empty_rows",
        ("remove_empty_rows", "remove the empty rows"),
        ("unsupported", "keep the duplicates"),
    )

    reply, delivered = _delivered_turn(
        tmp_path, "remove the empty rows but keep the duplicates", [brain, _THINK_PROCEEDS],
        speaker_kind,
    )

    assert _counts(delivered) == (5, 2, 0)
    assert "can't do" not in reply and "keep the duplicates" not in reply, reply
    assert "Partly done" not in reply
    assert "It still has 2 exact duplicate rows." in reply
    _no_completion_claim(reply)


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_l2_the_same_unsupported_part_is_said_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_duplicate_rows",
        ("remove_duplicate_rows", "remove duplicates"),
        ("unsupported", "sort by amount"),
        ("unsupported", "Sort by amount"),
        ("sort_rows", "sort the rows by amount"),  # not the user's words: generic
        ("report", "make a chart"),  # not the user's words: generic
    )

    reply, _ = _delivered_turn(
        tmp_path, "remove duplicates and sort by amount", [brain, _THINK_PROCEEDS],
        speaker_kind,
    )

    assert reply.count("I can't do this part yet: “sort by amount”.") == 1, reply
    assert reply.count("I read part of your request as something I can't do yet.") == 1, reply


@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_m1_the_analyzers_all_clear_is_qualified_next_to_a_residual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str
) -> None:
    """Review M1: "It still has 2 exact duplicate rows." can't be followed by "None of the
    issues I check for were found." The analysis now reports exact duplicates itself
    (backlog item 3), with the same count the residual measurement gives."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply("remove_empty_rows", None)

    reply, _ = _delivered_turn(
        tmp_path, "remove the duplicate rows please", [brain, _THINK_PROCEEDS], speaker_kind
    )

    assert "It still has 2 exact duplicate rows." in reply
    assert "I also noticed: 2 rows repeat an earlier row exactly." in reply
    assert "none of the" not in reply.lower()


# -- Verified outcome (PR-B fixes 2): re-review N1 and N2, end to end ----------------------

_DEDUPE_LEAD_AND_GENERIC = (
    "Partly done — removed 2 exact duplicate rows. "
    "I read part of your request as something I can't do yet."
)


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    "extra", ["delete the file", "clean the data", "drop the sheet", "email me the report"]
)
def test_n1_a_real_unsupported_part_is_never_dropped_behind_a_done_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    extra: str,
    speaker_kind: str,
    session_id: str,
) -> None:
    """Re-review N1 (x2, x3, x4, x5): Brain's unsupported entry is the whole message, which
    names the dedupe that ran plus a part Buddy can't do. Verb and noun words like "delete"
    or "file" must not be treated as filler: the not-done sentence stays."""
    monkeypatch.chdir(tmp_path)
    message = f"remove duplicates and {extra}"
    brain = _brain_ledger_reply(
        "remove_duplicate_rows",
        ("remove_duplicate_rows", "remove duplicates"),
        ("unsupported", message),
    )

    reply, delivered = _delivered_turn(
        tmp_path, message, [brain, _THINK_PROCEEDS], speaker_kind, session_id
    )

    assert _counts(delivered) == (4, 0, 1)
    assert _DEDUPE_LEAD_AND_GENERIC in reply, reply
    assert not reply.startswith("Done —"), reply
    assert "Done —" not in reply, reply


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_n1_delete_the_file_alone_is_still_quoted_as_something_buddy_cant_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str, session_id: str
) -> None:
    """Re-review x6: a phrase that names no action is unchanged: quoted, not dropped."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_duplicate_rows",
        ("remove_duplicate_rows", "remove duplicates"),
        ("unsupported", "delete the file"),
    )

    reply, _ = _delivered_turn(
        tmp_path, "remove duplicates and delete the file", [brain, _THINK_PROCEEDS],
        speaker_kind, session_id,
    )

    assert "I can't do this part yet: “delete the file”." in reply, reply
    assert "Partly done — removed 2 exact duplicate rows." in reply, reply


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_n1_a_dropped_entry_needs_only_function_words_left_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str, session_id: str
) -> None:
    """The entries the H2 fix meant to drop still are: only articles, politeness, negation
    and keep/leave words are left once the action's own words are removed."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(
        "remove_empty_rows",
        ("remove_empty_rows", "remove the empty rows"),
        ("unsupported", "don't remove the duplicates please"),
    )

    reply, _ = _delivered_turn(
        tmp_path,
        "remove the empty rows but don't remove the duplicates please",
        [brain, _THINK_PROCEEDS],
        speaker_kind,
        session_id,
    )

    assert "can't do" not in reply, reply
    assert "Removed 1 fully-empty row" in reply or "emoved 1 fully-empty row" in reply


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_n2_unsupported_entries_alone_are_no_reading_to_compare_a_step_with(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str, session_id: str
) -> None:
    """Re-review N2 (x1, e1): Brain's only entry is an unsupported phrase, and the planner
    ran the dedupe. "I didn't read that as part of your request" needs a supported Brain
    action to compare the step with; without one the reply says nothing about it."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply("multi_step_request", ("unsupported", "sort by amount"))
    plan = _plan_reply(("spreadsheet.deduplicate", "remove duplicates"))

    reply, delivered = _delivered_turn(
        tmp_path, "remove duplicates and sort by amount", [brain, _THINK_PROCEEDS, plan],
        speaker_kind, session_id,
    )

    assert _counts(delivered) == (4, 0, 1)
    assert "didn't read that" not in reply, reply
    assert "emoved 2 exact duplicate rows" in reply, reply
    assert "I can't do this part yet: “sort by amount”." in reply, reply
    _no_completion_claim(reply)


# -- Verified outcome (PR-B fixes 3): re-review 2 P1, P2, P3 and Q1, end to end ------------

_P_CANT_DO_GENERIC = "I read part of your request as something I can't do yet."


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    ("case", "message", "entries", "counts"),
    [
        (  # P1: the done action's phrase is the whole message, "delete the file" included
            "s22",
            "get rid of the repeated entries and delete the file",
            [
                ("remove_duplicate_rows", "get rid of the repeated entries and delete the file"),
                ("unsupported", "get rid of the repeated entries and delete the file"),
            ],
            (4, 0, 1),
        ),
        (  # P2: Brain's own words for an empty-row request that never ran
            "t23",
            "dedupe and get rid of rows with nothing in them",
            [("remove_duplicate_rows", "dedupe"), ("unsupported", "remove empty rows")],
            (4, 0, 1),
        ),
        (  # P3: "keep"/"only" are not filler when the named action ran
            "f10",
            "keep only the duplicate rows",
            [("unsupported", "keep only the duplicate rows")],
            (4, 0, 1),
        ),
    ],
    ids=["s22", "t23", "f10"],
)
def test_p1_p2_p3_a_flagged_part_is_never_dropped_behind_a_done_lead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
    entries: list[tuple[str, str]],
    counts: tuple[int, int, int],
    speaker_kind: str,
    session_id: str,
) -> None:
    """Re-review 2 (s22, t23, f10): Brain flagged part of the request as unsupported and
    the dedupe ran. The reply must not lead "Done —"; it says part of the request is
    something Buddy can't do yet."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply("remove_duplicate_rows", *entries)

    reply, delivered = _delivered_turn(
        tmp_path, message, [brain, _THINK_PROCEEDS], speaker_kind, session_id
    )

    assert _counts(delivered) == counts, case
    assert "Done —" not in reply, reply
    # s22 overlaps the done action's phrase: the reply is true whether the flag was a slip or not
    # (R1-2, owner's option b), so it neither says "Partly done" nor "can't do yet".
    expected = NO_SKILL_UNSURE_SENTENCE if case == "s22" else _P_CANT_DO_GENERIC
    assert expected in reply, reply
    if case == "s22":
        assert "Partly done" not in reply and "can't do" not in reply, reply


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize(
    ("case", "message", "intent", "entries", "counts", "removed"),
    [
        (
            "g26",
            "please remove all duplicates",
            "remove_duplicate_rows",
            [
                ("remove_duplicate_rows", "remove all duplicates"),
                ("unsupported", "please remove all duplicates"),
            ],
            (4, 0, 1),
            "removed 2 exact duplicate rows.",
        ),
        (  # near-miss action name, rich phrase
            "g27",
            "remove all the duplicate rows from my sheet",
            "remove_duplicate_rows",
            [("remove_duplicates", "remove all the duplicate rows from my sheet")],
            (4, 0, 1),
            "removed 2 exact duplicate rows.",
        ),
        (
            "g28",
            "delete the duplicates",
            "remove_duplicate_rows",
            [
                ("remove_duplicate_rows", "delete the duplicates"),
                ("unsupported", "delete the duplicates"),
            ],
            (4, 0, 1),
            "removed 2 exact duplicate rows.",
        ),
        (
            "g29",
            "can you remove the duplicates",
            "remove_duplicate_rows",
            [("deduplicate", "can you remove the duplicates")],
            (4, 0, 1),
            "removed 2 exact duplicate rows.",
        ),
        (
            "g30",
            "remove the empty rows from this sheet",
            "remove_empty_rows",
            [("remove_blank_rows", "remove the empty rows from this sheet")],
            (5, 2, 0),
            "removed 1 fully-empty row.",
        ),
    ],
    ids=["g26", "g27", "g28", "g29", "g30"],
)
def test_q1_a_fully_done_request_leads_done_with_no_cant_do(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    message: str,
    intent: str,
    entries: list[tuple[str, str]],
    counts: tuple[int, int, int],
    removed: str,
    speaker_kind: str,
    session_id: str,
) -> None:
    """Re-review 2 (g26-g30): ordinary words around the action ("all", "from my sheet",
    "can you", "delete") don't make a completed request "Partly done … can't do yet"."""
    monkeypatch.chdir(tmp_path)
    brain = _brain_ledger_reply(intent, *entries)

    reply, delivered = _delivered_turn(
        tmp_path, message, [brain, _THINK_PROCEEDS], speaker_kind, session_id
    )

    assert _counts(delivered) == counts, case
    assert f"Done — {removed}" in reply, reply  # the lead, then the count
    assert "Done —" in reply, reply  # an opener may precede the lead
    assert "can't do" not in reply and "Partly done" not in reply, reply


_R1_MESSAGE = "remove duplicates across the workbook"
_R1_BRAIN = _brain_ledger_reply(
    "remove_duplicate_rows",
    ("remove_duplicate_rows", "remove duplicates"),
    ("unsupported", _R1_MESSAGE),
)


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
def test_r1_a_two_sheet_workbook_delivers_nothing_and_the_reply_never_says_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker_kind: str, session_id: str
) -> None:
    """Re-review 3 (z09): the skills refuse a workbook with more than one sheet, so nothing
    is handed back, and no reply may lead "Done —" whatever Brain flagged."""
    monkeypatch.chdir(tmp_path)

    state, provider, original_path, original_bytes = _run_ledger_turn(
        tmp_path, _R1_MESSAGE, [_R1_BRAIN, _THINK_PROCEEDS], speaker_kind, _MODEL_REPLY,
        session_id, second_sheet=True,
    )

    assert state.execution_result is not None
    assert state.execution_result.outputs == ()  # nothing delivered
    reply = state.reply
    assert reply is not None
    assert "Done —" not in reply, reply
    assert _MODEL_REPLY not in reply
    assert provider._replies == [_MODEL_REPLY]  # the Voice model was never called
    assert original_path.read_bytes() == original_bytes  # rule 2


@pytest.mark.parametrize("session_id", _SEEDS)
@pytest.mark.parametrize("speaker_kind", _SPEAKERS)
@pytest.mark.parametrize("scope", ["across the workbook", "in the workbook"])
def test_r1_a_flagged_workbook_scope_is_partly_done_not_done_on_a_one_sheet_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, scope: str, speaker_kind: str, session_id: str
) -> None:
    """Re-review 3 (z09, z10, z45): Brain flags a workbook-wide scope as unsupported. The
    location strip must not take it, so the reply is "Partly done" and never "Done —", even
    when the one sheet was fully deduplicated."""
    monkeypatch.chdir(tmp_path)
    message = f"remove duplicates {scope}"
    brain = _brain_ledger_reply(
        "remove_duplicate_rows",
        ("remove_duplicate_rows", "remove duplicates"),
        ("unsupported", message),
    )

    reply, delivered = _delivered_turn(
        tmp_path, message, [brain, _THINK_PROCEEDS], speaker_kind, session_id
    )

    assert _counts(delivered) == (4, 0, 1)
    assert "Partly done — removed 2 exact duplicate rows." in reply, reply
    assert "Done —" not in reply, reply
    assert _P_CANT_DO_GENERIC in reply, reply
