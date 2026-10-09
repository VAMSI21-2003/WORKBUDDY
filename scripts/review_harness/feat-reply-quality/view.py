"""Print replies of a probe output JSON: python view.py out/a-head.json [prefix ...] [--speakers template,friend,focus] [--base out/a-base.json]"""
import json, sys
args = sys.argv[1:]
speakers = ("template", "friend", "focus")
base = None
if "--speakers" in args:
    i = args.index("--speakers"); speakers = tuple(args[i+1].split(",")); del args[i:i+2]
if "--base" in args:
    i = args.index("--base"); base = json.load(open(args[i+1])); del args[i:i+2]
res = json.load(open(args[0])); pref = args[1:]
bmap = {(r["name"], r["speaker"], r["turn"]): r for r in (base or [])}
cur = None
for r in res:
    if pref and not any(r["name"].startswith(p) for p in pref): continue
    if r["speaker"] not in speakers: continue
    if r["name"] != cur:
        cur = r["name"]; print(f"\n== {cur}")
    if "error" in r:
        print(f"  [{r['speaker'][:3]}] t{r['turn']} ERROR {r['error']}"); continue
    steps = ",".join(f"{c}:{s}" for c, s in r["steps"])
    fl = [f for f in r["flags"]]
    print(f"  [{r['speaker'][:3]}] t{r['turn']} dlv={int(r['delivered'])} steps={steps} stop={r['stopped_at']} fail={r['failure']} flags={fl}")
    print(f"      > {r['msg']}")
    print(f"      HEAD: {r['reply']}")
    if r.get('final_facts'): print(f"      final={r['final_facts']} orig={r['orig_facts']}")
    b = bmap.get((r["name"], r["speaker"], r["turn"]))
    if b is not None and "reply" in b:
        print(f"      BASE: {b['reply']}  [dlv={int(b['delivered'])}]")
