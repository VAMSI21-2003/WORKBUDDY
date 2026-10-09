"""Round 3 probe: report requests the round-3 guards (N2) no longer read as reports, through the real pipeline, plus the
mention sentence beside a report on empty rows. Brain returns `unsupported` with the narrow phrase a model would copy.

    python r3_fn.py [--tree DIR] [--speakers template,friend,focus]
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
CASES = [  # (message, phrase Brain copies, brain intent)
    ("give me a summary so I can review it", "give me a summary", "unclear"),
    ("I can't check the file myself, give me a report", "give me a report", "unclear"),
    ("please don't forget to send me a summary", "send me a summary", "unclear"),
    ("analyze my file; I can fix the rest myself", "analyze my file", "unclear"),
    ("summarize the file so I can look at the problems", "summarize the file", "unclear"),
    ("give me a report on the empty rows", "give me a report on the empty rows", "unclear"),
    ("check my file for blank rows", "check my file for blank rows", "unclear"),
]
for msg, ph, intent in CASES:
    for sp in A.speakers.split(","):
        r = rq.run(dict(file="plain", turns=[dict(msg=msg, b=brain(intent, ("unsupported", ph)))]), sp)["turns"][0]
        print(f"[{sp}] {msg!r} (Brain phrase {ph!r}) orig_ok={r['orig_unchanged']} flags={r['flags']}\n    {r['reply']}\n")
