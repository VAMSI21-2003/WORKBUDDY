# ruff: noqa: E501
"""Round 3: PDF turns through the real app (fake LLM only), three speakers, aimed at the round-2 fixes.

    python r3_pipeline.py [sNN ...]       # output: out/r3_pipeline.json
s01-s06: the planner's new "no analysis on a non-spreadsheet" rule (report alone, report + extraction,
report on the extracted copy as a follow-up, mixed uploads). s07: an ordinary cell "Mon - Fri".
s08: a sentence drawn one word per text object, running into the empty next cell. s09: the same from
a LibreOffice Calc export (a note with one bold word, next cell empty). s10: extract, then
"extract the table and give me a report" again (the active file is now the extracted copy).
"""
from __future__ import annotations

import sys

from pl import AN, DD, EX, RD, RPT, SPEAKERS, XA, brain, dump, plan, sess_pdf, show  # isort: skip
from fu import h, outpath  # isort: skip  (pl puts fu on the path)
import pdfs  # isort: skip
import r3_layout  # isort: skip
import r3_lo_rich  # isort: skip

OUT = outpath("r3_pipeline.json")
EXB = brain(XA, (XA, "extract the table"))
EXP = plan((EX, "extract the table"))
RPTB = brain(RPT, (RPT, "give me a report"))
RPTP = plan((AN, "give me a report"))
DDRB = brain("multi_step_request", (RD, "remove the duplicate rows"), (RPT, "give me a report"))
DDRP = plan((DD, "remove the duplicate rows"), (AN, "give me a report"))
FRUIT = [["Name", "Qty", "Price"], ["Apple", "10", "2.50"], ["Pear", "3", "10.00"], ["Apple", "10", "2.50"]]


def scenarios():
    fruit = pdfs.grid_pdf(FRUIT)
    # (tag, pdf bytes, [(message, brain, plan)], extra upload before/after the pdf)
    yield "s01-report-alone-on-pdf", fruit, [("give me a report on this", RPTB, RPTP)], None
    yield "s02-extract-then-report-user-order", fruit, [(
        "extract the table and then give me a report",
        brain("multi_step_request", (XA, "extract the table"), (RPT, "give me a report")),
        plan((EX, "extract the table"), (AN, "give me a report")))], None
    yield "s03-extract-then-followup-report", fruit, [("extract the table", EXB, EXP), ("now give me a report", RPTB, RPTP)], None
    yield "s04-extract-then-followup-dedupe-and-report", fruit, [("extract the table", EXB, EXP),
                                                                 ("remove the duplicate rows and give me a report", DDRB, DDRP)], None
    yield "s05-xlsx-then-pdf-uploaded-dedupe-and-report", fruit, [("remove the duplicate rows and give me a report", DDRB, DDRP)], "xlsx-first"
    yield "s06-pdf-then-xlsx-uploaded-dedupe-and-report", fruit, [("remove the duplicate rows and give me a report", DDRB, DDRP)], "xlsx-after"
    monfri = pdfs.grid_pdf([["Shop", "Open"], ["North", "Mon - Fri"], ["South", "Sat"]])
    yield "s07-mon-fri-cell", monfri, [("extract the table", EXB, EXP)], None
    rows = r3_layout._overflow_rows("Paid in full on delivery")
    split = r3_layout.drawn(rows + [["Bob", "short", "3"]], [50, 80, 60], unit="wordsp")
    yield "s08-word-objects-overflow", split, [("extract the table", EXB, EXP),
                                              ("now remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")), plan((DD, "remove the duplicate rows")))], None
    lo = r3_lo_rich.calc_pdf([["Name", "Note", "Qty"], ["Alice", ("Needs ", "manager", " approval"), ""], ["Bob", "short", "3"]], 14, True)
    yield "s09-lo-rich-overflow", lo, [("extract the table", EXB, EXP)], None
    both = ("extract the table and give me a report", brain("multi_step_request", (XA, "extract the table"), (RPT, "give me a report")),
            plan((EX, "extract the table"), (AN, "give me a report")))
    yield "s10-extract-twice-with-report", fruit, [("extract the table", EXB, EXP), both], None


def main():
    only = sys.argv[1:]
    recs = []
    for tag, data, turns, extra in scenarios():
        if only and not any(tag.startswith(o) for o in only):
            continue
        for sp in SPEAKERS:
            if extra == "xlsx-first":
                s = sess_pdf(sp, h.xlsx(h.DUP_ROWS), name="sales.xlsx")
                s.upload(data, "report.pdf")
            else:
                s = sess_pdf(sp, data)
                if extra == "xlsx-after":
                    s.upload(h.xlsx(h.DUP_ROWS), "sales.xlsx")
            for k, (msg, b, p) in enumerate(turns):
                rec = show(f"{tag}/{sp}/t{k + 1}", s.turn(msg, b, p), s)
                rec["active_file_id"] = s.log[-1].get("active_file_id")
                recs.append(rec)
            s.close()
    dump(OUT, recs)


if __name__ == "__main__":
    main()
