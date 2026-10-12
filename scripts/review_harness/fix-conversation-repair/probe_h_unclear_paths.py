# ruff: noqa: E501
"""Unclear-name paths where the reply is NOT the question: greeting, refused file, unsupported, no upload.  python probe_h_unclear_paths.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
import glob
def pend(s):
    ps = glob.glob(str(s.app.settings.sessions_dir / "*" / "session.json"))
    return json.loads(open(ps[0]).read()).get("pending_request", "ABSENT") if ps else "NOFILE"
for sp in ("template", "friend", "focus"):
    print("=====", sp)
    s = mk(sp, [("messy-data.xlsx", LOOSE)])
    for msg, b in (("hi, I uploaded budget.xlsx", bj("greeting")),
                   ("sort budget.xlsx by amount", bj("unsupported_request", phrase="sort budget.xlsx by amount", action="unsupported")),
                   ("thanks for budget.xlsx", bj("unclear")),
                   ("remove duplicate rows in budget.xlsx", bj(phrase="remove duplicate rows"))):
        r = s.turn(msg, b, think_=tj())
        print(f"  {msg!r}: {r['outcome']} pending={pend(s)} | {r['reply'][:260]}")
    s.close()
