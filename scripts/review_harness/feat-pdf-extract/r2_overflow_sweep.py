# ruff: noqa: E501
"""Round 2: does `verify`'s new `_text_overflows` catch every overflow into an empty neighbour cell?

A hand-drawn 3-column grid; row 1's first cell holds a sentence wider than the cell, drawn in full
(no clip), running into the empty middle cell. The first column's width is swept in small steps so
the rule lands at every position in the sentence. For each width: extract, verify, and whether the
verifier would pass (the two readings agree) while the agreed cells are not what was drawn.

    python r2_overflow_sweep.py [--text "..."] [--step 0.5] [--font Helvetica] [--size 10]
Output: out/r2_overflow_sweep.json; prints the delivered-wrong widths.
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))

from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from skills.pdf import sandbox  # noqa: E402


def build(text: str, w0: float, *, font: str, size: float, w1: float = 80, w2: float = 60) -> bytes:
    rows = [["Name", "Note", "Qty"], [text, "", "10"], ["Bob", "short", "3"]]
    xs = [50, 50 + w0, 50 + w0 + w1, 50 + w0 + w1 + w2]
    y0, h = 780, 20
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setLineWidth(0.5)
    for j in range(len(rows) + 1):
        c.line(xs[0], y0 - j * h, xs[-1], y0 - j * h)
    for x in xs:
        c.line(x, y0, x, y0 - len(rows) * h)
    c.setFont(font, size)
    for j, row in enumerate(rows):
        for i, t in enumerate(row):
            if t:
                c.drawString(xs[i] + 3, y0 - (j + 1) * h + 6, t)
    c.showPage()
    c.save()
    return buf.getvalue()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="Paid in full on delivery")
    ap.add_argument("--step", type=float, default=0.5)
    ap.add_argument("--font", default="Helvetica")
    ap.add_argument("--size", type=float, default=10)
    a = ap.parse_args()
    full = stringWidth(a.text, a.font, a.size)
    results = []
    w = 20.0
    while w < full + 3:
        data = build(a.text, w, font=a.font, size=a.size)
        ex = sandbox.run("extract", data)
        ve = sandbox.run("verify", data)
        exr = ex.data.get("rows") if ex.ok else None
        ver = ve.data.get("rows") if ve.ok else None
        agree = ex.ok and ve.ok and ve.data.get("plain") is True and exr == ver
        rec = {"w0": w, "rule_at_char": None, "extract": ex.reason or "ok", "verify": ve.reason or "ok", "agree": agree,
               "row1": exr[1] if agree else None}
        # which character the rule falls in (or after)
        acc = 3.0
        for k, ch in enumerate(a.text):
            cw = stringWidth(ch, a.font, a.size)
            if acc <= w < acc + cw:
                rec["rule_at_char"] = f"{k}:{ch!r} ({a.text[:k]}|{a.text[k:]})"
                break
            acc += cw
        results.append(rec)
        w = round(w + a.step, 3)
    wrong = [r for r in results if r["agree"]]
    out = HERE / "out" / "r2_overflow_sweep.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(results, indent=1, ensure_ascii=False))
    print(f"text={a.text!r} font={a.font} size={a.size}: {len(results)} widths, {len(wrong)} would be delivered (verifier passes)")
    for r in wrong:
        print(json.dumps({k: r[k] for k in ("w0", "rule_at_char", "row1")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
