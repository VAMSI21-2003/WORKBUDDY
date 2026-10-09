"""Round 2 probe X: (A) a chain whose planner names the report step `spreadsheet.report`, with report wordings the
fix does / does not recognise; (B) capability questions that use the new nouns "overview" / "rundown";
(C) guards that must still say "can't do" (a report plus something Buddy cannot do); (D) Think failing.

    python r2_misc.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
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
ap.add_argument("--out", default=str(HERE / "out" / "r2-x.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import AN, DD, ER, brain, plan, think  # noqa: E402

rq.setup(A.tree)

S = {}
# (A) chains
for i, ph in enumerate(["give me a report", "analyze my file", "give me a report on the duplicate rows",
                        "give me a report on data quality", "a summary of the missing values", "I'd like a summary please"]):
    msg = f"remove the duplicate rows and then {ph}"
    S[f"A{i}-chain-dedupe-then-report|{ph}"] = dict(file="plain", turns=[dict(
        msg=msg, b=brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", ph)),
        p=plan((DD, "remove the duplicate rows"), ("spreadsheet.report", ph)))])
# (B) capability questions
for i, ph in enumerate(["give me an overview of what you can do", "give me a rundown of what you can do",
                        "can you give me a summary of what you do?", "what can you do with my file?",
                        "give me an overview of your features", "hi, what can you help with?", "show me a summary of your abilities"]):
    S[f"B{i}-capability|{ph}"] = dict(file="plain", turns=[dict(msg=ph, b=brain("conversation"))])
# (C) guards
for i, (msg, acts) in enumerate([
    ("give me a report and sort it by amount", [("unsupported", "give me a report"), ("unsupported", "sort it by amount")]),
    ("give me a report on the duplicates sorted by amount", [("unsupported", "give me a report on the duplicates sorted by amount")]),
    ("give me a report and a chart", [("unsupported", "give me a report and a chart")]),
    ("summarize the file and email it to me", [("unsupported", "summarize the file and email it to me")]),
    ("give me a report on the duplicate rows and highlight them", [("unsupported", "give me a report on the duplicate rows and highlight them")]),
    ("analyze my file and fix the formatting", [("unsupported", "analyze my file and fix the formatting")]),
    ("a summary, then delete column B", [("unsupported", "a summary, then delete column B")]),
]):
    S[f"C{i}-guard|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("unclear", *acts))])
# (D) Think failing
for i, (msg, b) in enumerate([("remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))),
                              ("give me a report", brain("unclear", ("unsupported", "give me a report"))),
                              ("hi", brain("conversation")),
                              ("remove the duplicate rows and give me a report", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", "give me a report")))]):
    for tk in ("not json", RuntimeError("boom TOPSECRET"), '{"clarification_required": true, "clarification_reason": "failed"}'):
        S[f"D{i}-think-fails|{msg}|{str(tk)[:12]}"] = dict(file="plain", turns=[dict(msg=msg, b=b, t=tk)])
S["D9-think-fails-nofile"] = dict(file=None, turns=[dict(msg="remove the duplicate rows", b=brain("remove_duplicate_rows"), t="not json")])

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    for r in res:
        if "error" in r:
            print("ERROR", r["name"], r["error"])
            continue
        print(f"\n[{r['speaker']}] {r['name']}  delivered={int(r['delivered'])} steps={[f'{c}:{s}' for c, s in r['steps']]} flags={r['flags']}")
        print("   ", r["reply"])
