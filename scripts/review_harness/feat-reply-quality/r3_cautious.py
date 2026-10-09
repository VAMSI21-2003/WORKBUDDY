"""Round 3 probe: a cautious user asks for a report and says NOT to change anything. The closed list of "other work" verbs contains
remove / delete / fix / clean / drop / correct / add / save, so "don't delete anything" in the same phrase reads as a second request.
Brain returns `unsupported` with the whole phrase (it is all one request), alone and after a real dedupe request.

    python r3_cautious.py [--tree DIR] [--speakers template,friend,focus]
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
PHRASES = [
    "just analyze my file, don't delete anything",
    "give me a report but don't remove anything",
    "give me a report first, don't clean anything yet",
    "analyze my file without fixing anything",
    "give me a summary but don't change or delete the data",
    "give me a report, no need to remove anything",
    "I only want a report, please don't drop any rows",
    "report on the file but leave everything as it is",
    "give me a report and don't save anything",
    "analyze my file but do not add anything",
]
for ph in PHRASES:
    for sp in A.speakers.split(","):
        r = rq.run(dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))]), sp)["turns"][0]
        print(f"[{sp}] {ph!r}\n    {r['reply'][:330]}\n")
