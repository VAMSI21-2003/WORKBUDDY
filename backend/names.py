"""The names a person sees for the files Buddy hands back.

A skill writes ``report_deduplicated_<session>-task-1.xlsx`` so that two files never share a
path on disk (``skills/excel/*.py``, ``Hands._build_task``). The session id and the task number are
internal, and a person downloading the file should get ``report_deduplicated.xlsx``. These
functions compute that name from the stored one; nothing stored changes (a download's stored name is
still the file's name on disk), so no migration is involved.

Two downloads in one session can come out with the same name (two turns that both
deduplicated). The later one gets `` (2)``, the next `` (3)``, in the order they were delivered,
so the same file always has the same name and none collides.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from backend.sessions import DeliveredFile
from backend.uploads import MAX_NAME_CHARS

# The characters an upload's name may have (``backend.uploads.sanitize_name``); anything else in a
# stored stem (there is none today) is shown as ``_``, so a name can't carry a quote or a newline
# into a header.
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]")

# The operation word each skill puts in the name of the file it writes
# (``skills/excel/*._build_output_path``), and how a person reads it.
_OPERATION_WORDS = {"deduplicated": "deduplicated", "no_empty_rows": "cleaned"}


def download_name(stored_name: str, session_key: str) -> str:
    """The name a person downloads ``stored_name`` as: the internal ids removed.

    A skill's file is ``<stem>_<operation>_<session>-task-<n>.xlsx``, and a chain's file carries
    one ``_<operation>_<session>-task-<n>`` per step. Only those generated parts are changed:
    each loses its id and the empty-row skill's ``no_empty_rows`` is written ``cleaned``. The
    stem, the person's own words, is kept (cut, if it must be, to leave room for the operations,
    so the name still says what was done). A name with ``-task-`` ids in another shape loses them;
    any other name is returned as it is. Characters an upload's name can't have are shown as ``_``.
    """
    key = re.escape(session_key)
    operation = "|".join(map(re.escape, _OPERATION_WORDS))
    step = rf"_(?:{operation})_{key}-task-\d+"
    shape = rf"(?P<stem>.*?)(?P<steps>(?:{step})+)(?P<extension>\.[A-Za-z0-9]+)"
    found = re.fullmatch(shape, stored_name, re.DOTALL)
    if found is not None:
        words = re.findall(rf"_({operation})_{key}-task-\d+", found["steps"])
        tail = "_" + "_".join(_OPERATION_WORDS[word] for word in words) + found["extension"]
        stem = _SAFE_CHARS.sub("_", found["stem"])  # the same alphabet uploads are limited to
        return stem[: max(1, MAX_NAME_CHARS - len(tail))] + tail
    return _SAFE_CHARS.sub("_", re.sub(rf"_{key}-task-\d+", "", stored_name))


def display_names(session_key: str, delivered: Sequence[DeliveredFile]) -> dict[str, str]:
    """``file_id -> name`` for every delivered file of a session, with no two the same.

    Args:
        session_key: The session's key (what the skills put in their file names).
        delivered: The session's delivered files, oldest first
            (``SessionRecord.delivered``).
    """
    taken: dict[str, int] = {}
    names: dict[str, str] = {}
    for file in delivered:
        base = download_name(file.name, session_key)
        taken[base.casefold()] = taken.get(base.casefold(), 0) + 1
        count = taken[base.casefold()]
        if count == 1:
            names[file.file_id] = base
            continue
        stem, dot, extension = base.rpartition(".")
        names[file.file_id] = f"{stem} ({count}).{extension}" if dot else f"{base} ({count})"
    return names
