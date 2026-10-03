"""Unit tests for buddy.actions: the action table and the ledger helpers."""

from __future__ import annotations

import pytest

from buddy import planner
from buddy.actions import (
    ACTION_VOCABULARY,
    ACTIONS,
    INTENT_CAPABILITIES,
    UNSUPPORTED,
    find_phrase,
    mentioned_actions,
    requested_ledger,
)
from buddy.brain import INTENT_VOCABULARY
from buddy.engine_factory import _build_registry
from buddy.inspector import _TARGET_COUNTERS
from buddy.models import (
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
        assert action.target in _TARGET_COUNTERS
    assert ACTION_VOCABULARY == {a.name for a in ACTIONS} | {UNSUPPORTED}


def test_the_planner_routes_intents_with_the_same_table() -> None:
    assert planner.INTENT_CAPABILITIES is INTENT_CAPABILITIES
    assert INTENT_CAPABILITIES == {
        "remove_duplicate_rows": "spreadsheet.deduplicate",
        "remove_empty_rows": "spreadsheet.remove_empty_rows",
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
    ],
)
def test_mentioned_actions(message: str, expected: list[tuple[str, str]]) -> None:
    assert [(a.name, text) for a, text in mentioned_actions(message)] == expected


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
