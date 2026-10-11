"""Round 5: Brain folds a FILE-CHANGING action into the report's phrase (the mention cross-check ignores a mention inside it).
   python r5_fold2.py [--tree DIR]"""
import r5lib
A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, ER, brain, plan  # noqa: E402
RPT = "report_on_file"
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
msg = "remove the duplicate rows, remove the empty rows and give me a report"
S = {
 "G1-folded-empty-rows-in-report-phrase|planner plans only dedupe": dict(file="plain", turns=[dict(msg=msg,
     b=brain("multi_step_request", DUP, (RPT, "remove the empty rows and give me a report")),
     p=plan((DD, "remove the duplicate rows")))]),
 "G2-folded-empty-rows-in-report-phrase|planner plans both": dict(file="plain", turns=[dict(msg=msg,
     b=brain("multi_step_request", DUP, (RPT, "remove the empty rows and give me a report")),
     p=plan((DD, "remove the duplicate rows"), (ER, "remove the empty rows")))]),
 "G3-split|planner plans only dedupe": dict(file="plain", turns=[dict(msg=msg,
     b=brain("multi_step_request", DUP, ("remove_empty_rows", "remove the empty rows"), (RPT, "give me a report")),
     p=plan((DD, "remove the duplicate rows")))]),
 "G4-single-intent-dedupe,report-phrase=rest": dict(file="plain", turns=[dict(msg=msg,
     b=brain("remove_duplicate_rows", DUP, (RPT, "remove the empty rows and give me a report")))]),
}
res = rq.run_all(S, (), tuple(A.speakers.split(",")))
for r in res:
    if r["speaker"] == "template":
        print(r["name"], r["steps"], "\n    ", r["reply"][:420])
