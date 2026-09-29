"""Voice catalog — the reviewed openers and closers a delivered reply may use.

On a turn that delivered a verified file, the reply's facts are rendered by
code (``buddy.outcome``). The only personality around them is one opener
and one closer, chosen **by code** from this catalog: no model writes or
picks reply text (``docs/design/verified-outcome-design.md``, decision D1).

Every entry is reviewed together with its eligibility predicate. An entry
that says anything about the file or the work is eligible only when the
``OutcomeReport`` makes it true. Entries carry no placeholders and no
numbers, so a count can only ever come from the outcome block.

Adding an entry: give it a unique id (``<mode>.<slot>.<name>``), keep its
text free of completion claims (``buddy.voice._COMPLETION_CLAIM``; the
outcome block's lead word is the only "Done"), and make its predicate
require whatever its text implies. ``tests/buddy/test_voice_catalog.py``
checks all of this.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from buddy.outcome import DONE, OutcomeReport

OPENER = "opener"
CLOSER = "closer"
MODES = ("friend", "focus")
SLOTS = (OPENER, CLOSER)


@dataclass(frozen=True)
class Phrase:
    """One reviewed catalog entry.

    Attributes:
        id: Unique, ``<mode>.<slot>.<name>``.
        mode: ``"friend"`` or ``"focus"``.
        slot: ``OPENER`` or ``CLOSER``.
        text: The exact text. ``""`` means "no phrase in this slot".
        when: Eligibility, checked by code against the report.
    """

    id: str
    mode: str
    slot: str
    text: str
    when: Callable[[OutcomeReport], bool]


def _always(report: OutcomeReport) -> bool:
    return True


def _removed_some(capability: str) -> Callable[[OutcomeReport], bool]:
    """Eligible when a done ``capability`` step measurably removed at least one row.

    Repeated blank rows don't count: "seeing double" is about the user's data.
    """

    def predicate(report: OutcomeReport) -> bool:
        return any(
            action.capability == capability
            and action.status == DONE
            and action.measurement is not None
            and action.measurement.removed_data is not None
            and action.measurement.removed_data >= 1
            for action in report.actions
        )

    return predicate


CATALOG: tuple[Phrase, ...] = (
    # Friend mode: warm, with a light touch that's specific to what happened.
    Phrase("friend.opener.neutral", "friend", OPENER, "Here's where things landed.", _always),
    Phrase("friend.opener.rundown", "friend", OPENER, "Okay, here's the rundown.", _always),
    Phrase(
        "friend.opener.seeing_double",
        "friend",
        OPENER,
        "That sheet was seeing double.",
        _removed_some("spreadsheet.deduplicate"),
    ),
    Phrase(
        "friend.opener.blank_rows",
        "friend",
        OPENER,
        "Those blank rows were just taking up space.",
        _removed_some("spreadsheet.remove_empty_rows"),
    ),
    Phrase("friend.closer.none", "friend", CLOSER, "", _always),
    # Focus mode: no opener, and at most a two-word closer.
    Phrase("focus.opener.none", "focus", OPENER, "", _always),
    Phrase("focus.closer.none", "focus", CLOSER, "", _always),
)


def eligible_phrases(report: OutcomeReport, mode: str, slot: str) -> tuple[Phrase, ...]:
    """Every catalog entry for ``mode`` and ``slot`` whose predicate holds, sorted by id."""
    return tuple(
        sorted(
            (p for p in CATALOG if p.mode == mode and p.slot == slot and p.when(report)),
            key=lambda p: p.id,
        )
    )


def select_phrases(report: OutcomeReport, mode: str, seed: str) -> tuple[Phrase, Phrase]:
    """Pick one opener and one closer for ``report``, deterministically.

    The choice among eligible entries is a stable hash of ``seed`` (the
    session id and turn index), so the same turn always gets the same
    reply, while different sessions and turns get some variety. Python's
    built-in ``hash`` is salted per process, so ``hashlib`` is used.

    Args:
        report: The report the phrases must be true of.
        mode: ``"friend"`` or ``"focus"``; anything else is treated as friend.
        seed: A stable per-turn string.

    Returns:
        ``(opener, closer)``. Either may be an entry with empty text.
    """
    mode = mode if mode in MODES else "friend"
    return (
        _pick(eligible_phrases(report, mode, OPENER), f"{seed}|{mode}|{OPENER}"),
        _pick(eligible_phrases(report, mode, CLOSER), f"{seed}|{mode}|{CLOSER}"),
    )


def _pick(candidates: tuple[Phrase, ...], key: str) -> Phrase:
    # Every (mode, slot) has an always-eligible entry, so this is never empty.
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return candidates[int.from_bytes(digest[:8], "big") % len(candidates)]
