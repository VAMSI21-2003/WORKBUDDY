"""Round 5: is the exception text in a stopped-chain reply (L11, pre-existing) the same on the base?
Runs "remove the duplicate rows" with pandas.read_excel failing on call N (1..6), on a tree (head or base).

    python r5_l11.py [--tree DIR]
"""
import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import brain  # noqa: E402

DUP = ("remove_duplicate_rows", "remove the duplicate rows")
S = {}
for n in range(1, 7):
    S[f"L11-read-fails-call-{n}"] = dict(
        file="plain", patch=r5lib.read_excel_raises({n}), secrets=("TOPSECRET", "boom", "/secret/"),
        turns=[dict(msg="remove the duplicate rows", b=brain("remove_duplicate_rows", DUP))])
res = rq.run_all(S, (), ("template",))
for r in res:
    leak = any(x in (r.get("reply") or "") for x in ("TOPSECRET", "boom", "/secret/"))
    print(r["name"], "dlv", r.get("delivered"), "LEAK" if leak else "clean")
    if leak:
        print("    ", r["reply"][:300])
