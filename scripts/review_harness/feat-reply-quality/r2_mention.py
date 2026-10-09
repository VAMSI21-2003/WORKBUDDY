"""Round 2 probe M: report wordings that mention an action's object ("duplicate rows", "empty rows"), with Brain listing
NO action (intent unclear / conversation / a wrong intent). What does the reply say?

    python r2_mention.py [--tree DIR]
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

MSGS = ["give me a report on the duplicate rows", "report on the empty rows", "how many duplicate rows are there?",
        "analyze the duplicate rows", "tell me about the empty rows", "show me the duplicate rows",
        "what are the duplicates?", "which rows are duplicates"]
for intent in ("unclear", "conversation"):
    for m in MSGS:
        spec = dict(file="plain", turns=[dict(msg=m, b=brain(intent))])
        for sp in ("template",):
            r = rq.run(spec, sp)["turns"][0]
            print(f"[{intent}] {m!r} -> delivered={r['delivered']} steps={r['steps']}\n    {r['reply']}")
