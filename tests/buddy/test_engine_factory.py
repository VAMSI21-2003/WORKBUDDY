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
from buddy.voice import (
    _COMPLETION_CLAIM,
    RECHECK_FAILED_SENTENCE,
    PersonalityVoice,
    Speaker,
    SpeakingRequest,
    TemplateSpeaker,
)


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
    ).to_excel(path, index=False)


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
    assert "nothing notable found" in state.reply
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
    assert state.reply is not None
    assert "Before anything else, here's what I noticed" in state.reply
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
    tmp_path: Path, message: str, replies: list[str], speaker: Speaker | None = None
) -> tuple[BuddyState, Path, bytes]:
    (tmp_path / "uploads").mkdir()
    input_path = Path("uploads") / "report.xlsx"
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


_B1_MESSAGE = "remove the duplicate rows and then give me a report"


def _b1_replies() -> list[str]:
    return _chain_replies(
        ("spreadsheet.deduplicate", "remove the duplicate rows"),
        ("spreadsheet.report", "give me a report"),
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
    """The exact scenario from the review: dedupe runs and passes, then the
    report step has no skill. Nothing is handed back, Inspector sees the
    failing step, and the reply never claims completion."""
    monkeypatch.chdir(tmp_path)

    state, original_path, original_bytes = _run_chain(tmp_path, _B1_MESSAGE, _b1_replies())

    result = state.execution_result
    assert result is not None
    assert result.outputs == ()
    assert result.stopped_at == "spreadsheet.report"
    assert [(s.capability, s.status) for s in result.steps] == [
        ("spreadsheet.analyze", "success"),
        ("spreadsheet.deduplicate", "success"),
        ("spreadsheet.report", "failure"),
    ]
    assert original_path.read_bytes() == original_bytes  # untouched

    verification = state.verification_result
    assert verification is not None
    assert verification.passed is False
    verdicts = {v.capability: v.passed for v in verification.steps}
    assert verdicts["spreadsheet.report"] is False

    reply = state.reply
    assert reply is not None
    assert "Done" not in reply
    assert _COMPLETION_CLAIM.search(reply) is None, reply
    assert "I stopped at the report step and no file was handed back" in reply
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
    assert reply.startswith("Done")
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

    assert state.reply == "Both cleanups went through. Your cleaned copy of report.xlsx is ready."
    prompt = speaker._provider.calls[0][1][0].content  # type: ignore[attr-defined]
    assert "could not be re-checked for other problems" in prompt
    assert "spreadsheet." not in prompt
