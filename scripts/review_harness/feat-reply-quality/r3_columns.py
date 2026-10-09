"""Round 3 probe: an ordinary report request that names a column or sheet whose name is an everyday spreadsheet word that
`buddy.outcome._OTHER_REQUEST_WORDS` lists as "other work" (email, mail, share, post, plot, color, schedule, filter, print, ...).
As Brain's `unsupported` phrase, copied whole, alone. Class FALSE-CANT: "Report on" and a "can't do" sentence in one reply.

    python r3_columns.py [--tree DIR] [--speakers template,friend,focus] [--out FILE]
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
ap.add_argument("--out", default=str(HERE / "out" / "r3-columns.json"))
A = ap.parse_args()
import rq  # noqa: E402
from rq import brain  # noqa: E402

rq.setup(A.tree)
COLUMNS = ["email", "Email Address", "mail", "share", "Market Share", "post code", "post", "plot", "plot number", "color", "colour",
           "schedule", "Schedule", "filter", "print", "forecast", "dashboard", "trim", "split", "total", "bold", "date", "address",
           "Description", "Notes", "Price", "hide", "drop", "add", "save", "download", "attach", "publish", "rotate"]
PHRASES = []
for c in COLUMNS:
    PHRASES += [f"give me a report on the {c} column", f"analyze the {c} column", f"summarize the {c} data"]
S = {f"c{i:03d}|{ph}": dict(file="plain", turns=[dict(msg=ph, b=brain("unclear", ("unsupported", ph)))]) for i, ph in enumerate(PHRASES)}


def classify(rep):
    rep = rep or ""
    report = "Report on" in rep
    cant = ("can't do" in rep) or ("Partly done" in rep) or ("didn't run" in rep)
    return "FALSE-CANT" if report and cant else ("OK" if report else ("CANT-NO-REPORT" if cant else "NOREPORT"))


if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["class"] = classify(r["reply"])
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    t = [r for r in res if r["speaker"] == "template" and "reply" in r]
    print(len(res), "turns;", dict(Counter(r["class"] for r in t)))
    for r in t:
        if r["class"] != "OK":
            print(f"  {r['class']:15s} {r['msg']}")
