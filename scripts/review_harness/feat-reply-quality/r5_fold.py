"""Round 5: the round-3 H1 class under the new mechanism.  The user asks for a report AND something Buddy can't do
(send it, a format, a place in the file).  Brain can either SPLIT the extra into an `unsupported` entry (the reply names it) or FOLD it
into the report's phrase (`report_on_file` with the whole wording).  A folded extra is invisible to the code: what does the reply say?

  python r5_fold.py [--tree DIR] [--speakers ...] [--out FILE]
"""
import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, brain, plan  # noqa: E402

RPT = "report_on_file"
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
EXTRAS = [  # (the report part, the extra part)  message = "remove the duplicate rows and " + whole
    ("give me a report", "in pdf"), ("give me a report", "in French"), ("put the report", "in a new sheet"),
    ("write the report", "to a new tab"), ("send the report", "to my boss"), ("email me the report", ""),
    ("give me a summary", "and print it"), ("give me a report", "as a chart"), ("give me a report", "and highlight the problems in red"),
    ("give me a report", "and save it as report.pdf"), ("give me a report", "and post it to Slack"),
]
S = {}
for i, (rep, extra) in enumerate(EXTRAS):
    whole = f"{rep} {extra}".strip()
    msg = f"remove the duplicate rows and {whole}"
    tag = f"E{i:02d}|{whole}"
    # FOLDED: one report entry holding the whole wording
    S[f"{tag}|folded|dedupe"] = dict(file="plain", turns=[dict(msg=msg, b=brain("multi_step_request", DUP, (RPT, whole)),
                                                            p=plan((DD, "remove the duplicate rows"), (AN, whole)))])
    S[f"{tag}|folded|alone"] = dict(file="plain", turns=[dict(msg=whole, b=brain(RPT, (RPT, whole)))])
    # SPLIT: the report part is the report, the extra is its own unsupported entry (only when there is an extra)
    if extra:
        S[f"{tag}|split|dedupe"] = dict(file="plain", turns=[dict(
            msg=msg, b=brain("multi_step_request", DUP, (RPT, rep), ("unsupported", extra)),
            p=plan((DD, "remove the duplicate rows"), (AN, rep)))])
        S[f"{tag}|split|alone"] = dict(file="plain", turns=[dict(msg=whole, b=brain(RPT, (RPT, rep), ("unsupported", extra)))])
    # BASE-LIKE: the whole wording is `unsupported` (what the base's Brain prompt asked for)
    S[f"{tag}|unsupported|dedupe"] = dict(file="plain", turns=[dict(msg=msg, b=brain("remove_duplicate_rows", DUP, ("unsupported", whole)))])

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["tags"] = r5lib.classify(r["reply"])
    r5lib.dump(res, A.out or str(r5lib.HERE / "out" / "r5-fold.json"))
    import collections
    c = collections.Counter()
    for r in res:
        if r["speaker"] != "template" or "reply" not in r:
            continue
        kind = r["name"].split("|")[2] + "|" + r["name"].split("|")[3]
        rep = r["reply"]
        said = ("can't do" in rep) or ("Partly done" in rep)
        c[(kind, "extra named / Partly done" if said else ("Done, extra unmentioned" if "Done —" in rep else "report only, extra unmentioned"))] += 1
    for k, v in sorted(c.items()):
        print(k, v)
    print()
    for r in res:
        if r["speaker"] == "template" and r["name"].split("|")[1] in ("give me a report in pdf", "send the report to my boss") :
            print(r["name"]); print("   ", r["reply"][:430])
