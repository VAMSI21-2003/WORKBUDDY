"""Damaged and protected workbooks for the sheet-list tests, built from a real one."""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

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
