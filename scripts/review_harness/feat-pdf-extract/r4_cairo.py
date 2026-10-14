# ruff: noqa: E501
"""Round 4: tables drawn by a real generator that sets text as `/F 1 Tf` + a scaled text matrix.

cairo (the system libcairo, through ctypes) writes every text object with a font size of 1 and the
real size in `Tm`, as macOS Quartz does. PDFium's `FPDFText_GetFontSize` returns the `Tf` operand
(1), not the size on the page, and `geometry.text_layout_problem` measures its gaps in that "em".

    python r4_cairo.py [group ...]     # groups: plain, single, overflow, overflow1, tf (hand-written Tf/Tm variants)
Output: out/r4/cairo.json; one line per case: DELIVER-RIGHT / DELIVER-WRONG / REFUSED(reason).
"""
from __future__ import annotations

import ctypes
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(HERE))

from r3_layout import judge  # noqa: E402  (round 3's: real sandboxed extract + verify, compare)

OUT = HERE / "out" / "r4"
OUT.mkdir(parents=True, exist_ok=True)

_cairo = ctypes.CDLL("libcairo.so.2")
for name, res, args in [
    ("cairo_pdf_surface_create", ctypes.c_void_p, [ctypes.c_char_p, ctypes.c_double, ctypes.c_double]),
    ("cairo_create", ctypes.c_void_p, [ctypes.c_void_p]),
    ("cairo_select_font_face", None, [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int, ctypes.c_int]),
    ("cairo_set_font_size", None, [ctypes.c_void_p, ctypes.c_double]),
    ("cairo_move_to", None, [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]),
    ("cairo_line_to", None, [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]),
    ("cairo_rel_move_to", None, [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]),
    ("cairo_stroke", None, [ctypes.c_void_p]),
    ("cairo_set_line_width", None, [ctypes.c_void_p, ctypes.c_double]),
    ("cairo_show_text", None, [ctypes.c_void_p, ctypes.c_char_p]),
    ("cairo_destroy", None, [ctypes.c_void_p]),
    ("cairo_surface_finish", None, [ctypes.c_void_p]),
    ("cairo_surface_destroy", None, [ctypes.c_void_p]),
]:
    fn = getattr(_cairo, name)
    fn.restype, fn.argtypes = res, args

FAMILY = b"DejaVu Sans"


def cairo_grid(rows, widths, *, size=10.0, h=20.0, rich=False) -> bytes:
    """A ruled grid drawn with cairo. Each cell: one `show_text` call; with `rich`, a cell whose
    text has a word between `*`s draws that word bold (a separate call, as a rich-text run is)."""
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "t.pdf"
        surf = _cairo.cairo_pdf_surface_create(str(path).encode(), 595.0, 842.0)
        cr = _cairo.cairo_create(surf)
        _cairo.cairo_set_line_width(cr, 0.5)
        xs = [50.0]
        for w in widths:
            xs.append(xs[-1] + w)
        y0 = 60.0
        for j in range(len(rows) + 1):
            _cairo.cairo_move_to(cr, xs[0], y0 + j * h)
            _cairo.cairo_line_to(cr, xs[-1], y0 + j * h)
            _cairo.cairo_stroke(cr)
        for x in xs:
            _cairo.cairo_move_to(cr, x, y0)
            _cairo.cairo_line_to(cr, x, y0 + len(rows) * h)
            _cairo.cairo_stroke(cr)
        for j, row in enumerate(rows):
            for i, text in enumerate(row):
                if not text:
                    continue
                _cairo.cairo_move_to(cr, xs[i] + 3, y0 + (j + 1) * h - 6)
                pieces = [(text, 0)]
                if rich and "*" in text:
                    a, b, c = text.split("*")
                    pieces = [(a, 0), (b, 1), (c, 0)]
                for piece, bold in pieces:
                    if piece:
                        _cairo.cairo_select_font_face(cr, FAMILY, 0, bold)
                        _cairo.cairo_set_font_size(cr, size)
                        _cairo.cairo_show_text(cr, piece.encode())
        _cairo.cairo_destroy(cr)
        _cairo.cairo_surface_finish(surf)
        _cairo.cairo_surface_destroy(surf)
        return path.read_bytes()


def expected(rows):
    return [[c.replace("*", "") for c in r] for r in rows]


def group_plain():
    tables = {
        "two_word_cells": [["Name", "City", "Qty"], ["Ann Lee", "New York", "10"], ["Bob", "Paris", "3"]],
        "header_two_words": [["Item", "Unit price"], ["Apple", "2.50"], ["Pear", "1.10"]],
        "fruit": [["Fruit", "Colour"], ["Green apple", "green"], ["Banana", "yellow"]],
        "dates": [["Shop", "Open"], ["North", "Mon - Fri"], ["South", "Sat"]],
    }
    for size in (9.0, 10.0, 12.0):
        for name, rows in tables.items():
            yield f"plain/{name}/{size:g}pt", cairo_grid(rows, [110, 110, 60][: len(rows[0])], size=size), expected(rows)


def group_single():
    rows = [["Name", "Code", "Qty"], ["Ann", "A-17", "10"], ["Bob", "B-02", "3"]]
    for size in (9.0, 10.0, 12.0):
        yield f"single/{size:g}pt", cairo_grid(rows, [80, 80, 60], size=size), rows


def _overflow(text, rich, size):
    full = len(text) * size * 0.62
    w = 20.0
    while w < full + 3:
        rows = [["Name", "Note", "Qty"], [text, "", "10"], ["Bob", "ok", "3"]]
        yield f"{'rich' if rich else 'one'}/{text}/{size:g}pt/w{w:.0f}", cairo_grid(rows, [w, 80, 60], size=size, rich=rich), expected(rows)
        w += 1.0


def group_overflow():
    """A two-word note, first word bold (two text objects), overflowing into the empty next cell."""
    for size in (10.0, 12.0):
        yield from _overflow("*Urgent* reorder", True, size)
        yield from _overflow("*Paid* tomorrow", True, size)


def group_overflow1():
    """The same note in one show_text call (one text object): the object rule should refuse."""
    yield from _overflow("Urgent reorder", False, 10.0)


GROUPS = {"plain": group_plain, "single": group_single, "overflow": group_overflow, "overflow1": group_overflow1}


def main(argv):
    names = argv or list(GROUPS)
    results = {}
    counts = {}
    for g in names:
        for case, pdf, rows in GROUPS[g]():
            r = judge(pdf, rows)
            results[case] = r
            key = (g, r["result"].split("(")[0] if r["result"].startswith("REFUSED") else r["result"])
            counts[key] = counts.get(key, 0) + 1
            if g in ("plain", "single") or r["result"] == "DELIVER-WRONG":
                print(f"{case}: {r['result']}" + (f"  got={r.get('got')}" if "got" in r else ""))
    for (g, res), n in sorted(counts.items()):
        print(f"SUMMARY {g}: {res} x{n}")
    (OUT / f"cairo_{'_'.join(names)}.json").write_text(json.dumps(results, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv[1:])
