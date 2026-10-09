"""Reply quality: why nothing ran, greetings, Think's question, requested reports.

Every test runs the real ``build_engine`` (real skills, planner, Inspector and Voice) with only the
LLM faked, in all three speakers (template, friend, focus), and checks the reply against the file
read independently. Rule 1 is the centre of it: a reply that delivers nothing never claims work,
and every sentence that is new comes from code, from fixed catalog text, or from verifier data.
"""

from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd
import pytest

from buddy.actions import ACTIONS
from buddy.engine_factory import build_engine
from buddy.state import BuddyState
from buddy.voice import (
    _COMPLETION_CLAIM,
    NO_CHANGES_SENTENCE,
    PersonalityVoice,
)
from buddy.voice_catalog import (
    CAPABILITIES,
    CLARIFICATION_QUESTIONS,
    GREETINGS,
    NEXT_STEP_WITH_FILE,
    NEXT_STEP_WITHOUT_FILE,
    NO_FILE_SENTENCE,
    NOT_UNDERSTOOD_SENTENCE,
    REPORT_LEAD_COPY,
    REPORT_LEAD_ORIGINAL,
    REPORT_NOT_PRODUCED_SENTENCE,
    REPORT_NOT_RUN_SENTENCE,
    REPORT_RECHECK_FAILED_SENTENCE,
    UNDERSTANDING_FAILED_SENTENCE,
)
from skills.excel.analyze import SpreadsheetAnalysisSkill
from tests import workbook_fixtures as fixtures

SPEAKERS = ["template", "friend", "focus"]
STYLE = {"template": "plain", "friend": "friend", "focus": "focus"}

DEDUPE = "spreadsheet.deduplicate"
EMPTY_ROWS = "spreadsheet.remove_empty_rows"
ROWS = {
    "name": ["Alice", "Bob", None, "Alice", "Carol", "Bob"],
    "amount": [10, 20, None, 10, 30, 20],
}  # 6 rows: 2 repeat an earlier row, 1 is empty
_SECRET = "WHICH-COLUMN-SECRET"


def _json(**fields: Any) -> str:
    return json.dumps(fields)


def brain(intent: str | None, *actions: tuple[str, str]) -> str:
    return _json(
        intent=intent,
        confidence=0.9,
        requested_actions=[{"action": a, "source_phrase": p} for a, p in actions],
    )


def think(clarify: bool = False, **extra: Any) -> str:
    return _json(
        objective="o",
        summary=extra.pop("summary", "s"),
        clarification_required=clarify,
        confidence=0.9,
        **extra,
    )


def plan(*steps: tuple[str, str]) -> str:
    return _json(
        steps=[
            {
                "description": f"do {c}",
                "capability": c,
                "depends_on_step_numbers": [],
                "source_phrase": p,
            }
            for c, p in steps
        ]
    )


class Routed:
    """A fake LLM routed by system prompt; a call that isn't Brain, Think or the planner fails."""

    def __init__(self, brain_reply: str, think_reply: str, plan_reply: str | None = None) -> None:
        self.replies = {
            "intent classifier": brain_reply,
            "reasoning stage": think_reply,
            "planning stage": plan_reply,
        }
        self.calls: list[str] = []

    def complete(self, *, system: str, messages: Any, max_tokens: int = 1024) -> str:
        for marker, reply in self.replies.items():
            if marker in system:
                self.calls.append(marker)
                if reply is None:
                    raise AssertionError(f"{marker} was called but no reply was set")
                return reply
        raise AssertionError("a model call that is not Brain, Think or the planner")


@dataclass
class Turn:
    state: BuddyState
    reply: str
    steps: list[str]
    original: Path | None
    original_unchanged: bool
    provider: Routed

    @property
    def delivered(self) -> bool:
        er = self.state.execution_result
        return bool(er and er.outputs)


def _make_file(kind: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if kind == "plain":
        pd.DataFrame(ROWS).to_excel(path, index=False)
    elif kind == "dups_only":
        pd.DataFrame({"name": ["A", "A", "B"], "amount": [1, 1, 2]}).to_excel(path, index=False)
    elif kind == "clean":
        pd.DataFrame({"name": ["Alice", "Bob"], "amount": [1, 2]}).to_excel(path, index=False)
    elif kind == "multi":
        with pd.ExcelWriter(path) as writer:
            pd.DataFrame(ROWS).to_excel(writer, sheet_name="Q1", index=False)
            pd.DataFrame(ROWS).to_excel(writer, sheet_name="Q2", index=False)
    elif kind == "merged":
        fixtures.merged_cell_sheet(path)
    elif kind == "header3":
        fixtures.header_on_row_three_sheet(path)
    elif kind == "na":
        fixtures.na_text_sheet(path)
    elif kind == "password":
        fixtures.encrypted_package(path)
    elif kind == "xls":
        fixtures.legacy_xls(path)
    else:
        raise ValueError(kind)


def run_turn(
    tmp_path: Path,
    speaker: str,
    message: str,
    provider: Routed,
    *,
    file: str | None = "plain",
    session: str = "session-1",
    analyzed: bool = False,
    monkeypatch: pytest.MonkeyPatch | None = None,
) -> Turn:
    assert monkeypatch is not None
    tmp_path.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(tmp_path)
    state = BuddyState.new(session_id=session, workspace_id="w")
    state.user_message = message
    upload = tmp_path / "uploads" / "report.xlsx"
    before = None
    if file is not None:
        _make_file(file, upload)
        before = hashlib.sha256(upload.read_bytes()).hexdigest()
        state.context.add_uploaded_file("uploads/report.xlsx")
        state.context.switch_active_file("uploads/report.xlsx")
        if analyzed:
            state.context.mark_analyzed("uploads/report.xlsx")
    voice = None
    if speaker != "template":
        state.context.set_mode(speaker)
        voice = PersonalityVoice()
    build_engine(llm_provider=provider, speaker=voice).run(state)
    execution = state.execution_result
    return Turn(
        state=state,
        reply=state.reply or "",
        steps=[s.capability for s in (execution.steps if execution else ())],
        original=upload if file is not None else None,
        original_unchanged=(
            before is None or hashlib.sha256(upload.read_bytes()).hexdigest() == before
        ),
        provider=provider,
    )


def _assert_nothing_claimed(turn: Turn) -> None:
    assert not turn.delivered
    assert turn.original_unchanged
    assert _COMPLETION_CLAIM.search(turn.reply) is None, turn.reply
    assert "verifier" not in turn.reply.lower()
    assert "(s)" not in turn.reply


def _action_steps(turn: Turn) -> list[str]:
    return [s for s in turn.steps if s != "spreadsheet.analyze"]


# -- 1. "Nothing ran" says why --------------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    "intent",
    ["remove_duplicate_rows", "unclear", "conversation"],
)
def test_no_file_yet_says_so(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, intent: str
) -> None:
    actions = (
        (("remove_duplicate_rows", "remove the duplicate rows"),)
        if intent != "conversation"
        else ()
    )
    provider = Routed(brain(intent, *actions), think())

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the duplicate rows" if actions else "hi",
        provider,
        file=None,
        monkeypatch=monkeypatch,
    )

    if intent == "conversation":
        # A greeting with no file still invites an upload.
        assert NEXT_STEP_WITHOUT_FILE[STYLE[speaker]] in turn.reply
    else:
        assert turn.reply == f"{NO_FILE_SENTENCE} {NO_CHANGES_SENTENCE}"
    assert turn.steps == []
    _assert_nothing_claimed(turn)


_PLAIN_TABLES = (
    ", and only plain tables (one header row at the top, then the data) are supported for now"
)
# The refusals as the skills word them (``skills/excel/workbook.py``), written out here so the
# test doesn't ask the code under test what they are.
_REFUSED = [
    ("multi", "the workbook has 2 sheets, and only single-sheet workbooks are supported for now"),
    ("merged", "the sheet has merged cells" + _PLAIN_TABLES),
    ("header3", "a column in the sheet has no header" + _PLAIN_TABLES),
    (
        "na",
        "a cell holds an error value, or text such as NA or N/A, that would be read as empty, "
        "and keeping such cells isn't supported for now",
    ),
    ("password", "the workbook is password-protected, so it can't be opened"),
    (
        "xls",
        "older .xls workbooks (Excel 97–2003) aren't supported yet; saving it as .xlsx will work",
    ),
]


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(("kind", "expected"), _REFUSED)
def test_a_file_that_cannot_be_cleaned_says_why_in_the_skills_own_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, kind: str, expected: str
) -> None:
    provider = Routed(brain("unclear"), think())

    turn = run_turn(tmp_path, speaker, "tidy this up", provider, file=kind, monkeypatch=monkeypatch)

    assert turn.reply == f"I can't work on this file: {expected}. {NO_CHANGES_SENTENCE}"
    assert turn.steps == []
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_cleaning_request_on_a_refused_file_says_the_same_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(
        brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")),
        think(),
    )

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the duplicate rows",
        provider,
        file="multi",
        monkeypatch=monkeypatch,
    )

    assert "the workbook has 2 sheets, and only single-sheet workbooks are supported" in turn.reply
    assert "I ran into a problem before I could change anything." in turn.reply
    assert "partway through" not in turn.reply
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_an_unrecognised_request_lists_what_buddy_can_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(brain("unclear"), think())

    turn = run_turn(tmp_path, speaker, "make it nicer", provider, monkeypatch=monkeypatch)

    assert turn.reply.startswith(f"{NOT_UNDERSTOOD_SENTENCE} {CAPABILITIES[STYLE[speaker]]}")
    assert turn.reply.endswith(NO_CHANGES_SENTENCE)
    assert _action_steps(turn) == []
    _assert_nothing_claimed(turn)
    # The analysis ran and its findings are the file's: 2 repeated rows, 1 missing per column.
    frame = pd.read_excel(turn.original)
    assert f"{int(frame.duplicated().sum())} rows repeat an earlier row exactly" in turn.reply
    assert f"name: {int(frame['name'].isna().sum())} missing value (16.7%)" in turn.reply


def test_the_capability_lines_name_every_action_buddy_has() -> None:
    for style, line in CAPABILITIES.items():
        for action in ACTIONS:
            assert action.request in line, (style, action.name)
        assert re.search(r"look|check", line), style


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_an_unsupported_request_is_quoted_and_nothing_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(brain("unclear", ("unsupported", "sort by amount")), think())

    turn = run_turn(tmp_path, speaker, "sort by amount", provider, monkeypatch=monkeypatch)

    assert "I can't do that yet: “sort by amount”." in turn.reply
    assert CAPABILITIES[STYLE[speaker]] in turn.reply
    assert _action_steps(turn) == []
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_when_brain_fails_the_reply_says_so_not_that_the_user_was_unclear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed("this is not json", think())

    turn = run_turn(
        tmp_path, speaker, "remove the duplicate rows", provider, monkeypatch=monkeypatch
    )

    assert UNDERSTANDING_FAILED_SENTENCE in turn.reply
    assert NOT_UNDERSTOOD_SENTENCE not in turn.reply
    _assert_nothing_claimed(turn)


# -- 2. Greetings and capability questions --------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("message", ["hi", "what can you do?", "thanks!"])
def test_a_greeting_gets_the_fixed_greeting_and_no_analysis(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str
) -> None:
    provider = Routed(brain("conversation"), think())

    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    style = STYLE[speaker]
    greeting = turn.reply.split(CAPABILITIES[style])[0].strip()
    assert greeting in GREETINGS[style]
    assert turn.reply == f"{greeting} {CAPABILITIES[style]} {NEXT_STEP_WITH_FILE[style]}"
    # No analysis dump, none run, and the file is still unanalysed for the real request.
    assert turn.steps == []
    assert "noticed" not in turn.reply and "missing" not in turn.reply
    assert "uploads/report.xlsx" not in turn.state.context.analyzed_files
    assert NO_CHANGES_SENTENCE not in turn.reply
    _assert_nothing_claimed(turn)


def test_friend_greetings_vary_by_session_and_focus_stays_brief(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    friend = {
        run_turn(
            tmp_path / f"f{i}",
            "friend",
            "hi",
            Routed(brain("conversation"), think()),
            session=f"s{i}",
            monkeypatch=monkeypatch,
        ).reply
        for i in range(12)
    }
    focus = run_turn(
        tmp_path / "focus",
        "focus",
        "hi",
        Routed(brain("conversation"), think()),
        monkeypatch=monkeypatch,
    ).reply

    assert len(friend) > 1
    assert focus == f"Hi. {CAPABILITIES['focus']} {NEXT_STEP_WITH_FILE['focus']}"


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    ("message", "listed"),
    [
        # Brain misread an action request as conversation, and something gives it away ...
        ("remove the duplicate rows", ()),
        (
            "hi, remove the duplicate rows",
            (("remove_duplicate_rows", "remove the duplicate rows"),),
        ),
        ("hello, sort by amount", (("unsupported", "sort by amount"),)),
        ("hi, give me a report", ()),
        # ... or nothing does: the reply is still a greeting that claims nothing.
        ("get rid of the repeated entries", ()),
    ],
)
def test_an_action_request_misread_as_conversation_never_gets_a_completion_claim(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    speaker: str,
    message: str,
    listed: tuple[tuple[str, str], ...],
) -> None:
    provider = Routed(brain("conversation", *listed), think())

    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert not turn.delivered and _action_steps(turn) == []
    assert _COMPLETION_CLAIM.search(turn.reply) is None, turn.reply
    assert turn.original_unchanged
    style = STYLE[speaker]
    gives_it_away = message != "get rid of the repeated entries"
    is_greeting = turn.reply.endswith(NEXT_STEP_WITH_FILE[style])
    assert is_greeting != gives_it_away


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_greeting_the_model_calls_unclear_still_claims_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    turn = run_turn(
        tmp_path, speaker, "hi", Routed(brain("unclear"), think()), monkeypatch=monkeypatch
    )

    assert NOT_UNDERSTOOD_SENTENCE in turn.reply
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("reason", ["which_action", "which_file", "other"])
def test_think_asking_for_clarification_shows_a_fixed_question_not_its_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, reason: str
) -> None:
    provider = Routed(
        brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")),
        think(True, clarification_reason=reason, summary=f"I must ask about {_SECRET}"),
    )

    turn = run_turn(
        tmp_path, speaker, "remove the duplicate rows", provider, monkeypatch=monkeypatch
    )

    assert turn.reply in CLARIFICATION_QUESTIONS[reason][STYLE[speaker]]
    assert turn.reply.endswith("?")
    assert _SECRET not in turn.reply
    assert turn.steps == []  # nothing ran, so no analysis was spent either
    assert "uploads/report.xlsx" not in turn.state.context.analyzed_files
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("reason", ["", "Tell me " + _SECRET, "failed", None])
def test_an_unknown_clarification_reason_asks_the_general_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, reason: str | None
) -> None:
    extra = {} if reason is None else {"clarification_reason": reason}
    provider = Routed(brain("unclear"), think(True, summary=_SECRET, **extra))

    turn = run_turn(tmp_path, speaker, "clean this up", provider, monkeypatch=monkeypatch)

    assert turn.reply in CLARIFICATION_QUESTIONS["other"][STYLE[speaker]]
    assert _SECRET not in turn.reply
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_when_think_itself_fails_the_reply_does_not_ask_a_question(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(brain("remove_duplicate_rows"), "not json")

    turn = run_turn(
        tmp_path, speaker, "remove the duplicate rows", provider, monkeypatch=monkeypatch
    )

    assert UNDERSTANDING_FAILED_SENTENCE in turn.reply
    assert "?" not in turn.reply
    _assert_nothing_claimed(turn)


# -- 3. A requested report is delivered or stated as not available -------------------------


def _file_facts(path: str) -> dict[str, int]:
    frame = pd.read_excel(path)
    return {
        "rows": len(frame),
        "duplicates": int(frame.duplicated().sum()),
        "empty": int(frame.isna().all(axis=1).sum()),
        "missing_name": int(frame["name"].isna().sum()),
    }


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_dedupe_then_report_delivers_the_report_on_the_final_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    message = "remove the duplicate rows and then give me a report"
    provider = Routed(
        brain(
            "multi_step_request",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", "give me a report"),
        ),
        think(),
        plan((DEDUPE, "remove the duplicate rows"), ("spreadsheet.report", "give me a report")),
    )

    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert turn.delivered
    final = _file_facts(turn.state.execution_result.outputs[0])  # type: ignore[union-attr]
    assert final["duplicates"] == 0 and final["rows"] == 4
    # The report is the findings on the final file, labelled; it is not "can't do".
    assert REPORT_LEAD_COPY + f"name: {final['missing_name']} missing value (25.0%)" in turn.reply
    assert "can't do" not in turn.reply and "I also noticed" not in turn.reply
    assert turn.reply.startswith(("Done —", "Here's where", "Okay, here's"))
    assert "(s)" not in turn.reply
    assert turn.original_unchanged
    # The report was made by the analysis re-run on the final file, not carried over.
    assert turn.steps.count("spreadsheet.analyze") == 2


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_report_only_request_reports_on_the_original_and_changes_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(brain("unclear", ("unsupported", "give me a report")), think())

    turn = run_turn(tmp_path, speaker, "give me a report", provider, monkeypatch=monkeypatch)

    facts = _file_facts(str(turn.original))
    assert turn.reply.startswith(
        REPORT_LEAD_ORIGINAL + f"{facts['duplicates']} rows repeat an earlier row exactly"
    )
    assert f"name: {facts['missing_name']} missing value (16.7%)" in turn.reply
    assert "can't do" not in turn.reply and NOT_UNDERSTOOD_SENTENCE not in turn.reply
    assert turn.reply.endswith(NO_CHANGES_SENTENCE)
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_report_is_given_again_for_a_file_already_analysed_earlier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(brain("unclear"), think())

    turn = run_turn(
        tmp_path,
        speaker,
        "can I get the summary?",
        provider,
        analyzed=True,
        monkeypatch=monkeypatch,
    )

    assert turn.reply.startswith(REPORT_LEAD_ORIGINAL)
    assert "spreadsheet.analyze" in turn.steps


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_no_second_analysis_without_a_report_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    turn = run_turn(
        tmp_path,
        speaker,
        "make it nicer",
        Routed(brain("unclear"), think()),
        analyzed=True,
        monkeypatch=monkeypatch,
    )

    assert turn.steps == []
    assert NOT_UNDERSTOOD_SENTENCE in turn.reply


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("kind", ["multi", "merged", "password"])
def test_a_report_on_a_file_that_cannot_be_analysed_says_why_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, kind: str
) -> None:
    provider = Routed(brain("unclear", ("unsupported", "give me a report")), think())

    turn = run_turn(
        tmp_path, speaker, "give me a report", provider, file=kind, monkeypatch=monkeypatch
    )

    assert turn.reply.startswith("I can't work on this file: ")
    assert "can't do" not in turn.reply
    _assert_nothing_claimed(turn)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_exact_duplicates_that_remain_are_in_the_report_and_never_contradicted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    """The case of backlog item 3: "still has 2 duplicate rows" beside an all-clear."""
    message = "remove the empty rows and give me a report"
    provider = Routed(
        brain(
            "multi_step_request",
            ("remove_empty_rows", "remove the empty rows"),
            ("remove_duplicate_rows", "give me a report"),
        ),
        think(),
        plan((EMPTY_ROWS, "remove the empty rows"), ("spreadsheet.analyze", "give me a report")),
    )

    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert turn.delivered
    final = _file_facts(turn.state.execution_result.outputs[0])  # type: ignore[union-attr]
    assert final["duplicates"] == 2
    assert f"{final['duplicates']} rows repeat an earlier row exactly" in turn.reply
    assert "None of the issues" not in turn.reply and "none of the issues" not in turn.reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_remaining_duplicate_count_and_the_analysis_always_agree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(
        brain(
            "remove_empty_rows",
            ("remove_empty_rows", "remove the empty rows"),
            ("remove_duplicate_rows", "keep the duplicates"),
        ),
        think(),
    )

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the empty rows but remove the duplicates later",
        provider,
        monkeypatch=monkeypatch,
    )

    final = _file_facts(turn.state.execution_result.outputs[0])  # type: ignore[union-attr]
    assert final["duplicates"] == 2
    assert "2 duplicate rows" in turn.reply  # the verifier's residual count
    assert "2 rows repeat an earlier row exactly" in turn.reply  # the analysis, same number
    assert "none of the issues" not in turn.reply.lower()


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_chain_that_stops_does_not_pretend_to_have_a_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    message = "remove duplicates, sort by amount and give me a report"
    provider = Routed(
        brain("multi_step_request", ("remove_duplicate_rows", "remove duplicates")),
        think(),
        plan(
            (DEDUPE, "remove duplicates"),
            ("spreadsheet.sort", "sort by amount"),
            ("spreadsheet.analyze", "give me a report"),
        ),
    )

    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert not turn.delivered and turn.original_unchanged
    assert REPORT_NOT_PRODUCED_SENTENCE in turn.reply
    assert REPORT_LEAD_COPY not in turn.reply and REPORT_LEAD_ORIGINAL not in turn.reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_report_the_analysis_could_not_make_is_stated_as_not_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    """The re-run on the final file fails: the delivered copy has no report, and it says so."""
    real = SpreadsheetAnalysisSkill.execute
    calls = {"n": 0}

    def flaky(self: SpreadsheetAnalysisSkill, task: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            return real(self, task)
        from skills.base import SkillResult, SkillStatus

        return SkillResult(status=SkillStatus.FAILURE, errors=["could not read the workbook"])

    monkeypatch.setattr(SpreadsheetAnalysisSkill, "execute", flaky)
    provider = Routed(
        brain(
            "remove_duplicate_rows",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", "give me a report"),
        ),
        think(),
    )

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the duplicate rows and give me a report",
        provider,
        monkeypatch=monkeypatch,
    )

    assert turn.delivered
    assert REPORT_RECHECK_FAILED_SENTENCE in turn.reply
    assert REPORT_LEAD_COPY not in turn.reply and "can't do" not in turn.reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_no_report_is_given_when_the_user_says_not_to(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(
        brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")),
        think(),
    )

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the duplicate rows, no report please",
        provider,
        monkeypatch=monkeypatch,
    )

    assert turn.delivered
    assert REPORT_LEAD_COPY not in turn.reply and REPORT_NOT_RUN_SENTENCE not in turn.reply


# -- 4. Wording -----------------------------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    ("rows", "removed", "left"),
    [
        ({"a": [1, 1, 2]}, 1, 2),  # one duplicate: "1 duplicate row", "2 rows"
        ({"a": [1, 1, 1, 2]}, 2, 2),
        ({"a": [1, 2, 3]}, 0, 3),
    ],
)
def test_counts_use_the_right_plural(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    speaker: str,
    rows: dict,
    removed: int,
    left: int,
) -> None:
    monkeypatch.chdir(tmp_path)
    upload = tmp_path / "uploads" / "report.xlsx"
    upload.parent.mkdir()
    pd.DataFrame(rows).to_excel(upload, index=False)
    state = BuddyState.new(session_id="session-1", workspace_id="w")
    state.user_message = "remove the duplicate rows"
    state.context.add_uploaded_file("uploads/report.xlsx")
    state.context.switch_active_file("uploads/report.xlsx")
    voice = None
    if speaker != "template":
        state.context.set_mode(speaker)
        voice = PersonalityVoice()
    provider = Routed(
        brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")),
        think(),
    )

    build_engine(llm_provider=provider, speaker=voice).run(state)

    reply = state.reply or ""
    noun = "row" if removed == 1 else "rows"
    if removed:
        assert f"removed {removed} duplicate {noun}" in reply.lower()
    else:
        assert "no duplicate rows" in reply
    assert f"The copy has {left} rows, with 0 duplicate rows remaining." in reply
    assert "(s)" not in reply
    assert len(pd.read_excel(state.execution_result.outputs[0])) == left  # type: ignore[union-attr]


def _string_literals(source: str) -> list[tuple[int, str]]:
    """Every string literal in ``source`` that is not a docstring, with its line number."""
    tree = ast.parse(source)
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_text_a_reply_can_carry_uses_the_s_in_brackets_form() -> None:
    """ "1 row", "2 rows": never "row(s)". Log format strings (``%d``/``%s``) aren't replies."""
    root = Path(__file__).resolve().parents[2]
    offenders = []
    for folder in ("buddy", "skills", "backend"):
        for path in sorted((root / folder).rglob("*.py")):
            for line, text in _string_literals(path.read_text()):
                if re.search(r"\w\(s\)", text) and "%" not in text:
                    offenders.append(f"{path.relative_to(root)}:{line}: {text[:60]!r}")
    assert offenders == []


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_an_all_clear_is_still_qualified_next_to_a_residual_if_the_analysis_misses_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    """Defence in depth for the contradiction of backlog item 3: if the analyzer ever said
    "none of the issues I check for were found" while a duplicate remains, the reply
    qualifies it. (The analyzer now finds the duplicates itself; this patches that out.)"""
    monkeypatch.setattr("buddy.inspector._independent_duplicate_row_findings", lambda df: ())
    provider = Routed(
        brain("remove_empty_rows", ("remove_empty_rows", "remove the empty rows")), think()
    )

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the empty rows, I'll deal with the duplicates later",
        provider,
        file="dups_only",
        monkeypatch=monkeypatch,
    )

    assert turn.delivered
    assert "It still has 1 duplicate row." in turn.reply
    assert "None of the other issues I check for were found." in turn.reply
    assert "None of the issues I check for were found" not in turn.reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_report_to_be_emailed_is_not_given_as_the_report_and_is_stated_as_not_doable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(
        brain(
            "remove_duplicate_rows",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", "email me the report"),
        ),
        think(),
    )

    turn = run_turn(
        tmp_path,
        speaker,
        "remove the duplicate rows and email me the report",
        provider,
        monkeypatch=monkeypatch,
    )

    assert turn.delivered
    assert "I can't do this part yet: “email me the report”." in turn.reply
    assert REPORT_LEAD_COPY not in turn.reply


# Brain's prompt names "a report" as the example of `unsupported`, so any of these can come back
# as its `source_phrase`. The planner and the outcome renderer must agree that each is a report
# request, so a delivered report is never also "something I can't do" (review round 1, M1).
REPORT_WORDINGS = [
    "give me a report",
    "give me a report on this file",
    "give me a report of the file",
    "give me a report on my data",
    "give me a full report",
    "send me a report",
    "I need a report",
    "generate a report of all the issues",
    "write me a report about this spreadsheet",
    "report on the duplicates",
    "a report please",
    "analyze my file",
    "analyze the file",
    "analyze this spreadsheet",
    "analyze it",
    "analyze my data",
    "analyse the data",
    "do an analysis",
    "run an analysis",
    "give me an analysis of the file",
    "analysis please",
    "summarize my data",
    "summarize the file",
    "summarize this",
    "summarize it for me",
    "give me a summary of the file",
    "give me a summary of my data",
    "show me a summary",
    "show me a summary of the problems",
    "a short summary would be great",
    "give me a rundown",
    "tell me what's wrong with the file",
    "check the file for problems",
    "take a look at the file",
]


@pytest.mark.parametrize("wording", REPORT_WORDINGS)
def test_every_report_wording_is_one_report_request_for_every_stage(wording: str) -> None:
    from buddy.actions import is_only_a_report_request, mentions_report
    from buddy.models import RequestedAction, Understanding
    from buddy.outcome import build_outcome_report

    assert mentions_report(wording) and is_only_a_report_request(wording)
    understanding = Understanding(
        intent="unclear",
        requested_actions=(RequestedAction("unsupported", wording, "brain"),),
    )

    report = build_outcome_report(None, None, understanding, report_stated=True)

    assert report.actions == ()  # not also stated as "can't do"


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    "wording",
    [
        "analyze my file",
        "summarize my data",
        "give me a summary of the file",
        "write me a report about this spreadsheet",
        "a short summary would be great",
        "tell me what's wrong with the file",
        "give me a rundown",
    ],
)
def test_a_report_flagged_unsupported_is_delivered_and_not_also_cant_do(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, wording: str
) -> None:
    flagged = Routed(brain("unclear", ("unsupported", wording)), think())
    alone = run_turn(tmp_path / "alone", speaker, wording, flagged, monkeypatch=monkeypatch)

    assert alone.reply.startswith(REPORT_LEAD_ORIGINAL)
    assert "can't do" not in alone.reply and "something I can't" not in alone.reply
    _assert_nothing_claimed(alone)

    after = Routed(
        brain(
            "remove_duplicate_rows",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", wording),
        ),
        think(),
    )
    turn = run_turn(
        tmp_path / "after",
        speaker,
        f"remove the duplicate rows and {wording}",
        after,
        monkeypatch=monkeypatch,
    )

    assert turn.delivered
    assert REPORT_LEAD_COPY in turn.reply
    assert "can't do" not in turn.reply and "Partly done" not in turn.reply
    assert "Done —" in turn.reply


# -- Guards that had no test (review round 1, L1, L3) -----------------------------------------


def _request(**fields: Any) -> Any:
    from buddy.models import Understanding
    from buddy.voice import SpeakingRequest

    fields.setdefault("understanding", Understanding(intent="unclear"))
    fields.setdefault("execution_result", None)
    fields.setdefault("verification_result", None)
    fields.setdefault("mode", "friend")
    return SpeakingRequest(**fields)


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_findings_of_an_analysis_that_did_not_pass_are_never_given_as_the_report(
    speaker: str,
) -> None:
    from buddy.models import ExecutionResult, StepResult, StepVerification, VerificationResult
    from buddy.voice import TemplateSpeaker
    from buddy.voice_catalog import REPORT_ANALYSIS_FAILED_SENTENCE, style_for

    step = StepResult(capability="spreadsheet.analyze", skill="excel.analyze", status="success")
    request = _request(
        execution_result=ExecutionResult(success=True, steps=(step,)),
        verification_result=VerificationResult(
            passed=False,
            findings=("[spreadsheet.analyze] [missing_values] a figure nobody checked",),
            steps=(
                StepVerification(
                    capability="spreadsheet.analyze",
                    passed=False,
                    read_only=True,
                    findings=("[missing_values] a figure nobody checked",),
                ),
            ),
        ),
        report_requested=True,
        mode="focus" if speaker == "focus" else "friend",
    )

    style = "plain" if speaker == "template" else style_for(request.mode)
    reply = TemplateSpeaker().compose(request, style=style)

    assert REPORT_ANALYSIS_FAILED_SENTENCE in reply
    assert "a figure nobody checked" not in reply
    assert REPORT_LEAD_ORIGINAL not in reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_report_on_an_unchanged_file_says_when_the_analysis_did_not_run(speaker: str) -> None:
    from buddy.models import ExecutionResult
    from buddy.voice import TemplateSpeaker
    from buddy.voice_catalog import style_for

    request = _request(
        execution_result=ExecutionResult(success=True, warnings=("nothing to execute",)),
        report_requested=True,
        mode="focus" if speaker == "focus" else "friend",
    )

    style = "plain" if speaker == "template" else style_for(request.mode)
    reply = TemplateSpeaker().compose(request, style=style)

    assert REPORT_NOT_RUN_SENTENCE in reply
    assert REPORT_LEAD_ORIGINAL not in reply and NO_CHANGES_SENTENCE in reply


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_think_that_failed_still_says_nothing_was_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str
) -> None:
    provider = Routed(brain("remove_duplicate_rows"), "not json")

    turn = run_turn(
        tmp_path, speaker, "remove the duplicate rows", provider, monkeypatch=monkeypatch
    )

    assert turn.reply == f"{UNDERSTANDING_FAILED_SENTENCE} {NO_CHANGES_SENTENCE}"


# Wordings the first fix was not fitted to (the round-2 reviewer's), as Brain's `unsupported`
# `source_phrase`. A list of the words a report phrase may contain cannot be finished, so the rule
# is the other way round: a phrase that asks for a report is only a report unless it names work
# Buddy can't do (review round 2, M1).
FRESH_WORDINGS = [
    "analyze my spreadsheet",
    "give me a summary of this file",
    "I'd like a report on this spreadsheet",
    "analyze the data for me",
    "Analyze my sales data",
    "give me a quick summary",
    "summary of this sheet please",
    "Generate a report",
    "Create a summary report",
    "Prepare a data quality report",
    "report",
    "report please",
    "I want to see a report",
    "analyze",
    "give me a detailed analysis of the spreadsheet",
    "run a quality check on the file",
    "check the data quality",
    "get me a report on all issues in this file",
    "analyze and summarize my file",
    "provide a report",
    "Make a report of the missing values",
    "give me report on missing values",
    "summarise the spreadsheet",
    "Summarise my file for me please",
    "i need a summary of the data",
    "I need an analysis of my spreadsheet",
    "show me a report of the data problems",
    "give me an overview of the file",
    "what is wrong with my file",
    "give me a report on the duplicate rows",
    "analyze the first sheet",
    "provide a summary of the file",
    "I'd like a summary please",
    "give me a summary of the missing values",
    "report on everything",
    "check my file for duplicates",
    "analyze my file thoroughly",
    "write a report for me",
    "Do a full analysis of this file",
    "Send me a summary of what you find",
]


_VERBS = [
    "give me",
    "send me",
    "provide",
    "I'd like",
    "I want to see",
    "can you provide",
    "please make me",
    "generate",
    "show me",
]
_NOUNS = ["a report", "a summary", "an analysis", "an overview", "a quick report"]
_OBJECTS = [
    "",
    "of the file",
    "on this spreadsheet",
    "of the missing values",
    "on the duplicate rows",
    "about my sales data",
    "of everything",
    "on the first sheet",
    "of the data problems and the empty rows",
]
GENERATED_WORDINGS = [f"{v} {n} {o}".strip() for v in _VERBS for n in _NOUNS for o in _OBJECTS]


def _stated_but_not_done(phrase: str) -> tuple[Any, ...]:
    from buddy.models import RequestedAction, Understanding
    from buddy.outcome import build_outcome_report

    understanding = Understanding(
        intent="unclear", requested_actions=(RequestedAction("unsupported", phrase, "brain"),)
    )
    return build_outcome_report(None, None, understanding, report_stated=True).actions


@pytest.mark.parametrize("phrase", FRESH_WORDINGS + GENERATED_WORDINGS)
def test_a_phrase_that_asks_for_a_report_and_nothing_else_is_never_cant_do(phrase: str) -> None:
    from buddy.actions import mentions_report

    assert mentions_report(phrase), phrase
    assert _stated_but_not_done(phrase) == ()


@pytest.mark.parametrize(
    "phrase",
    [
        "give me a report and sort it by amount",
        "analyze my file and make a chart",
        "report on the file and email it to Bob",
        "analyze the data and highlight the duplicates",
        "give me a report then delete column B",
        "summarize the file and translate it",
        "a report on the file and send it to my boss",
        "analyze my data and fix the formatting",
        "report and add a total column",
    ],
)
def test_a_report_phrase_that_also_asks_for_work_buddy_cant_do_still_says_so(phrase: str) -> None:
    (action,) = _stated_but_not_done(phrase)

    assert action.reason == "no_skill"


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    ("message", "phrase"),
    [
        ("Can you provide a report?", "provide a report"),
        ("give me a report on the duplicate rows", "give me a report on the duplicate rows"),
        ("check my file for duplicates", "check my file for duplicates"),
        ("Make a report of the missing values", "Make a report of the missing values"),
        ("report on everything", "report on everything"),
    ],
)
def test_ordinary_report_requests_flagged_unsupported_are_delivered_with_no_contradiction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str, phrase: str
) -> None:
    provider = Routed(brain("unclear", ("unsupported", phrase)), think())
    alone = run_turn(tmp_path / "alone", speaker, message, provider, monkeypatch=monkeypatch)

    assert alone.reply.startswith(REPORT_LEAD_ORIGINAL)
    for text in ("can't do", "mentions", "didn't run", "Partly done"):
        assert text not in alone.reply, alone.reply
    _assert_nothing_claimed(alone)

    after = Routed(
        brain(
            "remove_duplicate_rows",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", phrase),
        ),
        think(),
    )
    turn = run_turn(
        tmp_path / "after",
        speaker,
        f"remove the duplicate rows and {message}",
        after,
        monkeypatch=monkeypatch,
    )

    assert turn.delivered and REPORT_LEAD_COPY in turn.reply
    for text in ("can't do", "Partly done"):
        assert text not in turn.reply, turn.reply
