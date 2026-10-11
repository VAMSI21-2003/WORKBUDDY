"""Round 2 probe F: report wordings the fix was NOT fitted to (not in round 1's 34, not in r2_wordings.py).

Each wording is typed the way a person would, Brain returns it as `unsupported` (its prompt names "a report" as the example),
copied character-for-character from the message (Brain's own rule), alone and after "remove the duplicate rows and ...".
Some are whole messages ("Could you please analyze my spreadsheet?" -> phrase "analyze my spreadsheet"), as Brain copies a part.

Classes (template speaker; the other two are checked for the same text):
  CONTRADICTION  "Report on" and a "can't do" / "mentions ... didn't run" / "Partly done" sentence in the same reply
  CANT+FINDINGS  "can't do" and the findings shown anyway ("Here's what I noticed" / "I also noticed")
  OK             "Report on" and no can't-do

    python r2_fresh_wordings.py [--tree DIR] [--out FILE] [--speakers template,friend,focus]
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--out", default=str(HERE / "out" / "r2-f.json"))
ap.add_argument("--speakers", default="template")
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import brain  # noqa: E402

# (message, the part of it Brain copies as the unsupported phrase)
CASES = [
    ("Could you please analyze my spreadsheet?", "analyze my spreadsheet"),
    ("Please give me a summary of this file.", "give me a summary of this file"),
    ("I'd like a report on this spreadsheet", "I'd like a report on this spreadsheet"),
    ("Can you analyze the data for me?", "analyze the data for me"),
    ("Analyze my sales data", "Analyze my sales data"),
    ("give me a quick summary", "give me a quick summary"),
    ("summary of this sheet please", "summary of this sheet please"),
    ("Generate a report", "Generate a report"),
    ("Create a summary report", "Create a summary report"),
    ("Prepare a data quality report", "Prepare a data quality report"),
    ("report", "report"),
    ("report please", "report please"),
    ("I want to see a report", "I want to see a report"),
    ("Please analyze", "analyze"),
    ("give me a detailed analysis of the spreadsheet", "give me a detailed analysis of the spreadsheet"),
    ("run a quality check on the file", "run a quality check on the file"),
    ("check the data quality", "check the data quality"),
    ("get me a report on all issues in this file", "get me a report on all issues in this file"),
    ("analyze and summarize my file", "analyze and summarize my file"),
    ("Can you provide a report?", "provide a report"),
    ("Make a report of the missing values", "Make a report of the missing values"),
    ("give me report on missing values", "give me report on missing values"),
    ("summarise the spreadsheet", "summarise the spreadsheet"),
    ("Summarise my file for me please", "Summarise my file for me please"),
    ("i need a summary of the data", "i need a summary of the data"),
    ("I need an analysis of my spreadsheet", "I need an analysis of my spreadsheet"),
    ("show me a report of the data problems", "show me a report of the data problems"),
    ("give me an overview of the file", "give me an overview of the file"),
    ("what is wrong with my file", "what is wrong with my file"),
    ("give me a report on the duplicate rows", "give me a report on the duplicate rows"),
    ("analyze the first sheet", "analyze the first sheet"),
    ("provide a summary of the file", "provide a summary of the file"),
    ("I'd like a summary please", "I'd like a summary please"),
    ("give me a summary of the missing values", "give me a summary of the missing values"),
    ("report on everything", "report on everything"),
    ("check my file for duplicates", "check my file for duplicates"),
    ("analyze my file thoroughly", "analyze my file thoroughly"),
    ("Please write a report for me", "write a report for me"),
    ("Do a full analysis of this file", "Do a full analysis of this file"),
    ("Send me a summary of what you find", "Send me a summary of what you find"),
]

S = {}
for i, (msg, ph) in enumerate(CASES):
    assert ph in msg, (msg, ph)
    S[f"f{i:02d}-alone|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("unclear", ("unsupported", ph)))])
    msg2 = f"remove the duplicate rows and {msg}"
    S[f"f{i:02d}-dedupe|{msg}"] = dict(file="plain", turns=[dict(
        msg=msg2, b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])


def classify(rep):
    rep = rep or ""
    report = "Report on" in rep
    cant = ("can't do" in rep) or ("Your message mentions" in rep) or ("though I read it as part" in rep) or ("Partly done" in rep)
    noticed = "Here's what I noticed" in rep or "I also noticed" in rep
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
    t = [r for r in res if r["speaker"] == "template"]
    print(len(res), "turns;", dict(Counter(r["class"] for r in t)))
    print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r),
          "| voice calls:", sum(1 for r in res if r.get("voice_calls")))
    bad = Counter()
    for r in t:
        bad[(r["name"].split("-")[0], r["name"].split("|", 1)[1], r["class"])] += 1
    by_wording = {}
    for r in t:
        w = r["name"].split("|", 1)[1]
        by_wording.setdefault(w, {})[r["name"].split("|")[0].split("-", 1)[1]] = r["class"]
    for w, d in by_wording.items():
        print(f"  alone={d['alone']:14s} dedupe={d['dedupe']:14s} {w}")
