"""Round 5: compare two probe outputs (head vs base) turn by turn.

    python r5_cmp.py out/a-base.json out/a-head.json [--show N]

Prints: turns, turns where "a file was handed back" differs, flags on each side, and the replies that differ grouped by the
first sentence that changed (so a reviewer reads each group once). Never prints the same group twice.
"""
import collections
import json
import sys

base, head = (json.load(open(p)) for p in sys.argv[1:3])
show = int(sys.argv[sys.argv.index("--show") + 1]) if "--show" in sys.argv else 3
bm = {(r["name"], r["speaker"], r["turn"]): r for r in base}
hm = {(r["name"], r["speaker"], r["turn"]): r for r in head}
common = [k for k in hm if k in bm]
print("turns head", len(hm), "base", len(bm), "common", len(common))
dl = [k for k in common if hm[k].get("delivered") != bm[k].get("delivered")]
print("delivered differs:", len(dl))
for k in dl[:15]:
    print("   ", k, "base", bm[k].get("delivered"), "head", hm[k].get("delivered"))
orig = [k for k in hm if hm[k].get("orig_unchanged") is False]
print("original changed on head:", len(orig), "| on base:", sum(1 for k in bm if bm[k].get("orig_unchanged") is False))
fh = collections.Counter(f.split(":")[0] for r in head for f in r.get("flags", []))
fb = collections.Counter(f.split(":")[0] for r in base for f in r.get("flags", []))
print("flags head:", dict(fh))
print("flags base:", dict(fb))
print("errors head:", sum(1 for r in head if "error" in r), "base:", sum(1 for r in base if "error" in r))
diff = [k for k in common if hm[k].get("reply") != bm[k].get("reply")]
print("replies that differ:", len(diff), "of", len(common))
groups = collections.defaultdict(list)
for k in diff:
    a, b = (bm[k].get("reply") or ""), (hm[k].get("reply") or "")
    sa = [s.strip() for s in a.replace("? ", "?. ").split(". ")]
    sb = [s.strip() for s in b.replace("? ", "?. ").split(". ")]
    only_b = [s for s in sb if s not in sa]
    only_a = [s for s in sa if s not in sb]
    groups[(tuple(only_a[:2]), tuple(only_b[:2]))].append(k)
print("distinct change groups:", len(groups))
for (oa, ob), ks in sorted(groups.items(), key=lambda kv: -len(kv[1]))[: show * 10]:
    print(f"\n--- {len(ks)} turn(s): base-only {list(oa)!r}\n                  head-only {list(ob)!r}\n    e.g. {ks[0]}")
