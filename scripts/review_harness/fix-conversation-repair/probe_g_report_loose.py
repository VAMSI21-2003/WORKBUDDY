# ruff: noqa: E501
"""Loose-duplicate finding through report / analysis-only / chain paths, x3 speakers, with an independent recount from the delivered file.  python probe_g_report_loose.py 2>/dev/null"""
from cr import *  # noqa
import pandas as pd
ROWS = {"Name": ["Alice", "alice ", "Bob", "Bob", "BOB", "Carol", None, "Dan "], "Email": ["a@x.com", "a@x.com", "b@x.com", "b@x.com", "B@x.com", "c@x.com", None, "d@x.com"], "Amount": [1, 1, 2, 2, 2, 3, None, 4]}
df = pd.DataFrame(ROWS)
exact = int(df.duplicated().sum())
fold = df.copy()
for c in fold.columns: fold[c] = [x.strip().casefold() if isinstance(x, str) else x for x in fold[c]]
loose_only = int((fold.duplicated() & ~df.duplicated()).sum())
print("independent: exact dups", exact, "loose-only", loose_only, " (empty rows: 1)")
for sp in ("template", "friend", "focus"):
    s = mk(sp, [("rows.xlsx", ROWS)])
    r1 = s.turn("give me a report", bj(RPT, "give me a report", action=RPT), think_=tj())
    r2 = s.turn("remove duplicate rows and give me a report", bj("multi_step_request", extra={"requested_actions": [{"action": RD, "source_phrase": "remove duplicate rows"}, {"action": RPT, "source_phrase": "give me a report"}]}), think_=tj(), p=plan((DD, "remove duplicate rows")))
    r3 = s.turn("remove the empty rows", bj("remove_empty_rows", "remove the empty rows"), think_=tj())
    for r in (r1, r2, r3):
        print(f"[{sp}] {r['label']!r} -> {r['outcome']} delivered={r['delivered_names']} describe={r['final_describe']} uploads_unchanged={r['uploads_unchanged']}\n    {r['reply']}")
    s.close()
