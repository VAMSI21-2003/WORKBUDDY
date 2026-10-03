"""Uploads — sanitized names, a streamed size cap, and structural checks.

Design Sections 6.2 and 6.3, "Decisions (2026-10-01)" item 6, and the owner
decision that ``.xls`` is out of scope ("Decisions (2026-10-04)").

The API never parses an upload's content (no ``openpyxl``, no ``pandas``):
parsing untrusted content is the skill's job, inside a turn, where a failure
becomes an honest reply. What runs here reads only the first bytes and the
ZIP central directory. An accepted upload is written once, made read-only,
and never opened for writing again (rule 2). A refused upload leaves nothing
behind.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from backend.errors import XLS_MESSAGE, ApiError, ErrorCode, file_too_large_message
from backend.settings import Settings

logger = logging.getLogger(__name__)

CHUNK_BYTES = 1024 * 1024
MAX_NAME_CHARS = 100
XLS_EXTENSION = ".xls"

_ZIP_MAGIC = b"PK\x03\x04"
_CONTENT_TYPES = "[content_types].xml"
# OPC part names are case-insensitive, so these match case-insensitively:
# a workbook can't slip a second sheet past the count by changing case.
_WORKSHEET = re.compile(r"^xl/worksheets/[^/]+\.xml$", re.IGNORECASE)
_OTHER_SHEET = re.compile(r"^xl/(chartsheets|dialogsheets|macrosheets)/[^/]+\.xml$", re.IGNORECASE)

_ALLOWED_NAME_CHARS = re.compile(r"[^A-Za-z0-9._ -]")
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{i}" for i in range(1, 10)}
    | {f"LPT{i}" for i in range(1, 10)}
)


def sanitize_name(raw: str) -> str | None:
    """The name an upload is stored and shown under, or ``None`` if nothing usable is left.

    Design Section 6.3: the last path component (splitting on ``/`` and
    ``\\``), NFKC-normalized, without control or format characters (which
    include the bidirectional overrides), with anything outside
    ``[A-Za-z0-9._ -]`` replaced by ``_``, without leading dots or spaces
    (or trailing ones, which Windows drops), not a Windows device name, and
    at most 100 characters with the extension kept.
    """
    last = re.split(r"[/\\]", raw)[-1]
    text = unicodedata.normalize("NFKC", last)
    text = "".join(ch for ch in text if unicodedata.category(ch) not in ("Cc", "Cf"))
    text = _ALLOWED_NAME_CHARS.sub("_", text)
    text = text.lstrip(". ").rstrip(". ")
    if not text:
        return None
    if text.split(".")[0].strip().upper() in _WINDOWS_DEVICE_NAMES:
        text = "_" + text
    if len(text) > MAX_NAME_CHARS:
        stem, ext = os.path.splitext(text)
        text = stem[: MAX_NAME_CHARS - len(ext)] + ext if len(ext) <= 10 else text[:MAX_NAME_CHARS]
    return text


def check_extension(name: str | None, settings: Settings) -> None:
    """Refuse a name whose extension isn't accepted (``415``).

    Raises:
        ApiError: ``unsupported_file_type``, with the ``.xls`` message for
            an ``.xls`` name.
    """
    extension = os.path.splitext(name)[1].lower() if name else ""
    if extension == XLS_EXTENSION:
        raise ApiError(ErrorCode.UNSUPPORTED_FILE_TYPE, message=XLS_MESSAGE)
    if extension not in settings.accepted_extensions:
        raise ApiError(ErrorCode.UNSUPPORTED_FILE_TYPE)


@dataclass(frozen=True)
class StoredUpload:
    """An accepted upload, written and made read-only."""

    name: str
    size: int
    sha256: str
    path: Path


def store_upload(source: BinaryIO, folder: Path, name: str, settings: Settings) -> StoredUpload:
    """Copy ``source`` into ``folder/name``, checking it on the way.

    ``folder`` must not exist yet (it is the new upload's own folder). The
    copy is streamed in chunks and hashed as it goes, and stops as soon as
    it passes the size cap. On any refusal or error, the folder and
    everything in it are removed.

    Raises:
        ApiError: ``file_too_large``, ``invalid_file`` or ``multiple_sheets``.
    """
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    partial = folder / (".partial-" + name)
    try:
        hasher = hashlib.sha256()
        size = 0
        with open(partial, "xb") as out:
            while chunk := source.read(CHUNK_BYTES):
                size += len(chunk)
                if size > settings.max_upload_bytes:
                    raise ApiError(
                        ErrorCode.FILE_TOO_LARGE,
                        message=file_too_large_message(settings.max_upload_bytes),
                    )
                hasher.update(chunk)
                out.write(chunk)
        if size == 0:
            raise ApiError(ErrorCode.INVALID_FILE)
        check_workbook(partial, settings)
        final = folder / name
        os.replace(partial, final)
        os.chmod(final, 0o444)
        os.chmod(folder, 0o555)
        return StoredUpload(name=name, size=size, sha256=hasher.hexdigest(), path=final)
    except BaseException:
        discard_folder(folder)
        raise


def discard_folder(folder: Path) -> None:
    """Remove an upload's folder and, if that leaves it empty, the session's uploads folder."""
    try:
        os.chmod(folder, 0o700)
    except OSError:
        pass
    shutil.rmtree(folder, ignore_errors=True)
    try:
        folder.parent.rmdir()
    except OSError:
        pass  # not empty (other uploads), or already gone


def check_workbook(path: Path, settings: Settings) -> None:
    """The structural check: a real ``.xlsx`` with exactly one sheet, read from the directory only.

    - It starts with the ZIP magic, and its central directory reads.
    - It has ``[Content_Types].xml``.
    - At most ``max_zip_entries`` entries, whose declared uncompressed
      sizes sum to at most ``max_zip_uncompressed_bytes`` (zip-bomb guard).
    - Exactly one worksheet and no other sheet ("Decisions (2026-10-01)",
      item 6). Every directory entry counts, repeated names included, so
      the check fails closed.

    Nothing is decompressed.

    Raises:
        ApiError: ``invalid_file`` or ``multiple_sheets``.
    """
    with open(path, "rb") as handle:
        if handle.read(len(_ZIP_MAGIC)) != _ZIP_MAGIC:
            raise ApiError(ErrorCode.INVALID_FILE)
    try:
        with zipfile.ZipFile(path) as archive:
            entries = archive.infolist()
    except Exception:  # noqa: BLE001 — any unreadable directory is an invalid file
        logger.info("An upload's ZIP directory couldn't be read")
        raise ApiError(ErrorCode.INVALID_FILE) from None

    if len(entries) > settings.max_zip_entries:
        raise ApiError(ErrorCode.INVALID_FILE)
    if sum(entry.file_size for entry in entries) > settings.max_zip_uncompressed_bytes:
        raise ApiError(ErrorCode.INVALID_FILE)
    names = [entry.filename for entry in entries]
    if not any(n.lower() == _CONTENT_TYPES for n in names):
        raise ApiError(ErrorCode.INVALID_FILE)
    worksheets = sum(1 for n in names if _WORKSHEET.match(n))
    other_sheets = sum(1 for n in names if _OTHER_SHEET.match(n))
    if worksheets + other_sheets > 1:
        raise ApiError(ErrorCode.MULTIPLE_SHEETS)
    if worksheets == 0:
        raise ApiError(ErrorCode.INVALID_FILE)
