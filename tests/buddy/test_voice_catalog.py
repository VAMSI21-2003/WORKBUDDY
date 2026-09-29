"""Unit tests for buddy.voice_catalog: the reviewed phrases and their selector."""

from __future__ import annotations

import re

import pytest

from buddy.models import DUPLICATE_ROWS_TARGET, EMPTY_ROWS_TARGET, StepMeasurement
from buddy.outcome import DONE, ActionOutcome, OutcomeReport
from buddy.voice import _COMPLETION_CLAIM
from buddy.voice_catalog import CATALOG, MODES, SLOTS, eligible_phrases, select_phrases

_DEDUPE = "spreadsheet.deduplicate"
_EMPTY = "spreadsheet.remove_empty_rows"


def _report(*actions: ActionOutcome, open_findings: int = 0) -> OutcomeReport:
    return OutcomeReport(actions=actions, open_findings=open_findings)


def _done(capability: str, removed: int | None) -> ActionOutcome:
    target = DUPLICATE_ROWS_TARGET if capability == _DEDUPE else EMPTY_ROWS_TARGET
    measurement = None
    if removed is not None:
        measurement = StepMeasurement(target, output_rows=3, remaining=0, removed=removed)
    return ActionOutcome(capability, DONE, measurement)


# A spread of reports every predicate is exercised against.
_REPORTS = (
    _report(),
    _report(_done(_DEDUPE, 2)),
    _report(_done(_DEDUPE, 0)),
    _report(_done(_DEDUPE, None)),
    _report(_done(_EMPTY, 1)),
    _report(_done(_EMPTY, 0)),
    _report(_done(_EMPTY, 1), _done(_DEDUPE, 2)),
    _report(_done(_EMPTY, 0), _done(_DEDUPE, 0)),
    _report(_done(_DEDUPE, 2), open_findings=2),
)


# -- The catalog itself ------------------------------------------------------------------


def test_ids_are_unique_and_match_mode_and_slot() -> None:
    ids = [p.id for p in CATALOG]
    assert len(ids) == len(set(ids))
    for phrase in CATALOG:
        mode, slot, name = phrase.id.split(".")
        assert (mode, slot) == (phrase.mode, phrase.slot)
        assert phrase.mode in MODES and phrase.slot in SLOTS and name


def test_no_entry_claims_completion_carries_a_number_or_a_placeholder() -> None:
    """The outcome block's lead word is the only "Done"; counts only ever
    come from the outcome block."""
    for phrase in CATALOG:
        assert _COMPLETION_CLAIM.search(phrase.text) is None, phrase.id
        assert re.search(r"[\d{}]", phrase.text) is None, phrase.id


def test_every_mode_and_slot_always_has_an_eligible_entry() -> None:
    for mode in MODES:
        for slot in SLOTS:
            for report in _REPORTS:
                assert eligible_phrases(report, mode, slot), (mode, slot, report)


def test_focus_mode_never_has_an_opener() -> None:
    assert {p.text for p in CATALOG if p.mode == "focus" and p.slot == "opener"} == {""}


# -- Each factual entry is eligible only when the report makes it true -------------------


def _eligible_ids(report: OutcomeReport) -> set[str]:
    return {p.id for mode in MODES for slot in SLOTS for p in eligible_phrases(report, mode, slot)}


def test_seeing_double_needs_a_done_dedupe_that_removed_rows() -> None:
    phrase = "friend.opener.seeing_double"
    assert phrase in _eligible_ids(_report(_done(_DEDUPE, 2)))
    for report in (
        _report(),
        _report(_done(_DEDUPE, 0)),
        _report(_done(_DEDUPE, None)),
        _report(_done(_EMPTY, 3)),
    ):
        assert phrase not in _eligible_ids(report)


def test_blank_rows_needs_a_done_empty_row_removal_that_removed_rows() -> None:
    phrase = "friend.opener.blank_rows"
    assert phrase in _eligible_ids(_report(_done(_EMPTY, 1)))
    for report in (
        _report(),
        _report(_done(_EMPTY, 0)),
        _report(_done(_EMPTY, None)),
        _report(_done(_DEDUPE, 2)),
    ):
        assert phrase not in _eligible_ids(report)


def test_no_entry_claims_there_was_nothing_to_remove() -> None:
    """A catalog opener can't know every requested action ran: PR-A has no
    ledger of what was requested, so "nothing to remove" could be false (the
    planner may have dropped one of the actions). The outcome block states
    per-step zeros; whole-turn lines wait for the PR-B ledger."""
    assert "friend.opener.nothing_to_remove" not in {p.id for p in CATALOG}
    assert not [p.id for p in CATALOG if "nothing to remove" in p.text.lower()]


def test_no_closer_implies_the_request_is_finished() -> None:
    """M1: "Anything else?" / "What's next on the list?" presuppose that the
    request is done, and PR-A has no ledger of what was requested. Closers come
    back only when the ledger confirms every requested action is DONE (PR-B)."""
    removed = {
        "friend.closer.anything_else",
        "friend.closer.whats_next",
        "focus.closer.anything_else",
    }
    assert not removed & {p.id for p in CATALOG}
    assert [p.text for p in CATALOG if p.slot == "closer"] == ["", ""]
    for report in _REPORTS:
        for mode in MODES:
            assert [p.text for p in eligible_phrases(report, mode, "closer")] == [""]


def test_seeing_double_does_not_fire_for_repeated_blank_rows_alone() -> None:
    """H1: dedupe removed 2 rows, but both were repeated blank rows, so the
    user's data had no twins."""
    only_blanks = ActionOutcome(
        _DEDUPE,
        DONE,
        StepMeasurement(
            DUPLICATE_ROWS_TARGET,
            output_rows=3,
            remaining=0,
            input_rows=5,
            removed=2,
            removed_blank=2,
        ),
    )
    some_data = ActionOutcome(
        _DEDUPE,
        DONE,
        StepMeasurement(
            DUPLICATE_ROWS_TARGET,
            output_rows=3,
            remaining=0,
            input_rows=5,
            removed=2,
            removed_blank=1,
        ),
    )

    assert "friend.opener.seeing_double" not in _eligible_ids(_report(only_blanks))
    assert "friend.opener.seeing_double" in _eligible_ids(_report(some_data))


# -- The selector ------------------------------------------------------------------------


@pytest.mark.parametrize("mode", MODES)
def test_selection_is_deterministic_and_always_eligible(mode: str) -> None:
    for report in _REPORTS:
        for turn in range(20):
            first = select_phrases(report, mode, seed=f"session-1:{turn}")
            assert first == select_phrases(report, mode, seed=f"session-1:{turn}")
            opener, closer = first
            assert opener in eligible_phrases(report, mode, "opener")
            assert closer in eligible_phrases(report, mode, "closer")


def test_selection_varies_across_turns_and_sessions() -> None:
    report = _report(_done(_DEDUPE, 2))
    chosen = {
        select_phrases(report, "friend", seed=f"session-{s}:{t}")
        for s in range(5)
        for t in range(5)
    }

    assert len({opener.id for opener, _ in chosen}) > 1


def test_an_unknown_mode_is_treated_as_friend() -> None:
    report = _report(_done(_DEDUPE, 2))

    assert select_phrases(report, "chatty", "s:0") == select_phrases(report, "friend", "s:0")
