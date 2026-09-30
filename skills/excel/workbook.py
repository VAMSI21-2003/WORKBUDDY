"""Workbook — the single-sheet check the row-cleaning skills run first.

``ExcelDeduplicateSkill`` and ``ExcelRemoveEmptyRowsSkill`` read one sheet
and write one sheet, so a workbook with more than one sheet would come back
with every other sheet gone. Until per-sheet cleaning exists, they refuse
such a workbook instead (``docs/backlog.md`` item 1a).
"""

import openpyxl

# The first bytes of an .xlsx (a zip archive) and of a legacy .xls (an OLE2
# compound file). The format is read from the content, not the extension,
# the same way pandas picks its reader.
_ZIP_MAGIC = b"PK\x03\x04"
_OLE2_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


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
            counted. The message says which, in plain words.
    """
    names = _sheet_names(input_path)
    if len(names) != 1:
        raise WorkbookRefused(
            f"the workbook has {len(names)} sheets, and only single-sheet workbooks "
            "are supported for now"
        )
    return names[0]


def _sheet_names(input_path: str) -> list[str]:
    """Every sheet's name, worksheets and chart sheets alike, in order."""
    try:
        with open(input_path, "rb") as handle:
            magic = handle.read(8)
            if magic.startswith(_ZIP_MAGIC):
                # A file handle, not the path: openpyxl rejects an .xls name
                # even when the content is a workbook it can read.
                book = openpyxl.load_workbook(handle, read_only=True)
                try:
                    return list(book.sheetnames)
                finally:
                    book.close()
    except Exception as exc:  # noqa: BLE001 — unreadable or corrupt, reported as a refusal
        raise WorkbookRefused(f"could not read {input_path!r}: {exc}") from exc

    if magic != _OLE2_MAGIC:
        raise WorkbookRefused(f"could not read {input_path!r}: it isn't an Excel workbook")
    try:
        import xlrd
    except ImportError as exc:
        raise WorkbookRefused(
            "the sheets in an .xls workbook can't be counted because the xlrd library "
            "isn't installed"
        ) from exc
    try:
        book = xlrd.open_workbook(input_path, on_demand=True)
    except Exception as exc:  # noqa: BLE001
        raise WorkbookRefused(f"could not read {input_path!r}: {exc}") from exc
    try:
        return list(book.sheet_names())
    finally:
        book.release_resources()
