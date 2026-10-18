"""Workbooks that pass check_workbook_limits but that pandas reads far larger than the limits.

Each case is small on disk; the read is kept small enough to finish, and the printed shape shows
the grid pandas built. Usage (repo root or --tree root on sys.path):
    PYTHONPATH=. python scripts/review_harness/fix-security-pass/probe_limit_bypass.py
"""
import sys
import zipfile
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
from make_sparse_bomb import build  # noqa: E402

from skills.excel.workbook import MAX_CELLS, MAX_COLUMNS, MAX_ROWS, check_workbook_limits  # noqa: E402

OUT = Path(__file__).parent / "out"
OUT.mkdir(exist_ok=True)


def rewrite_sheet(path: Path, old: str, new: str) -> None:
    with zipfile.ZipFile(path) as z:
        parts = {i.filename: z.read(i.filename) for i in z.infolist()}
    parts["xl/worksheets/sheet1.xml"] = parts["xl/worksheets/sheet1.xml"].replace(
        old.encode(), new.encode()
    )
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in parts.items():
            z.writestr(name, data)


def case(name, rows, cols, row_attr=None, edit=None):
    p = OUT / f"bypass_{name}.xlsx"
    build(str(p), rows, cols, row_attr)
    if edit:
        rewrite_sheet(p, *edit)
    try:
        check_workbook_limits(str(p))
        verdict = "passes"
    except Exception as e:  # noqa: BLE001
        verdict = f"refused: {e}"
    shape = None
    if verdict == "passes":
        df = pd.read_excel(p, dtype=object)
        shape = df.shape
    grid = shape[0] * shape[1] if shape else None
    over = grid is not None and (grid > MAX_CELLS or shape[0] > MAX_ROWS or shape[1] > MAX_COLUMNS)
    print(f"{name:28s} {p.stat().st_size:6d} B  {verdict:10s} shape={shape} grid={grid} "
          f"over_a_limit={over}")


case("area_10k_x_1000", 10_000, 1000)  # inside every limit; grid 10M > 1M cells
case("row_attr_float_3e5", 0, 2, "3e5")  # openpyxl reads r="3e5" as row 300,000
case("row_attr_space", 0, 2, " 300000")  # int(" 300000") works in openpyxl
case("plain_far_col_control", 3, 2, None, ('r="B1"', 'r="BZZ1"'))  # same without $: refused
