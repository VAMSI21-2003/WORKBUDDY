# ruff: noqa: E501
"""Probe D: the `dtype=object` change to the row-cleaning skills and the Inspector (design decision 8).

Workbooks whose cells differ only in type or in text that looks like a number, through the real
app, head vs base (`--tree DIR`). Records the reply, the delivered cells with their types, and the
original's hash.

    python probe_d_dtype.py [--tree BASE] [--out FILE]      # default out/probe_d.json
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-follow-up-turns"))
from fu import Sess, brain, plan, dump, outpath, DD, ER, RD, RE, RPT, AN  # noqa: E402

OUT = outpath("probe_d.json")


def book(rows):
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in rows:
        ws.append(r)
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value.startswith("'"):
                c.value = c.value[1:]
                c.data_type = "s"
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def cells(content):
    import openpyxl
    ws = openpyxl.load_workbook(io.BytesIO(content)).active
    return [[(c.value, c.data_type) for c in row] for row in ws.iter_rows()]


CASES = {
    "d1-text7-vs-number7": [["Id", "Name"], ["'7", "a"], [7, "a"], [8, "b"]],
    "d2-text007-vs-text7": [["Id", "Name"], ["'007", "a"], ["'7", "a"], ["'8", "b"]],
    "d3-int1-vs-true": [["Flag", "Name"], [1, "a"], [True, "a"], [0, "b"]],
    "d4-text-amounts-kept": [["Id", "Amt"], ["'007", "'2.50"], ["'007", "'2.50"], ["'12345678901234567890", "'1"]],
    "d5-float-vs-int": [["Id", "Amt"], [1, 2.0], [1, 2], [3, 4]],
    "d6-empty-rows-text": [["Id", "Amt"], ["'007", "'2.50"], [None, None], ["'8", "'1"]],
}
REQ = {
    "dedupe": ("remove the duplicate rows", brain(RD, (RD, "remove the duplicate rows")), plan((DD, "remove the duplicate rows"))),
    "dedupe+report": ("remove the duplicate rows and then give me a report", brain("multi_step_request", (RD, "remove the duplicate rows"), (RPT, "give me a report")), plan((DD, "remove the duplicate rows"), (AN, "give me a report"))),
    "empty": ("remove the empty rows", brain(RE, (RE, "remove the empty rows")), plan((ER, "remove the empty rows"))),
}


def main():
    recs = []
    for name, rows in CASES.items():
        for req, (msg, b, p) in REQ.items():
            if name.startswith("d6") != (req == "empty"):
                continue
            s = Sess("template", data=book(rows), filename="data.xlsx")
            r = s.turn(msg, b, p)
            body = s.app.client.get(f"/api/v1/sessions/{s.sid}/turns/{r['turn_id']}").json()
            got = [cells(s.app.client.get(d["download_url"]).content) for d in body.get("delivered_files") or []]
            rec = {"case": name, "req": req, "outcome": r.get("outcome"), "reply": r.get("reply"), "cells": got, "orig_unchanged": r.get("uploads_unchanged")}
            recs.append(rec)
            print(json.dumps(rec, default=str, ensure_ascii=False))
            s.close()
    dump(OUT, recs)


if __name__ == "__main__":
    main()
