# ruff: noqa: E501
"""Probe B: PDF turns through the real app, three speakers. Output: out/probe_b.json.

    python probe_b_pipeline.py [scenario-prefix ...] [--speakers template,friend,focus]
"""
from __future__ import annotations

import json
import sys

from pl import (AN, DD, EX, RD, RPT, SPEAKERS, XA, brain, dump, fixture_pdf, pdfs, plan, sess_pdf, show)
from fu import Sess, outpath
import fu

OUT = outpath("probe_b.json")
FRUIT = [["Name", "Qty", "Price"], ["Apple", "10", "2.50"], ["Pear", "3", "10.00"], ["Apple", "10", "2.50"], ["Plum", "7", "1.25"]]
EXB = brain(XA, (XA, "extract the table"))
EXP = plan((EX, "extract the table"))


def one(tag, speaker, data, msg, b, p, follow=None, name="report.pdf", extra_uploads=()):
    s = sess_pdf(speaker, data, name)
    for d, n in extra_uploads:
        s.upload(d, n)
    recs = [show(f"{tag}/{speaker}", s.turn(msg, b, p), s)]
    for fmsg, fb, fp in follow or ():
        recs.append(show(f"{tag}/{speaker}/follow", s.turn(fmsg, fb, fp), s))
    s.close()
    return recs


def scenarios():
    lo_last = pdfs.libreoffice_pdf([["Name", "Comment"], ["Alice", "Paid in full on delivery"], ["Bob", "Pending"]])
    lo_next = pdfs.libreoffice_pdf([["Name", "Note", "Qty"], ["Alice", "a fairly long note that will not fit", ""], ["Bob", "short", "3"]])
    nfkc = pdfs.canvas_grid([["Size", "Area"], ["2½", "12 m²"], ["¼", "3³"]])
    formula = pdfs.canvas_grid([["Name", "F"], ["a", "=1+1"], ["a", "=1+1"], ["c", "x"]])
    landscape = pdfs.canvas_grid([["Name", "Qty"], ["Apple", "10"], ["Pear", "3"]], rotate_page=90, rotate_content=True)
    fruit = pdfs.libreoffice_pdf(FRUIT)
    xlsx = fu.pr3.h.xlsx(fu.DEF)
    dd_follow = [("now remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")), plan((DD, "remove the duplicate rows")))]
    yield "p01-happy-lo", fruit, "extract the table", EXB, EXP, dd_follow, {}
    yield "p02-overflow-last-col", lo_last, "extract the table", EXB, EXP, None, {}
    yield "p03-overflow-into-empty", lo_next, "extract the table", EXB, EXP, None, {}
    yield "p04-nfkc", nfkc, "extract the table", EXB, EXP, None, {}
    yield "p05-formula-text-then-dedupe", formula, "extract the table", EXB, EXP, dd_follow, {}
    yield "p06-landscape", landscape, "extract the table", EXB, EXP, None, {}
    yield "p07-scan", fixture_pdf("write_scanned_pdf"), "extract the table", EXB, EXP, None, {}
    yield "p08-two-tables", fixture_pdf("write_two_tables_pdf"), "extract the table", EXB, EXP, None, {}
    yield "p09-encrypted-empty-pw", fixture_pdf("write_table_pdf", password=("", "owner")), "extract the table", EXB, EXP, None, {}
    yield "p10-merged", fixture_pdf("write_table_pdf", spans=[((0, 1), (1, 1))]), "extract the table", EXB, EXP, None, {}
    yield "p11-brain-says-dedupe-on-pdf", fruit, "remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")), plan((DD, "remove the duplicate rows")), None, {}
    yield "p12-extract-then-dedupe-one-msg", fruit, "extract the table and then remove the duplicate rows", brain("multi_step_request", (XA, "extract the table"), (RD, "remove the duplicate rows")), plan((EX, "extract the table"), (DD, "remove the duplicate rows")), None, {}
    yield "p13-extract-and-report", fruit, "extract the table and give me a report", brain("multi_step_request", (XA, "extract the table"), (RPT, "give me a report")), plan((AN, "give me a report"), (EX, "extract the table")), None, {}
    yield "p14-report-only-on-pdf", fruit, "give me a report on this file", brain(RPT, (RPT, "give me a report")), plan((AN, "give me a report")), None, {}
    yield "p15-extract-on-xlsx", xlsx, "extract the table", EXB, EXP, None, {"name": "data.xlsx"}
    yield "p16-extract-twice", fruit, "extract the table", EXB, EXP, [("extract the table again", EXB, EXP)], {}
    yield "p17-planner-omits-extract", fruit, "extract the table", EXB, plan((AN, "extract the table")), None, {}
    yield "p18-brain-unclear", fruit, "extract the table", brain("unclear"), EXP, None, {}
    yield "p19-xlsx-active-pdf-named", fruit, "extract the table from report.pdf", EXB, EXP, None, {"extra": [(xlsx, "data.xlsx")]}
    import io
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
    buf = io.BytesIO()
    t = Table([r[:] for r in FRUIT], hAlign="LEFT")
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.black), ("LINEBELOW", (0, 0), (-1, -1), 0.5, colors.black)]))
    SimpleDocTemplate(buf).build([t])
    yield "p21-frame-and-row-rules-only", buf.getvalue(), "extract the table", EXB, EXP, dd_follow, {}
    yield "p20-unsupported-extra", fruit, "extract the table and email it to Bob", brain(XA, (XA, "extract the table"), ("unsupported", "email it to Bob")), EXP, None, {}


def main():
    only = [a for a in sys.argv[1:] if not a.startswith("--")]
    speakers = SPEAKERS
    if "--speakers" in sys.argv:
        speakers = tuple(sys.argv[sys.argv.index("--speakers") + 1].split(","))
        only = [a for a in only if a != sys.argv[sys.argv.index("--speakers") + 1]]
    recs = []
    for tag, data, msg, b, p, follow, opts in scenarios():
        if only and not any(tag.startswith(o) for o in only):
            continue
        for sp in speakers:
            recs += one(tag, sp, data, msg, b, p, follow, name=opts.get("name", "report.pdf"), extra_uploads=opts.get("extra", ()))
    dump(OUT, recs)


if __name__ == "__main__":
    main()
