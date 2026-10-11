"""Round 5, probe 5: hostile and odd input aimed at the new code (report_on_file, report_phrases, except_inside, the
planner's identity rule), through the real pipeline.  A reply must never carry text from the message beyond what the
older code already quotes (an `unsupported` entry's phrase), from an exception, or from Brain's own words.

    python r5_hostile.py [--tree DIR] [--speakers ...]
"""
import json

import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, brain, plan  # noqa: E402

RPT = "report_on_file"
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
S = {}


def sc(name, msg, b, p=None, file="plain"):
    t = dict(msg=msg, b=b)
    if p is not None:
        t["p"] = p
    S[name] = dict(file=file, turns=[t], secrets=("TOPSECRET", "boom", "/secret/", "BRAINWORDS"))


def raw(intent, actions, **kw):
    d = {"intent": intent, "confidence": 0.9, "requested_actions": actions}
    d.update(kw)
    return json.dumps(d)


sc("H01-empty-message", "", brain(RPT, (RPT, "")))
sc("H02-whitespace-message", "   \n\t ", brain(RPT, (RPT, " ")))
sc("H03-regex-chars-in-phrase", "give me a report (.*+?[a-z]{0,99}) please",
   brain(RPT, (RPT, "(.*+?[a-z]{0,99})")))
sc("H04-backslash-phrase", "give me a report \\ now", brain(RPT, (RPT, "\\")))
sc("H05-long-message", "remove the duplicate rows " + "and give me a report " * 400,
   brain("multi_step_request", DUP, (RPT, "give me a report")))
sc("H06-report-phrase-is-1-char-in-removal", "remove the duplicate rows and give me a report", brain(RPT, (RPT, "e")))
sc("H07-report-phrase-is-space", "remove the duplicate rows", brain(RPT, (RPT, " ")))
sc("H08-1000-report-entries", "give me a report", raw(RPT, [{"action": RPT, "source_phrase": "give me a report"}] * 1000))
sc("H09-actions-not-a-list", "give me a report", raw(RPT, {"action": RPT}))
sc("H10-action-null", "give me a report", raw(RPT, [{"action": None, "source_phrase": "give me a report"}]))
sc("H11-action-number", "give me a report", raw(RPT, [{"action": 7, "source_phrase": "give me a report"}]))
sc("H12-phrase-not-str", "give me a report", raw(RPT, [{"action": RPT, "source_phrase": ["give me a report"]}]))
sc("H13-brain-words-in-unknown-action", "give me a report",
   raw("unclear", [{"action": "BRAINWORDS: Done, all duplicates removed", "source_phrase": "give me a report"}]))
sc("H14-intent-unknown-string", "give me a report", raw("report", [{"action": RPT, "source_phrase": "give me a report"}]))
sc("H15-intent-report_on_file-caps", "give me a report", raw("REPORT_ON_FILE", []))
sc("H16-unicode", "give me a report ‮\u0000 ünïcödé 报告", brain(RPT, (RPT, "give me a report")))
sc("H17-newlines", "remove the duplicate rows\nand\ngive me a report", brain("multi_step_request", DUP, (RPT, "give me a report")),
   plan((DD, "remove the duplicate rows"), (AN, "give me a report")))
sc("H18-planner-step-empty-phrase", "remove the duplicate rows and give me a report",
   brain("multi_step_request", DUP, (RPT, "give me a report")),
   plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "")))
sc("H19-planner-step-missing-phrase", "remove the duplicate rows and give me a report",
   brain("multi_step_request", DUP, (RPT, "give me a report")),
   json.dumps({"steps": [
       {"description": "d", "capability": DD, "depends_on_step_numbers": [], "source_phrase": "remove the duplicate rows"},
       {"description": "r", "capability": "spreadsheet.report", "depends_on_step_numbers": []}]}))
sc("H20-report-phrase-empty-planner-empty", "remove the duplicate rows and give me a report",
   brain("multi_step_request", DUP, (RPT, "")), plan((DD, "remove the duplicate rows"), ("spreadsheet.report", "")))
sc("H21-planner-step-quotes-removal-as-report", "remove the duplicate rows and give me a report",
   brain("multi_step_request", (RPT, "remove the duplicate rows"), (RPT, "give me a report")),
   plan(("spreadsheet.deduplicate", "remove the duplicate rows"), (AN, "give me a report")))
sc("H22-planner-sort-step-quotes-report-phrase", "sort the rows by amount and then give me a report",
   brain("multi_step_request", (RPT, "sort the rows by amount and then give me a report")),
   plan(("spreadsheet.sort", "sort the rows by amount and then give me a report")))
sc("H23-planner-sort-step-quotes-report-phrase-only-report-in-brain", "sort the rows by amount and then give me a report",
   brain("multi_step_request", (RPT, "sort the rows by amount and then give me a report")),
   plan(("spreadsheet.sort", "sort the rows by amount and then give me a report"), (AN, "give me a report")))
sc("H24-planner-delete-column-step-quotes-report-phrase", "delete column B and give me a report",
   brain("multi_step_request", (RPT, "delete column B and give me a report")),
   plan(("spreadsheet.delete_column", "delete column B and give me a report")))

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["tags"] = r5lib.classify(r["reply"])
    out = A.out or str(r5lib.HERE / "out" / "r5-hostile.json")
    r5lib.dump(res, out)
    print(len(res), "turns ->", out)
    cur = None
    for r in res:
        if r["speaker"] != "template":
            continue
        if r["name"] != cur:
            cur = r["name"]
            print(f"\n== {cur}")
        if "error" in r:
            print("  ERROR", r["error"])
            continue
        st = ",".join(f"{c}:{s}" for c, s in r["steps"])
        print(f"  dlv={int(r['delivered'])} steps={st} flags={r['flags']} tags={r['tags']}")
        print(f"      {r['reply'][:600]}")
