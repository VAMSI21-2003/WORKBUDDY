# ruff: noqa: E501
"""Probe E: PDF uploads and turns when the sandbox is busy, and odd uploads, through the real app.

    python probe_e_api.py        # output: out/probe_e.json
"""
from __future__ import annotations

import json

from pl import EX, XA, brain, dump, fixture_pdf, pdfs, plan
from fu import Sess, outpath

OUT = outpath("probe_e.json")
FRUIT = [["Name", "Qty"], ["Apple", "10"], ["Pear", "3"]]


def main():
    from skills.pdf import sandbox
    recs = []
    good = pdfs.grid_pdf(FRUIT)
    s = Sess("template", data=good, filename="report.pdf")

    def up(label, data, name):
        r = s.app.client.post(f"/api/v1/sessions/{s.sid}/files", files={"file": (name, data, "application/octet-stream")})
        rec = {"case": label, "http": r.status_code, "body": r.json()}
        print(json.dumps(rec)[:400])
        recs.append(rec)

    up("garbage-after-magic", b"%PDF-1.4\n" + b"\x00garbage" * 100, "g.pdf")
    up("pdf-bytes-named-xlsx", good, "g.xlsx")
    up("xlsx-bytes-named-pdf", __import__("fu").pr3.h.xlsx(__import__("fu").DEF), "g.pdf")
    up("21-pages", fixture_pdf("write_table_pdf", text_pages_after=20), "long.pdf")
    # Every worker place taken (two long turns elsewhere in the server): an upload, then a turn.
    sandbox.WAIT_SECONDS = 1.0
    assert sandbox._places.acquire(timeout=1) and sandbox._places.acquire(timeout=1)
    try:
        up("upload-while-busy", good, "busy.pdf")
        r = s.turn("extract the table", brain(XA, (XA, "extract the table")), plan((EX, "extract the table")))
        rec = {"case": "turn-while-busy", "outcome": r.get("outcome"), "reply": r.get("reply"), "uploads_unchanged": r.get("uploads_unchanged")}
        print(json.dumps(rec)[:600])
        recs.append(rec)
    finally:
        sandbox._places.release()
        sandbox._places.release()
    s.close()
    dump(OUT, recs)


if __name__ == "__main__":
    main()
