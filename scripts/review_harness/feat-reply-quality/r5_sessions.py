"""Round 5, probe 3: sessions of several turns around a report Brain read (state carried between turns: the file is
analysed once per session unless a report is asked for, earlier deliveries, a greeting that must not use up the analysis).

    python r5_sessions.py [--tree DIR] [--speakers ...] [--out FILE]
"""
import r5lib

A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, ER, brain, plan, think  # noqa: E402

RPT = "report_on_file"
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
EMP = ("remove_empty_rows", "remove the empty rows")


def T(msg, b, p=None, **kw):
    d = dict(msg=msg, b=b)
    if p is not None:
        d["p"] = p
    d.update(kw)
    return d


S = {
    "S1-dedupe-then-report": dict(file="plain", turns=[
        T("remove the duplicate rows", brain("remove_duplicate_rows", DUP)),
        T("give me a report", brain(RPT, (RPT, "give me a report"))),
    ]),
    "S2-report-then-dedupe": dict(file="plain", turns=[
        T("give me a report", brain(RPT, (RPT, "give me a report"))),
        T("now remove the duplicate rows", brain("remove_duplicate_rows", DUP)),
    ]),
    "S3-hi-then-dedupe+report": dict(file="plain", turns=[
        T("hi", brain("conversation")),
        T("remove the duplicate rows and give me a report", brain("multi_step_request", DUP, (RPT, "give me a report")),
          plan((DD, "remove the duplicate rows"), (AN, "give me a report"))),
    ]),
    "S4-report-twice": dict(file="plain", turns=[
        T("give me a report", brain(RPT, (RPT, "give me a report"))),
        T("give me a report again", brain(RPT, (RPT, "give me a report again"))),
    ]),
    "S5-dedupe-then-report-on-cleaned": dict(file="plain", turns=[
        T("remove the duplicate rows", brain("remove_duplicate_rows", DUP)),
        T("give me a report on the cleaned file", brain(RPT, (RPT, "give me a report on the cleaned file"))),
    ]),
    "S6-dedupe+report-then-dedupe+report": dict(file="plain", turns=[
        T("remove the duplicate rows and give me a report", brain("multi_step_request", DUP, (RPT, "give me a report")),
          plan((DD, "remove the duplicate rows"), (AN, "give me a report"))),
        T("remove the empty rows and give me a report", brain("multi_step_request", EMP, (RPT, "give me a report")),
          plan((ER, "remove the empty rows"), (AN, "give me a report"))),
    ]),
    "S7-report-then-thanks": dict(file="plain", turns=[
        T("give me a report", brain(RPT, (RPT, "give me a report"))),
        T("thanks!", brain("conversation")),
    ]),
    "S8-think-asks-then-report": dict(file="plain", turns=[
        T("clean it", brain("unclear"), t=think(clarify=True, clarification_reason="which_action")),
        T("just give me a report", brain(RPT, (RPT, "just give me a report"))),
    ]),
    "S9-report-then-unclear": dict(file="plain", turns=[
        T("give me a report", brain(RPT, (RPT, "give me a report"))),
        T("hmm", brain("unclear")),
    ]),
    "S10-nofile-report-then-upload(simulated by file)": dict(file="plain", turns=[
        T("give me a report", brain(RPT, (RPT, "give me a report"))),
        T("give me a report on the duplicates", brain(RPT, (RPT, "give me a report on the duplicates"))),
    ]),
}

if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r:
            r["tags"] = r5lib.classify(r["reply"])
    out = A.out or str(r5lib.HERE / "out" / "r5-sessions.json")
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
        print(f"  t{r['turn']} dlv={int(r['delivered'])} steps={st} flags={r['flags']} tags={r['tags']}")
        print(f"      > {r['msg']}")
        print(f"      {r['reply']}")
