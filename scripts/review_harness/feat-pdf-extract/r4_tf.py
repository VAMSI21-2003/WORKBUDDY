# ruff: noqa: E501
"""Round 4: the em that `geometry.text_layout_problem` measures in is `FPDFText_GetFontSize`, the `Tf`
operand, not the size on the page. Same table, same page, written three ways:

- `tf`:   `/F1 10 Tf`, identity `Tm` (LibreOffice, reportlab, Word);
- `tm`:   `/F1 1 Tf` and `10 0 0 10 x y Tm` (macOS Quartz writes text this way, cairo too);
- `cm`:   `0.1 0 0 0.1 0 0 cm` around the page and `/F1 100 Tf` (a page drawn in tenths of a point,
          as Ghostscript's pdfwrite and some printer drivers do).
Each cell's text is its own text object (`BT ... ET`); a `*word*` is drawn bold as its own object.

    python r4_tf.py [group ...]    # groups: em (what PDFium reports), plain, overflow, unruled, wordspacing
Output: out/r4/tf_<groups>.json; one line per case; a SUMMARY per group and writing.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from r3_layout import judge  # noqa: E402
from reportlab.pdfbase.pdfmetrics import stringWidth  # noqa: E402

from tests.pdf_fixtures import build_pdf  # noqa: E402

OUT = HERE / "out" / "r4"
OUT.mkdir(parents=True, exist_ok=True)
WRITINGS = ("tf", "tm", "cm")


def _n(v: float) -> str:
    return f"{v:.3f}".rstrip("0").rstrip(".")


def table_pdf(rows, widths, *, writing="tf", size=10.0, h=20.0, pad=4.0, extra=None, tw=0.0) -> bytes:
    """`extra`: {(row, col): [(dx, text), ...]} draws more objects in a cell at x + dx (unruled columns)."""
    k = 10.0 if writing == "cm" else 1.0  # page units per point
    xs = [50.0]
    for w in widths:
        xs.append(xs[-1] + w)
    y0 = 700.0
    ops = ["q", f"{_n(1 / k)} 0 0 {_n(1 / k)} 0 0 cm"] if writing == "cm" else ["q"]
    ops.append(f"{_n(0.5 * k)} w")
    for j in range(len(rows) + 1):
        ops.append(f"{_n(xs[0] * k)} {_n((y0 - j * h) * k)} m {_n(xs[-1] * k)} {_n((y0 - j * h) * k)} l S")
    for x in xs:
        ops.append(f"{_n(x * k)} {_n(y0 * k)} m {_n(x * k)} {_n((y0 - len(rows) * h) * k)} l S")

    def show(x, y, text, font):
        safe = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        if writing == "tm":
            ops.append(f"BT /{font} 1 Tf {_n(size)} 0 0 {_n(size)} {_n(x)} {_n(y)} Tm ({safe}) Tj ET")
        else:
            spacing = f"{_n(tw * k)} Tw " if tw else ""
            ops.append(f"BT /{font} {_n(size * k)} Tf {spacing}{_n(x * k)} {_n(y * k)} Td ({safe}) Tj ET")

    for j, row in enumerate(rows):
        for i, text in enumerate(row):
            x, y = xs[i] + pad, y0 - (j + 1) * h + 6
            if "*" in text:
                a, b, c = text.split("*")
                for piece, font in ((a, "F1"), (b, "F2"), (c, "F1")):
                    if piece:
                        show(x, y, piece, font)
                    x += stringWidth(piece, "Helvetica-Bold" if font == "F2" else "Helvetica", size)
            elif text:
                show(x, y, text, "F1")
            for dx, more in (extra or {}).get((j, i), []):
                show(xs[i] + pad + dx, y, more, "F1")
    ops.append("Q")
    content = "\n".join(ops).encode("cp1252")
    objects = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>",
        4: f"<< /Length {len(content)} >>\nstream\n".encode() + content + b"\nendstream",
        5: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        6: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>",
    }
    return build_pdf(objects)


def expected(rows, extra=None):
    out = [[c.replace("*", "") for c in r] for r in rows]
    return out


FRUIT = [["Fruit", "Colour", "Qty"], ["Green apple", "light green", "10"], ["Banana", "yellow", "3"]]
SINGLE = [["Name", "Code", "Qty"], ["Ann", "A-17", "10"], ["Bob", "B-02", "3"]]


def group_em():
    """What PDFium reports per writing (printed, not judged)."""
    import ctypes

    import pypdfium2 as pdfium
    import pypdfium2.raw as raw
    for w in WRITINGS:
        doc = pdfium.PdfDocument(table_pdf(FRUIT, [100, 100, 60], writing=w))
        tp = doc[0].get_textpage()
        m = raw.FS_MATRIX()
        raw.FPDFText_GetMatrix(tp.raw, 0, ctypes.byref(m))
        left, b, r, t = tp.get_charbox(0)
        print(f"em/{w}: FPDFText_GetFontSize={raw.FPDFText_GetFontSize(tp.raw, 0):g} FPDFText_GetMatrix=({m.a:g},{m.b:g},{m.c:g},{m.d:g}) charbox of 'F'={left:.2f},{b:.2f},{r:.2f},{t:.2f}")
    return []


def group_plain():
    for w in WRITINGS:
        for size in (10.0, 12.0):
            yield f"plain/{w}/fruit/{size:g}", table_pdf(FRUIT, [100, 100, 60], writing=w, size=size), expected(FRUIT)
            yield f"plain/{w}/single/{size:g}", table_pdf(SINGLE, [80, 80, 60], writing=w, size=size), expected(SINGLE)
            dates = [["Shop", "Open"], ["North", "Mon - Fri"], ["South", "Sat"]]
            yield f"plain/{w}/dates/{size:g}", table_pdf(dates, [80, 100], writing=w, size=size), expected(dates)


def group_overflow():
    """A two-word note, first word bold (two objects), overflowing into the empty next cell; every
    other cell one word. Width swept so the rule lands everywhere."""
    for w in WRITINGS:
        for text in ("*Urgent* reorder", "*Paid* tomorrow"):
            for size in (10.0, 12.0):
                full = stringWidth(text.replace("*", ""), "Helvetica-Bold", size)
                width = 20.0
                while width < full + 6:
                    rows = [["Name", "Note", "Qty"], [text, "", "10"], ["Bob", "ok", "3"]]
                    yield f"overflow/{w}/{text}/{size:g}/w{width:.0f}", table_pdf(rows, [width, 80, 60], writing=w, size=size), expected(rows)
                    width += 1.0


def group_unruled():
    """Review round 2's q03 in this writing: Name | Qty Price, the Qty/Price rule missing (the Price
    text drawn 2.2 em after Qty, in the same ruled cell)."""
    for w in WRITINGS:
        for size in (10.0, 12.0):
            rows = [["Name", "Qty"], ["Apple", "10"], ["Pear", "4"]]
            extra = {(0, 1): [(2.2 * size + stringWidth("Qty", "Helvetica", size), "Price")],
                     (1, 1): [(2.2 * size + stringWidth("10", "Helvetica", size), "2.50")],
                     (2, 1): [(2.2 * size + stringWidth("4", "Helvetica", size), "1.10")]}
            drawn = [["Name", "Qty", "Price"], ["Apple", "10", "2.50"], ["Pear", "4", "1.10"]]
            yield f"unruled/{w}/{size:g}", table_pdf(rows, [100, 140], writing=w, size=size, extra=extra), drawn


def group_wordspacing():
    """`Tw` (word spacing, as justified text sets it) widening the space in a two-object note that
    overflows into the empty next cell; `tf` writing only."""
    for tw in (1.0, 2.0, 3.0):
        for text in ("*Urgent* reorder", "*Paid* tomorrow"):
            full = stringWidth(text.replace("*", ""), "Helvetica-Bold", 10) + tw
            width = 20.0
            while width < full + 6:
                rows = [["Name", "Note", "Qty"], [text, "", "10"], ["Bob", "ok", "3"]]
                yield f"wordspacing/tw{tw:g}/{text}/w{width:.0f}", table_pdf(rows, [width, 80, 60], tw=tw), expected(rows)
                width += 1.0


GROUPS = {"wordspacing": group_wordspacing, "em": group_em, "plain": group_plain, "overflow": group_overflow, "unruled": group_unruled}


def main(argv):
    names = argv or [g for g in GROUPS if g != "wordspacing"]
    results, counts = {}, {}
    for g in names:
        for case, pdf, rows in GROUPS[g]() or []:
            r = judge(pdf, rows)
            results[case] = r
            res = r["result"]
            key = (g, case.split("/")[1], res)
            counts[key] = counts.get(key, 0) + 1
            if g not in ("overflow", "wordspacing") or res == "DELIVER-WRONG":
                print(f"{case}: {res}" + (f"  got={r.get('got')}" if "got" in r else ""))
    for (g, w, res), n in sorted(counts.items()):
        print(f"SUMMARY {g} {w}: {res} x{n}")
    (OUT / f"tf_{'_'.join(names)}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
