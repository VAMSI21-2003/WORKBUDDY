"""P8 (round 2): true drafts the round-1 fixes refuse (cost: the template reply, not rule 1).

    python p8_overrefusal.py
"""
import nb

nb.setup("../../..")
from scenarios import S  # noqa: E402


def d(*msgs):
    return lambda prompt: {"messages": list(msgs), "facts_used": nb.fact_ids(prompt)}


VO = "heads up, the copy keeps values only."
CYR = dict(S["s01-dedupe"], name="отчёт.xlsx")
DATES = {"file": "dates", "turns": S["s01-dedupe"]["turns"]}
CASES = [
    ("cyrillic-file-name", CYR, d("removed 2 duplicate rows from отчёт.xlsx.", VO)),
    ("casual-yo", S["s01-dedupe"], d("yo, 2 duplicate rows gone from report.xlsx, 4 rows left.", VO)),
    ("sort-turn-amount-finding", S["s04-dedupe+sort"], d(
        "removed 2 duplicate rows from report.xlsx. can't sort by amount yet.", "btw the amount column has 1 missing value.", VO)),
]
for cid, spec, draft in CASES:
    spec = dict(spec)
    spec["turns"] = [dict(x, writer=nb.const(draft)) for x in spec["turns"]]
    tr = nb.run(spec, "friend")["turns"][-1]
    print(f"{cid:26} {'SHOWN' if tr.get('writer_shown') else 'refused->template'} orig_ok={tr.get('orig_unchanged')} "
          f"delivered={tr.get('delivered')} final={tr.get('final_facts')}")
