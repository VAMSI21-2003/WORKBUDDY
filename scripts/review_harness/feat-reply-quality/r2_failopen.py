"""Round 2 probe O: what a "report + one extra thing, no connector" phrase says, on head and on the prototype fix
(out/r2-prototype-fix-for-M1.diff applied in a throwaway worktree). Shows the fail-open cost of the prototype.

    python r2_failopen.py [--tree DIR]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import brain  # noqa: E402

PHRASES = ["give me a report in pdf", "give me a report in French", "send me the report to my boss", "give me a report sorted by amount",
           "give me a report with charts", "give me a report on the file with colours", "give me a report and sort it by amount",
           "give me a report as a table in a new sheet", "give me a report in bold"]
for ph in PHRASES:
    r = rq.run(dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))]), "template")["turns"][0]
    cant = "can't do" in r["reply"]
    print(f"{ph!r:52} cant_do_stated={cant}\n    {r['reply'][:200]}")
