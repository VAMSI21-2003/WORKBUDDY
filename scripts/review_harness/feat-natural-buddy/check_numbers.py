"""Delivered replies in a p1 output: "Removed N ..." against the original's and the copy's rows (openpyxl).

    python check_numbers.py out/p1-head-echo.json
"""
import json
import re
import sys

bad = n = 0
for t in json.load(open(sys.argv[1])):
    if not t.get("delivered"):
        continue
    n += 1
    o, f = t["orig_facts"], t["final_facts"]
    said = sum(int(x) for x in re.findall(r"[Rr]emoved (\d+)", t["reply"]))
    if said != o["rows"] - f["rows"]:
        bad += 1
        print("MISMATCH", t["name"], t["speaker"], t["turn"], said, o, f)
print(f"{n} delivered turns, {bad} with a removed count that doesn't match the files")
