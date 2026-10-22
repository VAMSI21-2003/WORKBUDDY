"""Compare p1 outputs: head vs base, and urgent/frustrated vs calm-focus within head.
    python cmp1.py out/p1-head.json out/p1-base.json
"""
import json, sys, re
head = json.load(open(sys.argv[1])); base = json.load(open(sys.argv[2]))
key = lambda r: (r["name"], r["state"], r["speaker"])
H = {key(r): r for r in head}; B = {key(r): r for r in base}
def rep(r): return r.get("reply") if "error" not in r else "ERR " + r["error"]
print("== head vs base, calm/absent states (must be identical)")
bad = 0
for (n, s, sp), r in H.items():
    if s in ("calm", "absent"):
        o = B[(n, "calm", sp)]
        if rep(r) != rep(o) or r.get("delivered") != o.get("delivered"):
            bad += 1; print("DIFF", n, s, sp, "\n  head:", rep(r), "\n  base:", rep(o))
print("diffs:", bad)
print("== urgent/frustrated vs calm focus (head); template must equal calm template")
bad = 0
for (n, s, sp), r in H.items():
    if s in ("urgent", "frustrated"):
        ref = H[(n, "calm", "focus" if sp != "template" else "template")]
        if rep(r) != rep(ref):
            bad += 1; print("DIFF", n, s, sp, "\n  urgent:", rep(r), "\n  ref   :", rep(ref))
print("diffs:", bad)
print("== flags / orig changed / errors")
for r in head:
    if r.get("flags") or r.get("orig_unchanged") is False or "error" in r:
        print(key(r), r.get("flags"), r.get("orig_unchanged"), r.get("error"))
