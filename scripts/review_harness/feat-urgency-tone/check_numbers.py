"""Check the counts a delivered reply states against the independent openpyxl read (p1 output).
    python check_numbers.py out/p1-head.json
"""
import json, re, sys
bad = n = 0
for r in json.load(open(sys.argv[1])):
    if not r.get("delivered") or not r.get("final_facts") or not r.get("reply"):
        continue
    n += 1
    o, f, text = r["orig_facts"], r["final_facts"], r["reply"]
    m = re.search(r"The copy has (\d+) rows?", text)
    ok = bool(m) and int(m.group(1)) == f["rows"]
    d = re.search(r"removed (\d+) exact duplicate rows?", text)
    if d:
        ok &= int(d.group(1)) == o["dups"] - f["dups"] if f["dups"] == 0 else False
    e = re.search(r"[Rr]emoved (\d+) fully-empty rows?", text)
    if e:
        ok &= int(e.group(1)) == o["blank"] - f["blank"]
    if not ok:
        bad += 1
        print("MISMATCH", r["name"], r["state"], r["speaker"], o, f, text[:160])
print("checked", n, "delivered replies; mismatches:", bad)
