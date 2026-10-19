"""A hostile ``.xlsx`` is refused before its table is built, and the refusal says what is wrong.

A 2 MiB workbook can unpack to gigabytes of XML, hold millions of cells, make a reader pad a grid
of tens of millions of cells out of a handful, or point thousands of cells at one huge string.
``check_workbook_limits`` reads the zip directory, refuses any DOCTYPE or entity, and then walks
the workbook with the reader the table is built with (openpyxl read-only, as pandas uses it), one
row at a time, counting what that read would build. So every file here is a real workbook that
the reader opens, and no test depends on what the XML says about itself. Every test that should
be refused also makes sure pandas was never asked to build the table.
"""

from __future__ import annotations

import hashlib
import struct
import time
import zipfile
from collections.abc import Sequence
from pathlib import Path

import pandas as pd
import pytest

from backend.settings import Settings
from buddy.engine_factory import build_engine
from buddy.inspector import _read_cells
from buddy.planner import cleaning_refusal
from buddy.state import BuddyState
from skills.base import SkillStatus, SkillTask
from skills.excel import (
    ExcelDeduplicateSkill,
    ExcelRemoveEmptyRowsSkill,
    SpreadsheetAnalysisSkill,
    workbook,
)
from skills.excel.workbook import (
    MAX_CELL_CHARS,
    MAX_CELLS,
    MAX_COLUMNS,
    MAX_PARTS,
    MAX_ROWS,
    MAX_TEXT_BYTES,
    MAX_UNZIPPED_BYTES,
    MIB,
    TOO_BIG_UNPACKED,
    TOO_LONG_CELL,
    TOO_MANY_CELLS,
    TOO_MANY_COLUMNS,
    TOO_MANY_PARTS,
    TOO_MANY_ROWS,
    TOO_MUCH_TEXT,
    WorkbookRefused,
    check_workbook_limits,
    sheet_names,
)
from tests.buddy.test_engine_factory import _QueuedLLMProvider, _single_intent_replies

pytestmark = pytest.mark.real_scan_child


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_PACKAGE_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_SHEET_HEAD = f'<?xml version="1.0"?><worksheet xmlns="{_MAIN}"><sheetData>'
_SHEET_TAIL = "</sheetData></worksheet>"
_SHEET1 = "xl/worksheets/sheet1.xml"


def _sheet_xml(rows: int, columns: int = 1) -> bytes:
    cells = "".join(f"<c><v>{c}</v></c>" for c in range(columns))
    body = "".join(f"<row>{cells}</row>" for _ in range(rows))
    return (_SHEET_HEAD + body + _SHEET_TAIL).encode()


def _sheet_of(body: str) -> bytes:
    return (_SHEET_HEAD + body + _SHEET_TAIL).encode()


def _book(
    path: Path,
    sheet: bytes | Sequence[bytes] | None = None,
    *,
    sheet_at: str = _SHEET1,
    before: Sequence[tuple[str, bytes]] = (),
    extra: Sequence[tuple[str, bytes]] = (),
    shared: bytes | None = None,
) -> Path:
    """A real workbook the reader opens: the sheet(s) at ``sheet_at`` (and ``sheet2.xml`` …).

    ``before`` parts are written first in the zip (the reader never opens them), ``extra`` last.
    """
    sheets = [sheet if sheet is not None else _sheet_xml(1)] if not isinstance(
        sheet, (list, tuple)
    ) else list(sheet)
    locations = [sheet_at] + [f"xl/worksheets/sheet{i + 1}.xml" for i in range(1, len(sheets))]
    overrides = "".join(
        f'<Override PartName="/{where}" ContentType="application/vnd.openxmlformats-officedocument'
        f'.spreadsheetml.worksheet+xml"/>'
        for where in locations
    )
    if shared is not None:
        overrides += (
            '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxml'
            'formats-officedocument.spreadsheetml.sharedStrings+xml"/>'
        )
    types = (
        '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
        'content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-'
        'package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
        f'officedocument.spreadsheetml.sheet.main+xml"/>{overrides}</Types>'
    )
    entries = "".join(
        f'<sheet name="Sheet{i + 1}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i in range(len(sheets))
    )
    book = (
        f'<?xml version="1.0"?><workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
        f"<sheets>{entries}</sheets></workbook>"
    )
    links = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{_REL}/worksheet" Target="/{where}"/>'
        for i, where in enumerate(locations)
    )
    if shared is not None:
        links += (
            f'<Relationship Id="rId99" Type="{_REL}/sharedStrings" Target="/xl/sharedStrings.xml"/>'
        )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in before:
            archive.writestr(name, data)
        archive.writestr("[Content_Types].xml", types)
        archive.writestr(
            "_rels/.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{_PACKAGE_REL}">'
            f'<Relationship Id="rId1" Type="{_REL}/officeDocument" Target="/xl/workbook.xml"/>'
            "</Relationships>",
        )
        archive.writestr("xl/workbook.xml", book)
        archive.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<?xml version="1.0"?><Relationships xmlns="{_PACKAGE_REL}">{links}</Relationships>',
        )
        if shared is not None:
            archive.writestr("xl/sharedStrings.xml", shared)
        for where, data in zip(locations, sheets, strict=True):
            archive.writestr(where, data)
        for name, data in extra:
            archive.writestr(name, data)
    return path


def _shared_strings(*strings: str) -> bytes:
    items = "".join(f"<si><t>{s}</t></si>" for s in strings)
    return (
        f'<?xml version="1.0"?><sst xmlns="{_MAIN}" count="{len(strings)}" '
        f'uniqueCount="{len(strings)}">{items}</sst>'
    ).encode()


def _zeros_part(path: Path, megabytes: int) -> Path:
    """A workbook whose sheet is ``megabytes`` MiB of spaces: tiny on disk, huge unpacked."""
    path = _book(path)
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
        with archive.open("xl/media/bomb.bin", "w", force_zip64=True) as part:
            chunk = b" " * MIB
            for _ in range(megabytes):
                part.write(chunk)
    return path


@pytest.fixture
def no_table(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing may build the table (pandas) for a file that should have been refused."""

    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a table was built from a file that should have been refused")

    monkeypatch.setattr(pd, "read_excel", boom)


def _refusal(path: Path) -> str:
    with pytest.raises(WorkbookRefused) as refused:
        sheet_names(str(path))
    return str(refused.value)


# -- the limits --------------------------------------------------------------------------------


def test_the_limits_are_the_documented_ones() -> None:
    assert (MAX_UNZIPPED_BYTES, MAX_PARTS, MAX_ROWS, MAX_COLUMNS, MAX_CELLS) == (
        64 * MIB,
        1000,
        200_000,
        1000,
        1_000_000,
    )
    assert (MAX_CELL_CHARS, MAX_TEXT_BYTES) == (32_767, 4_000_000)


def test_a_workbook_that_unpacks_to_more_than_the_limit_is_refused_from_its_directory(
    tmp_path: Path, no_table: None
) -> None:
    """About 70 KB on disk, 65 MiB declared: refused without reading it."""
    path = _zeros_part(tmp_path / "bomb.xlsx", 65)
    assert path.stat().st_size < 500_000
    started = time.monotonic()
    assert _refusal(path) == TOO_BIG_UNPACKED
    assert time.monotonic() - started < 5


def test_many_parts_that_add_up_are_refused(tmp_path: Path, no_table: None) -> None:
    parts = [(f"xl/media/m{i}.bin", b" " * MIB) for i in range(66)]
    assert _refusal(_book(tmp_path / "many.xlsx", extra=parts)) == TOO_BIG_UNPACKED


def test_too_many_parts_are_refused(tmp_path: Path, no_table: None) -> None:
    parts = [(f"xl/media/m{i}.bin", b"x") for i in range(MAX_PARTS + 1)]
    assert _refusal(_book(tmp_path / "parts.xlsx", extra=parts)) == TOO_MANY_PARTS


def test_too_many_rows_are_refused(tmp_path: Path, no_table: None) -> None:
    path = _book(tmp_path / "rows.xlsx", _sheet_xml(MAX_ROWS + 1))
    assert _refusal(path) == TOO_MANY_ROWS


def test_exactly_the_most_rows_is_not_refused_for_its_rows(tmp_path: Path) -> None:
    check_workbook_limits(str(_book(tmp_path / "rows.xlsx", _sheet_xml(MAX_ROWS))))


@pytest.mark.parametrize(
    "body",
    [
        '<row r="1048576"><c r="A1048576"><v>1</v></c></row>',
        '<row r="250000"/>',
    ],
    ids=["row-and-cell", "row-alone"],
)
def test_a_far_row_pads_the_grid_and_is_refused_by_the_rows_it_would_build(
    tmp_path: Path, no_table: None, body: str
) -> None:
    path = _book(tmp_path / "far.xlsx", _sheet_of(body))
    assert _refusal(path) == TOO_MANY_ROWS


def test_a_cell_reference_far_down_does_not_move_a_row_the_reader_places_by_the_rows_number(
    tmp_path: Path,
) -> None:
    """The reader puts cells at the row's ``r``, so this is one row, as pandas reads it."""
    path = _book(tmp_path / "ref.xlsx", _sheet_of('<row><c r="A1048576"><v>1</v></c></row>'))
    check_workbook_limits(str(path))
    assert len(pd.read_excel(path, header=None)) == 1


@pytest.mark.parametrize("reference", ["ALM1", "XFD1"])
def test_a_cell_far_to_the_right_is_refused_by_its_column(
    tmp_path: Path, no_table: None, reference: str
) -> None:
    path = _book(tmp_path / "wide.xlsx", _sheet_of(f'<row r="1"><c r="{reference}"/></row>'))
    assert _refusal(path) == TOO_MANY_COLUMNS


def test_cells_without_references_are_counted_by_position(
    tmp_path: Path, no_table: None
) -> None:
    path = _book(tmp_path / "wide.xlsx", _sheet_xml(1, MAX_COLUMNS + 1))
    assert _refusal(path) == TOO_MANY_COLUMNS


def test_too_many_cells_are_refused(tmp_path: Path, no_table: None) -> None:
    rows = MAX_CELLS // MAX_COLUMNS + 1
    assert _refusal(_book(tmp_path / "cells.xlsx", _sheet_xml(rows, MAX_COLUMNS))) == TOO_MANY_CELLS


def test_the_grids_of_all_sheets_count_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_table: None
) -> None:
    monkeypatch.setattr(workbook, "MAX_CELLS", 250)
    one = _book(tmp_path / "one.xlsx", [_sheet_xml(10, 10), _sheet_xml(10, 10)])
    check_workbook_limits(str(one))  # 200 together
    three = _book(tmp_path / "three.xlsx", [_sheet_xml(10, 10)] * 3)
    assert _refusal(three) == TOO_MANY_CELLS


def test_a_directory_that_lies_about_a_parts_size_cannot_get_it_read_in_full(
    tmp_path: Path,
) -> None:
    """The directory says 10 bytes; the stream is 65 MiB of spaces.

    Python's zip reader stops at the declared size (and finds the checksum wrong), so neither the
    scan nor a later parser is handed more than the directory declared.
    """
    path = _book(tmp_path / "liar.xlsx")
    with zipfile.ZipFile(path, "a", zipfile.ZIP_DEFLATED) as archive:
        with archive.open("xl/media/liar.bin", "w") as part:
            for _ in range(65):
                part.write(b" " * MIB)
    data = bytearray(path.read_bytes())
    real = zipfile.ZipFile(path).getinfo("xl/media/liar.bin").file_size
    assert real > 64 * MIB
    needle, lie = struct.pack("<I", real), struct.pack("<I", 10)
    assert data.count(needle) >= 1
    path.write_bytes(bytes(data).replace(needle, lie))
    assert zipfile.ZipFile(path).getinfo("xl/media/liar.bin").file_size == 10

    with zipfile.ZipFile(path) as archive, archive.open("xl/media/liar.bin") as part:
        got = b""
        with pytest.raises(zipfile.BadZipFile):
            while chunk := part.read(1024):
                got += chunk
        assert len(got) <= 10
    started = time.monotonic()
    try:
        sheet_names(str(path))
    except WorkbookRefused:
        pass  # refused as unreadable: fine
    assert time.monotonic() - started < 5


_LAUGHS = (
    b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">'
    b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]>'
    b"<x>&c;</x>"
)


@pytest.mark.parametrize(
    "part",
    [
        "xl/styles.xml",
        "xl/_rels/other.rels",
        "xl/drawings/vmlDrawing1.vml",
        "xl/theme/theme1.xml",
        "xl/custom.bin",
        "customXml/item1",
    ],
)
def test_entity_expansion_is_refused_in_any_part_whatever_its_name(
    tmp_path: Path, no_table: None, part: str
) -> None:
    started = time.monotonic()
    message = _refusal(_book(tmp_path / "laughs.xlsx", extra=[(part, _LAUGHS)]))
    assert time.monotonic() - started < 5
    assert message == workbook._NOT_ALLOWED


def test_entity_expansion_is_refused_in_the_sheet_and_the_shared_strings(
    tmp_path: Path, no_table: None
) -> None:
    sheet = _LAUGHS.replace(b"<x>&c;</x>", b"<worksheet><sheetData/></worksheet>")
    assert _refusal(_book(tmp_path / "a.xlsx", sheet)) == workbook._NOT_ALLOWED
    assert _refusal(_book(tmp_path / "b.xlsx", shared=_LAUGHS)) == workbook._NOT_ALLOWED


def test_a_declaration_hidden_behind_a_long_comment_or_in_utf16_is_still_refused(
    tmp_path: Path, no_table: None
) -> None:
    """A byte search of the start of a part would miss both."""
    declaration = '<!DOCTYPE x [<!ENTITY a "aaaa">]>'
    behind_a_comment = (
        '<?xml version="1.0"?><!--' + "x" * 6000 + "-->" + declaration + "<x>&a;</x>"
    ).encode()
    utf16 = ('<?xml version="1.0" encoding="UTF-16"?>' + declaration + "<x>&a;</x>").encode(
        "utf-16"
    )
    for name, data in (("comment", behind_a_comment), ("utf16", utf16)):
        path = _book(tmp_path / f"{name}.xlsx", extra=[("xl/sharedStringsX.xml", data)])
        assert _refusal(path) == workbook._NOT_ALLOWED, name


def test_an_external_entity_is_refused_before_it_can_be_read(
    tmp_path: Path, no_table: None
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    xxe = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]>'
        f"<sst><si><t>&e;</t></si></sst>"
    )
    path = _book(tmp_path / "xxe.xlsx", shared=xxe.encode())
    message = _refusal(path)
    assert message == workbook._NOT_ALLOWED and "SECRET" not in message


def test_a_refusal_carries_no_text_from_the_file(tmp_path: Path) -> None:
    body = (
        '<row r="9999999"><c r="A9999999" t="inlineStr"><is><t>Done! all clean</t></is></c></row>'
    )
    assert "Done" not in _refusal(_book(tmp_path / "t.xlsx", _sheet_of(body)))


# -- what a reader builds, not what the XML says about itself ----------------------------------


def _cells_xml(cells: list[tuple[str, int]], row_numbers: dict[int, int] | None = None) -> bytes:
    """Rows of cells ``(column letters, row number)``; ``row_numbers`` overrides a row's ``r``."""
    rows: dict[int, list[str]] = {}
    for letters, number in cells:
        rows.setdefault(number, []).append(f'<c r="{letters}{number}"><v>1</v></c>')
    body = "".join(
        f'<row r="{(row_numbers or {}).get(n, n)}">{"".join(c)}</row>'
        for n, c in sorted(rows.items())
    )
    return _sheet_of(body)


def _headers(columns: int) -> list[tuple[str, int]]:
    def letters(index: int) -> str:
        name = ""
        while index:
            index, rest = divmod(index - 1, 26)
            name = chr(65 + rest) + name
        return name

    return [(letters(i), 1) for i in range(1, columns + 1)]


def test_one_value_far_down_beside_many_headers_is_refused_for_the_grid_it_would_build(
    tmp_path: Path, no_table: None
) -> None:
    """1,000 headers and one cell at row 20,000 is 1.5 KB of XML and 20 million cells to build."""
    cells = [*_headers(MAX_COLUMNS), ("A", 20_000)]
    assert _refusal(_book(tmp_path / "grid.xlsx", _cells_xml(cells))) == TOO_MANY_CELLS


def test_a_row_number_that_disagrees_with_its_cell_references_is_followed(
    tmp_path: Path, no_table: None
) -> None:
    """The reader puts a row's cells at the row's ``r``; the cell's own ``A1`` doesn't move it."""
    cells = [*_headers(MAX_COLUMNS), ("A", 2)]
    path = _book(tmp_path / "mismatch.xlsx", _cells_xml(cells, row_numbers={2: 20_000}))
    assert _refusal(path) == TOO_MANY_CELLS


def test_the_grid_limit_is_the_cell_limit_exactly(tmp_path: Path) -> None:
    last = MAX_CELLS // MAX_COLUMNS
    at_the_limit = [*_headers(MAX_COLUMNS), ("A", last)]
    check_workbook_limits(str(_book(tmp_path / "at.xlsx", _cells_xml(at_the_limit))))
    over = [*_headers(MAX_COLUMNS), ("A", last + 1)]
    with pytest.raises(WorkbookRefused):
        check_workbook_limits(str(_book(tmp_path / "over.xlsx", _cells_xml(over))))


@pytest.mark.parametrize("written", ["3e5", " 300000", "300000.0", "+300000", "300000 "])
def test_a_row_number_written_another_way_is_read_the_way_the_reader_reads_it(
    tmp_path: Path, no_table: None, written: str
) -> None:
    path = _book(tmp_path / "r.xlsx", _sheet_of(f'<row r="{written}"><c><v>1</v></c></row>'))
    assert _refusal(path) == TOO_MANY_ROWS


@pytest.mark.parametrize("written", ["2.5", "abc", "nan", "inf", ""])
def test_a_row_number_no_reader_can_use_is_left_to_the_reader_to_refuse(
    tmp_path: Path, written: str
) -> None:
    """The table's reader fails on it too, so the check says nothing and the skill refuses."""
    path = _book(tmp_path / "r.xlsx", _sheet_of(f'<row r="{written}"><c><v>1</v></c></row>'))
    check_workbook_limits(str(path))
    result = SpreadsheetAnalysisSkill().execute(
        SkillTask("t", "s", "w", "d", input_references=[str(path)])
    )
    assert result.status is SkillStatus.FAILURE and result.errors


@pytest.mark.parametrize(
    "where",
    [
        "xl/data/sheet1.xml",
        "other/anything.xml",
        "xl/worksheets/deep/sheet1.xml",
        "xl/worksheets/sheet1.bin",
        "xl/worksheets/sheet1",
    ],
)
def test_a_sheet_kept_anywhere_in_the_zip_is_found_the_way_the_reader_finds_it(
    tmp_path: Path, no_table: None, where: str
) -> None:
    """The workbook's own relationships say where a sheet is, whatever the name ends in."""
    path = _book(tmp_path / "moved.xlsx", _sheet_xml(MAX_ROWS + 1), sheet_at=where)
    assert _refusal(path) == TOO_MANY_ROWS, where


@pytest.mark.parametrize(
    "junk",
    [
        ("xl/a.xml", b"<a>"),
        ("xl/drawings/vmlDrawing1.vml", b"<xml><br></xml>"),
        ("xl/b.xml", b'<worksheet><sheetData><row><c r="A' + b"9" * 5000 + b'1"/></row>'),
        ("xl/c.bin", bytes(range(256)) * 40),
    ],
    ids=["malformed-xml", "unclosed-vml", "5000-digit-reference", "binary"],
)
def test_a_junk_part_placed_first_does_not_end_the_check(
    tmp_path: Path, no_table: None, junk: tuple[str, bytes]
) -> None:
    """The reader never opens the junk, so the check mustn't stop at it either."""
    path = _book(tmp_path / "junk.xlsx", _sheet_xml(MAX_ROWS + 1), before=[junk])
    assert _refusal(path) == TOO_MANY_ROWS


def test_a_damaged_part_placed_first_does_not_end_the_check(
    tmp_path: Path, no_table: None
) -> None:
    path = _book(
        tmp_path / "damaged.xlsx",
        _sheet_xml(MAX_ROWS + 1),
        before=[("xl/damaged.xml", b"<root>" + b"<a/>" * 5000 + b"</root>")],
    )
    data = bytearray(path.read_bytes())
    start = data.find(b"xl/damaged.xml") + len(b"xl/damaged.xml") + 4
    data[start : start + 16] = b"\xff" * 16  # inside that part's compressed stream
    path.write_bytes(bytes(data))
    assert _refusal(path) == TOO_MANY_ROWS


def test_rows_and_cells_of_other_namespaces_add_nothing(tmp_path: Path) -> None:
    foreign = _sheet_xml(MAX_ROWS + 1).replace(_MAIN.encode(), b"http://example.test/other")
    check_workbook_limits(str(_book(tmp_path / "foreign.xlsx", extra=[("xl/other.xml", foreign)])))


def test_corrupt_deflate_data_in_the_sheet_is_left_to_the_callers_not_raised(
    tmp_path: Path,
) -> None:
    path = _book(tmp_path / "corrupt.xlsx", _sheet_xml(50))
    data = bytearray(path.read_bytes())
    start = data.find(b"sheet1.xml") + len(b"sheet1.xml") + 4
    data[start : start + 16] = b"\xff" * 16  # inside the compressed stream
    path.write_bytes(bytes(data))

    check_workbook_limits(str(path))  # must not raise zlib.error or anything else
    task = SkillTask("t", "s", "w", "d", input_references=[str(path)])
    result = SpreadsheetAnalysisSkill().execute(task)
    assert result.status is SkillStatus.FAILURE and result.errors  # an answer, not a crash


# -- the text the cells hold -------------------------------------------------------------------


def _cells_pointing_at(index: int, count: int) -> bytes:
    rows = "".join(f'<row><c t="s"><v>{index}</v></c></row>' for _ in range(count))
    return _sheet_of(rows)


def test_one_huge_string_that_a_thousand_cells_point_at_is_refused(
    tmp_path: Path, no_table: None
) -> None:
    """22 KB zipped, and 10 billion characters for the table to build and then test as dates."""
    path = _book(
        tmp_path / "text.xlsx",
        _cells_pointing_at(0, 1000),
        shared=_shared_strings("x" * 10_000_000),
    )
    started = time.monotonic()
    assert _refusal(path) == TOO_LONG_CELL
    assert time.monotonic() - started < 30


def test_a_cell_of_exactly_excels_own_limit_is_not_refused(tmp_path: Path) -> None:
    path = _book(
        tmp_path / "ok.xlsx", _cells_pointing_at(0, 3), shared=_shared_strings("x" * MAX_CELL_CHARS)
    )
    check_workbook_limits(str(path))
    longer = _book(
        tmp_path / "long.xlsx",
        _cells_pointing_at(0, 1),
        shared=_shared_strings("x" * (MAX_CELL_CHARS + 1)),
    )
    with pytest.raises(WorkbookRefused):
        check_workbook_limits(str(longer))


def test_an_inline_string_over_the_limit_is_refused_too(tmp_path: Path, no_table: None) -> None:
    body = f'<row><c t="inlineStr"><is><t>{"y" * (MAX_CELL_CHARS + 1)}</t></is></c></row>'
    assert _refusal(_book(tmp_path / "inline.xlsx", _sheet_of(body))) == TOO_LONG_CELL


def test_all_the_text_in_the_cells_is_capped_together_in_the_bytes_it_is_stored_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_table: None
) -> None:
    """R4-3: CPython stores a character in 1, 2 or 4 bytes by the widest in the cell, so the
    same number of characters is 4 times as much memory in emoji as in ASCII."""
    monkeypatch.setattr(workbook, "MAX_TEXT_BYTES", 5000)
    ascii_text = _book(
        tmp_path / "ascii.xlsx", _cells_pointing_at(0, 100), shared=_shared_strings("z" * 100)
    )
    assert _refusal(ascii_text) == TOO_MUCH_TEXT  # 100 x 100 x 1 byte = 10,000
    fits = _book(
        tmp_path / "fits.xlsx", _cells_pointing_at(0, 40), shared=_shared_strings("z" * 100)
    )
    check_workbook_limits(str(fits))  # 4,000
    for name, char in (("cjk", "中"), ("emoji", "\U0001F600")):
        wide = _book(
            tmp_path / f"{name}.xlsx", _cells_pointing_at(0, 40), shared=_shared_strings(char * 100)
        )
        assert _refusal(wide) == TOO_MUCH_TEXT, name  # 40 characters x 100 x 2 or 4 bytes


def test_no_date_format_is_tried_on_text_too_long_to_be_a_date(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """strptime formats the whole value into its error message once per format, so it is never
    called for a value no listed format could be: asserted by counting calls, not by timing."""
    import buddy.inspector as inspector
    from skills.excel import analyze

    calls: list[str] = []

    class Counting(analyze.datetime):  # type: ignore[misc, name-defined]
        @classmethod
        def strptime(cls, value: str, date_format: str):  # type: ignore[no-untyped-def]
            calls.append(value)
            return super().strptime(value, date_format)

    for module in (inspector, analyze):
        monkeypatch.setattr(module, "datetime", Counting)
        calls.clear()
        assert module._matched_date_format("x" * 5_000_000) is None
        assert calls == []
        assert module._matched_date_format("2024-01-02") == "%Y-%m-%d"
        assert module._matched_date_format("September 30, 2026") == "%B %d, %Y"
        assert calls  # the guard doesn't switch the check off for ordinary values


# -- review round 3: the walk and the parts openpyxl reads whole -------------------------------


def _replaced(path: Path, **parts: bytes | str) -> Path:
    """The workbook with the named parts replaced or added (``workbook`` is xl/workbook.xml)."""
    names = {
        "workbook": "xl/workbook.xml",
        "rels": "xl/_rels/workbook.xml.rels",
        "styles": "xl/styles.xml",
        "sheet": _SHEET1,
    }
    wanted = {names.get(key, key): value for key, value in parts.items()}
    with zipfile.ZipFile(path) as source:
        kept = [(i.filename, source.read(i.filename)) for i in source.infolist()]
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as target:
        for name, data in kept:
            if name not in wanted:
                target.writestr(name, data)
        for name, data in wanted.items():
            target.writestr(name, data)
    return path


def _many_sheets_on_one_part(path: Path, count: int, body: str) -> Path:
    """``count`` sheets declared, every one of them pointing at the single sheet part."""
    _book(path, _sheet_of(body))
    entries = "".join(
        f'<sheet name="S{i}" sheetId="{i + 1}" r:id="rId{i + 1}"/>' for i in range(count)
    )
    links = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{_REL}/worksheet" Target="/{_SHEET1}"/>'
        for i in range(count)
    )
    return _replaced(
        path,
        workbook=(
            f'<?xml version="1.0"?><workbook xmlns="{_MAIN}" xmlns:r="{_REL}">'
            f"<sheets>{entries}</sheets></workbook>"
        ),
        rels=(
            f'<?xml version="1.0"?><Relationships xmlns="{_PACKAGE_REL}">{links}</Relationships>'
        ),
    )


def test_one_part_declared_as_thousands_of_sheets_is_refused_quickly_and_truthfully(
    tmp_path: Path, no_table: None
) -> None:
    """R3-1: the reader pads every sheet to its last row; rows of all sheets count together."""
    path = _many_sheets_on_one_part(tmp_path / "many.xlsx", 2000, '<row r="150000"/>')
    assert path.stat().st_size < 100_000
    started = time.monotonic()
    assert _refusal(path) == TOO_MANY_ROWS
    assert time.monotonic() - started < 10
    # True whether one sheet or many hold the rows: it doesn't claim a single sheet does.
    assert "sheet has" not in TOO_MANY_ROWS and "all its sheets" in TOO_MANY_ROWS


def test_the_rows_of_all_sheets_count_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_table: None
) -> None:
    monkeypatch.setattr(workbook, "MAX_ROWS", 25)
    check_workbook_limits(str(_book(tmp_path / "two.xlsx", [_sheet_xml(10), _sheet_xml(10)])))
    three = _book(tmp_path / "three.xlsx", [_sheet_xml(10)] * 3)
    assert _refusal(three) == TOO_MANY_ROWS


def test_every_openpyxl_load_is_made_without_external_links(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R3-3 and R3-6: pandas loads with ``keep_links=False``, so the walk, the sheet listing, the
    layout check and the formula count do the same; an external link is never parsed."""
    import openpyxl

    real = openpyxl.load_workbook
    seen: list[tuple[str, object]] = []

    def recording(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        seen.append((str(kwargs.get("data_only")), kwargs.get("keep_links")))
        return real(*args, **kwargs)

    monkeypatch.setattr(openpyxl, "load_workbook", recording)
    path = _ordinary(tmp_path / "ordinary.xlsx")
    workbook._scanned.clear()
    workbook.check_workbook_limits(str(path))
    workbook.plain_table_sheet_name(str(path))
    workbook.formula_cell_counts(str(path))

    assert len(seen) >= 5
    assert all(links is False for _, links in seen), seen


def test_a_broken_external_link_does_not_hide_a_grid_bomb_from_the_walk(
    tmp_path: Path, no_table: None
) -> None:
    """R3-6 (M2): an ``<externalReference>`` whose part is missing used to make the walk fail
    quietly, so the check passed and pandas, which ignores links, built the grid."""
    path = _book(tmp_path / "m2.xlsx", _sheet_of('<row r="150000"/>'))
    with zipfile.ZipFile(path) as source:
        rels = source.read("xl/_rels/workbook.xml.rels").decode()
        book = source.read("xl/workbook.xml").decode()
    rels = rels.replace(
        "</Relationships>",
        f'<Relationship Id="rId50" Type="{_REL}/externalLink" Target="externalLinks/missing.xml"/>'
        "</Relationships>",
    )
    book = book.replace(
        "</sheets>", '</sheets><externalReferences><externalReference r:id="rId50"/>'
        "</externalReferences>",
    )
    _replaced(path, workbook=book, rels=rels)
    # 150,000 rows is under the cap alone; two more of the same sheet would not be.
    check_workbook_limits(str(path))
    wide = _book(tmp_path / "m2b.xlsx", _sheet_of('<row r="250000"/>'))
    _replaced(wide, workbook=book, rels=rels)
    assert _refusal(wide) == TOO_MANY_ROWS


def test_a_dimension_that_understates_the_sheet_hides_nothing(
    tmp_path: Path, no_table: None
) -> None:
    """R3-7: without ``reset_dimensions()`` the reader trusts ``<dimension ref="A1:B2"/>`` and
    walks 2 rows of a sheet pandas (which ignores it) reads in full."""
    rows = "<row><c><v>1</v></c></row>" * (MAX_ROWS + 1)
    sheet = (
        f'<?xml version="1.0"?><worksheet xmlns="{_MAIN}"><dimension ref="A1:B2"/>'
        f"<sheetData>{rows}</sheetData></worksheet>"
    ).encode()
    assert _refusal(_book(tmp_path / "dim.xlsx", sheet)) == TOO_MANY_ROWS


# -- an ordinary workbook is not affected ------------------------------------------------------


def _ordinary(path: Path, rows: int = 50) -> Path:
    pd.DataFrame({"name": [f"n{i}" for i in range(rows)], "n": range(rows)}).to_excel(
        path, index=False
    )
    return path


def test_an_ordinary_workbook_passes_and_is_read_as_before(tmp_path: Path) -> None:
    path = _ordinary(tmp_path / "ok.xlsx")
    check_workbook_limits(str(path))
    assert sheet_names(str(path)) == ["Sheet1"]
    assert len(_read_cells(str(path))) == 50


def test_a_file_that_is_not_a_zip_is_left_to_the_callers_own_refusal(tmp_path: Path) -> None:
    path = tmp_path / "notes.xlsx"
    path.write_bytes(b"just some text")
    check_workbook_limits(str(path))  # not this check's business
    assert "could not read" in _refusal(path)


def test_a_missing_file_is_left_to_the_caller(tmp_path: Path) -> None:
    check_workbook_limits(str(tmp_path / "nope.xlsx"))


def test_a_file_rewritten_after_a_scan_is_scanned_again(tmp_path: Path) -> None:
    path = _ordinary(tmp_path / "w.xlsx")
    check_workbook_limits(str(path))
    _book(path, _sheet_xml(MAX_ROWS + 1))
    with pytest.raises(WorkbookRefused):
        check_workbook_limits(str(path))


# -- everything that opens a workbook goes through the check -----------------------------------


@pytest.fixture
def too_many_rows(tmp_path: Path) -> Path:
    return _book(tmp_path / "big.xlsx", _sheet_xml(MAX_ROWS + 1))


@pytest.mark.parametrize("skill_class", [ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill])
def test_the_cleaning_skills_refuse_it_and_write_nothing(
    tmp_path: Path, too_many_rows: Path, no_table: None, skill_class: type
) -> None:
    out = tmp_path / "out"
    task = SkillTask("t", "s", "w", "d", input_references=[str(too_many_rows)], output_dir=str(out))
    result = skill_class().execute(task)
    assert result.status is SkillStatus.FAILURE and result.errors == [TOO_MANY_ROWS]
    assert result.outputs == [] and not out.exists() or not list(out.iterdir())


def test_the_analysis_skill_refuses_it(too_many_rows: Path, no_table: None) -> None:
    task = SkillTask("t", "s", "w", "d", input_references=[str(too_many_rows)])
    result = SpreadsheetAnalysisSkill().execute(task)
    assert result.status is SkillStatus.FAILURE and result.errors == [TOO_MANY_ROWS]


def test_the_planner_reports_it_as_the_reason_the_file_cant_be_worked_on(
    too_many_rows: Path,
) -> None:
    assert cleaning_refusal(str(too_many_rows)) == TOO_MANY_ROWS


def test_the_inspector_will_not_read_it(too_many_rows: Path, no_table: None) -> None:
    with pytest.raises(WorkbookRefused):
        _read_cells(str(too_many_rows))


@pytest.mark.parametrize("speaker_kind", ["template", "friend", "focus"])
@pytest.mark.parametrize(
    ("message", "intent"),
    [
        ("remove the duplicate rows", "remove_duplicate_rows"),
        ("remove the empty rows", "remove_empty_rows"),
    ],
)
def test_a_turn_on_it_hands_nothing_back_and_says_why(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    too_many_rows: Path,
    speaker_kind: str,
    message: str,
    intent: str,
) -> None:
    from buddy.voice import PersonalityVoice

    monkeypatch.chdir(tmp_path)
    original_hash = hashlib.sha256(too_many_rows.read_bytes()).hexdigest()
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = message
    state.context.add_uploaded_file(str(too_many_rows))
    state.context.switch_active_file(str(too_many_rows))
    speaker = None
    if speaker_kind != "template":
        state.context.set_mode(speaker_kind)
        speaker = PersonalityVoice()
    provider = _QueuedLLMProvider(_single_intent_replies(intent))
    build_engine(llm_provider=provider, speaker=speaker).run(state)

    assert state.execution_result is not None and state.execution_result.outputs == ()
    reply = state.reply or ""
    assert TOO_MANY_ROWS in reply, reply
    assert "no file was handed back" in reply or "didn't change" in reply or "unchanged" in reply
    assert hashlib.sha256(too_many_rows.read_bytes()).hexdigest() == original_hash


def test_a_turn_on_a_huge_string_is_refused_with_a_fixed_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    path = _book(
        tmp_path / "text.xlsx", _cells_pointing_at(0, 1000), shared=_shared_strings("x" * 5_000_000)
    )
    state = BuddyState.new(session_id="session-1", workspace_id="workspace-1")
    state.user_message = "remove the duplicate rows"
    state.context.add_uploaded_file(str(path))
    state.context.switch_active_file(str(path))
    provider = _QueuedLLMProvider(_single_intent_replies("remove_duplicate_rows"))
    started = time.monotonic()
    build_engine(llm_provider=provider).run(state)

    assert time.monotonic() - started < 60
    assert state.execution_result is not None and state.execution_result.outputs == ()
    assert TOO_LONG_CELL in (state.reply or "")


# -- the upload route sees the same size -------------------------------------------------------


def test_the_upload_cap_on_the_declared_size_is_the_skills_cap(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path).max_zip_uncompressed_bytes == MAX_UNZIPPED_BYTES


def test_an_upload_declaring_more_than_the_cap_is_refused_with_the_size_in_the_message(
    tmp_path: Path,
) -> None:
    from backend.errors import ApiError, ErrorCode
    from backend.uploads import check_workbook

    path = _zeros_part(tmp_path / "bomb.xlsx", 65)
    with pytest.raises(ApiError) as refused:
        check_workbook(path, Settings(data_dir=tmp_path))
    assert refused.value.code is ErrorCode.INVALID_FILE
    assert "64 MB" in refused.value.message
