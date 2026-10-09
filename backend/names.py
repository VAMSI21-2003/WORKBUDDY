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
from backend.uploads import sanitize_name

# The part of a skill's operation name that reads badly to a person.
_FRIENDLY_OPERATIONS = (("_no_empty_rows", "_cleaned"),)


def download_name(stored_name: str, session_key: str) -> str:
    """The name a person downloads ``stored_name`` as: the internal task suffixes removed.

    Every ``_<session>-task-<n>`` is dropped (a chain's file carries one per step), and
    the empty-row skill's ``no_empty_rows`` is written ``cleaned``. A name that doesn't have
    that shape is returned as it is.
    """
    suffix = re.compile(rf"_{re.escape(session_key)}-task-\d+")
    name = suffix.sub("", stored_name)
    if name == stored_name:
        return stored_name
    for internal, friendly in _FRIENDLY_OPERATIONS:
        name = name.replace(internal, friendly)
    return sanitize_name(name) or stored_name


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
