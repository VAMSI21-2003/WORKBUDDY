"""Compare two p1 outputs turn by turn: reply, delivered, steps, final facts.

    python cmp1.py out/p1-head-raise.json out/p1-base.json
"""
import json
import sys

a = {(t["name"], t["speaker"], t["turn"]): t for t in json.load(open(sys.argv[1]))}
b = {(t["name"], t["speaker"], t["turn"]): t for t in json.load(open(sys.argv[2]))}
diff = 0
for k in sorted(a):
    x, y = a[k], b.get(k)
    for f in ("reply", "delivered", "steps", "final_facts", "orig_unchanged"):
        if y is None or x.get(f) != y.get(f):
            diff += 1
            print(k, f, "\n  A:", repr(x.get(f))[:400], "\n  B:", repr(y.get(f) if y else None)[:400])
print("turns", len(a), "diffs", diff)
