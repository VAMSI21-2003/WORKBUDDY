# ruff: noqa: E501
"""Round 2: PDF turns through the real app (fake LLM only), three speakers, aimed at the round-1 fixes.

    python r2_pipeline.py [qNN ...]       # output: out/r2_pipeline.json
"""
from __future__ import annotations

import io
import sys

from pl import AN, DD, EX, RD, RPT, SPEAKERS, XA, brain, dump, pdfs, plan, sess_pdf, show  # isort: skip
from fu import outpath  # isort: skip  (pl puts fu on the path)

OUT = outpath("r2_pipeline.json")
EXB = brain(XA, (XA, "extract the table"))
EXP = plan((EX, "extract the table"))
DDF = [("now remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")), plan((DD, "remove the duplicate rows")))]


def partial_rules() -> bytes:
    """A frame, a rule under each row, and one column rule after the first column (Name | Qty Price)."""
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    buf = io.BytesIO()
    rows = [["Name", "Qty", "Price"], ["Apple", "10", "2.50"], ["Pear", "3", "10.00"], ["Apple", "10", "2.50"]]
    t = Table(rows, hAlign="LEFT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.black),
                           ("LINEAFTER", (0, 0), (0, -1), 0.5, colors.black)]))
    SimpleDocTemplate(buf).build([t])
    return buf.getvalue()


def scenarios():
    ships = pdfs.libreoffice_pdf([["Name", "Note", "Qty"], ["Alice", "Ships next week", ""], ["Bob", "short", "3"]])
    desk = pdfs.libreoffice_pdf([["Name", "Note", "Qty"], ["Alice", "Left at the front desk", ""], ["Bob", "short", "3"], ["Alice", "Left at the front desk", ""]])
    yield "q01-overflow-ships-next-week", ships, "extract the table", EXB, EXP, None
    yield "q02-overflow-front-desk-then-dedupe", desk, "extract the table", EXB, EXP, DDF
    yield "q03-partial-column-rules-then-dedupe", partial_rules(), "extract the table", EXB, EXP, DDF
    fruit = pdfs.libreoffice_pdf([["Name", "Qty", "Price"], ["Apple", "10", "2.50"], ["Pear", "3", "10.00"], ["Apple", "10", "2.50"]])
    yield "q04-extract-and-report", fruit, "extract the table and give me a report", brain("multi_step_request", (XA, "extract the table"), (RPT, "give me a report")), plan((AN, "give me a report"), (EX, "extract the table")), None
    yield "q05-one-data-row", pdfs.libreoffice_pdf([["Name", "Qty"], ["Apple", "10"]]), "extract the table", EXB, EXP, DDF
    yield "q06-header-only", pdfs.libreoffice_pdf([["Name", "Qty"]]), "extract the table", EXB, EXP, None


def main():
    only = sys.argv[1:]
    recs = []
    for tag, data, msg, b, p, follow in scenarios():
        if only and not any(tag.startswith(o) for o in only):
            continue
        for sp in SPEAKERS:
            s = sess_pdf(sp, data)
            recs.append(show(f"{tag}/{sp}", s.turn(msg, b, p), s))
            for fmsg, fb, fp in follow or ():
                recs.append(show(f"{tag}/{sp}/follow", s.turn(fmsg, fb, fp), s))
            s.close()
    dump(OUT, recs)


if __name__ == "__main__":
    main()
