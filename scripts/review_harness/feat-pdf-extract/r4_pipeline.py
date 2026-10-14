# ruff: noqa: E501
"""Round 4: PDF turns through the real app (fake LLM only), three speakers.

    python r4_pipeline.py [tNN ...]       # output: out/r4_pipeline.json
t01: a note with one bold word, written `/F 1 Tf` + scaled `Tm` (macOS Quartz's way), overflowing
into the empty next cell, every other cell one word; then "now remove the duplicate rows".
t02: an ordinary ruled table with two-word cells written the same way. t03: review round 2's
unruled-columns table drawn in tenths of a point (`0.1 cm`, `100 Tf`). t04: the same t02 table
written with `10 Tf` (control). t05: the round-3 headline cases still right (dashes, FRUIT).
"""
from __future__ import annotations

import sys

from pl import DD, EX, RD, SPEAKERS, XA, brain, dump, plan, sess_pdf, show  # isort: skip
from fu import outpath  # isort: skip  (pl puts fu on the path)
import pdfs  # isort: skip
import r4_tf  # isort: skip

OUT = outpath("r4_pipeline.json")
EXB = brain(XA, (XA, "extract the table"))
EXP = plan((EX, "extract the table"))
DDB = brain(RD, (RD, "remove the duplicate rows"))
DDP = plan((DD, "remove the duplicate rows"))


def scenarios():
    rows = [["Name", "Note", "Qty"], ["*Urgent* reorder", "", "10"], ["Bob", "ok", "3"], ["Bob", "ok", "3"]]
    yield "t01-tm-overflow", r4_tf.table_pdf(rows, [38, 80, 60], writing="tm"), [("extract the table", EXB, EXP), ("now remove the duplicate rows", DDB, DDP)]
    yield "t02-tm-ordinary", r4_tf.table_pdf(r4_tf.FRUIT, [100, 100, 60], writing="tm"), [("extract the table", EXB, EXP)]
    g = list(r4_tf.group_unruled())
    yield "t03-cm-unruled", next(p for c, p, _ in g if c == "unruled/cm/10"), [("extract the table", EXB, EXP)]
    yield "t04-tf-ordinary", r4_tf.table_pdf(r4_tf.FRUIT, [100, 100, 60], writing="tf"), [("extract the table", EXB, EXP)]
    yield "t05-dashes", pdfs.grid_pdf([["Shop", "Open"], ["North", "Mon - Fri"], ["South", "Sat"]]), [("extract the table", EXB, EXP)]


def main():
    only = sys.argv[1:]
    recs = []
    for tag, data, turns in scenarios():
        if only and not any(tag.startswith(o) for o in only):
            continue
        for sp in SPEAKERS:
            s = sess_pdf(sp, data)
            for k, (msg, b, p) in enumerate(turns):
                recs.append(show(f"{tag}/{sp}/t{k + 1}", s.turn(msg, b, p), s))
            s.close()
    dump(OUT, recs)


if __name__ == "__main__":
    main()
