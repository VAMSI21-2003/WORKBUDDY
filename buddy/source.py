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
    if copy_offered and not (choice.pinned or start_over):
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
