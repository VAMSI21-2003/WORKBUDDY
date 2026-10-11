"""Round 3 probe: what a genuine report request that the new guards veto (`mentions_report` False) gets through the real pipeline.
Three readings by Brain: `unsupported` with the phrase it would copy, `unclear` with no entry, `conversation` with no entry.
Compare with a tree before the guards (`--tree` = a worktree of e51b2fc) and with the base.

    python r3_guards_engine.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--speakers", default="template")
ap.add_argument("--out", default=None)
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)
CASES = [  # (message, the phrase Brain copies)
    ("give me a summary so I can review it", "give me a summary"),
    ("give me a report so I can check it", "give me a report"),
    ("analyze my file so I can look at the problems", "analyze my file"),
    ("I built this file myself, can you analyze it", "analyze it"),
    ("I'm new to this myself, give me a summary of the file", "give me a summary of the file"),
    ("I will review it later, give me a summary now", "give me a summary"),
]
S = {}
for i, (msg, ph) in enumerate(CASES):
    S[f"g{i}-unsupported|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("unclear", ("unsupported", ph)))])
    S[f"g{i}-unclear|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("unclear"))])
    S[f"g{i}-conversation|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("conversation"))])
res = rq.run_all(S, (), tuple(A.speakers.split(",")))
if A.out:
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
for r in res:
    if "reply" in r:
        print(f"[{r['speaker'][:3]}] {r['name']}\n    {r['reply']}")
print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r))
