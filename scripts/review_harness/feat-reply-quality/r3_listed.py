"""Round 3 probe: PURE report requests (nothing else asked) that contain a word from the head's closed list of "other work"
(`buddy.outcome._OTHER_REQUEST_WORDS`: remove, clean, fix, correct, add, save, drop, post, replace, share, ...), because the report is
ABOUT that work ("a report on which rows to remove", "summary of what you cleaned"). The list was built to hold no word a report is
described with; this checks that. As Brain's `unsupported` phrase (copied whole), alone and after "remove the duplicate rows and ...".

Class: FALSE-CANT = "Report on" in the reply AND ("can't do" | "Partly done" | "didn't run")    OK = report and none of those.

    python r3_listed.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
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
ap.add_argument("--speakers", default="template")
ap.add_argument("--out", default=str(HERE / "out" / "r3-listed.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)

PURE = [
    "give me a report on which rows to remove",
    "analyze my file and tell me what needs to be fixed",
    "give me a report on what needs cleaning",
    "give me a summary of what you cleaned",
    "give me a summary of the rows you removed",
    "report on the duplicates that were removed",
    "give me a report on how clean the data is",
    "analyze the data and show me what I should clean up",
    "give me a report on any errors that need fixing",
    "summarize the problems I should correct",
    "report on the rows I could delete",
    "analyze my file so I know what to fix",
    "give me a report so I can decide what to drop",
    "report on which columns I should add",
    "summary of the problems before I save it",
    "analyze my file before I clean it",
    "report on trailing spaces to trim",
    "report on cells that were replaced",
    "give me a summary of everything that was dropped",
    "analysis of what needs to be removed",
    "check how clean my data is",
    "give me a report on what you fixed",
    "report of the changes you made and what you removed",
    "give me a quick report on rows that could be deleted",
    "summary of what to correct",
    "analyze my file and list the issues to fix",
    "what needs fixing in my file",
    "report on the data I can safely remove",
    "summarize which rows were added",
    "report on the pasted rows",  # "post"? no: control
    "give me a report on the colour of the cells",  # colour: listed; ordinary description
    "give me a report on the dashboard sheet",  # dashboard listed; name of a sheet
    "analyze the forecast sheet",  # forecast listed; name of a sheet
    "give me a summary of the schedule column",  # schedule listed
    "give me a report on the hidden rows",  # hide: listed as hide(s|d|ed|ing) - "hidden" is NOT matched
    "give me a report on the merged cells",  # merge listed -> merged
    "report on the split names",  # split listed
    "summarize the converted dates",  # convert
    "give me a report on the printed rows",  # print
    "give me a report on the filtered rows",  # filter
    "report on the sorted order of the file",  # sort
    "analyze the shared sheet",  # share
    "report on the frozen panes",  # freeze not matched (frozen)
    "give me a report on the bold headers",  # bold
]
S = {}
for i, ph in enumerate(PURE):
    S[f"l{i:02d}-alone|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))])
    S[f"l{i:02d}-dedupe|{ph}"] = dict(file="plain", turns=[dict(
        msg=f"remove the duplicate rows and {ph}",
        b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])


def classify(rep):
    rep = rep or ""
    report = "Report on" in rep
    cant = ("can't do" in rep) or ("Partly done" in rep) or ("didn't run" in rep)
    if report and cant:
        return "FALSE-CANT"
    if report:
        return "OK"
    if cant:
        return "CANT-NO-REPORT"
    return "NOREPORT"


if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["class"] = classify(r["reply"])
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    t = [r for r in res if r["speaker"] == "template" and "reply" in r]
    print(len(res), "turns;", dict(Counter(r["class"] for r in t)))
    print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r))
    by = {}
    for r in t:
        w = r["name"].split("|", 1)[1]
        by.setdefault(w, {})[r["name"].split("|")[0].split("-", 1)[1]] = r["class"]
    for w, d in by.items():
        print(f"  alone={d['alone']:15s} dedupe={d['dedupe']:15s} {w}")
