"""Unit tests for buddy.voice_catalog: the reviewed phrases and their selector."""

from __future__ import annotations

import re
from dataclasses import replace

import pytest

from buddy.models import (
    DUPLICATE_ROWS_TARGET,
    EMPTY_ROWS_TARGET,
    LEDGER_FROM_BRAIN,
    LEDGER_FROM_INTENT,
    LEDGER_FROM_MENTION,
    StepMeasurement,
)
from buddy.outcome import (
    DONE,
    DONE_UNREQUESTED,
    NO_SKILL,
    NOT_DONE,
    NOT_PLANNED,
    NOT_READ_AS_REQUEST,
    ActionOutcome,
    OutcomeReport,
)
from buddy.voice import _COMPLETION_CLAIM
from buddy.voice_catalog import CATALOG, MODES, SLOTS, eligible_phrases, select_phrases

_DEDUPE = "spreadsheet.deduplicate"
_EMPTY = "spreadsheet.remove_empty_rows"
_BRAIN, _INTENT, _MENTION = LEDGER_FROM_BRAIN, LEDGER_FROM_INTENT, LEDGER_FROM_MENTION


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


# -- Entries whose truth depends on what was requested (PR-B ledger) -------------------

_REQUEST_DEPENDENT = {
    "friend.opener.nothing_to_remove",
    "friend.closer.anything_else",
    "friend.closer.whats_next",
    "focus.closer.anything_else",
}


def _ledger_done(capability: str, removed: int | None, source: str = _BRAIN) -> ActionOutcome:
    return replace(_done(capability, removed), source=source)


def _not_done(capability: str, reason: str, source: str = _BRAIN) -> ActionOutcome:
    return ActionOutcome(capability, NOT_DONE, reason=reason, source=source)


# Reports where the request is NOT complete, though every step that ran passed.
_INCOMPLETE = {
    # PR-A review's C1 variant: the planner dropped empty-row removal; dedupe found nothing.
    "C1-nothing-to-dedupe": _report(
        _ledger_done(_DEDUPE, 0), _not_done(_EMPTY, NOT_PLANNED)
    ),
    "C1": _report(_ledger_done(_DEDUPE, 2), _not_done(_EMPTY, NOT_PLANNED)),
    # C3 / N1: only the mention detector has the unrun action.
    "C3": _report(
        _ledger_done(_DEDUPE, 2), _not_done(_EMPTY, NOT_READ_AS_REQUEST, _MENTION)
    ),
    "N1": _report(
        _ledger_done(_EMPTY, 0), _not_done(_DEDUPE, NOT_READ_AS_REQUEST, _MENTION)
    ),
    # N5: unrequested work ran, the requested action didn't.
    "N5": _report(
        ActionOutcome(
            _DEDUPE, DONE_UNREQUESTED, StepMeasurement(DUPLICATE_ROWS_TARGET, 4, 0, removed=0)
        ),
        _not_done(_EMPTY, NOT_PLANNED),
    ),
    "unrequested-extra": _report(
        _ledger_done(_EMPTY, 0),
        ActionOutcome(
            _DEDUPE, DONE_UNREQUESTED, StepMeasurement(DUPLICATE_ROWS_TARGET, 4, 0, removed=0)
        ),
    ),
    "dedupe-and-sort": _report(_ledger_done(_DEDUPE, 0), _not_done("", NO_SKILL)),
    # No reading of the request at all (PR-A's only case): nothing is confirmed.
    "no-reading": _report(_done(_DEDUPE, 0), _done(_EMPTY, 0)),
}

# Reports where every requested action is done and nothing else ran.
_COMPLETE = {
    "single": _report(_ledger_done(_DEDUPE, 2)),
    "single-nothing": _report(_ledger_done(_DEDUPE, 0, _INTENT)),
    "chain": _report(_ledger_done(_EMPTY, 1), _ledger_done(_DEDUPE, 2, _MENTION)),
    "chain-nothing": _report(_ledger_done(_EMPTY, 0), _ledger_done(_DEDUPE, 0)),
    "no-count": _report(_ledger_done(_DEDUPE, None)),
}


@pytest.mark.parametrize("name", sorted(_INCOMPLETE))
def test_no_request_dependent_entry_is_eligible_unless_the_request_is_complete(name: str) -> None:
    """Review M1 and PR-A's dropped "nothing to remove": these entries presuppose
    that every requested action is done, so a report with anything NOT_DONE,
    anything DONE_UNREQUESTED, or no ledger at all never makes them eligible."""
    report = _INCOMPLETE[name]
    assert not report.request_complete

    assert not _REQUEST_DEPENDENT & _eligible_ids(report)
    for mode in MODES:
        assert [p.text for p in eligible_phrases(report, mode, "closer")] == [""]


@pytest.mark.parametrize("name", sorted(_COMPLETE))
def test_closers_come_back_when_the_request_is_complete(name: str) -> None:
    report = _COMPLETE[name]
    assert report.request_complete

    closers = _eligible_ids(report) & _REQUEST_DEPENDENT - {"friend.opener.nothing_to_remove"}
    assert closers == {
        "friend.closer.anything_else",
        "friend.closer.whats_next",
        "focus.closer.anything_else",
    }
    # ... but never as a second question after the analysis findings' own.
    with_findings = replace(report, open_findings=1)
    for mode in MODES:
        assert [p.text for p in eligible_phrases(with_findings, mode, "closer")] == [""]


def test_nothing_to_remove_needs_every_requested_action_done_with_a_confirmed_zero() -> None:
    phrase = "friend.opener.nothing_to_remove"
    assert phrase in _eligible_ids(_COMPLETE["single-nothing"])
    assert phrase in _eligible_ids(_COMPLETE["chain-nothing"])
    for report in (
        _COMPLETE["single"],  # something was removed
        _COMPLETE["chain"],
        _COMPLETE["no-count"],  # the count isn't confirmed
        _report(_ledger_done(_EMPTY, 0), _ledger_done(_DEDUPE, 2)),  # one step removed rows
    ):
        assert phrase not in _eligible_ids(report)


def test_partial_opener_follows_the_partly_done_lead_word() -> None:
    """Decision D2: an action only the mention detector found (C3, N1) never
    makes the reply "partly done"."""
    phrase = "friend.opener.partial"
    for name, report in {**_INCOMPLETE, **_COMPLETE}.items():
        expected = report.lead_word == "Partly done"
        assert (phrase in _eligible_ids(report)) is expected, name
    assert phrase in _eligible_ids(_INCOMPLETE["C1"])
    assert phrase in _eligible_ids(_INCOMPLETE["dedupe-and-sort"])
    assert phrase not in _eligible_ids(_INCOMPLETE["C3"])
    assert phrase not in _eligible_ids(_INCOMPLETE["N1"])
    assert phrase not in _eligible_ids(_INCOMPLETE["N5"])


# What each entry's text claims, stated independently of its predicate: an
# entry may be eligible only when its claim holds for what ran AND what was
# requested. Entries that claim nothing map to None.
def _all_requested_done(report: OutcomeReport) -> bool:
    ledger = [a for a in report.actions if a.source]
    return bool(ledger) and len(ledger) == len(report.actions) and all(
        a.status == DONE for a in ledger
    )


_CLAIMS = {
    "friend.opener.neutral": None,
    "friend.opener.rundown": None,
    "friend.opener.seeing_double": lambda r: any(
        a.capability == _DEDUPE and a.status == DONE and (a.measurement.removed_data or 0) >= 1
        for a in r.actions
        if a.measurement is not None
    ),
    "friend.opener.blank_rows": lambda r: any(
        a.capability == _EMPTY and a.status == DONE and (a.measurement.removed or 0) >= 1
        for a in r.actions
        if a.measurement is not None
    ),
    "friend.opener.nothing_to_remove": lambda r: _all_requested_done(r)
    and all(a.measurement is not None and a.measurement.removed == 0 for a in r.actions),
    "friend.opener.partial": lambda r: any(
        a.status == DONE and a.source for a in r.actions
    )
    and any(a.status == NOT_DONE and a.source in (_BRAIN, _INTENT) for a in r.actions),
    "friend.closer.none": None,
    "friend.closer.anything_else": _all_requested_done,
    "friend.closer.whats_next": _all_requested_done,
    "focus.opener.none": None,
    "focus.closer.none": None,
    "focus.closer.anything_else": _all_requested_done,
}


def test_every_entry_is_eligible_only_when_its_text_is_true() -> None:
    assert set(_CLAIMS) == {p.id for p in CATALOG}  # a new entry needs a stated claim
    reports = (*_REPORTS, *_INCOMPLETE.values(), *_COMPLETE.values())
    for phrase in CATALOG:
        claim = _CLAIMS[phrase.id]
        for report in reports:
            if claim is not None and phrase.when(report):
                assert claim(report), (phrase.id, report)


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
