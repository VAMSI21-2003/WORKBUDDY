"""Round 2 probe W: report wordings that are NOT in round 1's list of 34 (the writer fitted the pattern to those).
Each wording goes to the real pipeline as Brain's `unsupported` entry (what Brain's prompt tells it to do with "a report"),
alone and after "remove the duplicate rows and ...". Classified automatically:

  CONTRADICTION  the reply says "Report on" (delivered) and also "can't do" / "mentions" / "didn't run" about the same request
  CANT+FINDINGS  the reply says "can't do" and shows analysis findings anyway ("Here's what I noticed ...")
  OK             "Report on" and no can't-do
  NOREPORT       no "Report on" and no can't-do (neither)

    python r2_wordings.py [--tree DIR] [--out FILE] [--speakers template,friend,focus] [--set NAME]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--out", default=str(HERE / "out" / "r2-w.json"))
ap.add_argument("--speakers", default="template")
ap.add_argument("--set", default="reports")
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import brain  # noqa: E402

REPORTS = [
    # a report on the thing this product works on (rows)
    "give me a report on the duplicate rows", "report on the empty rows", "report on duplicate and empty rows",
    "give me a report on the duplicates and empty rows", "analyze the duplicate rows", "give me a summary of the missing values",
    "summary of the data quality issues", "a report on data quality", "give me a report on columns with missing values",
    "give me a report on the file's problems", "give me a report about the problems in the file",
    "I want a summary of what's in the file", "report on everything", "a summary of everything in the file",
    # other ordinary wordings
    "analyze the first sheet", "analyze my data and tell me what's wrong", "I'd like a summary please", "provide a summary of the file",
    "check my file for duplicates", "give me a breakdown of the file", "find problems in my data", "scan the file for problems",
    "look for problems in the file", "tell me what you find", "give me stats on the file", "audit the file",
    "what does the file contain", "describe the file", "show me the file's problems",
    # shapes that worked in round 1 (control)
    "analyze my file", "give me a report on this file", "summarize the file",
]
S = {}
for i, ph in enumerate(REPORTS):
    S[f"w{i:02d}-alone|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))])
    msg = f"remove the duplicate rows and {ph}"
    S[f"w{i:02d}-dedupe|{ph}"] = dict(file="plain", turns=[dict(
        msg=msg, b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])


def classify(rep):
    rep = rep or ""
    report = "Report on" in rep
    cant = ("can't do" in rep) or ("Your message mentions" in rep) or ("though I read it as part" in rep) or ("didn't run" in rep)
    noticed = "Here's what I noticed" in rep or "I also noticed" in rep or "what I noticed" in rep
    if report and cant:
        return "CONTRADICTION"
    if cant and noticed:
        return "CANT+FINDINGS"
    if cant:
        return "CANT"
    if report:
        return "OK"
    return "NOREPORT"


if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    Path(A.out).parent.mkdir(parents=True, exist_ok=True)
    for r in res:
        r["class"] = classify(r.get("reply"))
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    from collections import Counter
    print(len(res), "turns", dict(Counter(r["class"] for r in res if r["speaker"] == "template")))
    print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r))
    for r in res:
        if r["speaker"] == "template":
            print(f"{r['class']:14s} dlv={int(r['delivered'])} {r['name']}")
