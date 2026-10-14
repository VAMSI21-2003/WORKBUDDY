# ruff: noqa: E501
"""Library for the feat/pdf-extract pipeline probes. Reviewer tooling, not product code.

Real app (`backend.main.create_app`) + real `build_engine` (real skills, sandbox, Hands, Inspector,
Voice), only the LLM faked: imports `../feat-follow-up-turns/fu.py` (not edited), which handles
`--tree DIR`. Adds PDF uploads and an independent read of each delivered sheet's cells as openpyxl
stores them (value, data_type), so formulas and numbers show.
"""
from __future__ import annotations

import io
import json
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-follow-up-turns"))
from fu import Sess, brain, think, plan, short, dump, DD, ER, AN, RD, RE, RPT  # noqa: E402,F401
import fu  # noqa: E402

sys.path.insert(0, str(HERE))
import pdfs  # noqa: E402

EX = "pdf.extract_table"
XA = "extract_pdf_table"
SPEAKERS = ("template", "friend", "focus")


def cells_of(content: bytes):
    import openpyxl
    wb = openpyxl.load_workbook(io.BytesIO(content), data_only=False)
    out = {}
    for ws in wb.worksheets:
        out[ws.title] = [[(c.value, c.data_type) for c in row] for row in ws.iter_rows()]
    return out


def delivered_cells(s: Sess, r: dict):
    """Re-download each delivered file of turn r and read its cells with types."""
    body = s.app.client.get(f"/api/v1/sessions/{s.sid}/turns/{r['turn_id']}").json()
    got = []
    for d in body.get("delivered_files") or []:
        resp = s.app.client.get(d["download_url"])
        got.append({"name": d["name"], "cells": cells_of(resp.content) if resp.status_code == 200 else resp.status_code})
    return got


def fixture_pdf(builder, *args, **kw) -> bytes:
    sys.path.insert(0, str(fu.ROOT))
    import tests.pdf_fixtures as pf
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "x.pdf"
        getattr(pf, builder)(p, *args, **kw)
        return p.read_bytes()


def sess_pdf(speaker: str, data: bytes, name: str = "report.pdf") -> Sess:
    return Sess(speaker, data=data, filename=name)


def show(tag: str, r: dict, s: Sess | None = None):
    rec = {"tag": tag, **short(r), "detail_steps": r.get("detail_steps"), "plans": r.get("plans"),
           "outcome": r.get("outcome")}
    if s is not None and r.get("turn_id"):
        rec["cells"] = delivered_cells(s, r)
    print(json.dumps({k: rec.get(k) for k in ("tag", "outcome", "reply", "delivered_names", "uploads_unchanged", "voice_calls", "detail_steps")}, ensure_ascii=False, default=str))
    if rec.get("cells"):
        print("   cells:", json.dumps(rec["cells"], ensure_ascii=False, default=str)[:600])
    return rec
