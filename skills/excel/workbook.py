"""Workbook — the single-sheet check the row-cleaning skills run first.

``ExcelDeduplicateSkill`` and ``ExcelRemoveEmptyRowsSkill`` read one sheet
and write one sheet, so a workbook with more than one sheet would come back
with every other sheet gone. Until per-sheet cleaning exists, they refuse
such a workbook instead (``docs/backlog.md`` item 1a).

The sheet list comes from ``sheet_names``, which the Inspector uses too: it
reads the sheets *declared* in the workbook's own ``workbook.xml``, then
cross-checks them with openpyxl. A library's list alone isn't trusted,
because openpyxl silently drops a declared sheet whose XML part is missing.
Every message in a ``WorkbookRefused`` is fixed text: it reaches the user's
reply, so it never carries an exception's text or a name read from the file.
"""

import logging
import zipfile
from pathlib import Path
from xml.etree import ElementTree

import openpyxl

logger = logging.getLogger(__name__)

# The first bytes of an .xlsx (a zip archive) and of an OLE2 compound file.
# The format is read from the content, not the extension, the same way
# pandas picks its reader.
_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# Password-protected .xlsx files are OLE2 containers too, holding a stream
# with this name (stored as UTF-16-LE in the container's directory).
_ENCRYPTED_STREAM = "EncryptedPackage".encode("utf-16-le")

# The content types of a workbook's main part: .xlsx and macro-enabled.
_WORKBOOK_CONTENT_TYPES = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml",
    "application/vnd.ms-excel.sheet.macroEnabled.main+xml",
)

# An upper bound on the XML parts read: a real workbook.xml is a few KB.
_MAX_PART_BYTES = 16 * 1024 * 1024

_UNREADABLE = "could not read the workbook; it may be damaged or not an Excel file"
_NO_SHEET = "the workbook looks damaged: it has no readable sheet"
_SHEETS_UNREADABLE = "the workbook looks damaged: it lists sheets that can't be read"
_PASSWORD = "the workbook is password-protected, so it can't be opened"
_LEGACY_XLS = (
    "older .xls workbooks (Excel 97–2003) aren't supported yet; saving it as .xlsx will work"
)


class WorkbookRefused(Exception):
    """The workbook can't be cleaned. ``str(exc)`` is the user-facing reason."""


def single_sheet_name(input_path: str) -> str:
    """Return the name of the workbook's only sheet, opening it read-only.

    Args:
        input_path: The workbook to check. It is only ever read.

    Returns:
        The name of its one sheet.

    Raises:
        WorkbookRefused: It has more than one sheet, or its sheets can't be
            listed. The message says which, in plain words.
    """
    names = sheet_names(input_path)
    if len(names) != 1:
        raise WorkbookRefused(
            f"the workbook has {len(names)} sheets, and only single-sheet workbooks "
            "are supported for now"
        )
    return names[0]


def sheet_names(input_path: str) -> list[str]:
    """Every sheet the workbook declares, worksheets and chart sheets alike, in order.

    Read-only, from the content rather than the extension. For zip content
    (``.xlsx``) the names are the ``<sheet>`` entries of the workbook part,
    and openpyxl must list the same names, or the workbook is refused: a
    declared sheet openpyxl can't load would otherwise be dropped unnoticed.
    OLE2 content (a password-protected ``.xlsx`` or a legacy ``.xls``) is
    refused without being counted.

    Args:
        input_path: The workbook to read. It is only ever read.

    Returns:
        The declared sheet names, at least one.

    Raises:
        WorkbookRefused: The workbook can't be read, is password-protected,
            is a legacy ``.xls``, or looks damaged. The message is fixed text.
    """
    try:
        with open(input_path, "rb") as handle:
            magic = handle.read(8)
            handle.seek(0)
            if magic.startswith(_ZIP_MAGIC):
                return _zip_sheet_names(handle)
            if magic == _OLE2_MAGIC:
                raise WorkbookRefused(_PASSWORD if _has_stream(handle) else _LEGACY_XLS)
    except WorkbookRefused:
        raise
    except Exception as exc:  # noqa: BLE001 — unreadable or corrupt, reported as a refusal
        logger.exception("could not read the sheet names of %r", input_path)
        raise WorkbookRefused(_UNREADABLE) from exc
    logger.info("%r isn't an Excel workbook", input_path)
    raise WorkbookRefused(_UNREADABLE)


def _zip_sheet_names(handle) -> list[str]:  # noqa: ANN001 — a binary file object
    """The declared sheet names of an .xlsx, cross-checked with openpyxl."""
    with zipfile.ZipFile(handle) as archive:
        declared = _declared_sheet_names(archive)
    if not declared:
        raise WorkbookRefused(_NO_SHEET)
    handle.seek(0)
    # A file handle, not the path: openpyxl rejects an .xls name even when the
    # content is a workbook it can read.
    book = openpyxl.load_workbook(handle, read_only=True)
    try:
        loaded = list(book.sheetnames)
    finally:
        book.close()
    if loaded != declared:
        logger.info("declared sheets %r, openpyxl loaded %r", declared, loaded)
        raise WorkbookRefused(_SHEETS_UNREADABLE)
    return declared


def _declared_sheet_names(archive: zipfile.ZipFile) -> list[str]:
    """The ``<sheet name=…>`` entries of the workbook part, in order."""
    content_types = ElementTree.fromstring(_read_part(archive, "[Content_Types].xml"))
    parts = [
        element.get("PartName", "")
        for element in content_types.iter()
        if _local(element.tag) == "Override"
        and element.get("ContentType") in _WORKBOOK_CONTENT_TYPES
    ]
    if len(parts) != 1:
        raise WorkbookRefused(_UNREADABLE)
    workbook = ElementTree.fromstring(_read_part(archive, parts[0].lstrip("/")))
    return [
        sheet.get("name", "")
        for sheets in workbook.iter()
        if _local(sheets.tag) == "sheets"
        for sheet in sheets
        if _local(sheet.tag) == "sheet"
    ]


def _read_part(archive: zipfile.ZipFile, name: str) -> bytes:
    """One part of the archive, refusing an absurdly large one."""
    with archive.open(name) as part:
        data = part.read(_MAX_PART_BYTES + 1)
    if len(data) > _MAX_PART_BYTES:
        raise WorkbookRefused(_UNREADABLE)
    return data


def _local(tag: str) -> str:
    """An XML tag without its ``{namespace}`` prefix."""
    return tag.rpartition("}")[2]


def _has_stream(handle) -> bool:  # noqa: ANN001 — a binary file object
    """Whether the OLE2 file holds the ``EncryptedPackage`` stream's name."""
    overlap = len(_ENCRYPTED_STREAM) - 1
    tail = b""
    while chunk := handle.read(1 << 20):
        if _ENCRYPTED_STREAM in tail + chunk:
            return True
        tail = chunk[-overlap:]
    return False


def discard_partial_output(output_path: Path) -> None:
    """Delete what a failed write left at ``output_path``, if anything.

    The path is always a freshly built one under ``workspace/outputs/``
    (never the upload), and a failed write must not leave a half-written
    file there that could be mistaken for a result.
    """
    try:
        output_path.unlink(missing_ok=True)
    except OSError:
        logger.exception("could not remove the partial output %r", output_path)
