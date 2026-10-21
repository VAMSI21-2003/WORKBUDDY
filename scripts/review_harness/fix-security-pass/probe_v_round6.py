"""Round 6: ordinary workbooks near the new limits (250,000 cells, 30 s), through the real check.

Each file is an ordinary table someone could have: no crafted XML. Prints the check's verdict and
its time with the real child (outside pytest, so ``tests/conftest.py``'s stub is not in play).

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_v_round6.py [--tree DIR] [--only V1,V3]
"""
import argparse
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out" / "r6"


def build(path: Path, rows: int, cols: int, kind: str) -> None:
    import openpyxl

    book = openpyxl.Workbook(write_only=True)
    sheet = book.create_sheet("Sheet1")
    sheet.append([f"col{c}" for c in range(cols)])
    for r in range(2, rows + 2):
        if kind == "numbers":
            sheet.append([r * cols + c for c in range(cols)])
        elif kind == "half_formulas":  # a column of numbers and a formula beside it, unshared
            half = cols // 2
            sheet.append([r + c for c in range(half)]
                         + [f"=A{r}*{c + 2}+B{r}" for c in range(cols - half)])
        elif kind == "text":  # short distinct text, as a contact list has
            sheet.append([f"name {r} field {c}" for c in range(cols)])
    book.save(path)


CASES = {
    "V1_numbers_at_cap": (24_999, 10, "numbers"),
    "V2_half_formulas_at_cap": (24_999, 10, "half_formulas"),
    "V3_half_formulas_100k": (9_999, 10, "half_formulas"),
    "V4_text_at_cap": (24_999, 10, "text"),
    "V5_numbers_over_cap": (25_001, 10, "numbers"),
}

CHILD = r"""
import sys, time
sys.path.insert(0, sys.argv[2])
from skills.excel.workbook import check_workbook_limits, WorkbookRefused
s = time.time()
try:
    check_workbook_limits(sys.argv[1]); v = "PASS"
except WorkbookRefused as e:
    v = "REFUSED: " + str(e)
print(f"{time.time() - s:.1f}s {v}")
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    for name, spec in CASES.items():
        if a.only and name not in a.only.split(","):
            continue
        path = OUT / f"{name}.xlsx"
        if not path.exists():
            build(path, *spec)
        start = time.time()
        r = subprocess.run([sys.executable, "-c", CHILD, str(path), a.tree], capture_output=True,
                           text=True, cwd=a.tree)
        print(f"{name} ({path.stat().st_size // 1024} KB): {r.stdout.strip() or r.stderr[-300:]}"
              f" [wall {time.time() - start:.1f}s]", flush=True)


if __name__ == "__main__":
    main()
