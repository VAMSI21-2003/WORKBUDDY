# ruff: noqa: E501
"""Round 3: the new `_text_layout_problem` (object identity, run gap, column gap), at the worker.

For each PDF: the real sandboxed `extract` and `verify`; the table that was drawn; whether the turn
would deliver (both ok, cells equal) and whether what it would deliver equals what was drawn.

    python r3_layout.py [group ...]      # groups: punct, words, words12, wordsp, wordsp_col, glyphs, colmajor, lo_punct, lo_words
Output: out/r3_layout.json; prints one line per case: DELIVER-RIGHT / DELIVER-WRONG / REFUSED(reason).
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

import pdfs  # noqa: E402
from reportlab.lib.pagesizes import A4  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from skills.pdf import sandbox  # noqa: E402

OUT = HERE / "out"
OUT.mkdir(exist_ok=True)


def drawn(rows, widths, *, unit="string", order="row", font="Helvetica", size=10, h=20) -> bytes:
    """A ruled grid; text drawn per `unit`: one string per cell, one object per word, or one per glyph.
    `order`: row-major or column-major drawing. Text is never clipped (it may run past a rule)."""
    xs = [50]
    for w in widths:
        xs.append(xs[-1] + w)
    y0 = 780
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    c.setLineWidth(0.5)
    for j in range(len(rows) + 1):
        c.line(xs[0], y0 - j * h, xs[-1], y0 - j * h)
    for x in xs:
        c.line(x, y0, x, y0 - len(rows) * h)
    c.setFont(font, size)
    cells = [(j, i) for j in range(len(rows)) for i in range(len(widths))]
    if order == "column":
        cells.sort(key=lambda ji: (ji[1], ji[0]))
    for j, i in cells:
        text = rows[j][i]
        if not text:
            continue
        x, y = xs[i] + 3, y0 - (j + 1) * h + 6
        if unit == "string":
            c.drawString(x, y, text)
        elif unit in ("word", "wordsp"):
            words = text.split(" ")
            for k, word in enumerate(words):
                if word:
                    shown = word + " " if unit == "wordsp" and k < len(words) - 1 else word
                    c.drawString(x + stringWidth(" ".join(words[:k]) + (" " if k else ""), font, size), y, shown)
        else:  # glyph
            for k, ch in enumerate(text):
                if ch != " ":
                    c.drawString(x + stringWidth(text[:k], font, size), y, ch)
    c.showPage()
    c.save()
    return buf.getvalue()


def judge(pdf: bytes | None, rows):
    if pdf is None:
        return {"result": "NO-PDF"}
    ex, ve = sandbox.run("extract", pdf), sandbox.run("verify", pdf)
    if not ve.ok:
        return {"result": f"REFUSED({ve.reason})"}
    if not ex.ok:
        return {"result": f"REFUSED-extract({ex.reason})"}
    got = ve.data.get("rows")
    agree = ex.data.get("rows") == got
    if not agree:
        return {"result": "DISAGREE(refused at verification)", "verify": got, "extract": ex.data.get("rows")}
    return {"result": "DELIVER-RIGHT" if got == [list(r) for r in rows] else "DELIVER-WRONG", "got": got}


PUNCT = ["Mon - Fri", "Mon – Fri", "Mon — Fri", "Smith - one", "A - B", "x ~ y", "on _ off", "Jan . end",
         "Inc., Ltd", "e.g. now", "a, b, c", "9:00 - 17:00", "low - high", "Paid in full", "cost ° c", "re: memo"]


def group_punct():
    for text in PUNCT:
        rows = [["Name", "Note"], ["Alice", text], ["Bob", "ok"]]
        yield f"punct/{text}", drawn(rows, [80, 160]), rows


def _overflow_rows(text):
    return [["Name", "Note", "Qty"], [text, "", "10"], ["Bob", "short", "3"]]


def _sweep(unit, order="row", text="Paid in full on delivery", size=10):
    full = stringWidth(text, "Helvetica", size)
    w = 20.0
    while w < full + 3:
        rows = _overflow_rows(text)
        yield f"{unit}-{order}-{size}/{text}/w{w:.1f}", drawn(rows, [w, 80, 60], unit=unit, order=order, size=size), rows
        w += 1.0


def group_words():
    yield from _sweep("word")
    yield from _sweep("word", text="ships on monday or tuesday")


def group_glyphs():
    yield from _sweep("glyph")


def group_colmajor():
    yield from _sweep("word", order="column")


def group_words12():
    yield from _sweep("word", size=12)


def group_wordsp():
    yield from _sweep("wordsp")
    yield from _sweep("wordsp", size=12)


def group_wordsp_col():
    yield from _sweep("wordsp", order="column", size=12)


def group_lo_punct():
    for text in ["Mon - Fri", "Smith - one", "Mon – Fri", "a - c", "x ~ y", "low - high"]:
        rows = [["Name", "Note"], ["Alice", text], ["Bob", "ok"]]
        yield f"lo_punct/{text}", pdfs.libreoffice_pdf(rows), rows


def group_lo_words():
    # LibreOffice Writer (an HTML table): justified/word-split text if any
    for text in ["Mon - Fri", "Paid in full", "Smith - one"]:
        rows = [["Name", "Note"], ["Alice", text], ["Bob", "ok"]]
        yield f"lo_writer/{text}", pdfs.libreoffice_pdf(rows, kind="writer"), rows


GROUPS = {"punct": group_punct, "words12": group_words12, "wordsp": group_wordsp, "wordsp_col": group_wordsp_col, "words": group_words, "glyphs": group_glyphs, "colmajor": group_colmajor,
          "lo_punct": group_lo_punct, "lo_words": group_lo_words}


def main():
    only = sys.argv[1:] or list(GROUPS)
    recs = []
    for g in only:
        counts = {}
        for tag, pdf, rows in GROUPS[g]():
            r = judge(pdf, rows)
            recs.append({"tag": tag, **r})
            counts[r["result"].split("(")[0]] = counts.get(r["result"].split("(")[0], 0) + 1
            if r["result"] != "DELIVER-RIGHT" or g in ("punct", "lo_punct", "lo_words"):
                print(tag, "->", r["result"], json.dumps(r.get("got"))[:160] if r.get("got") else "")
        print(f"== {g}: {counts}")
    (OUT / "r3_layout.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
