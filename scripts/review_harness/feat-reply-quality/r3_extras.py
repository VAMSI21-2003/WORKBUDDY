"""Round 3 probe: a report request plus ONE extra that Buddy cannot do, in the words a person would use,
as Brain's `unsupported` phrase (copied whole), alone and after "remove the duplicate rows and ...".
The round-3 head decides "is this also something I can't do?" with a closed list of verbs
(`buddy.outcome._OTHER_REQUEST_WORDS`); an extra without a listed verb is dropped without a word.

For each case record: does the reply say the extra was not done ("can't do" / "Partly done"), and the lead word.
Classes: SAYS (reply names that something wasn't done)  SILENT (extra dropped; reply reads as complete)

    python r3_extras.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
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
ap.add_argument("--out", default=str(HERE / "out" / "r3-extras.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)

# phrases Brain copies whole; every one asks for the report AND something else Buddy has no skill for
EXTRAS = [
    # sending it somewhere
    "send the report to my boss", "send the summary to Bob", "send my manager the report", "send Bob a summary of the file",
    "give my boss a report on this file", "show my team a summary of the data", "message me the report when it is ready",
    "text me the report", "whatsapp the summary to my team", "drop the report in the shared folder", "put the report on the drive",
    # another format / place
    "give me a report in pdf", "give me a report as a pdf", "give me a report in a word doc", "give me the report as a csv",
    "give me a report in a powerpoint slide", "give me a report in French", "give me a report in Spanish",
    "give me a report as a table in a new sheet", "put the report in a new sheet", "write the report to a new tab",
    "include the report in the file", "append the summary to the sheet", "put the summary at the top of the file",
    "give me a report as an image", "give me a report with conditional formatting",
    # more analysis than the analyzer does
    "analyze my file and tell me which customer spent the most", "give me a report and calculate the total of the amount column",
    "analyze my file and sum the amount column", "give me a summary with the average amount", "report on the file and count the unique names",
    "summarize the file and group it by name", "analyze my data and compare it to last month", "give me a report and combine it with the other file",
    "analyze the file and join it with sheet2", "give me a report and update the totals", "analyze my file and predict next month",
    "give me a report and find the outliers", "analyze my file and tell me what to do next",
    # asks for changing the file
    "give me a report and move column B", "analyze my file and copy it to a new file", "give me a report and lock the header row",
    "summarize the file and shorten the names", "give me a report and make the header bold", "analyze my file and fix the typos",
    "give me a report and normalise the dates", "analyze the file and standardise the names", "give me a report and clean up the formatting",
    "analyze my file and tidy it up", "report on the file and also sort out the blanks",
    # controls: must stay "can't do" (listed) and must stay silent-OK (pure report)
    "give me a report and email it to Bob", "give me a report and sort it by amount", "give me a report",
]

S = {}
for i, ph in enumerate(EXTRAS):
    S[f"x{i:02d}-alone|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))])
    S[f"x{i:02d}-dedupe|{ph}"] = dict(file="plain", turns=[dict(
        msg=f"remove the duplicate rows and {ph}",
        b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])


def classify(rep):
    rep = rep or ""
    says = ("can't do" in rep) or ("Partly done" in rep) or ("didn't run" in rep)
    lead = rep.split(" ")[0].rstrip("—")
    return ("SAYS" if says else "SILENT"), lead


if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["class"], r["lead"] = classify(r["reply"])
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    t = [r for r in res if r["speaker"] == "template" and "reply" in r]
    print(len(res), "turns;", dict(Counter(r["class"] for r in t)))
    print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r),
          "| voice calls:", sum(1 for r in res if r.get("voice_calls")))
    by = {}
    for r in t:
        w = r["name"].split("|", 1)[1]
        by.setdefault(w, {})[r["name"].split("|")[0].split("-", 1)[1]] = (r["class"], r["lead"])
    for w, d in by.items():
        print(f"  alone={d['alone'][0]:6s} dedupe={d['dedupe'][0]:6s}/{d['dedupe'][1]:6s} {w}")
