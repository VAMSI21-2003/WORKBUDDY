"""Workbook — the single-sheet and plain-table checks the row-cleaning skills run first.

``ExcelDeduplicateSkill`` and ``ExcelRemoveEmptyRowsSkill`` read one sheet
and write one sheet, so a workbook with more than one sheet would come back
with every other sheet gone. Until per-sheet cleaning exists, they refuse
such a workbook instead (``docs/backlog.md`` item 1a).

They also write the sheet's *values* back with pandas, so a sheet that isn't
a plain table (one header row, then the data) would come back changed: a
merged note cell as an "Unnamed" column, a header that isn't on the first row
read as data, a duplicate header renamed. They refuse such a sheet too
(``docs/design/values-only-copy.md``). The check reads the file with openpyxl,
never from a skill's DataFrame, and the Inspector runs the same one on each
step's input.

The sheet list comes from ``sheet_names``, which the Inspector uses too: it
reads the sheets *declared* in the workbook's own ``workbook.xml``, then
cross-checks them with openpyxl. A library's list alone isn't trusted,
because openpyxl silently drops a declared sheet whose XML part is missing.
Every message in a ``WorkbookRefused`` is fixed text: it reaches the user's
reply, so it never carries an exception's text or a name read from the file.
"""

import logging
import zipfile
from dataclasses import dataclass
from pathlib import Path
from xml.etree import ElementTree
from xml.parsers import expat

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

# How much of a sheet's XML is fed to the merged-cell scan at a time.
_SCAN_CHUNK_BYTES = 1 << 20

_UNREADABLE = "could not read the workbook; it may be damaged or not an Excel file"
_NO_SHEET = "the workbook looks damaged: it has no readable sheet"
_SHEETS_UNREADABLE = "the workbook looks damaged: it lists sheets that can't be read"
_PASSWORD = "the workbook is password-protected, so it can't be opened"
_LEGACY_XLS = (
    "older .xls workbooks (Excel 97–2003) aren't supported yet; saving it as .xlsx will work"
)


# What a sheet that isn't a plain table is refused for. Fixed text: it reaches the
# user's reply, so it never carries a name or a value read from the file.
_MERGED_CELLS = "the sheet has merged cells"
_HEADER_NOT_FIRST = "the sheet's header isn't on the first row"
_COLUMN_WITHOUT_HEADER = "a column in the sheet has no header"
_DUPLICATE_HEADER = "two columns in the sheet have the same header"
_PLAIN_TABLES_ONLY = (
    ", and only plain tables (one header row at the top, then the data) are supported for now"
)
# A cell pandas reads as missing although it isn't empty: an error value (a formula's
# ``#N/A``, a typed ``#DIV/0!``) or text such as ``NA`` or ``null``. A values-only copy
# would hold an empty cell there, and a row of them would look empty to the skills.
_READS_AS_EMPTY = (
    "a cell holds an error value, or text such as NA or N/A, that would be read as empty, "
    "and keeping such cells isn't supported for now"
)
# The strings ``pandas.read_excel`` reads as missing by default (its ``STR_NA_VALUES``,
# less the empty string, which is an empty cell); a test keeps this in step with pandas.
_PANDAS_NA_TEXT = frozenset(
    (
        "-1.#IND", "-1.#QNAN", "-NaN", "-nan", "1.#IND", "1.#QNAN", "<NA>", "N/A", "NA", "NULL",
        "NaN", "None", "n/a", "nan", "null", "#N/A", "#N/A N/A", "#NA",
    )
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


def plain_table_sheet_name(input_path: str) -> str:
    """Return the name of the workbook's only sheet, if it is a plain table.

    The check the row-cleaning skills run before reading anything: one sheet
    (``single_sheet_name``), and that sheet a plain table (``plain_table_problem``).

    Args:
        input_path: The workbook to check. It is only ever read.

    Returns:
        The name of its one sheet.

    Raises:
        WorkbookRefused: It has more than one sheet, can't be read, or its
            sheet isn't a plain table. The message says which, in plain words.
    """
    name = single_sheet_name(input_path)
    problem = _sheet_problem(input_path, name)
    if problem is not None:
        raise WorkbookRefused(problem)
    return name


def plain_table_problem(input_path: str) -> str | None:
    """Say why a sheet of the workbook isn't a plain table, if one isn't.

    A plain table has its header on row 1 with a header over every column up
    to the last, its data below, no merged cells, and no two equal headers:
    the shape ``pandas.read_excel`` and ``to_excel`` round-trip unchanged. A
    sheet with no values at all isn't a problem (there is nothing to change).
    Every sheet the workbook declares is checked, in order.

    Read with openpyxl (read-only), so the verdict is independent of any
    skill's DataFrame; the cell values are the ones a pandas read sees.

    Args:
        input_path: The workbook to check. It is only ever read.

    Returns:
        ``None`` if every sheet is a plain table; otherwise a fixed sentence
        saying which rule the first offending sheet breaks.

    Raises:
        WorkbookRefused: The workbook can't be read (see ``sheet_names``).
    """
    for name in sheet_names(input_path):
        problem = _sheet_problem(input_path, name)
        if problem is not None:
            return problem
    return None


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


def _sheet_problem(input_path: str, sheet_name: str) -> str | None:
    """The refusal text for a sheet that isn't a plain table, or ``None``."""
    try:
        with open(input_path, "rb") as handle:
            book = openpyxl.load_workbook(handle, read_only=True, data_only=True)
            try:
                sheet = book[sheet_name]
                # A wrong <dimension> would hide cells that pandas (which ignores it) reads.
                sheet.reset_dimensions()
                reason = _MERGED_CELLS if _has_merged_cells(sheet) else _layout_problem(sheet)
            finally:
                book.close()
    except Exception as exc:  # noqa: BLE001 — unreadable or corrupt, reported as a refusal
        logger.exception("could not check the layout of %r", input_path)
        raise WorkbookRefused(_UNREADABLE) from exc
    if reason is None or reason == _READS_AS_EMPTY:
        return reason
    return reason + _PLAIN_TABLES_ONLY


def _is_empty(value: object) -> bool:
    """Whether pandas reads this cell as missing: nothing, or an empty string."""
    return value is None or value == ""


def _layout_problem(sheet) -> str | None:  # noqa: ANN001 — an openpyxl read-only sheet
    """Which plain-table rule the sheet's cells break (merged cells aside), if any."""
    headers: dict[int, object] = {}  # column -> its value on row 1
    data_columns: set[int] = set()  # columns with a value below row 1
    seen_first_row = False
    for row in sheet.iter_rows():
        for cell in row:
            if _is_empty(cell.value):
                continue
            if cell.data_type == "e" or (
                cell.row != 1 and isinstance(cell.value, str) and cell.value in _PANDAS_NA_TEXT
            ):
                return _READS_AS_EMPTY
            if not seen_first_row:
                if cell.row != 1:
                    return _HEADER_NOT_FIRST  # nothing on row 1, something below it
                seen_first_row = True
            if cell.row == 1:
                headers[cell.column] = cell.value
            else:
                data_columns.add(cell.column)
    if not headers:
        return None  # an empty sheet: nothing to change
    last_header = max(headers)
    if len(headers) != last_header or any(column > last_header for column in data_columns):
        return _COLUMN_WITHOUT_HEADER
    if len(set(headers.values())) != len(headers):
        return _DUPLICATE_HEADER
    return None


class _MergedCellFound(Exception):
    """Raised inside the XML scan to stop at the first ``<mergeCell>``."""


def _has_merged_cells(sheet) -> bool:  # noqa: ANN001 — an openpyxl read-only sheet
    """Whether the sheet's XML declares a merged range.

    openpyxl's read-only mode doesn't expose merged cells, so the sheet's own
    part (opened through openpyxl) is scanned for ``<mergeCell>`` with a
    streaming parser that builds no tree, so a big sheet costs no memory.
    """
    parser = expat.ParserCreate(namespace_separator=" ")

    def on_start(name: str, attributes: dict[str, str]) -> None:
        if name.rpartition(" ")[2] == "mergeCell":
            raise _MergedCellFound

    parser.StartElementHandler = on_start
    with sheet._get_source() as source:
        try:
            while chunk := source.read(_SCAN_CHUNK_BYTES):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
        except _MergedCellFound:
            return True
    return False


@dataclass(frozen=True)
class FormulaCounts:
    """The formula cells of a workbook, as two fresh reads of it count them.

    Attributes:
        formulas: Cells that hold a formula.
        without_value: Of those, the cells with no saved result (never
            calculated, or an empty one): a values-only copy has an empty
            cell there.
    """

    formulas: int
    without_value: int


def formula_cell_counts(input_path: str) -> FormulaCounts:
    """Count the formula cells of every sheet, and those with no saved value.

    Two read-only openpyxl passes over the same file, in step: one showing
    formulas, one showing the values the file saved for them (what a pandas
    read, and so a values-only copy, gets).

    Args:
        input_path: The workbook to read. It is only ever read.

    Returns:
        The counts.

    Raises:
        WorkbookRefused: The workbook can't be read (see ``sheet_names``).
    """
    names = sheet_names(input_path)
    formulas = without_value = 0
    try:
        with open(input_path, "rb") as shown_handle, open(input_path, "rb") as saved_handle:
            shown_book = openpyxl.load_workbook(shown_handle, read_only=True, data_only=False)
            saved_book = openpyxl.load_workbook(saved_handle, read_only=True, data_only=True)
            try:
                for name in names:
                    shown_sheet, saved_sheet = shown_book[name], saved_book[name]
                    # A wrong <dimension> would hide cells that pandas (which ignores it) reads.
                    shown_sheet.reset_dimensions()
                    saved_sheet.reset_dimensions()
                    for shown_row, saved_row in zip(
                        shown_sheet.iter_rows(), saved_sheet.iter_rows(), strict=True
                    ):
                        for shown, saved in zip(shown_row, saved_row, strict=True):
                            if shown.data_type == "f":
                                formulas += 1
                                without_value += _is_empty(saved.value)
            finally:
                shown_book.close()
                saved_book.close()
    except Exception as exc:  # noqa: BLE001 — unreadable or corrupt, reported as a refusal
        logger.exception("could not count the formulas of %r", input_path)
        raise WorkbookRefused(_UNREADABLE) from exc
    return FormulaCounts(formulas=formulas, without_value=without_value)


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
