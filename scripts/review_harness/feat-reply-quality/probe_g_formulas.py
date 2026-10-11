"""Round-1 probe G: the values-only (formula) sentences, singular and plural, head vs base."""
import sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
tree = sys.argv[sys.argv.index("--tree")+1] if "--tree" in sys.argv else str(HERE.parents[2])
import rq
rq.setup(tree)
from rq import brain
fx = rq.T["fx"]
DUP = brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))
def saved(p): fx.formatted_formula_sheet(p, saved=True)
def unsaved(p): fx.formatted_formula_sheet(p, saved=False)
S = {
 "g1-formulas-saved": dict(file=saved, turns=[dict(msg="remove the duplicate rows", b=DUP)]),
 "g2-formulas-unsaved": dict(file=unsaved, turns=[dict(msg="remove the duplicate rows", b=DUP)]),
}
for r in rq.run_all(S, (), ("template", "focus")):
    print(r["name"], r["speaker"], r.get("error") or r["reply"][:700])
