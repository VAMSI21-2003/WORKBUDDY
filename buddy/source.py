"""Source — which file a turn works on: the latest copy handed back, or the person's upload.

A follow-up request builds on the work already done (owner's decision, ``docs/design/follow-up
-turns.md``): by default a turn works on the latest copy Buddy handed back from the same upload,
so "remove the duplicates", then "now remove the empty rows too", gives one file with both done.
The person starts again from the upload only by asking to, and **Brain** reads that: its
``file_to_use`` field, in a closed vocabulary (``buddy.actions.FILE_VOCABULARY``). Nothing here
reads a word of the message.

A caller that offers more than one file (the backend API) sets ``BuddyState.file_choice``; this
runs right after Brain, picks one, points ``Context.active_file`` at it (every later stage reads
that), and records the choice on ``BuddyState.file_used`` for the reply. It never opens a file.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher

from buddy.actions import ACTIONS_BY_NAME, brain_ledger
from buddy.models import (
    FILE_KIND_COPY,
    FILE_KIND_ORIGINAL,
    FILE_ORIGINAL,
    FileUsed,
    Understanding,
)
from buddy.state import BuddyState


def choose_file(state: BuddyState, understanding: Understanding) -> None:
    """Decide which file this turn works on and record it on ``state``.

    - A newly uploaded file, or a file the person picked (``FileChoice.pinned``), is used as
      itself.
    - Brain's ``file_to_use`` of ``FILE_ORIGINAL`` ("start over", "use my original file") uses the
      upload.
    - So does a request Brain read as an action that reads the upload itself (a table taken out
      of a PDF: ``Action.reads_upload``): the copy handed back from a PDF is a spreadsheet, and
      the request is about the PDF. The reply then says the copy was not used.
    - Otherwise the latest copy handed back from the upload is used, when there is one.

    With no ``FileChoice`` the file is whatever ``Context.active_file`` is, and counts as the
    person's upload.
    """
    choice = state.file_choice
    start_over = understanding.file_to_use == FILE_ORIGINAL
    if choice is None:
        path = state.context.active_file
        state.file_used = (
            FileUsed(path=path, kind=FILE_KIND_ORIGINAL, start_over=start_over) if path else None
        )
        return
    copy_offered = choice.copy is not None
    reads_upload = any(
        ACTIONS_BY_NAME[entry.action].reads_upload
        for entry in brain_ledger(understanding)
        if entry.action in ACTIONS_BY_NAME
    )
    if copy_offered and not (choice.pinned or start_over or reads_upload):
        assert choice.copy is not None
        used = FileUsed(
            path=choice.copy, kind=FILE_KIND_COPY, copy_name=choice.copy_name, start_over=False
        )
    else:
        used = FileUsed(
            path=choice.original,
            kind=FILE_KIND_ORIGINAL,
            copy_skipped=copy_offered or choice.copy_unavailable,
            start_over=start_over,
        )
    state.file_used = used
    state.context.switch_active_file(used.path)


# -- Finding a session's file in a message ----------------------------------------------------
#
# A person names a file by typing it, with slips ("mess-data.xlsx", "messy-data.xslx"). Matching
# what they typed against the names of the session's own files is looking a string up in a list
# Buddy already has; it reads no meaning from the words around it, and it never decides what the
# request is (that is Brain's). Only a token that has the shape of a file name (a spreadsheet's, or
# a PDF's) counts as naming a file at all, so an ordinary word can't match one.

# How alike (0 to 1) what was typed and a file's name must be. "mess-data.xlsx" against
# "messy-data.xlsx" is 0.97; a different word of the same length is far below.
NAME_MATCH_MIN = 0.85
# Two different names this close to each other are not told apart by a slip: ask.
NAME_MATCH_MARGIN = 0.04
# The formats a person can upload (and the spreadsheet ones a slip may look like), and the slips
# in them that still read as one: a PDF is a file of the session like a workbook.
_SPREADSHEET_EXTENSIONS = ("xlsx", "xlsm", "xls", "csv", "pdf")
_EXTENSION_MIN = 0.75
_STRIP = " \t\r\n\"'`“”‘’()[]<>{},;:!?"
# Matching reads at most this much of a message: it needs a file's name, not an essay.
_MAX_MESSAGE_CHARS = 2000
_MAX_WINDOW_WORDS = 8


@dataclass(frozen=True)
class NameMatch:
    """What a message names among a session's files.

    Attributes:
        indices: Positions (in the names given) of the one file the message names. Several only
            when those files have the same name (the same file uploaded twice): the caller
            chooses among them. Empty when nothing was named, or the naming is unclear.
        unclear: The message names a file as close to two different names that a slip can't
            tell them apart. ``close`` holds every one of them; the caller may find they are
            all of one upload.
        close: Positions of the names that were close, when ``unclear``.
        unknown: The message names a file (a stem and an extension like a spreadsheet's or a
            PDF's) and it is none of the names. Whether that matters is the caller's: with one
            upload there is nothing to choose, with several Buddy asks which file is meant.
    """

    indices: tuple[int, ...] = ()
    unclear: bool = False
    close: tuple[int, ...] = ()
    unknown: bool = False


def match_file_name(message: str, names: Sequence[str]) -> NameMatch:
    """Which of ``names`` the ``message`` names, tolerating slips in how it is typed.

    A name matches when some run of the message's words is at least ``NAME_MATCH_MIN`` alike to
    it (case ignored). An exact name beats a merely close one; two different names that are
    equally close, or a spreadsheet file name that matches none, are ``unclear``. A message that
    names no file (a spreadsheet or a PDF) gives an empty, clear result: nothing was named.
    """
    words = (w.strip(_STRIP).rstrip(".") for w in message[:_MAX_MESSAGE_CHARS].split())
    tokens = [w for w in words if w]
    windows = {
        " ".join(tokens[start : start + width]).casefold()
        for width in range(1, _MAX_WINDOW_WORDS + 1)
        for start in range(len(tokens) - width + 1)
    }
    windows = {w for w in windows if "." in w}
    by_name: dict[str, list[int]] = {}
    for index, name in enumerate(names):
        by_name.setdefault(name.casefold(), []).append(index)
    scores = {
        name: max((_alike(window, name) for window in windows), default=0.0) for name in by_name
    }
    close = sorted(
        ((score, name) for name, score in scores.items() if score >= NAME_MATCH_MIN), reverse=True
    )
    if close:
        best, name = close[0]
        # An exact name is told apart from a near one by its being exact; two exact names, or
        # two near ones within the margin, are not told apart.
        floor = best if best == 1.0 else best - NAME_MATCH_MARGIN
        if any(score >= floor for score, _ in close[1:]):
            together = sorted(i for score, n in close if score >= floor for i in by_name[n])
            return NameMatch(unclear=True, close=tuple(together))
        return NameMatch(indices=tuple(by_name[name]))
    return NameMatch(unknown=any(_looks_like_spreadsheet(t) for t in tokens))


def _alike(window: str, name: str) -> float:
    matcher = SequenceMatcher(None, window, name)
    if matcher.real_quick_ratio() < NAME_MATCH_MIN or matcher.quick_ratio() < NAME_MATCH_MIN:
        return 0.0
    return matcher.ratio()


def _looks_like_spreadsheet(token: str) -> bool:
    """Whether ``token`` is shaped like an uploadable file's name: a stem and an extension like a
    spreadsheet's (``.xlsx``, or a slip such as ``.xslx``) or ``.pdf``."""
    stem, dot, extension = token.casefold().rpartition(".")
    if not dot or not stem or len(extension) < 3:
        return False
    return any(
        SequenceMatcher(None, extension, known).ratio() >= _EXTENSION_MIN
        for known in _SPREADSHEET_EXTENSIONS
    )
