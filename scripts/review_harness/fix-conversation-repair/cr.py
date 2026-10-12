# ruff: noqa: E501
"""Library for the fix/conversation-repair review probes. Reviewer tooling, not product code.

Imports fu.py (feat-follow-up-turns) which imports pr3.py / harness.py from other branches' folders
(none edited). `--tree DIR` (consumed before product imports) probes another checkout, e.g. a worktree
of origin/main.  Real app + real build_engine, only the LLM is faked.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-follow-up-turns"))
from fu import *  # noqa: F401,F403,E402
import fu  # noqa: E402
from fu import h, pr3, Sess, Routed, brain, think, plan, short, dump, outpath, DD, ER, AN, RD, RE, RPT  # noqa: E402

# 6 rows: row 4 repeats row 1 exactly; row 3 empty; Email c@x.com linked to two Names.
MESSY = {
    "Name": ["Alice", "Bob", None, "Alice", "Carol", "Dan"],
    "Email": ["a@x.com", "b@x.com", None, "a@x.com", "c@x.com", "c@x.com"],
    "Amount": [10, 20, None, 10, 30, 40],
}
# loose duplicates: row 3 = row 1 with space/case; row 5 = exact dup of row 2
LOOSE = {
    "Name": ["Alice", "Bob", " alice ", "Bob", "Carol"],
    "Email": ["a@x.com", "b@x.com", "A@X.COM", "b@x.com", "c@x.com"],
    "Amount": [10, 20, 10, 20, 30],
}
OTHER = {"Name": ["x", "x", "y"], "Email": ["x@x.com", "x@x.com", "y@x.com"], "Amount": [1, 1, 2]}


def bj(intent="remove_duplicate_rows", phrase=None, answers=None, file=None, action=None, extra=None):
    d = {"intent": intent, "confidence": 0.9}
    if phrase is not None:
        d["requested_actions"] = [{"action": action or intent, "source_phrase": phrase}]
    if answers is not None:
        d["answers_pending"] = answers
    if file is not None:
        d["file_to_use"] = file
    if extra:
        d.update(extra)
    return json.dumps(d)


def tj(reason=None):
    d = {"objective": "o", "summary": "s", "clarification_required": reason is not None, "confidence": 0.9}
    if reason:
        d["clarification_reason"] = reason
    return json.dumps(d)


def mk(speaker, uploads, **kw):
    """Sess with several uploads: list of (name, rows-dict-or-bytes). First is Sess's orig; the rest uploaded in order."""
    (n0, d0), rest = uploads[0], uploads[1:]
    s = Sess(speaker, data=h.xlsx(d0) if isinstance(d0, dict) else d0, filename=n0, **kw)
    s.ids = {n0: s.orig_id}
    for n, d in rest:
        fid, _ = s.upload(h.xlsx(d) if isinstance(d, dict) else d, n)
        s.ids[n] = fid
    return s


def rows_of(r):
    """independent description of each delivered file"""
    return r.get("final_describe")


def outpath(default):  # override fu's: write into THIS folder's out/ (git-ignored)
    if "--out" in sys.argv:
        i = sys.argv.index("--out")
        p = Path(sys.argv[i + 1])
        del sys.argv[i:i + 2]
        return p
    return Path(__file__).resolve().parent / "out" / default
