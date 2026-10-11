"""Round 6: ordinary report wordings that name a removal, handed over as a faithful Brain would (dedupe + report_on_file), 3 speakers.
   python r6_ordinary.py [--tree DIR]"""
import r5lib
A = r5lib.args(); rq = r5lib.start(A)
from rq import AN, DD, brain, plan
RPT = "report_on_file"; DUP = ("remove_duplicate_rows", "remove the duplicate rows")
S = {}
for i, (msg, rp) in enumerate([
  ("remove the duplicate rows and give me a report on the empty rows", "give me a report on the empty rows"),
  ("remove the duplicate rows and give me a report on what you removed", "give me a report on what you removed"),
  ("remove the duplicate rows and give me a report on the duplicates", "give me a report on the duplicates"),
  ("remove the duplicate rows, then a report", "a report"),
  ("give me a report on the empty rows", "give me a report on the empty rows"),
  ("report on how many empty rows there are", "report on how many empty rows there are"),
]):
    if msg.startswith("remove"):
        S[f"O{i}|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain("multi_step_request", DUP, (RPT, rp)), p=plan((DD, "remove the duplicate rows"), (AN, rp)))])
    else:
        S[f"O{i}|{msg}"] = dict(file="plain", turns=[dict(msg=msg, b=brain(RPT, (RPT, rp)))])
res = rq.run_all(S, (), tuple(A.speakers.split(",")))
for r in res:
    print(r["name"], r["speaker"], int(r["delivered"]), r["flags"], "::", r["reply"][:330])
