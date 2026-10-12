"""Unit tests for buddy.actions: the action table and the ledger helpers."""

from __future__ import annotations

import pytest

from buddy import planner
from buddy.actions import (
    ACTION_VOCABULARY,
    ACTIONS,
    ACTIONS_BY_NAME,
    CONVERSATION,
    INTENT_CAPABILITIES,
    REPORT_ON_FILE,
    UNSUPPORTED,
    asks_for_report,
    find_phrase,
    is_conversation,
    mentioned_actions,
    report_phrases,
    requested_ledger,
)
from buddy.brain import INTENT_VOCABULARY
from buddy.engine_factory import _build_registry
from buddy.inspector import _TARGET_COUNTERS
from buddy.models import (
    EXTRACTED_TABLE_TARGET,
    LEDGER_FROM_BRAIN,
    LEDGER_FROM_INTENT,
    LEDGER_FROM_MENTION,
    RequestedAction,
    Understanding,
)

# -- The table ---------------------------------------------------------------------------


def test_every_action_is_a_brain_intent_with_a_registered_skill_and_a_measured_target() -> None:
    registered = {
        capability for skill in _build_registry().available_skills()
        for capability in skill.supported_inputs
    }
    for action in ACTIONS:
        assert action.name in INTENT_VOCABULARY
        assert action.capability in registered
        if action.changes_file and action.removes_rows:
            assert action.target in _TARGET_COUNTERS
            assert action.mention is not None
        elif action.changes_file:
            # Taking a table out of a PDF: measured by its own verifier, and found by Brain's
            # reading only (no word list).
            assert action.extracts_table and action.target == EXTRACTED_TABLE_TARGET
            assert action.mention is None
    assert ACTION_VOCABULARY == {a.name for a in ACTIONS} | {UNSUPPORTED}


def test_the_report_is_a_row_of_the_table_with_no_mention_pattern_and_no_file_change() -> None:
    report = ACTIONS_BY_NAME[REPORT_ON_FILE]

    assert REPORT_ON_FILE in ACTION_VOCABULARY and REPORT_ON_FILE in INTENT_VOCABULARY
    assert report.capability == "spreadsheet.analyze"
    assert report.mention is None
    assert not report.changes_file
    assert REPORT_ON_FILE not in INTENT_CAPABILITIES  # planned as the analysis, not as a step


def test_the_planner_routes_intents_with_the_same_table() -> None:
    assert planner.INTENT_CAPABILITIES is INTENT_CAPABILITIES
    assert INTENT_CAPABILITIES == {
        "remove_duplicate_rows": "spreadsheet.deduplicate",
        "remove_empty_rows": "spreadsheet.remove_empty_rows",
        "extract_pdf_table": "pdf.extract_table",
    }


# -- The mention detector ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("remove the duplicate rows please", [("remove_duplicate_rows", "duplicate rows")]),
        ("remove duplicates", [("remove_duplicate_rows", "duplicates")]),
        ("dedupe this", [("remove_duplicate_rows", "dedupe")]),
        ("Deduplicate it", [("remove_duplicate_rows", "Deduplicate")]),
        ("any dupes?", [("remove_duplicate_rows", "dupes")]),
        ("drop duplicated entries", [("remove_duplicate_rows", "duplicated entries")]),
        ("delete the blank rows", [("remove_empty_rows", "blank rows")]),
        ("strip empty lines", [("remove_empty_rows", "empty lines")]),
        ("remove empty-rows", [("remove_empty_rows", "empty-rows")]),
        (
            "remove the empty rows but keep the duplicates",
            [("remove_empty_rows", "empty rows"), ("remove_duplicate_rows", "duplicates")],
        ),
        (
            "remove duplicates, and delete the blank rows",
            [("remove_duplicate_rows", "duplicates"), ("remove_empty_rows", "blank rows")],
        ),
        # Not mentions: paraphrases (the known limit) and unrelated words.
        ("get rid of the repeated entries", []),
        ("hi, what can you do?", []),
        ("sort by amount", []),
        ("fill the empty cells", []),
        # The report has no mention pattern: no wording of a report request is ever found.
        ("give me a report", []),
        ("summarize the file", []),
        ("what is wrong with my file", []),
        ("analyze my data and give me an overview", []),
    ],
)
def test_mentioned_actions(message: str, expected: list[tuple[str, str]]) -> None:
    assert [(a.name, text) for a, text in mentioned_actions(message)] == expected


def test_a_mention_inside_the_phrase_brain_gave_to_the_report_is_not_a_request() -> None:
    message = "remove the empty rows and give me a report on the duplicate rows"

    assert [a.name for a, _ in mentioned_actions(message)] == [
        "remove_empty_rows",
        "remove_duplicate_rows",
    ]
    inside = mentioned_actions(message, except_inside=["give me a report on the duplicate rows"])
    assert [a.name for a, _ in inside] == ["remove_empty_rows"]
    # A phrase that isn't in the message, or an empty one, excludes nothing.
    assert mentioned_actions(message, except_inside=["not there", ""]) == mentioned_actions(message)


# -- The report and conversation come from Brain's reading only -----------------------------


def test_a_report_is_asked_for_only_when_brains_reading_has_it() -> None:
    by_action = Understanding(
        intent="multi_step_request",
        requested_actions=(
            RequestedAction("remove_duplicate_rows", "remove duplicates", LEDGER_FROM_BRAIN, True),
            RequestedAction(REPORT_ON_FILE, "a report on the rows", LEDGER_FROM_BRAIN, True),
        ),
    )
    by_intent = Understanding(intent=REPORT_ON_FILE)
    unsupported = Understanding(
        intent="unclear",
        requested_actions=(
            RequestedAction(UNSUPPORTED, "give me a report", LEDGER_FROM_BRAIN, True),
        ),
    )

    assert asks_for_report(by_action) and report_phrases(by_action) == ("a report on the rows",)
    assert asks_for_report(by_intent) and report_phrases(by_intent) == ()
    # Brain did not read a report: nothing is labelled one, whatever the message says.
    assert not asks_for_report(unsupported)
    assert not asks_for_report(Understanding(intent="unclear"))
    assert not asks_for_report(Understanding(intent=None))


@pytest.mark.parametrize(
    ("intent", "actions", "message", "expected"),
    [
        (CONVERSATION, (), "hello", True),
        (CONVERSATION, (), "thanks for the summary!", True),
        (CONVERSATION, (), "an overview of what you can do?", True),
        (CONVERSATION, (), "give me a report", True),  # Brain's reading decides, not the words
        (CONVERSATION, (), "remove the duplicate rows", False),  # a file-changing mention
        (CONVERSATION, (RequestedAction(REPORT_ON_FILE, "a report", "brain", True),), "x", False),
        (CONVERSATION, (RequestedAction(UNSUPPORTED, "sort", "brain", True),), "x", False),
        ("unclear", (), "hello", False),
    ],
)
def test_a_conversation_is_brains_intent_with_nothing_contradicting_it(
    intent: str, actions: tuple[RequestedAction, ...], message: str, expected: bool
) -> None:
    understanding = Understanding(intent=intent, requested_actions=actions)

    assert is_conversation(understanding, message) is expected


# -- Phrases and the ledger ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("message", "phrase", "expected"),
    [
        ("Remove Duplicates now", "remove duplicates", "Remove Duplicates"),
        ("remove duplicates", "remove duplicates", "remove duplicates"),
        ("remove duplicates", "  remove duplicates ", "remove duplicates"),
        ("remove duplicates", "deduplicate", None),
        ("remove duplicates", "", None),
        ("sort (by amount)", "(by amount)", "(by amount)"),
    ],
)
def test_find_phrase_returns_the_users_own_words_or_none(
    message: str, phrase: str, expected: str | None
) -> None:
    assert find_phrase(message, phrase) == expected


def test_the_ledger_is_brains_reading_then_mention_only_entries_each_action_once() -> None:
    dedupe = RequestedAction("remove_duplicate_rows", "remove duplicates", LEDGER_FROM_BRAIN, True)
    sort = RequestedAction(UNSUPPORTED, "sort by amount", LEDGER_FROM_BRAIN, True)
    chart = RequestedAction(UNSUPPORTED, "make a chart", LEDGER_FROM_BRAIN, True)
    understanding = Understanding(
        intent="remove_empty_rows",
        requested_actions=(
            dedupe,
            dedupe,  # listed twice by Brain: kept once
            sort,
            chart,  # every unsupported entry is kept
            RequestedAction("remove_duplicate_rows", "duplicates", LEDGER_FROM_MENTION, True),
            RequestedAction("remove_empty_rows", "empty rows", LEDGER_FROM_MENTION, True),
        ),
    )

    assert requested_ledger(understanding) == (
        dedupe,
        sort,
        chart,
        # Brain's intent is its reading too, so the mention entry for it is dropped.
        RequestedAction("remove_empty_rows", "", LEDGER_FROM_INTENT, False),
    )


@pytest.mark.parametrize("intent", ["multi_step_request", "unclear", None])
def test_an_intent_that_names_no_single_action_adds_nothing(intent: str | None) -> None:
    assert requested_ledger(Understanding(intent=intent)) == ()
