# ruff: noqa: E501
"""Round 3: LibreOffice Calc exports where an overflowing note is rich text (one word bold or coloured),
so LibreOffice draws it as several text objects. Next cell empty; the first column's width is swept.

    python r3_lo_rich.py          # output: out/r3_lo_rich.json; prints delivered-wrong cases
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r3_layout  # noqa: E402  (puts ROOT on the path)

NOTES = [("Paid in ", "full", " on delivery"), ("Ships ", "next", " week"), ("Left at the ", "front", " desk"),
         ("Needs ", "manager", " approval"), ("Call ", "before", " noon")]
WIDTHS = [6, 8, 10, 12, 14, 16, 18]  # Calc column widths (characters)


def calc_pdf(rows, width, bold_mid) -> bytes | None:
    import openpyxl
    from openpyxl.cell.rich_text import CellRichText, TextBlock
    from openpyxl.cell.text import InlineFont
    from openpyxl.styles import Border, Side
    with tempfile.TemporaryDirectory() as d:
        d = Path(d)
        wb = openpyxl.Workbook()
        ws = wb.active
        thin = Side(style="thin")
        for r, row in enumerate(rows, 1):
            for c, v in enumerate(row, 1):
                if isinstance(v, tuple):
                    v = CellRichText(v[0], TextBlock(InlineFont(b=True) if bold_mid else InlineFont(color="FF0000"), v[1]), v[2])
                cell = ws.cell(r, c, v)
                cell.border = Border(left=thin, right=thin, top=thin, bottom=thin)
        ws.column_dimensions["B"].width = width
        src = d / "t.xlsx"
        wb.save(src)
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(d), str(src)],
                       capture_output=True, timeout=120, env={"HOME": str(d), "PATH": "/usr/bin:/bin"})
        out = d / "t.pdf"
        return out.read_bytes() if out.exists() else None


def main():
    if shutil.which("soffice") is None:
        print("no soffice")
        return
    recs, counts = [], {}
    for note in NOTES:
        for bold in (True, False):
            for w in WIDTHS:
                rows = [["Name", "Note", "Qty"], ["Alice", note, ""], ["Bob", "short", "3"]]
                want = [["Name", "Note", "Qty"], ["Alice", "".join(note), ""], ["Bob", "short", "3"]]
                r = r3_layout.judge(calc_pdf(rows, w, bold), want)
                key = r["result"].split("(")[0]
                counts[key] = counts.get(key, 0) + 1
                recs.append({"note": "".join(note), "bold": bold, "width": w, **r})
                if key in ("DELIVER-WRONG",):
                    print("".join(note), "bold" if bold else "red", w, "->", r["result"], r.get("got", [None, None])[1])
    print(counts)
    (r3_layout.OUT / "r3_lo_rich.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
