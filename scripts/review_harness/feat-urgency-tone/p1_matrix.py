"""Round-1 probe 1: the same turn with Brain saying calm / urgent / frustrated / odd values.

    python p1_matrix.py [--tree DIR] [--out FILE] [--show] [ids...]

Real build_engine on a fake LLM (library: ../feat-reply-quality/rq.py, imported, not edited).
Head vs base: on the base `user_state` is ignored, so base == head-calm is the regression check.
Each record has the reply, whether a file was delivered, independent facts and original hash.
"""
import argparse, json, sys, re
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--out", default=str(HERE / "out" / "p1.json"))
ap.add_argument("--show", action="store_true")
ap.add_argument("only", nargs="*")
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import plan, think, DD, ER, AN  # noqa: E402

STATES = {"calm": "calm", "urgent": "urgent", "frustrated": "frustrated", "absent": None}


def b(intent, acts, state):
    d = {"intent": intent, "confidence": 0.9,
         "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]}
    if state is not None:
        d["user_state"] = state
    return json.dumps(d)


DUP = [("remove_duplicate_rows", "remove the duplicate rows")]
EMP = [("remove_empty_rows", "remove the empty rows")]
BOTH = [("remove_duplicate_rows", "remove duplicates"), ("remove_empty_rows", "remove empty rows")]

# name -> (file, intent, acts, think kwargs, plan, message)
SC = {
    "dedupe": ("plain", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "clean_nothing": ("clean", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "empty_only": ("plain", "remove_empty_rows", EMP, {}, None, "remove the empty rows"),
    "chain": ("plain", "multi_step_request", BOTH, {}, plan((DD, "remove duplicates"), (ER, "remove empty rows")), "remove duplicates then remove empty rows"),
    "dd_report": ("plain", "remove_duplicate_rows", DUP + [("report_on_file", "give me a report")], {}, None, "remove the duplicate rows and then give me a report"),
    "partly": ("plain", "multi_step_request", [("remove_duplicate_rows", "remove duplicates"), ("unsupported", "sort by amount")], {}, plan((DD, "remove duplicates")), "remove duplicates and sort by amount"),
    "chain_stops": ("plain", "multi_step_request", [("remove_duplicate_rows", "remove duplicates"), ("unsupported", "sort by amount")], {}, plan((DD, "remove duplicates"), ("spreadsheet.sort", "sort by amount")), "remove duplicates and sort by amount"),
    "greeting_file": ("plain", "conversation", [], {}, None, "hello"),
    "greeting_nofile": (None, "conversation", [], {}, None, "hello"),
    "q_action": ("plain", "unclear", [], {"clarify": True, "clarification_reason": "which_action"}, None, "fix it"),
    "q_file": ("plain", "unclear", [], {"clarify": True, "clarification_reason": "which_file"}, None, "fix it"),
    "q_other": ("plain", "unclear", [], {"clarify": True, "clarification_reason": "other"}, None, "fix it"),
    "nofile": (None, "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "unsupported": ("plain", "unclear", [("unsupported", "email it to my boss")], {}, None, "email it to my boss"),
    "garbage_file": ("garbage", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "password_file": ("password", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "hostile_hdr": ("hostile_headers", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "brain_raises": ("plain", RuntimeError("boom gsk_SECRET"), None, {}, None, "remove the duplicate rows"),
    "brain_junk": ("plain", "NOT JSON", None, {}, None, "remove the duplicate rows"),
    "think_raises": ("plain", "remove_duplicate_rows", DUP, RuntimeError("think boom"), None, "remove the duplicate rows"),
    "multi_sheet": ("multi", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
    "near_dups": ("near_dups", "remove_duplicate_rows", DUP, {}, None, "remove the duplicate rows"),
}


def spec(sc, state):
    f, intent, acts, tk, pl, msg = SC[sc]
    if isinstance(intent, BaseException):
        bb = intent
    elif intent == "NOT JSON":
        bb = intent
    else:
        bb = b(intent, acts, state)
    t = tk if isinstance(tk, BaseException) else think(**{k.replace("clarify", "clarify"): v for k, v in tk.items()} if tk else {})
    if isinstance(tk, dict) and tk:
        d = json.loads(think(clarify=tk.get("clarify", False)))
        d["clarification_reason"] = tk.get("clarification_reason", "")
        t = json.dumps(d)
    return {"file": f, "turns": [{"msg": msg, "b": bb, "t": t, "p": pl}]}


res = []
for sc in SC:
    if A.only and not any(sc.startswith(o) for o in A.only):
        continue
    for st_name, st in STATES.items():
        for sp in rq.SPEAKERS:
            r = rq.run(spec(sc, st), sp)
            for t in r["turns"]:
                t["name"], t["state"] = sc, st_name
                res.append(t)
Path(A.out).write_text(json.dumps(res, indent=1, default=str))
print(len(res), "turns ->", A.out)
if A.show:
    for r in res:
        print(f"{r['name']:14} {r['state']:10} {r['speaker']:8} deliv={r.get('delivered')} orig_ok={r.get('orig_unchanged')} flags={r.get('flags')}\n    {r.get('reply') or r.get('error')}")
