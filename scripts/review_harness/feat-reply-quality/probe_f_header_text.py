"""Round-1 probe F: file text (a column header) in a reply, head vs base. Pre-existing class (backlog P1)."""
import sys, json
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tree = sys.argv[sys.argv.index("--tree")+1] if "--tree" in sys.argv else str(HERE.parents[2])
import rq
rq.setup(tree)
from rq import brain
DUP = brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))
S = {
 "f1-dedupe": dict(file="hostile_headers2", turns=[dict(msg="remove the duplicate rows", b=DUP)]),
 "f2-report": dict(file="hostile_headers2", turns=[dict(msg="give me a report", b=brain("unclear", ("unsupported", "give me a report")))]),
 "f3-unclear": dict(file="hostile_headers2", turns=[dict(msg="make it nicer", b=brain("unclear"))]),
}
res = rq.run_all(S, (), ("template", "friend"))
for r in res:
    print(r["name"], r["speaker"], "->", r["reply"][:420])
