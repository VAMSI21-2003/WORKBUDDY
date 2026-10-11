"""Round 6: compare two probe outputs (JSON lists of records) turn by turn.
   python r6_cmp.py A.json B.json [--show N]   -> counts of identical/different replies, delivered flips, original hash changes, flags."""
import json, sys, collections
a = {(r["name"], r["speaker"]): r for r in json.load(open(sys.argv[1]))}
b = {(r["name"], r["speaker"]): r for r in json.load(open(sys.argv[2]))}
show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 10
same = diff = 0; dflip = []; diffs = []
for k in sorted(set(a) & set(b)):
    ra, rb = a[k], b[k]
    if "reply" not in ra or "reply" not in rb:
        if ra.get("error") != rb.get("error"): diffs.append((k, ra.get("error"), rb.get("error")))
        continue
    if ra["reply"] == rb["reply"]: same += 1
    else: diff += 1; diffs.append((k, ra["reply"], rb["reply"]))
    if ra["delivered"] != rb["delivered"]: dflip.append(k)
print("turns", len(set(a) & set(b)), "same", same, "diff", diff, "delivered flips", len(dflip), "only-A", len(set(a)-set(b)), "only-B", len(set(b)-set(a)))
chg = [(k) for d in (a, b) for k, r in d.items() if r.get("orig_unchanged") is False]
print("changed originals", chg)
print("errors A", sum("error" in r for r in a.values()), "errors B", sum("error" in r for r in b.values()))
print("flags A", collections.Counter(f for r in a.values() for f in r.get("flags", [])), "flags B", collections.Counter(f for r in b.values() for f in r.get("flags", [])))
for k, x, y in diffs[:show]:
    print("\n", k, "\n  A:", x, "\n  B:", y)
