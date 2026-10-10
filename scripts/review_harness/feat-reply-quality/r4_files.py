"""Round 4: report requests (pure, with an extra, after a dedupe) on every odd file kind of `rq.make_file`, 3 speakers.
Looks for: an exception, a changed original, a flag (completion word in an undelivered reply, "(s)", a raw id, file text), a stated count
that the independent read disagrees with (run check_numbers.py on the output).

    python r4_files.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--speakers", default="template,friend,focus")
ap.add_argument("--out", default=str(HERE / "out" / "r4-files.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)
KINDS = ["plain", "clean", "one_dup", "one_dup_noblank", "blanks", "empty_only", "one_row", "multi", "merged", "header3", "na", "password",
         "xls", "garbage", "hostile_headers", "hostile_headers2", "near_dups", "outliers", "numeric_dups", "dates", "wide_dups"]
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
S = {}
for k in KINDS:
    S[f"{k}|pure"] = dict(file=k, turns=[dict(msg="give me a report", b=brain("unclear", ("unsupported", "give me a report")))])
    S[f"{k}|extra"] = dict(file=k, turns=[dict(msg="give me a report in pdf", b=brain("unclear", ("unsupported", "give me a report in pdf")))])
    S[f"{k}|dedupe+extra"] = dict(file=k, turns=[dict(
        msg="remove the duplicate rows and give me a report in pdf",
        b=brain("remove_duplicate_rows", DUP, ("unsupported", "give me a report in pdf")))])
    S[f"{k}|negated"] = dict(file=k, turns=[dict(
        msg="give me a summary but don't delete the duplicates", b=brain("unclear", ("unsupported", "give me a summary")))])

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print("turns", len(res), "| errors:", sum(1 for r in res if "error" in r), "| original changed:", sum(1 for r in res if r.get("orig_unchanged") is False),
          "| voice calls:", sum(1 for r in res if r.get("voice_calls")))
    for r in res:
        if "error" in r:
            print("ERROR", r["speaker"], r["name"], r["error"])
        elif r["flags"]:
            print("FLAGS", r["speaker"], r["name"], r["flags"], "\n    ", r["reply"][:300])
    if "template" in A.speakers:
        for r in res:
            if r["speaker"] == "template" and "reply" in r and r["name"].split("|")[0] in ("empty_only", "hostile_headers", "merged", "header3", "na"):
                print(f"[tem] {r['name']}\n    {r['reply'][:420]}")
