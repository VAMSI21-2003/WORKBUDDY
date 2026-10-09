"""Round-1 probe B: ordinary report wordings, as Brain's `unsupported` entry (the Brain prompt names "a report"
as the example of `unsupported`), alone and after "remove the duplicate rows and ...".
A reply that says "Report on ..." and also "I can't do ..." contradicts itself (a false "can't do" about work done).

    python probe_b_report_phrases.py [--tree DIR] [--out FILE]
"""
import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--out", default=str(HERE / "out" / "b.json"))
ap.add_argument("--speakers", default="template,friend")
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import brain  # noqa: E402

PHRASES = [
    "give me a report", "give me a report on this file", "give me a report of the file", "give me a report on my data",
    "give me a full report", "send me a report", "I need a report", "generate a report of all the issues",
    "write me a report about this spreadsheet", "report on the duplicates", "a report please",
    "analyze my file", "analyze the file", "analyze this spreadsheet", "analyze it", "analyze my data", "analyse the data", "do an analysis",
    "run an analysis", "give me an analysis of the file", "analysis please",
    "summarize my data", "summarize the file", "summarize this", "summarize it for me", "give me a summary of the file", "give me a summary of my data",
    "show me a summary", "show me a summary of the problems", "a short summary would be great",
    "give me a rundown", "tell me what's wrong with the file", "check the file for problems", "take a look at the file",
]

S = {}
for i, ph in enumerate(PHRASES):
    S[f"b{i:02d}-alone|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))])
    msg = f"remove the duplicate rows and {ph}"
    S[f"b{i:02d}-dedupe|{ph}"] = dict(file="plain", turns=[dict(
        msg=msg, b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    Path(A.out).parent.mkdir(parents=True, exist_ok=True)
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    n_contra = 0
    rows = []
    for r in res:
        rep = r.get("reply") or ""
        report = "Report on" in rep
        cant = "can't do" in rep
        mentions = "Your message mentions" in rep or "though I read it as part" in rep
        tag = []
        if report and cant:
            tag.append("CONTRADICTION(report+cant)")
        if not report and cant and ("Here's what I noticed" in rep or "I also noticed" in rep):
            tag.append("cant+findings")
        if tag:
            n_contra += 1
        rows.append((r["name"], r["speaker"], r["delivered"], ",".join(tag)))
    print(len(res), "turns;", n_contra, "tagged")
    for name, sp, dlv, tag in rows:
        if sp == "template":
            print(f"{name:70s} dlv={int(dlv)} {tag}")
