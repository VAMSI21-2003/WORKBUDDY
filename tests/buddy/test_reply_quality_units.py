"""Unit tests for the pieces behind the reply-quality change.

The end-to-end replies are in ``test_reply_quality.py``. These pin the parts that decide them:
the plural helper, the report and conversation detectors, Think's reason code, Brain's new
intent, the planner's choices, the catalog's new text, and the exact-duplicate finding.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from buddy import voice_catalog as catalog
from buddy.actions import (
    CONVERSATION,
    is_conversation,
    is_only_a_report_request,
    mentions_report,
)
from buddy.brain import INTENT_VOCABULARY, ClaudeInterpreter
from buddy.inspector import (
    NO_ANALYSIS_FINDINGS,
    SpreadsheetAnalysisVerifier,
    VerificationRequest,
)
from buddy.models import (
    LEDGER_FROM_BRAIN,
    LEDGER_FROM_MENTION,
    ExecutionResult,
    RequestedAction,
    StepResult,
    Understanding,
)
from buddy.planner import ClaudePlanner, Planner, cleaning_refusal
from buddy.state import BuddyState
from buddy.text import count_noun
from buddy.think import (
    CLARIFY_FAILED,
    CLARIFY_OTHER,
    CLARIFY_WHICH_ACTION,
    ClaudeThinker,
    NullThinker,
    Reasoning,
    ThinkingRequest,
)
from buddy.voice import _COMPLETION_CLAIM
from skills.excel.analyze import ExactDuplicateRowAnalyzer


def test_count_noun_uses_the_singular_only_for_exactly_one() -> None:
    assert count_noun(0, "row") == "0 rows"
    assert count_noun(1, "row") == "1 row"
    assert count_noun(2, "row") == "2 rows"
    assert count_noun(1, "value that doesn't", "values that don't") == "1 value that doesn't"
    assert count_noun(3, "value that doesn't", "values that don't") == "3 values that don't"


# -- Detectors -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        "give me a report",
        "remove the duplicate rows and then give me a report",
        "Send me the summary, please",
        "can I get a full report on this?",
        "analyze my file",
        "I need an analysis",
    ],
)
def test_a_message_that_asks_for_a_report_is_detected(message: str) -> None:
    assert mentions_report(message) is True


@pytest.mark.parametrize(
    "message",
    [
        "hi",
        "remove the duplicate rows",
        "no report please",
        "don't give me a report",
        "sort it",
        # A question about Buddy, a part of the file's name, or something the person will do
        # themselves is not a request for a report on the file.
        "give me an overview of what you can do",
        "give me a rundown of your features",
        "can you give me a summary of what you do?",
        "remove the duplicate rows but don't look at my data",
        "I'll check the file myself",
        "thanks, I'll review the data now",
        "remove the duplicate rows from the overview sheet",
        "remove duplicates by the Analysis column",
        # A report to be sent somewhere Buddy can't send it is not a request for the report.
        "email me the report",
        "remove duplicates and print the summary",
    ],
)
def test_a_message_that_does_not_ask_for_a_report_is_not(message: str) -> None:
    assert mentions_report(message) is False


def test_only_a_pure_report_request_counts_as_one() -> None:
    assert is_only_a_report_request("give me a report")
    assert is_only_a_report_request("and then give me the summary, please")
    assert not is_only_a_report_request("remove duplicates and give me a report")
    assert not is_only_a_report_request("sort by amount")
    assert not is_only_a_report_request("don't give me a report")
    assert not is_only_a_report_request("email me the report")


def test_conversation_needs_brains_intent_and_nothing_that_contradicts_it() -> None:
    chat = Understanding(intent=CONVERSATION)
    assert is_conversation(chat, "hi")
    assert not is_conversation(Understanding(intent="unclear"), "hi")
    # A message that mentions an action, asks for a report, or has any ledger entry is not chat.
    assert not is_conversation(chat, "hi, remove the duplicate rows")
    assert not is_conversation(chat, "hi, send me a report")
    listed = Understanding(
        intent=CONVERSATION,
        requested_actions=(RequestedAction("unsupported", "sort", LEDGER_FROM_BRAIN),),
    )
    assert not is_conversation(listed, "hi")
    mention = Understanding(
        intent=CONVERSATION,
        requested_actions=(RequestedAction("remove_duplicate_rows", "dupes", LEDGER_FROM_MENTION),),
    )
    assert not is_conversation(mention, "hi")


def test_brain_knows_the_conversation_intent_and_still_closes_the_vocabulary() -> None:
    assert CONVERSATION in INTENT_VOCABULARY
    understanding = ClaudeInterpreter._parse_reply(
        json.dumps({"intent": "conversation", "confidence": 0.9, "requested_actions": []})
    )
    assert understanding.intent == CONVERSATION
    other = ClaudeInterpreter._parse_reply(json.dumps({"intent": "chitchat", "confidence": 0.9}))
    assert other.intent == "unclear"


# -- Think's reason code ---------------------------------------------------------------------


def _reply(**fields: object) -> str:
    return json.dumps({"clarification_required": True, "confidence": 0.5, **fields})


def test_think_keeps_only_a_known_reason_code() -> None:
    parse = ClaudeThinker._parse_reply
    assert parse(_reply(clarification_reason="which_action")).clarification_reason == "which_action"
    assert parse(_reply(clarification_reason=" Which_File ")).clarification_reason == "which_file"
    # Anything else the model wrote becomes the general reason, never kept as text.
    for raw in ("columns to compare", "", None, 7, ["which_action"], "failed"):
        assert parse(_reply(clarification_reason=raw)).clarification_reason == CLARIFY_OTHER
    assert parse(_reply()).clarification_reason == CLARIFY_OTHER


def test_think_has_no_reason_when_it_does_not_ask() -> None:
    reasoning = ClaudeThinker._parse_reply(
        json.dumps(
            {"clarification_required": False, "clarification_reason": "which_file", "confidence": 1}
        )
    )
    assert reasoning.clarification_required is False
    assert reasoning.clarification_reason == ""


def test_a_failed_think_is_marked_failed_by_code_not_by_the_model() -> None:
    assert ClaudeThinker._parse_reply("not json").clarification_reason == CLARIFY_FAILED
    assert ClaudeThinker._parse_reply("[1]").clarification_reason == CLARIFY_FAILED
    request = ThinkingRequest(Understanding(), (), (), None, ())

    class Raises:
        def complete(self, **_: object) -> str:
            raise RuntimeError("boom")

    assert ClaudeThinker(Raises())(request).clarification_reason == CLARIFY_FAILED  # type: ignore[arg-type]
    assert NullThinker()(request).clarification_reason == CLARIFY_FAILED
    assert Reasoning().clarification_reason == ""


# -- The planner -----------------------------------------------------------------------------


def _state(tmp_path: Path, rows: dict | None = None, message: str = "") -> tuple[BuddyState, str]:
    path = tmp_path / "report.xlsx"
    pd.DataFrame(rows or {"a": [1, 1, 2]}).to_excel(path, index=False)
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.context.add_uploaded_file(str(path))
    state.context.switch_active_file(str(path))
    state.user_message = message
    state.understanding = Understanding(intent="unclear")
    state.reasoning = Reasoning()
    return state, str(path)


def test_cleaning_refusal_gives_the_skills_own_text(tmp_path: Path) -> None:
    state, path = _state(tmp_path)
    assert cleaning_refusal(path) is None
    assert cleaning_refusal(str(tmp_path / "missing.xlsx")) is None
    with pd.ExcelWriter(tmp_path / "two.xlsx") as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="A", index=False)
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="B", index=False)
    assert cleaning_refusal(str(tmp_path / "two.xlsx")) == (
        "the workbook has 2 sheets, and only single-sheet workbooks are supported for now"
    )


def test_the_planner_records_why_it_would_not_analyse_a_file(tmp_path: Path) -> None:
    with pd.ExcelWriter(tmp_path / "two.xlsx") as writer:
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="A", index=False)
        pd.DataFrame({"a": [1]}).to_excel(writer, sheet_name="B", index=False)
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.context.add_uploaded_file(str(tmp_path / "two.xlsx"))
    state.user_message = "hello"
    state.understanding = Understanding(intent="unclear")
    state.reasoning = Reasoning()

    request = Planner._build_request(state)

    assert request.unanalyzed_spreadsheet_files == ()
    assert request.file_refusal == state.file_refusal
    assert "2 sheets" in (state.file_refusal or "")


def test_no_analysis_is_planned_for_conversation_or_a_pending_question(tmp_path: Path) -> None:
    state, path = _state(tmp_path, message="hi")
    state.understanding = Understanding(intent=CONVERSATION)
    assert Planner._build_request(state).unanalyzed_spreadsheet_files == ()

    state.understanding = Understanding(intent="unclear")
    assert Planner._build_request(state).unanalyzed_spreadsheet_files == (path,)

    state.reasoning = Reasoning(clarification_required=True, clarification_reason="other")
    assert Planner._build_request(state).unanalyzed_spreadsheet_files == ()


def test_a_report_request_analyses_even_a_file_analysed_before_but_nothing_else_does(
    tmp_path: Path,
) -> None:
    state, path = _state(tmp_path, message="send me a report")
    state.context.mark_analyzed(path)
    assert Planner._build_request(state).unanalyzed_spreadsheet_files == (path,)

    state.user_message = "make it nicer"
    assert Planner._build_request(state).unanalyzed_spreadsheet_files == ()


def test_a_report_request_still_analyses_when_the_intent_is_conversation(tmp_path: Path) -> None:
    state, path = _state(tmp_path, message="hi, send me a report")
    state.understanding = Understanding(intent=CONVERSATION)
    assert Planner._build_request(state).unanalyzed_spreadsheet_files == (path,)


@pytest.mark.parametrize(
    ("capability", "phrase", "expected"),
    [
        ("spreadsheet.report", "give me a report", "spreadsheet.analyze"),
        ("spreadsheet.summary", "send me the summary", "spreadsheet.analyze"),
        ("spreadsheet.report", "sort by amount", "spreadsheet.report"),
        (
            "spreadsheet.deduplicate",
            "remove duplicates and give me a report",
            "spreadsheet.deduplicate",
        ),
    ],
)
def test_a_step_that_only_asks_for_a_report_is_planned_as_the_analysis(
    capability: str, phrase: str, expected: str
) -> None:
    message = f"{phrase}"
    reply = json.dumps(
        {
            "steps": [
                {
                    "description": "d",
                    "capability": capability,
                    "depends_on_step_numbers": [],
                    "source_phrase": phrase,
                }
            ]
        }
    )

    (step,) = ClaudePlanner._parse_reply(reply, message)

    assert step.capability == expected


# -- The catalog's new text ------------------------------------------------------------------


def _all_new_lines() -> list[str]:
    lines: list[str] = []
    for table in (catalog.GREETINGS,):
        lines += [text for options in table.values() for text in options]
    for table in (
        catalog.CAPABILITIES,
        catalog.NEXT_STEP_WITH_FILE,
        catalog.NEXT_STEP_WITHOUT_FILE,
    ):
        lines += list(table.values())
    for by_style in catalog.CLARIFICATION_QUESTIONS.values():
        lines += [text for options in by_style.values() for text in options]
    lines += [
        catalog.NO_FILE_SENTENCE,
        catalog.REFUSED_TEMPLATE.format(reason="x"),
        catalog.NOT_UNDERSTOOD_SENTENCE,
        catalog.UNDERSTANDING_FAILED_SENTENCE,
        catalog.REPORT_ANALYSIS_FAILED_SENTENCE,
        catalog.REPORT_NOT_RUN_SENTENCE,
        catalog.REPORT_RECHECK_FAILED_SENTENCE,
        catalog.REPORT_NOT_PRODUCED_SENTENCE,
        catalog.REPORT_LEAD_COPY,
        catalog.REPORT_LEAD_ORIGINAL,
    ]
    return lines


def test_no_new_line_claims_work_carries_a_number_or_a_placeholder() -> None:
    for line in _all_new_lines():
        assert _COMPLETION_CLAIM.search(line) is None, line
        assert not any(ch.isdigit() for ch in line), line
        assert "{" not in line and "}" not in line, line
        assert "(s)" not in line, line


def test_every_style_has_every_line_and_the_questions_are_questions() -> None:
    styles = {catalog.STYLE_PLAIN, "friend", "focus"}
    for table in (
        catalog.GREETINGS,
        catalog.CAPABILITIES,
        catalog.NEXT_STEP_WITH_FILE,
        catalog.NEXT_STEP_WITHOUT_FILE,
    ):
        assert set(table) == styles
    for reason in ("which_action", "which_file", "other"):
        assert set(catalog.CLARIFICATION_QUESTIONS[reason]) == styles
        for options in catalog.CLARIFICATION_QUESTIONS[reason].values():
            assert options and all(text.endswith("?") for text in options)


def test_the_question_for_an_unknown_reason_is_the_general_one() -> None:
    general = catalog.CLARIFICATION_QUESTIONS[CLARIFY_OTHER]["focus"]
    assert catalog.clarification_question("anything else", "focus", "seed") in general
    assert catalog.clarification_question(CLARIFY_WHICH_ACTION, "focus", "seed") == (
        "Duplicate rows, empty rows, or both?"
    )


def test_a_greeting_reply_is_chosen_by_seed_and_style() -> None:
    first = catalog.greeting_reply("friend", has_file=True, seed="a:0")
    assert first == catalog.greeting_reply("friend", has_file=True, seed="a:0")
    seen = {catalog.greeting_reply("friend", has_file=True, seed=f"s{i}:0") for i in range(30)}
    assert len(seen) == len(catalog.GREETINGS["friend"])
    assert catalog.style_for(None) == catalog.STYLE_PLAIN
    assert catalog.style_for("focus") == "focus"
    assert catalog.style_for("whatever") == "friend"


def test_the_refusal_template_takes_only_the_skills_text() -> None:
    assert re.fullmatch(r"I can't work on this file: \{reason\}\.", catalog.REFUSED_TEMPLATE)


# -- The analyzer's exact-duplicate finding --------------------------------------------------


def test_the_analyzer_reports_exact_duplicate_rows_with_a_count() -> None:
    frame = pd.DataFrame({"a": [1, 1, 2, 2, 2], "b": ["x", "x", "y", "y", "y"]})
    assert ExactDuplicateRowAnalyzer()(frame) == ("3 rows repeat an earlier row exactly",)
    assert ExactDuplicateRowAnalyzer()(frame.iloc[[0, 1]]) == (
        "1 row repeats an earlier row exactly",
    )
    assert ExactDuplicateRowAnalyzer()(frame.drop_duplicates()) == ()


def _verify(path: Path) -> tuple[str, ...]:
    step = StepResult(capability="spreadsheet.analyze", skill="excel.analyze", status="success")
    request = VerificationRequest(
        understanding=Understanding(),
        execution_result=ExecutionResult(success=True, steps=(step,)),
        active_file=str(path),
    )
    result = SpreadsheetAnalysisVerifier()(request)
    assert result.passed
    return result.findings


def test_the_verifier_recomputes_exact_duplicates_itself(tmp_path: Path) -> None:
    path = tmp_path / "d.xlsx"
    pd.DataFrame({"a": [1, 1, 2, 2, 2]}).to_excel(path, index=False)

    findings = _verify(path)

    assert findings == ("[duplicate_rows] 3 rows repeat an earlier row exactly",)


def test_a_file_with_no_repeats_still_gets_the_all_clear(tmp_path: Path) -> None:
    path = tmp_path / "d.xlsx"
    pd.DataFrame({"a": [1, 2, 3]}).to_excel(path, index=False)

    assert _verify(path) == (NO_ANALYSIS_FINDINGS,)


def test_the_finding_counts_what_the_dedupe_verifier_counts(tmp_path: Path) -> None:
    """Repeated blank rows count in both, so "still has N" and this finding agree."""
    from buddy.inspector import _duplicate_rows

    frame = pd.DataFrame({"a": [1, None, None, 1], "b": [2, None, None, 2]})
    path = tmp_path / "d.xlsx"
    frame.to_excel(path, index=False)

    count = _duplicate_rows(pd.read_excel(path))
    expected = f"[duplicate_rows] {count} rows repeat an earlier row exactly"
    assert expected in _verify(path)


def test_the_verifiers_finding_uses_the_singular_for_one_repeated_row(tmp_path: Path) -> None:
    path = tmp_path / "d.xlsx"
    pd.DataFrame({"a": [1, 1, 2]}).to_excel(path, index=False)

    assert _verify(path) == ("[duplicate_rows] 1 row repeats an earlier row exactly",)


@pytest.mark.parametrize(
    "message",
    [
        "Send me a summary of what you find",
        "I don't know what's wrong, can you analyze it?",
        "what is wrong with my file",
        "run a quality check on the file",
        "check my file for duplicates",
        "give me an overview of the file",
    ],
)
def test_requests_that_look_like_the_guarded_ones_but_are_requests(message: str) -> None:
    assert mentions_report(message)


def test_what_a_report_is_about_is_not_a_request_to_remove_it() -> None:
    from buddy.actions import mentioned_actions

    assert mentioned_actions("give me a report on the duplicate rows") == ()
    assert [
        a.name for a, _ in mentioned_actions("remove the duplicate rows and give me a report")
    ] == ["remove_duplicate_rows"]
    assert [
        a.name
        for a, _ in mentioned_actions(
            "give me a report on the duplicate rows and remove the duplicate rows"
        )
    ] == ["remove_duplicate_rows"]
