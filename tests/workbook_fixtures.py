"""Damaged, protected and non-plain workbooks for the sheet-list and plain-table tests."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Font, PatternFill

_OLE2_HEADER = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 504
_WORKBOOK_CONTENT_TYPE = (
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"
)


def rewrite(source: Path, target: Path, edits: dict[str, bytes | None]) -> Path:
    """Copy the zip ``source`` to ``target``, replacing (or, for ``None``, removing) parts."""
    with zipfile.ZipFile(source) as original, zipfile.ZipFile(target, "w") as copy:
        for item in original.infolist():
            if item.filename in edits:
                if edits[item.filename] is not None:
                    copy.writestr(item.filename, edits[item.filename])
            else:
                copy.writestr(item, original.read(item.filename))
    return target


def without_sheet_part(source: Path, target: Path, part: str = "xl/worksheets/sheet2.xml") -> Path:
    """``workbook.xml`` still declares the sheet; its XML part is gone (openpyxl drops it)."""
    return rewrite(source, target, {part: None})


def without_declared_sheets(source: Path, target: Path) -> Path:
    """A workbook whose ``<sheets>`` list is empty."""
    with zipfile.ZipFile(source) as archive:
        xml = archive.read("xl/workbook.xml").decode()
    return rewrite(
        source,
        target,
        {"xl/workbook.xml": re.sub(r"<sheets>.*?</sheets>", "<sheets/>", xml, flags=re.S).encode()},
    )


def with_content_types_part_named(source: Path, target: Path, name: str) -> Path:
    """``[Content_Types].xml`` points the workbook at a part called ``name``, which isn't there."""
    with zipfile.ZipFile(source) as archive:
        xml = archive.read("[Content_Types].xml").decode()
    pattern = rf'PartName="[^"]*"( ContentType="{re.escape(_WORKBOOK_CONTENT_TYPE)}")'
    crafted, count = re.subn(pattern, rf'PartName="/{name}"\1', xml)
    assert count == 1
    return rewrite(source, target, {"[Content_Types].xml": crafted.encode()})


def encrypted_package(target: Path) -> Path:
    """The OLE2 magic plus the ``EncryptedPackage`` stream name: how Excel marks a password."""
    target.write_bytes(_OLE2_HEADER + "EncryptedPackage".encode("utf-16-le") + b"\0" * 64)
    return target


def legacy_xls(target: Path) -> Path:
    """The OLE2 magic and nothing that names an encrypted stream."""
    target.write_bytes(_OLE2_HEADER + "Workbook".encode("utf-16-le") + b"\0" * 64)
    return target


# -- Sheets for the values-only-copy tests ------------------------------------------------

_DATA_ROWS = [("Alice", 10), ("Bob", 20), ("Alice", 10), None, ("Carol", 30)]


def save_formula_values(
    source: Path, target: Path, values: dict[str, object], errors: tuple[str, ...] = ()
) -> Path:
    """Give formula cells of the first sheet a saved result, as Excel would on save.

    openpyxl writes formulas with no saved value (``<v />``). ``values`` maps a
    cell (``"C2"``) to the result to store; every other formula stays unsaved. A cell
    listed in ``errors`` is stored as an error result (``t="e"``, e.g. ``"#N/A"``).
    """
    with zipfile.ZipFile(source) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
    for cell, value in values.items():
        xml, count = re.subn(
            rf'(<c r="{cell}"[^>]*><f>[^<]*</f>)<v\s*/>', rf"\g<1><v>{value}</v>", xml
        )
        assert count == 1, cell
        if cell in errors:
            xml = xml.replace(f'<c r="{cell}">', f'<c r="{cell}" t="e">', 1)
    return rewrite(source, target, {"xl/worksheets/sheet1.xml": xml.encode()})


def formatted_formula_sheet(path: Path, *, saved: bool) -> Path:
    """A plain table with formulas and formatting: name, amount, double (``=B*2``).

    Rows 2-6: Alice 10, Bob 20, Alice 10 (an exact duplicate), an empty row, Carol 30.
    Four formula cells (``C2``, ``C3``, ``C4``, ``C6``), a bold red header, a yellow fill,
    a wide column and a percent format. ``saved=True`` stores the formulas' results (20, 40,
    20, 60) as a workbook last saved by Excel would; ``False`` leaves them unsaved.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["name", "amount", "double"])
    for index, row in enumerate(_DATA_ROWS, start=2):
        sheet.append([*row, f"=B{index}*2"] if row else [])
    sheet["A1"].font = Font(bold=True, color="FFFF0000")
    sheet["B2"].fill = PatternFill("solid", fgColor="FFFFFF00")
    sheet["B3"].number_format = "0.00%"
    sheet.column_dimensions["A"].width = 40
    if not saved:
        book.save(path)
        return path
    unsaved = path.with_name(path.stem + "_unsaved.xlsx")
    book.save(unsaved)
    save_formula_values(unsaved, path, {"C2": 20, "C3": 40, "C4": 20, "C6": 60})
    unsaved.unlink()
    return path


def merged_cell_sheet(path: Path) -> Path:
    """The same table with a merged note cell beside it (``E1:F1``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["name", "amount"])
    for row in _DATA_ROWS:
        sheet.append(list(row) if row else [])
    sheet["E1"] = "note: totals below"
    sheet.merge_cells("E1:F1")
    book.save(path)
    return path


def header_on_row_three_sheet(path: Path) -> Path:
    """The same table with a title on row 1, a blank row 2 and the header on row 3."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet["A1"] = "Quarterly report"
    sheet.append([])
    sheet.append(["name", "amount"])
    for row in _DATA_ROWS:
        sheet.append(list(row) if row else [])
    book.save(path)
    return path


def plain_sheet(path: Path) -> Path:
    """The same table, plain: no formulas, no formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["name", "amount"])
    for row in _DATA_ROWS:
        sheet.append(list(row) if row else [])
    book.save(path)
    return path


def na_text_sheet(path: Path) -> Path:
    """Country codes and notes that pandas reads as missing: ``NA`` (Namibia), ``N/A``, ``null``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["country", "note"])
    for row in [("NA", "N/A"), ("Namibia", "null"), ("NA", "None"), ("US", "nan")]:
        sheet.append(list(row))
    book.save(path)
    return path


def error_result_sheet(path: Path) -> Path:
    """A plain table whose formulas saved ``#N/A``, ``#DIV/0!``, an empty string and ``40``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["name", "amount", "lookup"])
    for index, row in enumerate([("Alice", 1), ("Bob", 2), ("Carol", 3), ("Dana", 4)], start=2):
        sheet.append([*row, f"=B{index}*2"])
    unsaved = path.with_name(path.stem + "_unsaved.xlsx")
    book.save(unsaved)
    save_formula_values(
        unsaved, path, {"C2": "#N/A", "C3": "#DIV/0!", "C5": 40}, errors=("C2", "C3")
    )
    unsaved.unlink()
    return path


def with_dimension(source: Path, target: Path, ref: str) -> Path:
    """The first sheet's ``<dimension>`` replaced by ``ref`` (some writers get it wrong)."""
    with zipfile.ZipFile(source) as archive:
        xml = archive.read("xl/worksheets/sheet1.xml").decode()
    crafted, count = re.subn(r'<dimension ref="[^"]*"\s*/>', f'<dimension ref="{ref}"/>', xml)
    assert count == 1
    return rewrite(source, target, {"xl/worksheets/sheet1.xml": crafted.encode()})


def gap_header_wrong_dimension_sheet(path: Path) -> Path:
    """Header ``name, <empty>, amount`` with ``<dimension ref="A1"/>`` hiding the rest."""
    path.parent.mkdir(parents=True, exist_ok=True)
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = "Sheet1"
    sheet.append(["name", None, "amount"])
    for row in [("Alice", None, 1), ("Alice", None, 1), ("Bob", None, 2)]:
        sheet.append(list(row))
    plain = path.with_name(path.stem + "_dim.xlsx")
    book.save(plain)
    with_dimension(plain, path, "A1")
    plain.unlink()
    return path
