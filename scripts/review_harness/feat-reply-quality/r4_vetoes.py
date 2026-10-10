"""Round 4: genuine report requests that `mentions_report` vetoes (so the reply says "I can't do that yet" beside the findings),
through the real pipeline. Brain is FAITHFUL here (it copies the report clause, not the negated clause, as the prompt tells it to).

  V1  a report request plus an unrelated negated verb clause ("... and don't show me all the rows")
  V2  unpunctuated "no / not / don't" within 24 characters before the report noun ("no changes just a summary")
  V3  controls: the same requests with the clause removed (must give "Report on ...")

    python r4_vetoes.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
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
ap.add_argument("--out", default=str(HERE / "out" / "r4-vetoes.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)

V1 = [
    ("give me a short summary and don't show me all the rows", "give me a short summary"),
    ("analyze my file but don't give me too many details", "analyze my file"),
    ("summarize the data, don't send anything to anyone", "summarize the data"),
    ("give me a report; don't send it to anyone", "give me a report"),
    ("summarize my data but never show me the raw rows", "summarize my data"),
    ("give me a report on the duplicates, don't look at the other columns", "give me a report on the duplicates"),
    ("analyze my file, no need to check the formatting", "analyze my file"),
    ("summarize it and no need to show details", "summarize it"),
    ("give me a summary of the file and don't show the row numbers", "give me a summary of the file"),
    ("analyze this and don't give me the long version", "analyze this"),
    ("report on the data and don't review the second column", "report on the data"),
]
V2 = [
    ("no changes just a summary", "just a summary"),
    ("no worries just give me a summary of the file", "give me a summary of the file"),
    ("no rush give me a report on my data", "give me a report on my data"),
    ("dont fix anything report only", "report only"),
    ("no need to be fancy give me a report", "give me a report"),
    ("not in detail just a short summary", "a short summary"),
    ("i don't have time give me a summary", "give me a summary"),
    ("don't delete it give me a summary", "give me a summary"),
]
V3 = [("give me a short summary", "give me a short summary"), ("analyze my file", "analyze my file"), ("give me a summary", "give me a summary")]

S = {}
for grp, rows in (("V1", V1), ("V2", V2), ("V3", V3)):
    for i, (msg, ph) in enumerate(rows):
        S[f"{grp}-{i:02d}-alone|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("unclear", ("unsupported", ph)))])
        S[f"{grp}-{i:02d}-unclear|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("unclear"))])
        S[f"{grp}-{i:02d}-conv|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("conversation"))])
        dm = f"remove the duplicate rows and {msg}"
        S[f"{grp}-{i:02d}-dedupe|{dm}"] = dict(file="plain", turns=[dict(
            msg=dm, b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    bad = 0
    for r in res:
        if "error" in r:
            print("ERROR", r["name"], r["error"])
            continue
        report = "Report on " in r["reply"]
        cd = "can't do" in r["reply"] or "didn't read that as something I can do" in r["reply"]
        if r["speaker"] == "template" and not r["name"].startswith("V3"):
            print(f"{'RPT' if report else '---'} {'CD' if cd else '--'} {r['name']}\n      {r['reply'][:330]}")
        if not report and not r["name"].startswith("V3"):
            bad += 1
    print("\nturns", len(res), "| without a report:", bad, "| original changed:", sum(1 for r in res if r.get("orig_unchanged") is False),
          "| voice calls:", sum(1 for r in res if r.get("voice_calls")))
