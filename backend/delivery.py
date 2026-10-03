"""Delivery — the one function that decides which files a turn hands back.

Design Section 5.3, and items 1 and 3 of "What a PR-2 author must do" in
``docs/reviews/pipeline-failure-and-output-dir-review.md``. Nothing else in
the API ever turns a path into a response: the turn record stores what this
function returns, and listing and download read only that.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from datetime import datetime
from pathlib import Path

from backend.sessions import DeliveredFile
from buddy.state import BuddyState
from buddy.voice import is_verified_delivery

logger = logging.getLogger(__name__)


def delivered_files(
    state: BuddyState, *, turn_dir: Path, turn_id: str, now: datetime
) -> tuple[DeliveredFile, ...]:
    """The files this turn hands back, all or nothing.

    Call it only for a turn whose ``engine.run`` returned normally, whose
    deadline didn't pass and whose integrity check passed; the turn
    procedure never calls it otherwise (a run that raised is discarded
    unread). A file is delivered only if **every** rule holds, checked in
    this order:

    1. ``state.failure is None``. Checked first: a stage may have raised
       after Hands produced a real, even verified, file.
    2. ``execution_result.outputs`` names it: the pipeline's own list of
       what it hands back. Never ``state.outputs`` or a step's outputs,
       which include intermediate files.
    3. ``verification_result`` exists and passed.
    4. The reply says a verified file was handed back
       (``buddy.voice.is_verified_delivery``), so the files and the reply
       can't disagree.
    5. Its path is a regular, non-empty file directly inside the turn's
       folder, not a symlink (``check_turn_file``).

    If any output fails rule 5, nothing is delivered.

    Returns:
        The delivered files, with ids ``d_{turn_id}_{n}`` and the SHA-256
        each had at this moment. Empty if nothing is delivered.
    """
    if state.failure is not None:
        return ()
    execution = state.execution_result
    if execution is None or not execution.outputs:
        return ()
    verification = state.verification_result
    if verification is None or verification.passed is not True:
        return ()
    if not is_verified_delivery(state):
        return ()
    files: list[DeliveredFile] = []
    for index, output in enumerate(execution.outputs, start=1):
        checked = check_turn_file(Path(output), turn_dir)
        if checked is None:
            logger.error("Turn %s: an output failed the delivery path check", turn_id)
            return ()
        path, data = checked
        files.append(
            DeliveredFile(
                file_id=f"d_{turn_id}_{index}",
                name=path.name,
                size=len(data),
                sha256=hashlib.sha256(data).hexdigest(),
                path=path,
                turn_id=turn_id,
                delivered_at=now,
            )
        )
    return tuple(files)


def check_turn_file(path: Path, turn_dir: Path) -> tuple[Path, bytes] | None:
    """``(path, its bytes)`` if ``path`` may be handed back from ``turn_dir``, else ``None``.

    It must be absolute, sit directly in ``turn_dir`` (by name, and after
    resolving), not be a symlink (nor may ``turn_dir``), and be a regular,
    non-empty file. The bytes returned are the bytes checked, so a caller
    that sends them sends exactly what was checked.
    """
    try:
        if not path.is_absolute():
            return None
        if os.path.normpath(path.parent) != os.path.normpath(turn_dir):
            return None
        if turn_dir.is_symlink() or path.is_symlink():
            return None
        if path.resolve(strict=True).parent != turn_dir.resolve(strict=True):
            return None
        if not stat.S_ISREG(path.lstat().st_mode):
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if not data:
        return None
    return path, data


def make_read_only(files: tuple[DeliveredFile, ...]) -> None:
    """Mark delivered files ``0o444``: a mitigation only (root ignores it)."""
    for delivered in files:
        try:
            os.chmod(delivered.path, 0o444)
        except OSError:
            logger.warning("Couldn't mark a delivered file read-only (turn %s)", delivered.turn_id)


def uploads_unchanged(expected: dict[str, tuple[Path, str]]) -> bool:
    """Rule 2's runtime check: every upload still has its upload-time SHA-256.

    Design Section 5.2, item 3. A missing, replaced (symlink) or unreadable
    upload counts as changed.
    """
    for file_id, (path, sha256) in expected.items():
        try:
            if path.is_symlink() or not path.is_file():
                logger.critical("Upload %s is missing or replaced after a turn", file_id)
                return False
            if hashlib.sha256(path.read_bytes()).hexdigest() != sha256:
                logger.critical("Upload %s changed during a turn", file_id)
                return False
        except OSError:
            logger.critical("Upload %s couldn't be read after a turn", file_id)
            return False
    return True
