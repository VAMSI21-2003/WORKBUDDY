"""Check the numbers a reply states against the independent openpyxl facts recorded with each turn.

    python check_numbers.py out/a-head.json [out/b-head.json ...]

Checked: "The copy has N rows", "removed N duplicate rows", "Removed N fully-empty rows" (single-step deliveries),
"It still has N duplicate rows", and every "N row(s) repeat(s) an earlier row exactly" against the file it is
about (the original for "Report on your original file" / "when I looked it over"; the copy for "Report on the copy"
and for "I also noticed" on a delivered turn).
"""
import json, re, sys
checked = bad = 0
for f in sys.argv[1:]:
    for t in json.load(open(f)):
        rep = t.get("reply") or ""
        o, c = t.get("orig_facts"), t.get("final_facts")
        if not o:
            continue
        def chk(name, claimed, actual):
            global checked, bad
            checked += 1
            if claimed != actual:
                bad += 1
                print("MISMATCH", f, t["name"], t["speaker"], name, claimed, "!=", actual, "|", rep[:160])
        if t["delivered"] and c:
            for m in re.finditer(r"The copy has (\d+) rows?", rep): chk("copy rows", int(m.group(1)), c["rows"])
            steps = [s for s, _ in t["steps"] if s != "analyze"]
            if steps == ["deduplicate"]:
                for m in re.finditer(r"[Rr]emoved (\d+) duplicate rows?", rep): chk("removed dups", int(m.group(1)), o["dups"])
                if "no duplicate rows to remove" in rep: chk("no dups to remove", 0, o["dups"])
            if steps == ["remove_empty_rows"]:
                for m in re.finditer(r"[Rr]emoved (\d+) fully-empty rows?", rep): chk("removed blanks", int(m.group(1)), o["blank"])
            for m in re.finditer(r"It still has (\d+) duplicate rows?", rep): chk("still dups", int(m.group(1)), c["dups"])
            for m in re.finditer(r"(?:Report on the copy: |I also noticed: )(\d+) rows? repeats? an earlier row exactly", rep): chk("copy dups finding", int(m.group(1)), c["dups"])
        else:
            for m in re.finditer(r"(?:Report on your original file: |when I looked it over: )(\d+) rows? repeats? an earlier row exactly", rep): chk("orig dups finding", int(m.group(1)), o["dups"])
print("numbers checked:", checked, "mismatches:", bad)
