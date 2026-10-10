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


# -- 3b. A phrase that asks for a report -----------------------------------------------------
#
# Brain's `unsupported` entry is often the report itself ("a report" is its own example). The
# decision for such a phrase never rests on a list of words (review rounds 1 to 3, each of which
# found a list wrong in one direction): a phrase that is only a report request, in nouns, is
# silent; a phrase with a report request AND any other word gets one fixed sentence that is true
# however the other words are read ("I did nothing beyond the report for that part of your
# request") and no "Done" or "Partly done"; a phrase without a report request is the ordinary
# "I can't do that yet".

# A report request and one more thing, in the words a person would use (the round-3
# reviewer's, plus round 2's nine).
REPORT_PLUS_AN_EXTRA = [
    "send the report to my boss",
    "send the summary to Bob",
    "send my manager the report",
    "send Bob a summary of the file",
    "give my boss a report on this file",
    "show my team a summary of the data",
    "message me the report when it is ready",
    "text me the report",
    "whatsapp the summary to my team",
    "drop the report in the shared folder",
    "put the report on the drive",
    "give me a report in pdf",
    "give me a report as a pdf",
    "give me a report in a word doc",
    "give me the report as a csv",
    "give me a report in a powerpoint slide",
    "give me a report in French",
    "give me a report in Spanish",
    "give me a report as a table in a new sheet",
    "put the report in a new sheet",
    "write the report to a new tab",
    "include the report in the file",
    "append the summary to the sheet",
    "put the summary at the top of the file",
    "give me a report as an image",
    "give me a report with conditional formatting",
    "analyze my file and tell me which customer spent the most",
    "give me a report and calculate the total of the amount column",
    "analyze my file and sum the amount column",
    "give me a summary with the average amount",
    "report on the file and count the unique names",
    "summarize the file and group it by name",
    "analyze my data and compare it to last month",
    "give me a report and combine it with the other file",
    "analyze the file and join it with sheet2",
    "give me a report and update the totals",
    "analyze my file and predict next month",
    "give me a report and find the outliers",
    "analyze my file and tell me what to do next",
    "give me a report and move column B",
    "analyze my file and copy it to a new file",
    "give me a report and lock the header row",
    "summarize the file and shorten the names",
    "give me a report and make the header bold",
    "analyze my file and fix the typos",
    "give me a report and normalise the dates",
    "analyze the file and standardise the names",
    "give me a report and clean up the formatting",
    "analyze my file and tidy it up",
    "report on the file and also sort out the blanks",
    "give me a report and sort it by amount",
    "analyze my file and make a chart",
    "report on the file and email it to Bob",
    "analyze the data and highlight the duplicates",
    "give me a report then delete column B",
    "summarize the file and translate it",
    "a report on the file and send it to my boss",
    "analyze my data and fix the formatting",
    "report and add a total column",
    "give me a report and email it to Bob",
]

# A plain report request that mentions work as the thing it is ABOUT (round-3 reviewer's).
REPORT_ABOUT_WORK = [
    "give me a report on which rows to remove",
    "analyze my file and tell me what needs to be fixed",
    "give me a report on what needs cleaning",
    "give me a summary of what you cleaned",
    "give me a summary of the rows you removed",
    "report on the duplicates that were removed",
    "give me a report on how clean the data is",
    "analyze the data and show me what I should clean up",
    "give me a report on any errors that need fixing",
    "summarize the problems I should correct",
    "report on the rows I could delete",
    "analyze my file so I know what to fix",
    "give me a report so I can decide what to drop",
    "report on which columns I should add",
    "summary of the problems before I save it",
    "analyze my file before I clean it",
    "report on trailing spaces to trim",
    "report on cells that were replaced",
    "give me a summary of everything that was dropped",
    "analysis of what needs to be removed",
    "give me a report on what you fixed",
    "report of the changes you made and what you removed",
    "give me a quick report on rows that could be deleted",
    "summary of what to correct",
    "analyze my file and list the issues to fix",
    "report on the data I can safely remove",
    "summarize which rows were added",
    "report on the pasted rows",
    "give me a report on the colour of the cells",
    "give me a report on the dashboard sheet",
    "analyze the forecast sheet",
    "give me a summary of the schedule column",
    "give me a report on the hidden rows",
    "give me a report on the merged cells",
    "report on the split names",
    "summarize the converted dates",
    "give me a report on the printed rows",
    "give me a report on the filtered rows",
    "report on the sorted order of the file",
    "analyze the shared sheet",
    "report on the frozen panes",
    "give me a report on the bold headers",
]

# Everyday spreadsheet words that are the names of a column or sheet.
EVERYDAY_COLUMNS = [
    "email",
    "Email Address",
    "mail",
    "share",
    "Market Share",
    "post code",
    "post",
    "plot",
    "plot number",
    "color",
    "colour",
    "schedule",
    "Schedule",
    "filter",
    "print",
    "forecast",
    "dashboard",
    "trim",
    "split",
    "total",
    "bold",
    "date",
    "address",
    "Description",
    "Notes",
    "Price",
    "hide",
    "drop",
    "add",
    "save",
    "download",
    "attach",
    "publish",
    "rotate",
]

# Not a request for a report, but the bare noun is read as one (backlog RQ-L2).
NOT_A_REPORT_REQUEST = [
    "rewrite the summary",
    "update the summary",
    "make the summary shorter",
    "proofread the report",
    "finalize the report",
    "approve the report",
    "improve the analysis",
    "polish the summary",
    "move the summary to the top",
    "number the summary lines",
    "fix the summary",
    "sort the summary",
    "delete the summary",
]

# A report asked for, with a promise not to change anything.
CAUTIOUS_REPORT_REQUESTS = [
    "just analyze my file, don't delete anything",
    "give me a report but don't remove anything",
    "give me a report first, don't clean anything yet",
    "analyze my file without fixing anything",
    "give me a summary but don't change or delete the data",
    "give me a report, no need to remove anything",
    "I only want a report, please don't drop any rows",
    "report on the file but leave everything as it is",
    "give me a report and don't save anything",
    "analyze my file but do not add anything",
]

# Genuine report requests that hold a guard word ("myself", "I can", "don't forget").
REQUESTS_WITH_A_GUARD_WORD = [
    "give me a summary so I can review it",
    "give me a report so I can check it",
    "analyze my file so I can look at the problems",
    "summarize the data so I can check the numbers",
    "give me a report on this file, then I can decide what to do",
    "I can't tell what's wrong with my file, can you analyze it",
    "I can't read this spreadsheet, give me a summary",
    "I can't find the problem myself, check my file",
    "I built this file myself, can you analyze it",
    "I made this spreadsheet myself. Give me a report on it",
    "I'm new to this myself, give me a summary of the file",
    "I'll upload a new version later; for now give me a report",
    "I'll be away tomorrow, so please analyze my data now",
    "I'll need a full report on the file",
    "I will review it later, give me a summary now",
    "I can send it to my boss after you analyze my file",
    "please don't forget to include a summary of the problems",
    "don't forget to give me a report on the file",
    "don't worry about the formatting, just analyze my file",
    "don't hesitate to give me a report on everything you find",
    "never mind the duplicates, give me a summary of the file",
    "no need to be fancy, just give me a report on the data",
    "no need to wait, analyze my spreadsheet",
    "won't take long: give me a summary of this file",
    "never used this before, can you analyze my data",
    "I can't wait to see the report on my file",
    "can you check my data? I don't have time to do it myself",
    "I can use a summary of the data",
    "don't take too long, a summary of the file please",
    "check my data and tell me what you find, I'm too busy to look myself",
]

# Messages that decline a report, or will do it themselves.
GENUINE_DECLINES = [
    "I won't check the file, just remove the empty rows",
    "never look at my data",
    "I'll summarize it myself",
    "I can analyze it myself",
    "let me check the data myself",
    "I won't look at the file again",
    "don't give me a report",
    "no report please",
    "don't analyze my file",
    "I don't want a summary",
    "no need to check my file",
    "no need to give me a report",
    "I'll check the file myself",
    "I will review the data myself",
    "I can look at the file myself",
    "don't send me a summary, just remove the duplicates",
    "skip the report",
    "without a report",
    "remove the duplicate rows but don't look at my data",
    "email me the report",
]


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
        intent="unclear",
        requested_actions=(RequestedAction("unsupported", phrase, "brain", phrase_found=True),),
    )
    return build_outcome_report(None, None, understanding, report_stated=True).actions


REPORT_ONLY = "report_only"
NOTHING_BEYOND_THE_REPORT = "I did nothing beyond the report for that part of your request."

# Plain wordings made of a report request and nouns about the file: nothing else is asked.
SILENT_WORDINGS = [
    "give me a report",
    "provide a report",
    "report",
    "Generate a report",
    "analyze my spreadsheet",
    "give me a summary of this file",
    "give me a quick summary",
    "analyze the data for me",
    "report on everything",
    "analyze the first sheet",
    "report on the empty rows",
    "give me a summary of the missing values",
    "check my file for duplicates",
    "give me a report on the duplicate rows",
    "what is wrong with my file",
]


def _outcome_actions(phrase: str) -> tuple[Any, ...]:
    return _stated_but_not_done(phrase)


@pytest.mark.parametrize("phrase", SILENT_WORDINGS)
def test_a_phrase_that_is_only_a_report_request_is_silent(phrase: str) -> None:
    from buddy.actions import is_only_a_report_request, mentions_report

    assert mentions_report(phrase) and is_only_a_report_request(phrase), phrase
    assert _stated_but_not_done(phrase) == ()


@pytest.mark.parametrize(
    "phrase",
    FRESH_WORDINGS
    + GENERATED_WORDINGS
    + REPORT_ABOUT_WORK
    + CAUTIOUS_REPORT_REQUESTS
    + [f"{head} the {c} column" for c in EVERYDAY_COLUMNS for head in ("analyze", "report on")],
)
def test_a_report_request_is_never_also_cant_do(phrase: str) -> None:
    from buddy.actions import mentions_report

    assert mentions_report(phrase), phrase
    actions = _stated_but_not_done(phrase)
    assert [a.reason for a in actions] in ([], [REPORT_ONLY]), phrase
    assert all(a.source == "" and a.user_phrase == "" for a in actions)


@pytest.mark.parametrize("phrase", REPORT_PLUS_AN_EXTRA + NOT_A_REPORT_REQUEST)
def test_a_report_request_with_anything_else_is_never_silent(phrase: str) -> None:
    """Whatever the extra is, a listed verb or not, the reply cannot read as complete."""
    (action,) = _stated_but_not_done(phrase)

    assert action.reason == REPORT_ONLY and action.source == ""


@pytest.mark.parametrize(
    "phrase",
    [
        "sort by amount",
        "make a chart of the amounts",
        "email the file to Bob",
        "highlight the empty cells",
        "translate the names to French",
    ],
)
def test_a_phrase_with_no_report_request_still_says_cant_do_and_quotes_it(phrase: str) -> None:
    (action,) = _stated_but_not_done(phrase)

    assert action.reason == "no_skill" and action.user_phrase == phrase


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    "extra",
    [
        "send the report to my boss",
        "give me a report in pdf",
        "put the report in a new sheet",
        "analyze the email column",
        "update the summary",
        "finalize the report",
        "analyze my file and tell me what needs to be fixed",
    ],
)
def test_a_delivery_with_a_report_and_an_unnamed_extra_never_says_done(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, extra: str
) -> None:
    """Review round 3, H1/M1: flat is allowed; reading as complete, or as 'can't', is not."""
    provider = Routed(
        brain(
            "remove_duplicate_rows",
            ("remove_duplicate_rows", "remove the duplicate rows"),
            ("unsupported", extra),
        ),
        think(),
    )
    message = f"remove the duplicate rows and {extra}"
    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert turn.delivered and REPORT_LEAD_COPY in turn.reply, turn.reply
    assert NOTHING_BEYOND_THE_REPORT in turn.reply
    for text in ("Done", "Partly done", "can't do", "mentions", "didn't run"):
        assert text not in turn.reply, turn.reply
    assert turn.original_unchanged


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize(
    "message",
    [
        "analyze the email column",
        "give me a report on which rows to remove",
        "give me a report but don't remove anything",
        "report on everything and put it in a pdf",
    ],
)
def test_a_report_request_with_more_words_gets_the_report_first_and_one_flat_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, message: str
) -> None:
    provider = Routed(brain("unclear", ("unsupported", message)), think())
    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert turn.reply.startswith(REPORT_LEAD_ORIGINAL), turn.reply
    assert turn.reply.count(NOTHING_BEYOND_THE_REPORT) == 1
    assert turn.reply.index(NOTHING_BEYOND_THE_REPORT) > turn.reply.index(REPORT_LEAD_ORIGINAL)
    for text in ("can't do", "mentions", "Done", "Partly done"):
        assert text not in turn.reply, turn.reply
    assert CAPABILITIES[STYLE[speaker]] not in turn.reply
    _assert_nothing_claimed(turn)


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


# -- 3c. The guards on a report request (review round 3, M2) ----------------------------------


@pytest.mark.parametrize("message", REQUESTS_WITH_A_GUARD_WORD)
def test_a_report_request_is_not_vetoed_by_a_word_that_only_looks_like_a_guard(
    message: str,
) -> None:
    from buddy.actions import mentions_report

    assert mentions_report(message), message


@pytest.mark.parametrize("message", GENUINE_DECLINES)
def test_a_message_that_declines_a_report_is_still_not_one(message: str) -> None:
    from buddy.actions import mentions_report

    assert not mentions_report(message), message


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("intent", ["unclear", "conversation"])
@pytest.mark.parametrize(
    "message",
    [
        "give me a summary so I can review it",
        "I built this file myself, can you analyze it",
        "don't hesitate to give me a report on everything you find",
        "I'm new to this myself, give me a summary of the file",
    ],
)
def test_a_genuine_report_request_with_a_guard_word_gets_the_report_in_every_reading(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, speaker: str, intent: str, message: str
) -> None:
    """Brain may say 'unclear', 'conversation' or nothing useful: the report is still delivered."""
    provider = Routed(brain(intent), think())
    turn = run_turn(tmp_path, speaker, message, provider, monkeypatch=monkeypatch)

    assert turn.reply.startswith(REPORT_LEAD_ORIGINAL), turn.reply
    assert not any(g in turn.reply for g in GREETINGS[STYLE[speaker]])
    _assert_nothing_claimed(turn)
