"""Round 6: the new TemplateSpeaker._no_changes_message branch (RQ-R5-3 fix): a requested report whose analysis fails when nothing was delivered,
in combinations the writer's one test does not cover. Real build_engine, fake LLM only.
   python r6_voice_branch.py [--tree DIR] [--out FILE]"""
import r5lib
A = r5lib.args()
rq = r5lib.start(A)
from rq import AN, DD, ER, brain, plan, think  # noqa: E402
S = {}
RPT = "report_on_file"
DUP = ("remove_duplicate_rows", "remove the duplicate rows")
SEC = ("TOPSECRET", "boom", "/secret/")
def sc(name, msg, b, p=None, file="plain", patch=None, t=None):
    turn = dict(msg=msg, b=b)
    if p is not None: turn["p"] = p
    if t is not None: turn["t"] = t
    spec = dict(file=file, turns=[turn], secrets=SEC)
    if patch: spec["patch"] = patch
    S[name] = spec
m1 = "give me a report on the file"
m2 = "remove the duplicate rows and give me a report"
b1 = brain(RPT, (RPT, m1))
b2 = brain("multi_step_request", DUP, (RPT, "give me a report"))
p2 = plan((DD, "remove the duplicate rows"), (AN, "give me a report"))
FAILS = [("analyzer-raises-all", r5lib.analyzer_raises("all")), ("analyzer-raises-1st", r5lib.analyzer_raises({1})),
         ("an-verifier-fails-all", r5lib.analysis_verifier_fails("all")), ("an-verifier-fails-1st", r5lib.analysis_verifier_fails({1})),
         ("read-excel-raises-all", r5lib.read_excel_raises("all")), ("none", None)]
for lab, patch in FAILS:
    for fk in ("plain", "clean", "blanks", "multi", "merged", "empty_only"):
        sc(f"V1-alone|{fk}|{lab}", m1, b1, patch=patch, file=fk)
        sc(f"V2-dedupe+report|{fk}|{lab}", m2, b2, p2, patch=patch, file=fk)
    # report beside an unsupported extra
    sc(f"V3-report+sort|{lab}", "give me a report and sort by amount",
       brain("multi_step_request", (RPT, "give me a report"), ("unsupported", "sort by amount")), patch=patch)
    # report + removal that is declined to clarify
    sc(f"V4-think-asks|{lab}", m2, b2, p2, patch=patch, t=think(clarify=True, clarification_reason="which_action"))
    # report named but also a removal Brain read as a different file action
    sc(f"V5-report+empty|{lab}", "give me a report and remove the empty rows",
       brain("multi_step_request", (RPT, "give me a report"), ("remove_empty_rows", "remove the empty rows")),
       plan((ER, "remove the empty rows"), (AN, "give me a report")), patch=patch)
    sc(f"V6-report-intent-only|{lab}", m1, brain(RPT), patch=patch)
    sc(f"V7-report-twice|{lab}", "give me a report, I mean a summary", brain(RPT, (RPT, "give me a report"), (RPT, "a summary")), patch=patch)
sc("V8-dedupe-verifier+analysis-fail", m2, b2, p2, patch=r5lib.dedupe_verifier_fails())
if __name__ == "__main__":
    res = rq.run_all(S, (), tuple(A.speakers.split(",")))
    for r in res:
        if "reply" in r: r["tags"] = r5lib.classify(r["reply"])
    r5lib.dump(res, A.out or str(r5lib.HERE / "out" / "r6-voice-branch.json"))
    print(len(res), "turns")
    if not A.quiet:
        cur = None
        for r in res:
            if r["speaker"] != "template": continue
            if "error" in r: print(r["name"], "ERROR", r["error"]); continue
            print(f"{r['name']:60s} dlv={int(r['delivered'])} {r['flags']} :: {r['reply'][:260]}")
