"""Voice catalog — the reviewed openers and closers a delivered reply may use.

On a turn that delivered a verified file, the reply's facts are rendered by
code (``buddy.outcome``). The only personality around them is one opener
and one closer, chosen **by code** from this catalog: no model writes or
picks reply text (``docs/design/verified-outcome-design.md``, decision D1).

Every entry is reviewed together with its eligibility predicate. An entry
that says anything about the file or the work is eligible only when the
``OutcomeReport`` makes it true, given both what ran and what was
requested (the requested-actions ledger). Entries carry no placeholders and
no numbers, so a count can only ever come from the outcome block.

Two kinds of entry depend on the whole request, not just on what ran:
closers that presuppose the request is finished ("Anything else?", "What's
next on the list?") and the "nothing to remove" opener. They are eligible
only when ``OutcomeReport.request_complete`` holds: every ledger action is
``DONE`` and nothing ran that the ledger doesn't have (review M1 and the
dropped ``nothing_to_remove`` of PR-A).

Adding an entry: give it a unique id (``<mode>.<slot>.<name>``), keep its
text free of completion claims (``buddy.voice._COMPLETION_CLAIM``; the
outcome block's lead word is the only "Done"), and make its predicate
require whatever its text implies. ``tests/buddy/test_voice_catalog.py``
checks all of this.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass

from buddy.models import wants_brief
from buddy.outcome import (  # noqa: F401  (the report sentences are re-exported)
    DONE,
    LEAD_PARTLY_DONE,
    REPORT_ANALYSIS_FAILED_SENTENCE,
    REPORT_NOT_RUN_SENTENCE,
    REPORT_RECHECK_FAILED_SENTENCE,
    OutcomeReport,
)
from buddy.think import CLARIFY_OTHER, CLARIFY_WHICH_ACTION, CLARIFY_WHICH_FILE

# The one personality mode that is brief: no opener, no humor, at most a two-word closer.
BRIEF_MODE = "focus"

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


def _nothing_to_remove(report: OutcomeReport) -> bool:
    """Eligible when every requested action ran, and each measurably removed nothing.

    ``request_complete`` means the ledger has nothing that wasn't done and
    nothing ran that it doesn't have; ``removed == 0`` is the verifier's
    confirmed count (``None`` never qualifies), repeated blank rows included.
    """
    return report.request_complete and all(
        action.measurement is not None and action.measurement.removed == 0
        for action in report.actions
    )


def _request_complete_and_nothing_asked(report: OutcomeReport) -> bool:
    """Eligible when the request is complete and the reply asks nothing else.

    For closers that presuppose the request is finished ("What's next on
    the list?"). ``open_findings == 0`` keeps them from asking a second
    question after "Want me to tackle any of these?".
    """
    return report.request_complete and report.open_findings == 0


def _partly_done(report: OutcomeReport) -> bool:
    """Eligible exactly when the outcome block leads with "Partly done —".

    That is, part of Brain's reading of the request was done and part
    wasn't. An action only the mention detector found never makes it
    eligible (decision D2).
    """
    return report.lead_word == LEAD_PARTLY_DONE


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
    Phrase(
        "friend.opener.nothing_to_remove",
        "friend",
        OPENER,
        "Short one: there was nothing to remove.",
        _nothing_to_remove,
    ),
    Phrase(
        "friend.opener.partial",
        "friend",
        OPENER,
        "Heads-up: only part of this went through.",
        _partly_done,
    ),
    Phrase("friend.closer.none", "friend", CLOSER, "", _always),
    Phrase(
        "friend.closer.anything_else",
        "friend",
        CLOSER,
        "Anything else you want me to look at?",
        _request_complete_and_nothing_asked,
    ),
    Phrase(
        "friend.closer.whats_next",
        "friend",
        CLOSER,
        "What's next on the list?",
        _request_complete_and_nothing_asked,
    ),
    # Focus mode: no opener, and at most a two-word closer.
    Phrase("focus.opener.none", "focus", OPENER, "", _always),
    Phrase("focus.closer.none", "focus", CLOSER, "", _always),
    Phrase(
        "focus.closer.anything_else",
        "focus",
        CLOSER,
        "Anything else?",
        _request_complete_and_nothing_asked,
    ),
)


def eligible_phrases(report: OutcomeReport, mode: str, slot: str) -> tuple[Phrase, ...]:
    """Every catalog entry for ``mode`` and ``slot`` whose predicate holds, sorted by id."""
    return tuple(
        sorted(
            (p for p in CATALOG if p.mode == mode and p.slot == slot and p.when(report)),
            key=lambda p: p.id,
        )
    )


def effective_mode(mode: str | None, user_state: str) -> str | None:
    """The mode a reply is worded in: ``BRIEF_MODE`` for an urgent or frustrated person.

    Tone only. The user's chosen ``mode`` still decides everything when they are calm, and a
    caller with no mode (``TemplateSpeaker``) keeps its plain wording. Every fact, count and
    disclosure in a reply is the same in every mode, so this can change only the opener, the
    closer, the greeting and the question. Nothing here reads a message: ``user_state`` is Brain's
    closed-vocabulary field.
    """
    if mode is not None and wants_brief(user_state):
        return BRIEF_MODE
    return mode


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
    "I can't vouch for this result: your original file no longer matches the one you "
    "uploaded. I've held back every file from this turn."
)

# -- Replies for a turn that changed nothing -------------------------------------------------
#
# What a reply says when no file-changing step ran. Every sentence is fixed text chosen by
# code from what the code knows: a greeting, Think's reason code, whether there is a file, the
# row-cleaning skills' own refusal text, Brain's closed-vocabulary reading. No model writes any
# of it, none carries a number or a name, and none claims work
# (``tests/buddy/test_voice_catalog.py`` checks each against ``buddy.voice._COMPLETION_CLAIM``).
# The one sentence built around data is ``REFUSED_TEMPLATE``: the refusal's own fixed text goes
# in its blank (``skills.excel.workbook.WorkbookRefused``).
#
# ``STYLE_PLAIN`` is ``TemplateSpeaker``; the other two are the personality modes.

STYLE_PLAIN = "plain"
STYLES = (STYLE_PLAIN, *MODES)


def style_for(mode: str | None) -> str:
    """``STYLE_PLAIN`` for ``None``; else the mode, with anything unknown treated as friend."""
    if mode is None:
        return STYLE_PLAIN
    return mode if mode in MODES else "friend"


GREETINGS: dict[str, tuple[str, ...]] = {
    STYLE_PLAIN: ("Hello.",),
    "friend": ("Hi there!", "Hey!", "Hello!"),
    "focus": ("Hi.",),
}

# What Buddy can do. It names each of ``buddy.actions.ACTIONS`` and the analysis; a test fails
# if a skill is added without this line.
CAPABILITIES: dict[str, str] = {
    STYLE_PLAIN: (
        "I can remove exact duplicate rows, remove empty rows, and look a spreadsheet over for "
        "possible problems."
    ),
    "friend": (
        "I can remove exact duplicate rows, remove empty rows, and look a spreadsheet over for "
        "anything that seems off."
    ),
    "focus": (
        "I can remove exact duplicate rows, remove empty rows, and check a spreadsheet for "
        "problems."
    ),
}

# After a greeting: what to do next, by whether a file has been uploaded.
NEXT_STEP_WITH_FILE: dict[str, str] = {
    STYLE_PLAIN: "Tell me what to do with your file.",
    "friend": "What would you like me to do with your file?",
    "focus": "What should I do with the file?",
}
NEXT_STEP_WITHOUT_FILE: dict[str, str] = {
    STYLE_PLAIN: "Upload a spreadsheet and tell me what to do with it.",
    "friend": "Upload a spreadsheet and tell me what you'd like me to do with it.",
    "focus": "Upload a spreadsheet and tell me what to do.",
}

# The question shown when Think asks for clarification, by Think's reason code. The user
# never sees Think's own words.
CLARIFICATION_QUESTIONS: dict[str, dict[str, tuple[str, ...]]] = {
    CLARIFY_WHICH_ACTION: {
        STYLE_PLAIN: ("Do you want me to remove duplicate rows, remove empty rows, or both?",),
        "friend": (
            "Quick question before I touch anything: do you want me to remove duplicate rows, "
            "remove empty rows, or both?",
            "Before I start: should I remove duplicate rows, remove empty rows, or both?",
        ),
        "focus": ("Duplicate rows, empty rows, or both?",),
    },
    CLARIFY_WHICH_FILE: {
        STYLE_PLAIN: ("Which file should I work on?",),
        "friend": (
            "Quick question before I touch anything: which file should I work on?",
            "Before I start: which file do you mean?",
        ),
        "focus": ("Which file?",),
    },
    CLARIFY_OTHER: {
        STYLE_PLAIN: ("What would you like me to do with the file?",),
        "friend": (
            "Quick question before I touch anything: what would you like me to do with the file?",
            "I want to get this right. What would you like me to do with the file?",
        ),
        "focus": ("What should I do with the file?",),
    },
}

NO_FILE_SENTENCE = "I don't have a file to work on yet. Upload a spreadsheet and ask again."
REFUSED_TEMPLATE = "I can't work on this file: {reason}."
NOT_UNDERSTOOD_SENTENCE = "I didn't read that as something I can do."
UNDERSTANDING_FAILED_SENTENCE = "I couldn't work out what you asked for just now. Please try again."

# A requested report that couldn't be produced (backlog item 3). The three reasons the analysis
# can't give it (``REPORT_ANALYSIS_FAILED_SENTENCE``, ``REPORT_NOT_RUN_SENTENCE``,
# ``REPORT_RECHECK_FAILED_SENTENCE``) are rendered with the other not-done actions by
# ``buddy.outcome``; this one is for a chain that stopped, where no file was handed back.
REPORT_NOT_PRODUCED_SENTENCE = (
    "I didn't produce the report you asked for, since no file was handed back."
)
# Leads the findings of a requested report, by which file they describe.
REPORT_LEAD_COPY = "Report on the copy: "
REPORT_LEAD_ORIGINAL = "Report on your original file: "
REPORT_LEAD_USED = "Report on the file I worked on: "
# A question about the file (Brain's ``question_about_file``): the check of the file, as it is now.
FILE_STATE_LEAD_ORIGINAL = "Here's what I see in your original file right now: "
FILE_STATE_LEAD_COPY = "Here's what I see in the latest copy right now: "
FILE_NOT_CHECKED_SENTENCE = "I couldn't check the file just now, so I can't say what's in it."


def pick_text(options: tuple[str, ...], seed: str, slot: str) -> str:
    """One of ``options``, chosen by a stable hash of ``seed``: the same turn, the same text."""
    digest = hashlib.sha256(f"{seed}|{slot}".encode()).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


def greeting_reply(style: str, *, has_file: bool, seed: str) -> str:
    """The reply to a greeting or a question about what Buddy can do: three fixed lines."""
    next_step = (NEXT_STEP_WITH_FILE if has_file else NEXT_STEP_WITHOUT_FILE)[style]
    return " ".join((pick_text(GREETINGS[style], seed, "greeting"), CAPABILITIES[style], next_step))


def clarification_question(reason: str, style: str, seed: str) -> str:
    """The fixed question for Think's ``reason`` (anything unknown asks the general one)."""
    by_style = CLARIFICATION_QUESTIONS.get(reason, CLARIFICATION_QUESTIONS[CLARIFY_OTHER])
    return pick_text(by_style[style], seed, "question")


# Added to a delivered reply: which file the turn worked on. One fixed sentence, chosen by code
# from the turn's ``FileUsed``; it claims nothing about the result. The copy's name is the one the
# person sees (the Files panel and the download).
WORKED_ON_ORIGINAL_TEMPLATE = "I worked on {file}, your original upload."
WORKED_ON_ORIGINAL_SKIPPING_COPY_TEMPLATE = (
    "I worked on {file}, your original upload, not on a copy I handed back earlier."
)
WORKED_ON_COPY_TEMPLATE = (
    "I worked on {file}, the latest copy I handed back, not on your original upload."
)
# Added to a reply when Brain read the message as only the answer to "which file?" and the request
# that was waiting was carried out: it makes a wrong reading visible. Claims nothing about results.
RESUMED_SENTENCE = (
    "I took your last message as the answer to my question and went on with your earlier request."
)
# The reply when a follow-up finds the latest copy already satisfies the request: no new file.
NOTHING_LEFT_TEMPLATE = (
    "The latest copy, {file}, already has no {rows}, so there was nothing to remove and I "
    "handed back no new file."
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
