"""Round 5: summary checks of a shared-harness head/base pair (harness.py, rereview3.py, round5.py): file handed back in the same
turns, original unchanged, voice calls, forbidden text.   python r5_harness_check.py base.json head.json"""
import json, re, sys
b = json.load(open(sys.argv[1])); h = json.load(open(sys.argv[2]))
key = lambda r: (r.get("name"), r.get("speaker"), r.get("turn", 0), r.get("sid"))
bm = {key(r): r for r in b}; hm = {key(r): r for r in h}
common = [k for k in hm if k in bm]
print("turns head", len(h), "base", len(b), "common", len(common))
print("delivered differs:", [k for k in common if bool(hm[k].get("delivered")) != bool(bm[k].get("delivered"))][:10])
print("orig changed head:", sum(1 for r in h if r.get("orig_unchanged") is False), "base:", sum(1 for r in b if r.get("orig_unchanged") is False))
print("voice calls head:", sum(r.get("voice_calls") or 0 for r in h), "base:", sum(r.get("voice_calls") or 0 for r in b))
rep = lambda r: r.get("reply") or ""
for lab, rx in [("(s)", r"\(s\)"), ("verifier", r"verifier"), ("capability id", r"\bspreadsheet\.[a-z_]+"), ("'Report on'", r"Report on"), ("'can't do'", r"can't do")]:
    print(f"{lab}: head {sum(1 for r in h if re.search(rx, rep(r), re.I))} base {sum(1 for r in b if re.search(rx, rep(r), re.I))}")
print("errors head:", sum(1 for r in h if r.get('error')), "base:", sum(1 for r in b if r.get('error')))
