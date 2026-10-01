"""Review harness for verified-outcome PR-B: real build_engine and skills, fake LLM.

Needs no API key. Usage: python harness.py [--tree DIR] [--out FILE] [--summary] [ID ...]
See README.md. Only the LLM is faked; it is routed by the system prompt.
"""
import argparse, hashlib, json, os, sys, tempfile
from collections import Counter
from pathlib import Path

_ap = argparse.ArgumentParser()
_ap.add_argument("--tree", default=str(Path(__file__).resolve().parents[2]))
_ap.add_argument("--out", default="review_harness_results.json")
_ap.add_argument("--summary", action="store_true")
_ap.add_argument("only", nargs="*")
ARGS = _ap.parse_args()
tree = str(Path(ARGS.tree).resolve())
OUT = Path(ARGS.out).resolve()  # resolved before the runs chdir into temp dirs
sys.path.insert(0, tree)
import pandas as pd  # noqa: E402
from buddy.engine_factory import build_engine
import buddy
assert Path(buddy.__file__).resolve().is_relative_to(Path(tree).resolve()), buddy.__file__
from buddy.state import BuddyState
from buddy.voice import PersonalityVoice

VOICE_MARK = "ZQXVOICE Done — everything is clean, duplicates removed."
THINK = json.dumps({"objective": "x", "summary": "ok", "clarification_required": False, "confidence": 0.9})

class P:
    def __init__(self, brain, plan):
        self.brain, self.plan, self.voice_calls = brain, plan, 0
    def complete(self, *, system, messages, max_tokens=1024):
        if "intent classifier" in system: return self.brain
        if "reasoning stage" in system: return THINK
        if "planning stage" in system:
            assert self.plan is not None, "planner called unexpectedly"
            return self.plan
        self.voice_calls += 1
        return VOICE_MARK

def brain(intent, *req):
    r = {"intent": intent, "confidence": 0.9}
    if req != (None,):
        r["requested_actions"] = [{"action": a, "source_phrase": p} for a, p in req]
    return json.dumps(r)

def plan(*steps):
    return json.dumps({"steps": [{"description": f"do {c}", "capability": c,
        "depends_on_step_numbers": [], "source_phrase": p} for c, p in steps]})

DD, ER = "spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"
DEF = {"name": ["Alice","Bob",None,"Alice","Carol","Bob"], "amount": [10,20,None,10,30,20]}
NODUP = {"name": ["Alice","Bob",None,"Carol"], "amount": [10,20,None,30]}

S = {}
def sc(name, msg, b, pl=None, rows=DEF, **exp): S[name] = dict(msg=msg, brain=b, plan=pl, rows=rows, exp=exp)

# --- H1
sc("c2", "don't remove duplicates, just remove the empty rows", brain("multi_step_request", ("remove_empty_rows","remove the empty rows")), plan((DD,"remove duplicates"),(ER,"remove the empty rows")))
sc("c2-dupsrows", "don't remove duplicates, just remove the empty rows", brain("multi_step_request", ("remove_empty_rows","remove the empty rows")), plan((DD,"remove duplicates"),(ER,"remove the empty rows")), rows=None)
sc("c3", "remove the empty rows but keep the duplicates", brain("multi_step_request", None), plan((ER,"remove the empty rows"),(DD,"the duplicates")))
sc("c7", "keep the duplicates, remove the empty rows", brain("multi_step_request", None), plan((DD,"the duplicates"),(ER,"remove the empty rows")))
sc("c6", "don't remove duplicates, just remove the empty rows", brain("multi_step_request", ("remove_empty_rows","remove the empty rows")), plan((DD,"remove duplicates")))
sc("c8-negated-intent", "do not dedupe, only remove blank rows", brain("remove_empty_rows", ("remove_empty_rows","remove blank rows")), plan((DD,"dedupe")))
# --- H2
for nm, act in [("k-remove_duplicates","remove_duplicates"),("k-Remove_Duplicate_Rows","Remove_Duplicate_Rows"),("k-trailing-space","remove_duplicate_rows "),("k-deduplicate","deduplicate"),("k-upper","REMOVE_DUPLICATE_ROWS")]:
    sc(nm, "remove the duplicate rows please", brain("remove_duplicate_rows", (act,"remove the duplicate rows")))
sc("k-multi-nearmiss", "remove duplicates then remove empty rows", brain("multi_step_request", ("remove_duplicates","remove duplicates"),("remove_empty_rows","remove empty rows")), plan((DD,"remove duplicates"),(ER,"remove empty rows")))
sc("a1", "remove duplicates and sort by amount", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove duplicates"),("unsupported","remove duplicates and sort by amount")))
sc("a2", "remove duplicates and sort by amount", brain("remove_duplicate_rows", ("unsupported","remove duplicates and sort by amount")))
sc("a3", "remove duplicates and sort by amount", brain("multi_step_request", ("unsupported","remove duplicates and sort by amount")), plan((DD,"remove duplicates")))
sc("a4", "remove duplicates and sort by amount", brain("remove_duplicate_rows", ("unsupported","remove duplicates")))
sc("a5", "sort by amount and remove duplicates", brain("multi_step_request", ("unsupported","sort by amount and remove duplicates"),("remove_duplicate_rows","remove duplicates")), plan((DD,"remove duplicates")))
sc("b5", "remove the empty rows but keep the duplicates", brain("remove_empty_rows", ("remove_empty_rows","remove the empty rows"),("unsupported","keep the duplicates")))
sc("b6", "remove the duplicate rows please", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove the duplicate rows"),("unsupported","please")))
sc("b2", "remove duplicates and sort by amount", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove duplicates"),("unsupported","sort by amount"),("unsupported","sort by amount")))
sc("b4-L3", "remove the empty rows but keep the duplicates", brain("remove_empty_rows", ("remove_empty_rows","remove the empty rows"),("remove_duplicate_rows","keep the duplicates")))
sc("e1-L1", "get rid of the repeated entries and sort by amount", brain("multi_step_request", ("unsupported","sort by amount")), plan((DD,"get rid of the repeated entries")))
sc("e2-paraphrase-dropped", "get rid of the repeated entries and sort by amount", brain("remove_duplicate_rows", ("remove_duplicate_rows","get rid of the repeated entries"),("unsupported","get rid of the repeated entries")))
# --- adversarial, aimed at the fixes
sc("x1-mention-only-but-asked", "remove duplicates and sort by amount", brain("multi_step_request", ("unsupported","sort by amount")), plan((DD,"remove duplicates")))
sc("x2-broad-delete-file", "remove duplicates and delete the file", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove duplicates"),("unsupported","remove duplicates and delete the file")))
sc("x3-broad-clean-data", "remove duplicates and clean the data", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove duplicates"),("unsupported","remove duplicates and clean the data")))
sc("x4-broad-drop-sheet", "remove duplicates and drop the sheet", brain("remove_duplicate_rows", ("unsupported","remove duplicates and drop the sheet")))
sc("x5-broad-email", "remove duplicates and email me the report", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove duplicates"),("unsupported","remove duplicates and email me the report")))
sc("x6-delete-file-alone", "remove duplicates and delete the file", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove duplicates"),("unsupported","delete the file")))
sc("x7-brain-missed-one", "remove duplicates and remove empty rows", brain("multi_step_request", ("remove_empty_rows","remove empty rows")), plan((DD,"remove duplicates"),(ER,"remove empty rows")))
sc("x8-genuine-both", "remove duplicates then remove empty rows", brain("multi_step_request", ("remove_duplicate_rows","remove duplicates"),("remove_empty_rows","remove empty rows")), plan((DD,"remove duplicates"),(ER,"remove empty rows")))
sc("x9-single-plain", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove the duplicate rows")))
sc("x10-nothing-to-dedupe", "remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows","remove the duplicate rows")), rows=NODUP)
# --- M1
sc("m1-N1", "remove the duplicate rows please", brain("remove_empty_rows", None))
sc("m1-d2", "remove the empty rows but keep the duplicates", brain("remove_empty_rows", ("remove_empty_rows","remove the empty rows")))
sc("m1-clean", "remove the empty rows", brain("remove_empty_rows", ("remove_empty_rows","remove the empty rows")), rows=NODUP)
# --- undelivered / regression vs main
sc("r-chain-stop", "remove duplicates and sort by amount", brain("multi_step_request", ("remove_duplicate_rows","remove duplicates"),("unsupported","sort by amount")), plan((DD,"remove duplicates"),("spreadsheet.sort","sort by amount")))
sc("r-unclear", "hello there", brain("unclear", None))

def run(name, spec, speaker, sid):
    d = Path(tempfile.mkdtemp()); os.chdir(d); (d/"uploads").mkdir()
    rows = spec["rows"] or DEF
    pd.DataFrame(rows).to_excel(d/"uploads/report.xlsx", index=False)
    h0 = hashlib.sha256((d/"uploads/report.xlsx").read_bytes()).hexdigest()
    orig = pd.read_excel(d/"uploads/report.xlsx")
    st = BuddyState.new(session_id=sid, workspace_id="w"); st.user_message = spec["msg"]
    st.context.add_uploaded_file("uploads/report.xlsx"); st.context.switch_active_file("uploads/report.xlsx")
    p = P(spec["brain"], spec["plan"]); sp = None
    if speaker != "template":
        st.context.set_mode(speaker); sp = PersonalityVoice(p)
    try:
        build_engine(llm_provider=p, speaker=sp).run(st)
    except Exception as e:
        return dict(name=name, speaker=speaker, sid=sid, error=repr(e))
    er = st.execution_result; out = er.outputs if er else ()
    rec = dict(name=name, speaker=speaker, sid=sid, reply=st.reply, voice_calls=p.voice_calls,
        delivered=bool(out), steps=[(s.capability, s.status) for s in (er.steps if er else ())],
        orig_unchanged=hashlib.sha256((d/"uploads/report.xlsx").read_bytes()).hexdigest()==h0,
        orig=(len(orig), int(orig.duplicated().sum()), int(orig.isna().all(axis=1).sum())))
    if out:
        df = pd.read_excel(out[0]); rec["final"] = (len(df), int(df.duplicated().sum()), int(df.isna().all(axis=1).sum()))
    return rec

def summarize(res):
    """One block per scenario: the template reply and how often markers appear per speaker."""
    marks = ["Done —", "Partly done", "Anything else", "What's next",
             "only part of this went through", "seeing double", "can't do"]
    print("turns:", len(res), "| errors:", sum("error" in r for r in res),
          "| voice-model calls:", sum(r.get("voice_calls", 0) for r in res),
          "| original changed:", sum(r.get("orig_unchanged") is False for r in res))
    for name in dict.fromkeys(r["name"] for r in res):
        rs = [r for r in res if r["name"] == name and "error" not in r]
        t = next(r for r in rs if r["speaker"] == "template")
        ran = [c.split(".")[-1] for c, _ in t["steps"] if "analyze" not in c]
        print(f"\n== {name} | delivered={t['delivered']} | steps={ran} | "
              f"original(rows,dups,empty)={t['orig']} final={t.get('final')}")
        print("  template:", t["reply"])
        for sp in ("friend", "focus"):
            c = Counter(m for r in rs if r["speaker"] == sp for m in marks if m in r["reply"])
            print(f"  {sp} markers (of {sum(r['speaker'] == sp for r in rs)} turns):", dict(c))


if __name__ == "__main__":
    res = []
    for name, spec in S.items():
        if ARGS.only and not any(name == i or name.startswith(i + "-") for i in ARGS.only):
            continue
        for speaker in ["template", "friend", "focus"]:
            sids = ["s%d" % i for i in range(1, 13)] if speaker == "friend" else ["session-1"]
            for sid in sids:
                res.append(run(name, spec, speaker, sid))
    OUT.write_text(json.dumps(res, indent=1))
    print(len(res), "turns ->", OUT)
    if ARGS.summary:
        summarize(res)
