"""Round 4 (second reviewer): my own adversarial cases through the real pipeline (real build_engine, skills, Inspector, Voice; fake LLM only).

  G1  a plain report request plus a NEGATED named action ("analyze my file but don't delete the duplicates"): Brain copies the
      message whole as `unsupported` (G1a), or follows its prompt and lists only the report clause (G1b)
  G2  two `unsupported` entries, one of them a report request with extra words: exact phrases (G2a) and a paraphrased second
      phrase (`phrase_found` False, G2b), both orders
  G3  an `unsupported` phrase that SPANS an action that did run
  G4  two turns in one session: a delivery, then a report request with an extra
  G5  curly apostrophes (iOS / macOS smart punctuation): declines and requests
  G6  chains whose planner step is a report with an extra (the chain must not start / must say so)
  G7  Brain's phrase is empty / missing / not a string, report requested in the message

    python r4_engine.py [--tree DIR] [--speakers template,friend,focus] [--out FILE] [--group G1,G2]
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
ap.add_argument("--out", default=str(HERE / "out" / "r4-engine.json"))
ap.add_argument("--group", default="")
ap.add_argument("--quiet", action="store_true")
A = ap.parse_args()
import rq  # noqa: E402
from rq import DD, ER, brain, plan  # noqa: E402

rq.setup(A.tree)

DUP = ("remove_duplicate_rows", "remove the duplicate rows")
S = {}


def sc(name, file="plain", **turn):
    S[name] = dict(file=file, turns=[turn])


# ---- G1: negated named action next to a report request
G1 = [
    ("analyze my file but don't remove the duplicates", "analyze my file", "remove the duplicates"),
    ("just analyze my file, don't delete the duplicates", "just analyze my file", "delete the duplicates"),
    ("give me a summary but leave the empty rows alone", "give me a summary", "leave the empty rows alone"),
    ("give me a report on the file, keep the duplicate rows", "give me a report on the file", "keep the duplicate rows"),
    ("summarize my data without removing the empty rows", "summarize my data", "removing the empty rows"),
    ("check my file for problems, don't remove any duplicate rows yet", "check my file for problems", "don't remove any duplicate rows yet"),
    ("report only, no need to delete duplicates", "report only", "no need to delete duplicates"),
    ("give me a summary and don't clean the empty rows", "give me a summary", "don't clean the empty rows"),
]
for i, (msg, report_clause, neg_clause) in enumerate(G1):
    sc(f"G1a-{i}-whole-unsupported|{msg}", msg=msg, b=brain("unclear", ("unsupported", msg)))
    sc(f"G1b-{i}-faithful|{msg}", msg=msg, b=brain("unclear", ("unsupported", report_clause)))

# ---- G2: two unsupported entries
G2 = [
    ("remove the duplicate rows, give me a report in pdf and sort by amount", "give me a report in pdf", "sort by amount", "sort by amount please"),
    ("remove the duplicate rows, send the summary to Anna and highlight the blanks", "send the summary to Anna", "highlight the blanks", "highlight blank cells"),
    ("remove the duplicate rows, give me a summary as a csv and make a chart", "give me a summary as a csv", "make a chart", "create a chart of it"),
]
for i, (msg, rep, other, other_para) in enumerate(G2):
    for kind, o in (("exact", other), ("paraphrased", other_para)):
        for order in ("report-first", "other-first"):
            ents = [("unsupported", rep), ("unsupported", o)]
            if order == "other-first":
                ents.reverse()
            sc(f"G2-{i}-{kind}-{order}|{msg}", msg=msg, b=brain("remove_duplicate_rows", DUP, *ents))

# ---- G3: a phrase that spans an action that ran
G3 = [
    ("remove the duplicate rows and give me a report in pdf", "remove the duplicate rows and give me a report in pdf"),
    ("remove the empty rows and email me a summary of the file", "remove the empty rows and email me a summary of the file"),
    ("remove duplicates then give me the summary as a word doc", "remove duplicates then give me the summary as a word doc"),
]
for i, (msg, span) in enumerate(G3):
    intent = "remove_duplicate_rows" if "duplicate" in msg else "remove_empty_rows"
    other = DUP if "duplicate" in msg else ("remove_empty_rows", "remove the empty rows")
    sc(f"G3-{i}-span-with-entry|{msg}", msg=msg, b=brain(intent, other, ("unsupported", span)))
    sc(f"G3-{i}-span-alone|{msg}", msg=msg, b=brain(intent, ("unsupported", span)))

# ---- G5: curly apostrophes
G5 = [
    ("don’t give me a report, just remove the duplicate rows", False),
    ("I don’t need a report, remove the empty rows", False),
    ("please don’t summarize my data", False),
    ("don’t look at my data", False),
    ("what’s wrong with my file", True),
    ("I’ll check the file myself", False),
    ("I’ll summarize it myself", False),
]
for i, (msg, wants) in enumerate(G5):
    sc(f"G5-{i}-unclear|{msg}", msg=msg, b=brain("unclear"))

# ---- G6: chains
G6 = [
    "give me the report as a pdf",
    "give me a report and email it to Bob",
    "I'd like a summary of the missing values in French",
    "send me a summary",
]
for i, ph in enumerate(G6):
    msg = f"remove the duplicate rows and then {ph}"
    sc(f"G6-{i}-chain|{ph}", msg=msg, b=brain("multi_step_request", DUP, ("unsupported", ph)),
       p=plan((DD, "remove the duplicate rows"), ("spreadsheet.report", ph)))

# ---- G7: odd Brain output
sc("G7-0-empty-phrase|give me a report in pdf", msg="give me a report in pdf", b=json.dumps(
    {"intent": "unclear", "confidence": 0.5, "requested_actions": [{"action": "unsupported", "source_phrase": ""}]}))
sc("G7-1-missing-phrase|give me a report in pdf", msg="give me a report in pdf", b=json.dumps(
    {"intent": "unclear", "confidence": 0.5, "requested_actions": [{"action": "unsupported"}]}))
sc("G7-2-nonstring-phrase|give me a report in pdf", msg="give me a report in pdf", b=json.dumps(
    {"intent": "unclear", "confidence": 0.5, "requested_actions": [{"action": "unsupported", "source_phrase": 42}]}))
sc("G7-3-phrase-not-in-message|give me a report in pdf", msg="give me a report in pdf", b=brain("unclear", ("unsupported", "produce a PDF report")))
sc("G7-4-phrase-not-in-message-dedupe|remove duplicates and give me a report in pdf", msg="remove duplicates and give me a report in pdf",
   b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove duplicates"), ("unsupported", "produce a PDF report")))
sc("G7-5-action-alias|give me a report in pdf", msg="give me a report in pdf", b=brain("unclear", ("report", "give me a report in pdf")))


def run_g4(speaker):
    """Two turns in one session."""
    spec = dict(file="plain", turns=[
        dict(msg="remove the duplicate rows", b=brain("remove_duplicate_rows", DUP)),
        dict(msg="now give me a report in pdf", b=brain("unclear", ("unsupported", "give me a report in pdf"))),
        dict(msg="and a summary of what changed in French", b=brain("unclear", ("unsupported", "a summary of what changed in French"))),
    ])
    r = rq.run(spec, speaker)
    for t in r["turns"]:
        t["name"] = "G4-two-turns"
    return r["turns"]


if __name__ == "__main__":
    only = tuple(A.group.split(",")) if A.group else ()
    res = rq.run_all(S, only, tuple(A.speakers.split(",")))
    if not only or "G4" in only:
        for sp in A.speakers.split(","):
            res.extend(run_g4(sp))
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    for r in res:
        if A.quiet:
            break
        if r["speaker"] != "template":
            continue
        if "error" in r:
            print(f"ERROR {r['name']} {r['error']}")
            continue
        print(f"\n## {r['name']}\n   > {r['msg']}\n   dlv={int(r['delivered'])} steps={[f'{c}:{s}' for c, s in r['steps']]} flags={r['flags']}\n   {r['reply']}")
    print("\nturns", len(res), "| original changed:", sum(1 for r in res if r.get("orig_unchanged") is False),
          "| errors:", sum(1 for r in res if "error" in r), "| voice calls:", sum(1 for r in res if r.get("voice_calls")))
