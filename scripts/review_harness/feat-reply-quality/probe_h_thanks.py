"""Round-1 probe H: thanks/praise that contains a report noun, after a delivered turn."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tree = sys.argv[sys.argv.index("--tree")+1] if "--tree" in sys.argv else str(HERE.parents[2])
import rq
rq.setup(tree)
from rq import brain
DUP = brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))
CONV = brain("conversation")
S = {}
for i, m in enumerate(["thanks for the summary!", "great analysis, thanks", "what does your report cover?", "Where can I find the report?", "I'll read the summary later"]):
    S[f"h{i}"] = dict(file="plain", turns=[dict(msg="remove the duplicate rows", b=DUP), dict(msg=m, b=CONV)])
for r in rq.run_all(S, (), ("friend",)):
    if r["turn"] == 1:
        print(r["name"], repr(r["msg"]), "->", r["reply"][:300])
