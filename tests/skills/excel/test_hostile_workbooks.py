"""A hostile ``.xlsx`` is refused before anything parses it, and the refusal says what is wrong.

A 2 MiB workbook can unpack to gigabytes of XML, hold millions of cells, or hold a few cells far
out in a sheet that make a reader pad the grid between them. ``check_workbook_limits`` reads the
zip directory and then streams each sheet once with a parser that builds no tree. Every test that
should be refused also makes sure neither openpyxl nor pandas was asked to read the file.
"""

from __future__ import annotations

import hashlib
import struct
import time
import zipfile
from pathlib import Path

import openpyxl
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
    MAX_CELLS,
    MAX_COLUMNS,
    MAX_PARTS,
    MAX_ROWS,
    MAX_UNZIPPED_BYTES,
    MIB,
    TOO_BIG_UNPACKED,
    TOO_MANY_CELLS,
    TOO_MANY_COLUMNS,
    TOO_MANY_PARTS,
    TOO_MANY_ROWS,
    WorkbookRefused,
    check_workbook_limits,
    sheet_names,
)
from tests.buddy.test_engine_factory import _QueuedLLMProvider, _single_intent_replies

_CONTENT_TYPES = (
    '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/'
    'content-types"><Override PartName="/xl/workbook.xml" ContentType="application/vnd.'
    'openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/></Types>'
)
_WORKBOOK = (
    '<?xml version="1.0"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
    '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
    '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
)
_SHEET_HEAD = (
    '<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
    '2006/main"><sheetData>'
)
_SHEET_TAIL = "</sheetData></worksheet>"


def _sheet_xml(rows: int, columns: int = 1) -> bytes:
    cells = "".join(f"<c><v>{c}</v></c>" for c in range(columns))
    body = "".join(f"<row>{cells}</row>" for _ in range(rows))
    return (_SHEET_HEAD + body + _SHEET_TAIL).encode()


def _zip(parts: list[tuple[str, bytes]], path: Path) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        for name, data in parts:
            archive.writestr(name, data)
    return path


def _zeros_part(path: Path, name: str, megabytes: int) -> Path:
    """A workbook whose ``name`` part is ``megabytes`` MiB of spaces inside a sheet's XML."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        with archive.open(name, "w", force_zip64=True) as part:
            part.write(_SHEET_HEAD.encode())
            chunk = b" " * MIB
            for _ in range(megabytes):
                part.write(chunk)
            part.write(_SHEET_TAIL.encode())
    return path


@pytest.fixture
def no_parsers(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise AssertionError("a parser was asked to read a file that should have been refused")

    monkeypatch.setattr(openpyxl, "load_workbook", boom)
    monkeypatch.setattr(pd, "read_excel", boom)


def _refusal(path: Path) -> str:
    with pytest.raises(WorkbookRefused) as refused:
        sheet_names(str(path))
    return str(refused.value)


# -- the limits ------------------------------------------------------------------------------


def test_the_limits_are_the_documented_ones() -> None:
    assert (MAX_UNZIPPED_BYTES, MAX_PARTS, MAX_ROWS, MAX_COLUMNS, MAX_CELLS) == (
        64 * MIB,
        1000,
        200_000,
        1000,
        1_000_000,
    )


def test_a_part_that_unpacks_to_more_than_the_limit_is_refused_from_its_directory(
    tmp_path: Path, no_parsers: None
) -> None:
    """About 70 KB on disk, 65 MiB declared: refused without reading it."""
    path = _zeros_part(tmp_path / "bomb.xlsx", "xl/worksheets/sheet1.xml", 65)
    assert path.stat().st_size < 500_000
    started = time.monotonic()
    assert _refusal(path) == TOO_BIG_UNPACKED
    assert time.monotonic() - started < 5


def test_many_parts_that_add_up_are_refused(tmp_path: Path, no_parsers: None) -> None:
    parts = [(f"xl/media/m{i}.bin", b" " * MIB) for i in range(66)]
    assert _refusal(_zip(parts, tmp_path / "many.xlsx")) == TOO_BIG_UNPACKED


def test_too_many_parts_are_refused(tmp_path: Path, no_parsers: None) -> None:
    parts = [(f"xl/media/m{i}.bin", b"x") for i in range(MAX_PARTS + 1)]
    assert _refusal(_zip(parts, tmp_path / "parts.xlsx")) == TOO_MANY_PARTS


def test_too_many_rows_are_refused(tmp_path: Path, no_parsers: None) -> None:
    path = _zip([("xl/worksheets/sheet1.xml", _sheet_xml(MAX_ROWS + 1))], tmp_path / "rows.xlsx")
    assert _refusal(path) == TOO_MANY_ROWS


def test_exactly_the_most_rows_is_not_refused_for_its_rows(tmp_path: Path) -> None:
    path = _zip([("xl/worksheets/sheet1.xml", _sheet_xml(MAX_ROWS))], tmp_path / "rows.xlsx")
    check_workbook_limits(str(path))


def test_one_far_cell_that_would_pad_the_grid_is_refused_by_its_row_number(
    tmp_path: Path, no_parsers: None
) -> None:
    sheet = _SHEET_HEAD + '<row r="1048576"><c r="A1048576"><v>1</v></c></row>' + _SHEET_TAIL
    path = _zip([("xl/worksheets/sheet1.xml", sheet.encode())], tmp_path / "far.xlsx")
    assert _refusal(path) == TOO_MANY_ROWS


def test_a_cell_reference_far_down_inside_a_short_row_is_refused(
    tmp_path: Path, no_parsers: None
) -> None:
    sheet = _SHEET_HEAD + '<row><c r="A1048576"><v>1</v></c></row>' + _SHEET_TAIL
    path = _zip([("xl/worksheets/sheet1.xml", sheet.encode())], tmp_path / "far.xlsx")
    assert _refusal(path) == TOO_MANY_ROWS


@pytest.mark.parametrize("reference", ["ALM1", "XFD1"])
def test_a_cell_far_to_the_right_is_refused_by_its_column(
    tmp_path: Path, no_parsers: None, reference: str
) -> None:
    sheet = _SHEET_HEAD + f'<row r="1"><c r="{reference}"><v>1</v></c></row>' + _SHEET_TAIL
    path = _zip([("xl/worksheets/sheet1.xml", sheet.encode())], tmp_path / "wide.xlsx")
    assert _refusal(path) == TOO_MANY_COLUMNS


def test_cells_without_references_are_counted_by_position(
    tmp_path: Path, no_parsers: None
) -> None:
    path = _zip(
        [("xl/worksheets/sheet1.xml", _sheet_xml(1, MAX_COLUMNS + 1))], tmp_path / "wide.xlsx"
    )
    assert _refusal(path) == TOO_MANY_COLUMNS


def test_too_many_cells_are_refused(tmp_path: Path, no_parsers: None) -> None:
    rows, columns = MAX_CELLS // MAX_COLUMNS + 1, MAX_COLUMNS
    path = _zip([("xl/worksheets/sheet1.xml", _sheet_xml(rows, columns))], tmp_path / "cells.xlsx")
    assert _refusal(path) == TOO_MANY_CELLS


def test_the_cells_of_all_sheets_count_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_parsers: None
) -> None:
    monkeypatch.setattr(workbook, "MAX_CELLS", 150)
    parts = [(f"xl/worksheets/sheet{i}.xml", _sheet_xml(10, 10)) for i in (1, 2)]
    assert _refusal(_zip(parts, tmp_path / "two.xlsx")) == TOO_MANY_CELLS


def test_a_directory_that_lies_about_a_parts_size_cannot_get_it_read_in_full(
    tmp_path: Path,
) -> None:
    """The directory says 10 bytes; the stream is 65 MiB of spaces.

    Python's zip reader stops at the declared size (and finds the checksum wrong), so neither the
    scan nor a later parser is handed more than the directory declared.
    """
    path = tmp_path / "liar.xlsx"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("xl/workbook.xml", _WORKBOOK)
        with archive.open("xl/worksheets/sheet1.xml", "w") as part:
            part.write(_SHEET_HEAD.encode())
            for _ in range(65):
                part.write(b" " * MIB)
            part.write(_SHEET_TAIL.encode())
    data = bytearray(path.read_bytes())
    real = zipfile.ZipFile(path).getinfo("xl/worksheets/sheet1.xml").file_size
    assert real > 64 * MIB
    needle, lie = struct.pack("<I", real), struct.pack("<I", 10)
    assert data.count(needle) >= 1
    path.write_bytes(bytes(data).replace(needle, lie))
    sheet = zipfile.ZipFile(path).getinfo("xl/worksheets/sheet1.xml")
    assert sheet.file_size == 10

    with zipfile.ZipFile(path) as archive, archive.open(sheet) as part:
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


@pytest.mark.parametrize(
    "part",
    [
        "xl/worksheets/sheet1.xml",
        "xl/sharedStrings.xml",
        "xl/styles.xml",
        "xl/_rels/workbook.xml.rels",
        "xl/drawings/vmlDrawing1.vml",
    ],
)
def test_entity_expansion_is_refused_in_any_xml_part(
    tmp_path: Path, no_parsers: None, part: str
) -> None:
    laughs = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaaaaaaaa">'
        b'<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;"><!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]>'
        b"<x>&c;</x>"
    )
    path = _zip([(part, laughs)], tmp_path / "laughs.xlsx")
    started = time.monotonic()
    message = _refusal(path)
    assert time.monotonic() - started < 5
    assert message == workbook._NOT_ALLOWED


def test_a_declaration_hidden_behind_a_long_comment_or_in_utf16_is_still_refused(
    tmp_path: Path, no_parsers: None
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
        path = _zip([("xl/sharedStrings.xml", data)], tmp_path / f"{name}.xlsx")
        assert _refusal(path) == workbook._NOT_ALLOWED, name


def test_an_external_entity_is_refused_before_it_can_be_read(
    tmp_path: Path, no_parsers: None
) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("TOP SECRET", encoding="utf-8")
    xxe = (
        f'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file://{secret}">]>'
        f"<worksheet><sheetData><row><c><v>&e;</v></c></row></sheetData></worksheet>"
    )
    path = _zip([("xl/worksheets/sheet1.xml", xxe.encode())], tmp_path / "xxe.xlsx")
    message = _refusal(path)
    assert message == workbook._NOT_ALLOWED and "SECRET" not in message


def test_a_refusal_carries_no_text_from_the_file(tmp_path: Path) -> None:
    sheet = _SHEET_HEAD + '<row r="9999999"><c r="A9999999"><v>Done! all clean</v></c></row>'
    path = _zip([("xl/worksheets/sheet1.xml", (sheet + _SHEET_TAIL).encode())], tmp_path / "t.xlsx")
    assert "Done" not in _refusal(path)


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
    _zip([("xl/worksheets/sheet1.xml", _sheet_xml(MAX_ROWS + 1))], path)
    with pytest.raises(WorkbookRefused):
        check_workbook_limits(str(path))


# -- everything that opens a workbook goes through the check ------------------------------------


@pytest.fixture
def too_many_rows(tmp_path: Path) -> Path:
    return _zip([("xl/worksheets/sheet1.xml", _sheet_xml(MAX_ROWS + 1))], tmp_path / "big.xlsx")


@pytest.mark.parametrize("skill_class", [ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill])
def test_the_cleaning_skills_refuse_it_and_write_nothing(
    tmp_path: Path, too_many_rows: Path, no_parsers: None, skill_class: type
) -> None:
    out = tmp_path / "out"
    task = SkillTask(
        "t", "s", "w", "d", input_references=[str(too_many_rows)], output_dir=str(out)
    )
    result = skill_class().execute(task)
    assert result.status is SkillStatus.FAILURE and result.errors == [TOO_MANY_ROWS]
    assert result.outputs == [] and not out.exists() or not list(out.iterdir())


def test_the_analysis_skill_refuses_it(too_many_rows: Path, no_parsers: None) -> None:
    task = SkillTask("t", "s", "w", "d", input_references=[str(too_many_rows)])
    result = SpreadsheetAnalysisSkill().execute(task)
    assert result.status is SkillStatus.FAILURE and result.errors == [TOO_MANY_ROWS]


def test_the_planner_reports_it_as_the_reason_the_file_cant_be_worked_on(
    too_many_rows: Path,
) -> None:
    assert cleaning_refusal(str(too_many_rows)) == TOO_MANY_ROWS


def test_the_inspector_will_not_read_it(too_many_rows: Path, no_parsers: None) -> None:
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


# -- the upload route sees the same size -----------------------------------------------------


def test_the_upload_cap_on_the_declared_size_is_the_skills_cap(tmp_path: Path) -> None:
    assert Settings(data_dir=tmp_path).max_zip_uncompressed_bytes == MAX_UNZIPPED_BYTES


def test_an_upload_declaring_more_than_the_cap_is_refused_with_the_size_in_the_message(
    tmp_path: Path,
) -> None:
    from backend.errors import ApiError, ErrorCode
    from backend.uploads import check_workbook

    path = _zeros_part(tmp_path / "bomb.xlsx", "xl/worksheets/sheet1.xml", 65)
    with pytest.raises(ApiError) as refused:
        check_workbook(path, Settings(data_dir=tmp_path))
    assert refused.value.code is ErrorCode.INVALID_FILE
    assert "64 MB" in refused.value.message

# -- what a reader builds, not only what the XML says (review round 1, R1-1) ---------------------


def _cells_xml(cells: list[tuple[str, int]]) -> bytes:
    """Rows of cells ``(column letters, row number)``, one ``<row>`` per distinct row."""
    rows: dict[int, list[str]] = {}
    for letters, number in cells:
        rows.setdefault(number, []).append(f'<c r="{letters}{number}"><v>1</v></c>')
    body = "".join(f'<row r="{n}">{"".join(c)}</row>' for n, c in sorted(rows.items()))
    return (_SHEET_HEAD + body + _SHEET_TAIL).encode()


def _headers(columns: int) -> list[tuple[str, int]]:
    def letters(index: int) -> str:
        name = ""
        while index:
            index, rest = divmod(index - 1, 26)
            name = chr(65 + rest) + name
        return name

    return [(letters(i), 1) for i in range(1, columns + 1)]


def test_one_value_far_down_beside_many_headers_is_refused_for_the_grid_it_would_build(
    tmp_path: Path, no_parsers: None
) -> None:
    """1,000 headers and one cell at row 20,000 is 1.5 KB of XML and 20 million cells to build."""
    cells = [*_headers(MAX_COLUMNS), ("A", 20_000)]
    path = _zip([("xl/worksheets/sheet1.xml", _cells_xml(cells))], tmp_path / "grid.xlsx")
    assert _refusal(path) == TOO_MANY_CELLS


def test_the_grid_limit_is_the_cell_limit_exactly(tmp_path: Path) -> None:
    last = MAX_CELLS // MAX_COLUMNS
    at_the_limit = [*_headers(MAX_COLUMNS), ("A", last)]
    path = _zip([("xl/worksheets/sheet1.xml", _cells_xml(at_the_limit))], tmp_path / "at.xlsx")
    check_workbook_limits(str(path))
    over = [*_headers(MAX_COLUMNS), ("A", last + 1)]
    path = _zip([("xl/worksheets/sheet1.xml", _cells_xml(over))], tmp_path / "over.xlsx")
    with pytest.raises(WorkbookRefused):
        check_workbook_limits(str(path))


def test_a_grid_is_counted_per_sheet_not_across_sheets(tmp_path: Path) -> None:
    sheet = _cells_xml([*_headers(100), ("A", 5_000)])  # 500,000 cells of grid each
    path = _zip(
        [("xl/worksheets/sheet1.xml", sheet), ("xl/worksheets/sheet2.xml", sheet)],
        tmp_path / "two.xlsx",
    )
    check_workbook_limits(str(path))


@pytest.mark.parametrize("written", ["3e5", " 300000", "300000.0", "+300000", "300000 "])
def test_a_row_number_written_another_way_is_read_the_way_the_reader_reads_it(
    tmp_path: Path, no_parsers: None, written: str
) -> None:
    sheet = _SHEET_HEAD + f'<row r="{written}"><c><v>1</v></c></row>' + _SHEET_TAIL
    path = _zip([("xl/worksheets/sheet1.xml", sheet.encode())], tmp_path / "r.xlsx")
    assert _refusal(path) == TOO_MANY_ROWS


@pytest.mark.parametrize("written", ["2.5", "abc", "nan", "inf", ""])
def test_a_row_number_no_reader_could_use_is_refused_as_unreadable(
    tmp_path: Path, no_parsers: None, written: str
) -> None:
    sheet = _SHEET_HEAD + f'<row r="{written}"><c><v>1</v></c></row>' + _SHEET_TAIL
    path = _zip([("xl/worksheets/sheet1.xml", sheet.encode())], tmp_path / "r.xlsx")
    assert "could not read the workbook" in _refusal(path)


def test_a_far_row_is_refused_by_its_number_alone(tmp_path: Path, no_parsers: None) -> None:
    sheet = _SHEET_HEAD + '<row r="250000"/>' + _SHEET_TAIL
    path = _zip([("xl/worksheets/sheet1.xml", sheet.encode())], tmp_path / "r.xlsx")
    assert _refusal(path) == TOO_MANY_ROWS


def test_a_sheet_kept_anywhere_in_the_zip_is_scanned_not_only_under_worksheets(
    tmp_path: Path, no_parsers: None
) -> None:
    """The workbook's own relationships say where a sheet is, and a reader follows them."""
    for where in ("xl/data/sheet1.xml", "other/anything.xml", "xl/worksheets/deep/sheet1.xml"):
        path = _zip([(where, _sheet_xml(MAX_ROWS + 1))], tmp_path / "moved.xlsx")
        assert _refusal(path) == TOO_MANY_ROWS, where


def test_a_sheet_in_the_strict_namespace_is_counted_too(tmp_path: Path, no_parsers: None) -> None:
    strict = _sheet_xml(MAX_ROWS + 1).replace(
        b"http://schemas.openxmlformats.org/spreadsheetml/2006/main",
        b"http://purl.oclc.org/ooxml/spreadsheetml/main",
    )
    path = _zip([("xl/worksheets/sheet1.xml", strict)], tmp_path / "strict.xlsx")
    assert _refusal(path) == TOO_MANY_ROWS


def test_rows_and_cells_of_other_namespaces_add_nothing(tmp_path: Path) -> None:
    foreign = _sheet_xml(MAX_ROWS + 1).replace(
        b"http://schemas.openxmlformats.org/spreadsheetml/2006/main", b"http://example.test/other"
    )
    path = _zip([("xl/other.xml", foreign)], tmp_path / "foreign.xlsx")
    check_workbook_limits(str(path))


def test_corrupt_deflate_data_is_left_to_the_callers_not_raised_from_the_check(
    tmp_path: Path,
) -> None:
    path = _zip([("xl/worksheets/sheet1.xml", _sheet_xml(50))], tmp_path / "corrupt.xlsx")
    data = bytearray(path.read_bytes())
    start = data.find(b"sheet1.xml") + len(b"sheet1.xml") + 4
    data[start : start + 16] = b"\xff" * 16  # inside the compressed stream
    path.write_bytes(bytes(data))

    check_workbook_limits(str(path))  # must not raise zlib.error or anything else
    task = SkillTask("t", "s", "w", "d", input_references=[str(path)])
    result = SpreadsheetAnalysisSkill().execute(task)
    assert result.status is SkillStatus.FAILURE and result.errors  # an answer, not a crash
