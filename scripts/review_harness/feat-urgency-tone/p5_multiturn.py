"""Round-1 probe 5: state is per turn (a calm turn after an urgent one is friend again), and the
mode the session picked is never changed by the state. Also: a question, then the answer, urgent.

    python p5_multiturn.py [--tree DIR]
"""
import argparse, json, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
DUP = [{"action": "remove_duplicate_rows", "source_phrase": "remove the duplicate rows"}]


def b(state, intent="remove_duplicate_rows", acts=DUP, **kw):
    d = {"intent": intent, "confidence": 0.9, "requested_actions": acts, "user_state": state, **kw}
    return json.dumps(d)


spec = {"file": "plain", "turns": [
    {"msg": "remove the duplicate rows", "b": b("urgent"), "t": rq.think()},
    {"msg": "remove the empty rows", "b": b("calm", acts=[{"action": "remove_empty_rows", "source_phrase": "remove the empty rows"}], intent="remove_empty_rows"), "t": rq.think()},
    {"msg": "remove the duplicate rows", "b": b("frustrated"), "t": rq.think()},
    {"msg": "hi", "b": b("calm", intent="conversation", acts=[]), "t": rq.think()},
]}
for sp in ("friend", "focus", "template"):
    r = rq.run(spec, sp)
    print("==", sp)
    for t in r["turns"]:
        print(f"  t{t['turn']} delivered={t.get('delivered')} orig_ok={t.get('orig_unchanged')} flags={t.get('flags')} :: {(t.get('reply') or t.get('error'))[:150]}")
# question then answer
spec2 = {"file": "plain", "turns": [
    {"msg": "fix it", "b": b("calm", intent="unclear", acts=[]), "t": json.dumps({**json.loads(rq.think(clarify=True)), "clarification_reason": "which_action"})},
    {"msg": "both, hurry up!", "b": b("urgent", intent="multi_step_request", acts=[{"action": "remove_duplicate_rows", "source_phrase": "both"}, {"action": "remove_empty_rows", "source_phrase": "both"}], answers_pending=True), "t": rq.think(),
     "p": rq.plan((rq.DD, "both"), (rq.ER, "both"))},
]}
for sp in ("friend", "focus", "template"):
    r = rq.run(spec2, sp)
    print("== q/a", sp)
    for t in r["turns"]:
        print(f"  t{t['turn']} delivered={t.get('delivered')} orig_ok={t.get('orig_unchanged')} flags={t.get('flags')} :: {(t.get('reply') or t.get('error'))[:200]}")
