"""Round 4: a stage fails or answers with garbage while the message asks for a report (+ an extra), template/friend/focus.

    python r4_stages.py [--tree DIR] [--speakers template,friend,focus]
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
ap.add_argument("--out", default=str(HERE / "out" / "r4-stages.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain, plan, think, DD  # noqa: E402

rq.setup(A.tree)
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
M1 = "give me a report in pdf"
M2 = "remove the duplicate rows and give me a report in pdf"
S = {
    "s0-brain-raises": dict(file="plain", turns=[dict(msg=M1, b=RuntimeError("brain boom TOPSECRET"))]),
    "s1-brain-garbage": dict(file="plain", turns=[dict(msg=M1, b="not json at all")]),
    "s2-brain-wrong-shape": dict(file="plain", turns=[dict(msg=M1, b=json.dumps({"intent": "unclear", "requested_actions": "oops"}))]),
    "s3-brain-entry-without-phrase": dict(file="plain", turns=[dict(msg=M1, b=json.dumps({"intent": "unclear", "requested_actions": [{"action": "unsupported"}]}))]),
    "s4-brain-entry-empty-phrase": dict(file="plain", turns=[dict(msg=M1, b=brain("unclear", ("unsupported", "")))]),
    "s5-brain-entry-phrase-not-in-message": dict(file="plain", turns=[dict(msg=M1, b=brain("unclear", ("unsupported", "a PDF report")))]),
    "s6-brain-entry-phrase-not-in-message-dedupe": dict(file="plain", turns=[dict(msg=M2, b=brain("remove_duplicate_rows", DUP, ("unsupported", "a PDF report")))]),
    "s7-think-raises": dict(file="plain", turns=[dict(msg=M1, b=brain("unclear", ("unsupported", M1)), t=RuntimeError("think boom TOPSECRET"))]),
    "s8-think-asks": dict(file="plain", turns=[dict(msg=M1, b=brain("unclear", ("unsupported", M1)), t=rq.think(clarify=True))]),
    "s9-planner-raises-dedupe": dict(file="plain", turns=[dict(msg=M2, b=brain("multi_step_request", DUP, ("unsupported", M1)), p=RuntimeError("plan boom TOPSECRET"))]),
    "s10-multistep-chain-with-report": dict(file="plain", turns=[dict(
        msg="remove the duplicate rows and then give me a report in pdf",
        b=brain("multi_step_request", DUP, ("unsupported", "give me a report in pdf")),
        p=plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "give me a report in pdf")))]),
    "s11-multistep-chain-report-first": dict(file="plain", turns=[dict(
        msg="give me a report and then remove the duplicate rows",
        b=brain("multi_step_request", ("unsupported", "give me a report"), DUP),
        p=plan(("spreadsheet.report", "give me a report"), (DD, "remove the duplicate rows")))]),
    "s12-two-turns-report-then-dedupe": dict(file="plain", turns=[
        dict(msg=M1, b=brain("unclear", ("unsupported", M1))),
        dict(msg="ok now remove the duplicate rows", b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))]),
    "s13-two-turns-dedupe-then-report": dict(file="plain", turns=[
        dict(msg="remove the duplicate rows", b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))),
        dict(msg="now give me a report in pdf", b=brain("unclear", ("unsupported", "give me a report in pdf")))]),
}
res = rq.run_all(S, (), tuple(A.speakers.split(",")))
Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
for r in res:
    if r["speaker"] == "friend":
        continue
    if "error" in r:
        print(f"[{r['speaker'][:3]}] {r['name']} t{r['turn']} ERROR {r['error']}")
        continue
    print(f"[{r['speaker'][:3]}] {r['name']} t{r['turn']} delivered={r['delivered']} flags={r['flags']}\n    {r['reply']}")
print("original changed:", sum(1 for r in res if r.get("orig_unchanged") is False), "| errors:", sum(1 for r in res if "error" in r), "| voice calls:", sum(1 for r in res if r.get("voice_calls")))
