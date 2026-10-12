"""Finding a session's file in a message; Think's "which file?" made to agree with code.

``match_file_name`` looks a typed string up in the names of the session's files. It reads no
meaning from the words around a name. ``settle_file_question`` keeps the question "which file?"
for a file that can't be found or told apart, and drops it when the file is settled.
"""

from __future__ import annotations

import json

import pytest

from buddy.brain import ClaudeInterpreter, MentionCrossCheck, UnderstandingRequest
from buddy.llm import LLMMessage
from buddy.models import FILE_KIND_ORIGINAL, FileChoice, FileUsed, Understanding
from buddy.source import NAME_MATCH_MARGIN, NAME_MATCH_MIN, match_file_name
from buddy.state import BuddyState
from buddy.think import (
    CLARIFY_FAILED,
    CLARIFY_OTHER,
    CLARIFY_WHICH_FILE,
    Reasoning,
    settle_file_question,
)

NAMES = ["messy-data.xlsx", "other.xlsx"]


@pytest.mark.parametrize(
    "message",
    [
        "messy-data.xlsx",
        "now remove the empty rows too in mess-data.xlsx",
        "messy-data.xslx",
        "MESSY-DATA.XLSX",
        "use 'messy-data.xlsx', please",
        "(messy-data.xlsx)",
        "remove duplicate rows from messy-data.xlsx.",
        "messy_data.xlsx",
        "messydata.xlsx",
    ],
)
def test_a_name_with_a_slip_matches_the_file(message: str) -> None:
    assert match_file_name(message, NAMES).indices == (0,)
    assert not match_file_name(message, NAMES).unclear


@pytest.mark.parametrize(
    "message",
    [
        "remove duplicate rows",
        "give me a report",
        "now remove the empty rows too",
        "e.g. the blank ones, etc.",
        "messy data",
        "version 2.5 of the plan",
        "",
    ],
)
def test_a_message_that_names_no_spreadsheet_file_matches_nothing_and_is_clear(
    message: str,
) -> None:
    found = match_file_name(message, NAMES)

    assert found.indices == () and not found.unclear


@pytest.mark.parametrize("message", ["use sales.xlsx", "remove dupes in totals.csv", "q3.xlsm"])
def test_a_spreadsheet_name_that_matches_no_file_is_unclear(message: str) -> None:
    found = match_file_name(message, NAMES)

    assert found.indices == () and found.unclear


def test_a_stem_alone_is_not_a_file_name() -> None:
    """Only the shape of a spreadsheet's name counts, so a word like "report" can't match one."""
    assert match_file_name("give me a report", ["report.xlsx"]).indices == ()
    assert match_file_name("report", ["report.xlsx"]).indices == ()


def test_two_different_names_equally_close_are_unclear() -> None:
    names = ["report_a.xlsx", "report_b.xlsx"]

    found = match_file_name("report_x.xlsx", names)

    assert found.unclear and found.indices == ()


def test_an_exact_name_beats_a_near_one() -> None:
    names = ["data.xlsx", "data1.xlsx"]

    assert match_file_name("clean data.xlsx please", names).indices == (0,)
    assert match_file_name("clean data1.xlsx please", names).indices == (1,)


def test_two_exact_names_in_one_message_are_unclear() -> None:
    assert match_file_name("merge messy-data.xlsx and other.xlsx", NAMES).unclear


def test_files_with_the_same_name_are_returned_together_for_the_caller_to_choose() -> None:
    found = match_file_name("clean a.xlsx", ["a.xlsx", "b.xlsx", "A.xlsx"])

    assert found.indices == (0, 2) and not found.unclear


def test_a_name_with_spaces_matches() -> None:
    names = ["my sales file.xlsx", "q3.xlsx"]

    assert match_file_name("clean my sales file.xlsx now", names).indices == (0,)
    assert match_file_name("clean my sales fiel.xlsx now", names).indices == (0,)


def test_a_very_long_message_is_read_only_as_far_as_the_limit() -> None:
    message = ("word " * 600) + "messy-data.xlsx"  # the name is past the limit

    assert match_file_name(message, NAMES).indices == ()


def test_the_thresholds_are_the_ones_the_tests_assume() -> None:
    assert 0.8 <= NAME_MATCH_MIN <= 0.9 and 0.02 <= NAME_MATCH_MARGIN <= 0.08


# -- settle_file_question -------------------------------------------------------------------


def _state(*, unclear: bool = False, settled: bool = True) -> BuddyState:
    state = BuddyState.new(session_id="s", workspace_id="w")
    state.turn_id = "t0001"
    state.file_choice = FileChoice(original="/d/a.xlsx", unclear=unclear)
    if settled:
        state.file_used = FileUsed(path="/d/a.xlsx", kind=FILE_KIND_ORIGINAL)
    return state


def _asks(reason: str) -> Reasoning:
    return Reasoning(summary="s", clarification_required=True, clarification_reason=reason)


def test_a_which_file_question_is_dropped_when_the_file_is_settled() -> None:
    settled = settle_file_question(_state(), _asks(CLARIFY_WHICH_FILE))

    assert settled.clarification_required is False and settled.clarification_reason == ""
    assert settled.summary == "s"  # the rest of Think's reading is kept


def test_other_questions_are_left_alone() -> None:
    for reason in ("which_action", CLARIFY_OTHER):
        asked = _asks(reason)
        assert settle_file_question(_state(), asked) == asked


def test_an_unclear_file_is_asked_about_even_when_think_did_not_ask() -> None:
    proceed = Reasoning(summary="s", clarification_required=False)

    asked = settle_file_question(_state(unclear=True), proceed)

    assert asked.clarification_required and asked.clarification_reason == CLARIFY_WHICH_FILE


def test_an_unclear_file_keeps_think_asking_which_file() -> None:
    asked = _asks(CLARIFY_WHICH_FILE)

    assert settle_file_question(_state(unclear=True), asked) == asked


def test_a_failed_think_stays_failed() -> None:
    failed = _asks(CLARIFY_FAILED)

    assert settle_file_question(_state(unclear=True), failed) == failed
    assert settle_file_question(_state(), failed) == failed


def test_with_no_file_used_the_question_stays() -> None:
    asked = _asks(CLARIFY_WHICH_FILE)

    assert settle_file_question(_state(settled=False), asked) == asked


# -- Brain: a waiting request ---------------------------------------------------------------


class _Scripted:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    def complete(
        self, *, system: str, messages: tuple[LLMMessage, ...], max_tokens: int = 1024
    ) -> str:
        self.prompts.append(messages[0].content)
        return self.reply


def _request(message: str, pending: str | None) -> UnderstandingRequest:
    return UnderstandingRequest(
        user_message=message,
        observations=(),
        uploaded_files=("a.xlsx",),
        active_task=None,
        active_file=None,
        recent_conversation=(),
        pending_request=pending,
    )


def _reply(**fields: object) -> str:
    return json.dumps({"intent": "remove_duplicate_rows", "confidence": 0.9, **fields})


def test_brains_answers_pending_is_honoured_only_when_a_request_is_waiting() -> None:
    brain = ClaudeInterpreter(_Scripted(_reply(answers_pending=True)))

    assert brain(_request("a.xlsx", "remove duplicate rows")).answers_pending is True
    assert brain(_request("a.xlsx", None)).answers_pending is False


@pytest.mark.parametrize("value", ["true", 1, "yes", None, [True]])
def test_anything_but_a_true_is_not_an_answer(value: object) -> None:
    brain = ClaudeInterpreter(_Scripted(_reply(answers_pending=value)))

    assert brain(_request("a.xlsx", "remove duplicate rows")).answers_pending is False


def test_the_phrases_of_an_answer_are_found_in_the_waiting_request() -> None:
    reply = _reply(
        answers_pending=True,
        requested_actions=[
            {"action": "remove_duplicate_rows", "source_phrase": "remove duplicate rows"}
        ],
    )
    brain = ClaudeInterpreter(_Scripted(reply))

    answered = brain(_request("a.xlsx", "please remove duplicate rows in x.xlsx"))
    own = ClaudeInterpreter(_Scripted(_reply(answers_pending=False, requested_actions=[
        {"action": "remove_duplicate_rows", "source_phrase": "remove duplicate rows"}
    ])))(_request("a.xlsx", "please remove duplicate rows in x.xlsx"))

    (entry,) = answered.requested_actions
    assert entry.phrase_found and entry.source_phrase == "remove duplicate rows"
    (not_found,) = own.requested_actions  # the message itself has no such words
    assert not not_found.phrase_found


def test_the_waiting_request_is_in_the_prompt_only_when_there_is_one() -> None:
    provider = _Scripted(_reply())
    brain = ClaudeInterpreter(provider)

    brain(_request("a.xlsx", "remove duplicate rows in x.xlsx"))
    brain(_request("a.xlsx", None))

    assert "waiting for the user to say which file it is about" in provider.prompts[0]
    assert "remove duplicate rows in x.xlsx" in provider.prompts[0]
    assert "waiting for the user" not in provider.prompts[1]


def test_the_mention_check_reads_the_waiting_request_when_brain_says_it_was_answered() -> None:
    """The words that ask for the work are in the waiting request, not in the answer."""
    waiting = "remove the duplicate rows"
    reply = json.dumps({"intent": "unclear", "confidence": 0.5, "answers_pending": True})
    checked = MentionCrossCheck(ClaudeInterpreter(_Scripted(reply)))

    answered = checked(_request("a.xlsx", waiting))
    not_answered = MentionCrossCheck(
        ClaudeInterpreter(_Scripted(json.dumps({"intent": "unclear", "confidence": 0.5})))
    )(_request("a.xlsx", waiting))

    assert [a.action for a in answered.requested_actions] == ["remove_duplicate_rows"]
    assert answered.requested_actions[0].source == "mention"
    assert not_answered.requested_actions == ()  # only the message itself is read then


def test_a_brain_that_resumes_swaps_the_turns_message_for_the_waiting_one() -> None:
    from buddy.brain import Brain

    state = BuddyState.new(session_id="s", workspace_id="w")
    state.user_message = "a.xlsx"
    state.pending_request = "remove duplicate rows in x.xlsx"

    class Answers:
        def __call__(self, request: UnderstandingRequest) -> Understanding:
            return Understanding(intent="remove_duplicate_rows", answers_pending=True)

    Brain(Answers())(state)

    assert state.user_message == "remove duplicate rows in x.xlsx"


def test_a_brain_that_does_not_resume_keeps_the_turns_message() -> None:
    from buddy.brain import Brain

    state = BuddyState.new(session_id="s", workspace_id="w")
    state.user_message = "remove the empty rows in a.xlsx"
    state.pending_request = "remove duplicate rows in x.xlsx"

    class OwnRequest:
        def __call__(self, request: UnderstandingRequest) -> Understanding:
            return Understanding(intent="remove_empty_rows", answers_pending=False)

    Brain(OwnRequest())(state)

    assert state.user_message == "remove the empty rows in a.xlsx"
