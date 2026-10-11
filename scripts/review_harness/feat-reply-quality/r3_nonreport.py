"""Round 3 probe: an unsupported request that is NOT a request for a report but contains a report noun ("update the summary",
"rewrite the analysis"). `mentions_report` reads the bare noun as a report request (backlog RQ-L2), so the reply states a report, and the
round-3 early return in `_no_skill_outcome` (`_OTHER_REQUEST_WORDS` has no word for the verb) drops the phrase without a word: "Done".
Brain returns the phrase as `unsupported` after a real dedupe request, and alone.

    python r3_nonreport.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
"""
import argparse
import json
import sys
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
PHRASES = ["rewrite the summary", "update the summary", "make the summary shorter", "proofread the report", "finalize the report",
           "approve the report", "improve the analysis", "polish the summary", "move the summary to the top", "number the summary lines",
           "fix the summary", "sort the summary", "delete the summary"]
S = {}
for i, ph in enumerate(PHRASES):
    S[f"n{i:02d}-dedupe|{ph}"] = dict(file="plain", turns=[dict(
        msg=f"remove the duplicate rows and {ph}",
        b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)))])
    S[f"n{i:02d}-alone|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))])
res = rq.run_all(S, (), tuple(A.speakers.split(",")))
if A.out:
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
for r in res:
    if "reply" in r and r["speaker"] == "template":
        lead = r["reply"][:60].replace("\n", " ")
        says = ("can't do" in r["reply"]) or ("Partly done" in r["reply"])
        print(f"{'SAYS  ' if says else 'SILENT'} {r['name']:48s} {lead}")
print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r))
