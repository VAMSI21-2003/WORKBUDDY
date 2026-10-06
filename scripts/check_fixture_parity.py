"""Do the workbook fixtures hold the same content whether or not ``lxml`` is installed?

openpyxl writes sheet XML differently when ``lxml`` is importable (``<v></v>`` for ``<v />``, the
namespace declarations in other places). ``tests/workbook_fixtures.py`` must not depend on that.

Run it with and without lxml installed (or once with ``OPENPYXL_LXML=False``, which makes openpyxl
write as it does without lxml)::

    python scripts/check_fixture_parity.py build OUT_DIR     # writes every fixture under OUT_DIR
    python scripts/check_fixture_parity.py compare DIR_A DIR_B

``compare`` reads every part of every workbook in both directories as an XML tree (tag with its
namespace, attributes, text, children; so prefixes, declarations and ``<v />`` against
``<v></v>`` don't count) and checks that the trees are equal, and that openpyxl reads the same
cell values and formulas from both. Exit status 1 if anything differs.
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

REPO = Path(__file__).resolve().parent.parent


def build(out: Path) -> None:
    sys.path.insert(0, str(REPO))
    import openpyxl

    from tests import workbook_fixtures as fx

    out.mkdir(parents=True, exist_ok=True)
    print(f"openpyxl.LXML={openpyxl.LXML}", file=sys.stderr)

    def two_sheets(path: Path) -> Path:
        book = openpyxl.Workbook()
        book.active.title = "Sheet1"
        book.active.append(["name", "amount"])
        book.create_sheet("Other").append(["x"])
        book.save(path)
        return path

    fx.formatted_formula_sheet(out / "formulas_saved.xlsx", saved=True)
    fx.formatted_formula_sheet(out / "formulas_unsaved.xlsx", saved=False)
    fx.merged_cell_sheet(out / "merged.xlsx")
    fx.header_on_row_three_sheet(out / "header_row3.xlsx")
    fx.plain_sheet(out / "plain.xlsx")
    fx.na_text_sheet(out / "na_text.xlsx")
    fx.error_result_sheet(out / "error_results.xlsx")
    fx.gap_header_wrong_dimension_sheet(out / "gap_header.xlsx")
    unsaved = fx.formatted_formula_sheet(out / "u.xlsx", saved=False)
    fx.save_formula_values(unsaved, out / "partly_saved.xlsx", {"C2": 20, "C6": 60})
    partly = out / "partly_saved.xlsx"
    fx.save_empty_string_result(partly, out / "empty_string_result.xlsx", "C3")
    fx.with_dimension(unsaved, out / "dimension_a1.xlsx", "A1")
    two = two_sheets(out / "two.xlsx")
    fx.without_sheet_part(two, out / "no_sheet_part.xlsx")
    fx.without_declared_sheets(two, out / "no_declared_sheets.xlsx")
    fx.with_content_types_part_named(two, out / "content_types_name.xlsx", "xl/nothing.xml")
    fx.encrypted_package(out / "encrypted.xlsx")
    fx.legacy_xls(out / "legacy.xls")
    (out / "u.xlsx").unlink()
    (out / "two.xlsx").unlink()


# The document properties carry the time the file was written, which differs between any two runs.
_TIMESTAMPS = ("}created", "}modified")


def tree(data: bytes) -> Any:
    def walk(element: ElementTree.Element) -> Any:
        return (
            element.tag,
            tuple(sorted(element.attrib.items())),
            (element.text or "").strip(),
            tuple(walk(child) for child in element if not child.tag.endswith(_TIMESTAMPS)),
        )

    return walk(ElementTree.fromstring(data))


def cells(path: Path) -> Any:
    import openpyxl

    book = openpyxl.load_workbook(path)
    try:
        return {
            sheet.title: [[c.value for c in row] for row in sheet.iter_rows()]
            for sheet in book.worksheets
        }
    finally:
        book.close()


def compare(a: Path, b: Path) -> int:
    names_a = sorted(p.name for p in a.iterdir())
    names_b = sorted(p.name for p in b.iterdir())
    problems = 0
    if names_a != names_b:
        print("different file lists", names_a, names_b)
        return 1
    parts_checked = 0
    for name in names_a:
        pa, pb = a / name, b / name
        if not zipfile.is_zipfile(pa):
            same = pa.read_bytes() == pb.read_bytes()
            print(f"{'same' if same else 'DIFFERENT':9} {name} (raw bytes)")
            problems += not same
            continue
        with zipfile.ZipFile(pa) as za, zipfile.ZipFile(pb) as zb:
            parts_a, parts_b = sorted(za.namelist()), sorted(zb.namelist())
            if parts_a != parts_b:
                print(f"DIFFERENT {name}: parts {parts_a} vs {parts_b}")
                problems += 1
                continue
            different = []
            for part in parts_a:
                parts_checked += 1
                try:
                    if tree(za.read(part)) != tree(zb.read(part)):
                        different.append(part)
                except ElementTree.ParseError:
                    if za.read(part) != zb.read(part):
                        different.append(part)
        try:
            cells_same = cells(pa) == cells(pb)
        except Exception as exc:  # noqa: BLE001 - a damaged fixture is read the same way in both
            cells_same = type(exc) is type(_reread_error(pb))
        status = "same" if not different and cells_same else "DIFFERENT"
        print(f"{status:9} {name}: {len(parts_a)} parts, cells read equal={cells_same}", end="")
        print(f", parts that differ: {different}" if different else "")
        problems += bool(different) or not cells_same
    print(f"{len(names_a)} files, {parts_checked} parts compared, {problems} different")
    return 1 if problems else 0


def _reread_error(path: Path) -> Exception:
    try:
        cells(path)
    except Exception as exc:  # noqa: BLE001
        return exc
    return RuntimeError("read fine")


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "build":
        build(Path(sys.argv[2]))
    elif len(sys.argv) == 4 and sys.argv[1] == "compare":
        sys.exit(compare(Path(sys.argv[2]), Path(sys.argv[3])))
    else:
        sys.exit(__doc__)
