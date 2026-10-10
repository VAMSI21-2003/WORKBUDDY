"""Round 4: a report request that ALSO says not to do a thing Buddy can do ("analyze my file but don't delete the duplicates").
Round 3's cautious wordings only had "anything"/"everything" after the negation, which leaves a non-function word behind and so got the
REPORT_ONLY sentence.  When the negated object is the object of a real action (duplicates, empty rows), `_no_skill_outcome` strips it
as "the action's words", leaves only function words ("just", "don't") and falls through to the old NO_SKILL branch
(`negated and named_passed`), i.e. "I read your request as something I can't do yet" next to the report.

Brain returns `unsupported` with the phrase copied whole (alone), as the report is its documented example of `unsupported`.

    python r4_negated.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
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
ap.add_argument("--out", default=None)
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)
PHRASES = [
    # duplicates
    "just analyze my file, don't delete the duplicates",
    "analyze my file but don't remove the duplicates",
    "give me a report but don't remove the duplicate rows",
    "give me a report, keep the duplicates",
    "give me a summary and keep the duplicate rows",
    "analyze my file without removing duplicates",
    "give me a report without deleting the duplicates",
    "report on the file but leave the duplicates alone",
    "just a report please, no need to remove the duplicates",
    "I only want a report, do not remove duplicates",
    "give me a report first, don't remove the duplicates yet",
    "summarize my data but keep the duplicate rows",
    # empty rows
    "analyze my file but don't remove the empty rows",
    "give me a report, keep the empty rows",
    "give me a report and leave the blank rows alone",
    "just analyze the file, don't delete the empty rows",
    "give me a summary without removing the blank rows",
    "report on the file, don't touch the empty rows",
    # cautious, no named object (controls: round 3 said these are fine)
    "give me a report but don't remove anything",
    "just analyze my file, don't delete anything",
    # plain controls
    "give me a report",
    "analyze my file",
]
S = {}
for i, ph in enumerate(PHRASES):
    S[f"n{i:02d}-alone|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))])
    S[f"n{i:02d}-conv|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("conversation"))])


def klass(rep):
    rep = rep or ""
    if "can't do" in rep:
        return "CANT_DO"
    if "I did nothing beyond the report" in rep:
        return "REPORT_ONLY"
    return "SILENT"


if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["class"] = klass(r["reply"])
    if A.out:
        Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    for r in res:
        if r["speaker"] == A.speakers.split(",")[0] and "-alone|" in r["name"]:
            print(f"{r['class']:11s} {r['msg']}")
            if r["class"] == "CANT_DO":
                print("      ", r["reply"][:420])
    c = Counter((r["name"].split("|")[0].split("-")[1], r["class"]) for r in res if "reply" in r and r["speaker"] == A.speakers.split(",")[0])
    print(dict(c))
    print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r))
