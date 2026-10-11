"""Round 5, probe 2: a report Brain read (`report_on_file`), on every kind of file and with every stage failing.

Groups
  F  file kinds x (report alone, dedupe + report), no file at all
  X  faults: analysis skill raises (first call / second call / all), analysis verifier fails (first / second / all),
     dedupe verifier fails, the planner raises, Hands cannot dispatch, Think fails, Think asks a question

    python r5_files_faults.py [--tree DIR] [--speakers ...] [--only F,X] [--out FILE]
"""
import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, ER, brain, plan, think  # noqa: E402

S = {}
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
RPT = "report_on_file"
SECRETS = ("TOPSECRET", "boom", "/secret/", "SECRET-HEADER")


def sc(name, msg, b, p=None, file="plain", patch=None, t=None, **kw):
    turn = dict(msg=msg, b=b)
    if p is not None:
        turn["p"] = p
    if t is not None:
        turn["t"] = t
    spec = dict(file=file, turns=[turn], secrets=SECRETS)
    if patch:
        spec["patch"] = patch
    S[name] = spec


KINDS = ["plain", "clean", "one_dup", "one_dup_noblank", "blanks", "empty_only", "one_row", "multi", "merged",
         "header3", "na", "password", "xls", "garbage", "hostile_headers", "hostile_headers2", "near_dups",
         "outliers", "numeric_dups", "dates", "wide_dups", None]
for k in KINDS:
    lab = k or "nofile"
    sc(f"F-{lab}-alone", "give me a report on the file", brain(RPT, (RPT, "give me a report on the file")), file=k)
    sc(f"F-{lab}-dedupe+report", "remove the duplicate rows and give me a report",
       brain("multi_step_request", DUP, (RPT, "give me a report")),
       plan((DD, "remove the duplicate rows"), (AN, "give me a report")), file=k)

m1, m2 = "give me a report on the file", "remove the duplicate rows and give me a report"
b1 = brain(RPT, (RPT, m1))
b2 = brain("multi_step_request", DUP, (RPT, "give me a report"))
p2 = plan((DD, "remove the duplicate rows"), (AN, "give me a report"))
for lab, patch in [
    ("analyzer-raises-all", r5lib.analyzer_raises("all")),
    ("analyzer-raises-1st", r5lib.analyzer_raises({1})),
    ("analyzer-raises-2nd", r5lib.analyzer_raises({2})),
    ("analysis-verifier-fails-all", r5lib.analysis_verifier_fails("all")),
    ("analysis-verifier-fails-1st", r5lib.analysis_verifier_fails({1})),
    ("analysis-verifier-fails-2nd", r5lib.analysis_verifier_fails({2})),
    ("dedupe-verifier-fails", r5lib.dedupe_verifier_fails()),
    ("read-excel-raises-all", r5lib.read_excel_raises("all")),
    ("read-excel-raises-1st", r5lib.read_excel_raises({1})),
    ("read-excel-raises-2nd", r5lib.read_excel_raises({2})),
    ("read-excel-raises-3rd", r5lib.read_excel_raises({3})),
    ("planner-stage-raises", r5lib.planner_raises()),
    ("hands-stage-raises", r5lib.hands_raises()),
]:
    sc(f"X-{lab}-alone", m1, b1, patch=patch)
    sc(f"X-{lab}-dedupe+report", m2, b2, p2, patch=patch)

sc("X-think-fails-alone", m1, b1, t=RuntimeError("think down"))
sc("X-think-fails-dedupe", m2, b2, p2, t=RuntimeError("think down"))
sc("X-think-asks-alone", m1, b1, t=think(clarify=True, clarification_reason="which_action"))
sc("X-think-asks-dedupe", m2, b2, p2, t=think(clarify=True, clarification_reason="which_file"))
sc("X-planner-raises-dedupe", m2, b2, RuntimeError("planner down"))
sc("X-think-asks-other-free-text", m1, b1, t=think(clarify=True, clarification_reason="TOPSECRET why", summary="TOPSECRET"))

if __name__ == "__main__":
    only = tuple(x for x in A.only.split(",") if x)
    res = rq.run_all(S, only, tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["tags"] = r5lib.classify(r["reply"])
    out = A.out or str(r5lib.HERE / "out" / "r5-files-faults.json")
    r5lib.dump(res, out)
    print(len(res), "turns ->", out)
    if not A.quiet:
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
            print(f"  dlv={int(r['delivered'])} steps={st} flags={r['flags']} tags={r['tags']} final={r.get('final_facts')}")
            print(f"      {r['reply']}")
