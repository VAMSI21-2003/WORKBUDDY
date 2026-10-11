"""Round 5, probe 1: every shape in which Brain can hand over a report request (and misreadings of one), through the
real pipeline (build_engine, real skills and verifiers; only the LLM is faked), three speakers.

Groups
  A  the report alone, in the ways Brain can say it: intent / requested_actions / both / neither / names with odd case,
     a phrase that is not in the message, intent contradicting the actions, an `unsupported` extra beside it
  B  a report with the dedupe: the planner names the report's step `spreadsheet.analyze`, `spreadsheet.report` (same
     words as Brain's phrase, different words), no plan at all, report first, three steps
  C  Brain does NOT read the report (the cost): `unsupported`, `unclear`, nothing, an unknown action name
  D  Brain's phrase for the report swallows more than the report (the whole message, the removal)

    python r5_shapes.py [--tree DIR] [--speakers ...] [--only A,B] [--out FILE]
"""
import json

import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, ER, brain, plan  # noqa: E402

S = {}
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
EMP = ("remove_empty_rows", "remove the empty rows")
RPT = "report_on_file"


def sc(name, msg, b, p=None, file="plain", patch=None, **kw):
    t = dict(msg=msg, b=b)
    if p is not None:
        t["p"] = p
    spec = dict(file=file, turns=[t], secrets=("TOPSECRET", "boom", "/secret/"))
    if patch:
        spec["patch"] = patch
    S[name] = spec


# ---- A ---------------------------------------------------------------------------------------------------------
m = "give me a report on the file"
sc("A01-intent+action", m, brain(RPT, (RPT, m)))
sc("A02-intent-only", m, brain(RPT))
sc("A03-action-only(unclear)", m, brain("unclear", (RPT, m)))
sc("A04-action+conversation", m, brain("conversation", (RPT, m)))
sc("A05-action-as-string", m, json.dumps({"intent": RPT, "confidence": 0.9, "requested_actions": [RPT]}))
sc("A06-odd-case-name", m, json.dumps({"intent": RPT, "confidence": 0.9,
                                       "requested_actions": [{"action": " Report_On_File ", "source_phrase": m}]}))
sc("A07-phrase-not-in-message", m, brain(RPT, (RPT, "a full summary of the workbook")))
sc("A08-empty-phrase", m, brain(RPT, (RPT, "")))
sc("A09-report+unsupported-sort", "give me a report and sort by amount",
   brain("multi_step_request", (RPT, "give me a report"), ("unsupported", "sort by amount")))
sc("A10-report+unsupported-email", "give me a report and email it to my boss",
   brain("multi_step_request", (RPT, "give me a report"), ("unsupported", "email it to my boss")))
sc("A11-twice", "give me a report, I mean a summary",
   brain(RPT, (RPT, "give me a report"), (RPT, "a summary")))
sc("A12-intent=dedupe-but-actions=report", "give me a report on the duplicates",
   brain("remove_duplicate_rows", (RPT, "give me a report on the duplicates")))
sc("A13-greeting-with-report", "hi! can you give me a report", brain("conversation", (RPT, "give me a report")))
sc("A14-hallucinated-on-hi", "hi", brain(RPT, (RPT, "hi")))
sc("A15-hallucinated-on-thanks", "thanks!", brain(RPT))
sc("A16-brain-fails", m, RuntimeError("brain down"))
sc("A17-brain-nonjson", m, "sure, here is a report")
sc("A18-report+dedupe-intent-only", "remove the duplicate rows and give me a report", brain("remove_duplicate_rows", (RPT, "give me a report")))

# ---- B ---------------------------------------------------------------------------------------------------------
m = "remove the duplicate rows and give me a report"
bs = brain("multi_step_request", DUP, (RPT, "give me a report"))
sc("B01-plan-analyze", m, bs, plan((DD, "remove the duplicate rows"), (AN, "give me a report")))
sc("B02-plan-spreadsheet.report-same-words", m, bs, plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "give me a report")))
sc("B03-plan-spreadsheet.report-other-words", m, bs, plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "a report")))
sc("B04-plan-empty(multi_step)", m, bs)
sc("B05-single-intent+both-actions", m, brain("remove_duplicate_rows", DUP, (RPT, "give me a report")))
sc("B06-single-intent+report-intent-actions", m, brain(RPT, DUP, (RPT, "give me a report")))
sc("B07-report-first", "give me a report, then remove the duplicate rows",
   brain("multi_step_request", (RPT, "give me a report"), DUP),
   plan((AN, "give me a report"), (DD, "remove the duplicate rows")))
sc("B08-three-steps", "remove the duplicate rows, remove the empty rows and give me a report",
   brain("multi_step_request", DUP, EMP, (RPT, "give me a report")),
   plan((DD, "remove the duplicate rows"), (ER, "remove the empty rows"), (AN, "give me a report")))
sc("B09-report-first-no-plan-order", "give me a summary then remove the empty rows",
   brain("remove_empty_rows", (RPT, "give me a summary"), EMP))
sc("B10-sort-in-chain", "remove the duplicate rows, sort by amount and give me a report",
   brain("multi_step_request", DUP, ("unsupported", "sort by amount"), (RPT, "give me a report")),
   plan((DD, "remove the duplicate rows"), ("spreadsheet.sort", "sort by amount"), (AN, "give me a report")))
sc("B11-planner-garbage", m, bs, "not json at all")
sc("B12-planner-step-quotes-whole-report-phrase", "sort by amount and give me a report",
   brain("multi_step_request", ("unsupported", "sort by amount"), (RPT, "sort by amount and give me a report")),
   plan(("spreadsheet.sort", "sort by amount and give me a report")))
sc("B13-step-quotes-phrase-other-case", m, bs,
   plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "  GIVE   me a REPORT ")))

# ---- C: Brain does not read the report ----------------------------------------------------------------------
for k, (lab, b) in enumerate([
    ("unsupported", brain("unclear", ("unsupported", "give me a report"))),
    ("unclear-nothing", brain("unclear")),
    ("unknown-name:report", brain("unclear", ("report", "give me a report"))),
    ("unknown-name:analyze_file", brain("unclear", ("analyze_file", "give me a report"))),
    ("conversation", brain("conversation")),
    ("dedupe-only-ignores-report", brain("remove_duplicate_rows", DUP)),
]):
    sc(f"C{k:02d}-alone-{lab}", "give me a report", b)
    sc(f"C{k:02d}-dedupe-{lab}", "remove the duplicate rows and give me a report",
       brain("remove_duplicate_rows", DUP, *[(a["action"], a["source_phrase"]) for a in json.loads(b)["requested_actions"]
                                              if a["action"] != "remove_duplicate_rows"]))

# ---- D: the report phrase swallows more -----------------------------------------------------------------------
sc("D01-report-phrase=whole-message", "remove the duplicate rows and give me a report",
   brain(RPT, (RPT, "remove the duplicate rows and give me a report")))
sc("D02-report-phrase=whole-message+dedupe-listed", "remove the duplicate rows and give me a report",
   brain("multi_step_request", DUP, (RPT, "remove the duplicate rows and give me a report")))
sc("D03-report-phrase=removal-only", "remove the duplicate rows and give me a report",
   brain(RPT, (RPT, "remove the duplicate rows")))
sc("D04-report-phrase-short-vs-long", "give me a report on the duplicate rows", brain(RPT, (RPT, "give me a report")))
sc("D05-report-phrase-full", "give me a report on the duplicate rows", brain(RPT, (RPT, "give me a report on the duplicate rows")))
sc("D06-empty-rows-report-short", "give me a report on the empty rows", brain(RPT, (RPT, "a report")))
sc("D07-empty-rows-report-full", "give me a report on the empty rows", brain(RPT, (RPT, "a report on the empty rows")))
sc("D08-phrase=the-duplicate-rows(first occurrence is the removal)",
   "remove the duplicate rows and give me a report on the duplicate rows",
   brain(RPT, (RPT, "the duplicate rows")))
sc("D09-hidden-removal: report phrase covers 'delete blank lines'",
   "give me a report and delete the blank lines", brain(RPT, (RPT, "give me a report and delete the blank lines")))
sc("D10-keep-duplicates", "give me a report but keep the duplicate rows", brain(RPT, (RPT, "give me a report")))
sc("D11-dont-remove", "give me a report but don't remove the empty rows", brain(RPT, (RPT, "give me a report")))

if __name__ == "__main__":
    only = tuple(x for x in A.only.split(",") if x)
    res = rq.run_all(S, only, tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["tags"] = r5lib.classify(r["reply"])
    out = A.out or str(r5lib.HERE / "out" / "r5-shapes.json")
    r5lib.dump(res, out)
    print(len(res), "turns ->", out)
    if not A.quiet:
        cur = None
        for r in res:
            if r["speaker"] != "template" and not A.only:
                continue
            if r["name"] != cur:
                cur = r["name"]
                print(f"\n== {cur}")
            if "error" in r:
                print("  ERROR", r["error"])
                continue
            st = ",".join(f"{c}:{s}" for c, s in r["steps"])
            print(f"  [{r['speaker'][:3]}] dlv={int(r['delivered'])} steps={st} flags={r['flags']} tags={r['tags']}")
            print(f"      {r['reply']}")
