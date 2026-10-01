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


# -- Failure replies (R6) ------------------------------------------------------
#
# Fixed text for a turn that couldn't finish, reviewed like the phrases above.
# No model writes them and they carry no per-request content, so they can't
# make a claim about the work. They say only what code knows for certain:
# nothing can be confirmed, and no file from this turn is handed back. They
# never say the original is unchanged: that is a fact about the file that
# nothing re-checked on this path. ``tests/buddy/test_voice_catalog.py``
# checks every one against ``buddy.voice._COMPLETION_CLAIM``.

# Where in the turn things went wrong, by ``StageFailure.stage``. A closed map:
# a stage not listed here gets FAILURE_STAGE_LABEL_DEFAULT.
FAILURE_STAGE_LABELS: dict[str, str] = {
    "eyes": "while reading your message",
    "brain": "while working out what you asked for",
    "think": "while working out what you asked for",
    "planner": "while planning the steps",
    "delegate": "while planning the steps",
    "hands": "while running the steps",
    "inspector": "while checking the result",
}
FAILURE_STAGE_LABEL_DEFAULT = "while I was working on this"

_FAILURE_REPLY_TEMPLATE = (
    "Something went wrong {where}, so I can't confirm that any change was made. "
    "I haven't handed back any file from this turn."
)

# The pipeline's own failure reply when no stage is known, and the API's
# last-resort reply when the pipeline produced none (Voice raised, a deadline
# passed). Same wording either way.
FALLBACK_PIPELINE_ERROR = _FAILURE_REPLY_TEMPLATE.format(where=FAILURE_STAGE_LABEL_DEFAULT)

# The API's reply when an upload's hash changed during the turn (rule 2's
# runtime check, docs/design/backend-api-design.md Section 5.2). Defined here
# so it is reviewed with the rest; nothing in the pipeline uses it.
FALLBACK_INTEGRITY_FAILURE = (
    "I can't vouch for this result: your original file changed while I was working. "
    "I've held back every file from this turn."
)


def failure_reply(stage: str | None) -> str:
    """The fixed reply for a turn in which ``stage`` raised.

    Args:
        stage: ``StageFailure.stage``, or ``None`` if unknown.

    Returns:
        One of a closed set of texts: the template with the stage's label,
        or ``FALLBACK_PIPELINE_ERROR`` for an unknown stage.
    """
    where = FAILURE_STAGE_LABELS.get(stage or "", FAILURE_STAGE_LABEL_DEFAULT)
    return _FAILURE_REPLY_TEMPLATE.format(where=where)
