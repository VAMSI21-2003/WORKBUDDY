# ruff: noqa: E501
"""Round 2: LibreOffice Calc exports where a cell's text runs into the empty cell to its right.

The ordinary way such a PDF is made: a sheet with borders, a note longer than its column, the next
cell empty (Calc then draws the note in full across it). For each note: extract, verify, and whether
the verifier would pass with cells that are not what the sheet held.

    python r2_lo_overflow.py         # output: out/r2_lo_overflow.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import pdfs  # noqa: E402

from skills.pdf import sandbox  # noqa: E402

NOTES = [
    "a fairly long note that will not fit",
    "Paid in full on delivery",
    "Returned to sender",
    "Awaiting customer reply",
    "Ships next week",
    "Call back on Monday",
    "Invoice sent twice",
    "Discount applied at checkout",
    "Left at the front desk",
    "Needs manager approval",
    "Out of stock until May",
    "Customer asked for a refund",
]


def cases():
    for n in NOTES:
        rows = [["Name", "Note", "Qty"], ["Alice", n, ""], ["Bob", "short", "3"]]
        data = pdfs.libreoffice_pdf(rows, kind="calc")
        if data:
            yield n, data, rows


def main() -> None:
    results = []
    for name, data, drawn in cases():
        ex = sandbox.run("extract", data)
        ve = sandbox.run("verify", data)
        exr = ex.data.get("rows") if ex.ok else None
        ver = ve.data.get("rows") if ve.ok else None
        agree = ex.ok and ve.ok and ve.data.get("plain") is True and exr == ver
        wrong = agree and exr != drawn
        rec = {"note": name, "extract": ex.reason or "ok", "verify": ve.reason or "ok", "agree": agree,
               "delivered_wrong": wrong, "row1": exr[1] if agree else None}
        results.append(rec)
        print(json.dumps(rec, ensure_ascii=False))
    out = HERE / "out" / "r2_lo_overflow.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print(f"{sum(r['delivered_wrong'] for r in results)} of {len(results)} would be delivered with a note split across two cells")


if __name__ == "__main__":
    main()
