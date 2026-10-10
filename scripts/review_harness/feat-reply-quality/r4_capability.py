"""Round 4: capability questions that use the verb/noun of a report ("can you analyze spreadsheets?", "do you make reports?"), Brain: conversation.
The round-3 list (7 wordings with "of what you can do"/"your features") is answered with the capability line; these are not.

    python r4_capability.py [--tree DIR] [--speakers template]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--speakers", default="template")
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)
MSGS = ["can you analyze spreadsheets?", "do you make reports?", "what can you analyze?", "can you summarize a file for me?",
        "give me an overview of how this works", "give me a summary of the capabilities", "what kinds of analysis can you do?", "do you summarize files?"]
for m in MSGS:
    for sp in A.speakers.split(","):
        r = rq.run(dict(file="plain", turns=[dict(msg=m, b=brain("conversation"))]), sp)["turns"][0]
        print(f"[{sp}] {m!r}\n    {r['reply'][:200]}")
