"""Round-1 probe I: Brain lists an action the message never asked for (hallucinated ledger) on a no-change turn."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tree = sys.argv[sys.argv.index("--tree")+1] if "--tree" in sys.argv else str(HERE.parents[2])
import rq
rq.setup(tree)
from rq import brain
S = {
 "i1-hi-dedupe-listed": dict(file="plain", turns=[dict(msg="hi", b=brain("conversation", ("remove_duplicate_rows", "remove duplicates")))]),
 "i2-hi-dedupe-listed-unclear": dict(file="plain", turns=[dict(msg="hi", b=brain("unclear", ("remove_duplicate_rows", "remove duplicates")))]),
 "i3-thanks-unsupported-listed": dict(file="plain", turns=[dict(msg="thanks", b=brain("conversation", ("unsupported", "send an email")))]),
 "i4-hi-empty-phrase": dict(file="plain", turns=[dict(msg="hi", b=brain("conversation", ("remove_empty_rows", "")))]),
}
for r in rq.run_all(S, (), ("template", "friend")):
    print(r["name"], r["speaker"], "->", r["reply"][:420])
