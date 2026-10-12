# ruff: noqa: E501
"""Round 2. The fixed 'I took your last message as the answer ... and went on with your earlier request.' sentence: on which replies does it appear, and is it true there?
Cases: Think fails twice on the answering turn; Brain says answers_pending but gives intent unclear / no phrase; plan declined; file refused; Think asks another question; follow-up already satisfied.
python probe_j_resumed_replies.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
import re
Q = "remove duplicate rows in budget.xlsx"
SENT = "went on with your earlier request"
out = {}

def case(name, fn, uploads=None, speakers=("template", "friend", "focus")):
    for sp in speakers:
        s = mk(sp, uploads or [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
        try:
            fn(s)
        except Exception:
            import traceback; s.log.append({"EXC": traceback.format_exc()})
        out[f"{name}/{sp}"] = s.log
        print("==", name, sp)
        for r in s.log:
            if "EXC" in r: print(r["EXC"][-600:]); continue
            print("  ", json.dumps({"msg": r["msg"][:50], "outcome": r["outcome"], "delivered": r["delivered_names"], "sentence": SENT in (r["reply"] or ""), "reply": r["reply"][:420], "unchanged": r["uploads_unchanged"]}))
        s.close()

ask = lambda s: s.turn(Q, bj(phrase="remove duplicate rows"), think_=tj())
ans = bj(answers=True, phrase="remove duplicate rows")

case("j1_think_fails_twice", lambda s: (ask(s), s.turn("messy-data.xlsx", ans, think_="garbage")))
case("j2_answers_true_intent_unclear", lambda s: (ask(s), s.turn("messy-data.xlsx", bj("unclear", answers=True), think_=tj())))
case("j3_answers_true_no_phrase", lambda s: (ask(s), s.turn("messy-data.xlsx", bj(answers=True), think_=tj())))
case("j4_plan_declined", lambda s: (ask(s), s.turn("messy-data.xlsx", ans, think_=tj(), decision="reject")))
case("j5_think_asks_another", lambda s: (ask(s), s.turn("messy-data.xlsx", ans, think_=tj("unclear_request"))))
def j6(s):
    ask(s)
    s.turn("messy-data.xlsx", ans, think_=tj())
    s.turn(Q, bj(phrase="remove duplicate rows"), think_=tj())
    s.turn("messy-data.xlsx", ans, think_=tj())   # 2nd round: latest copy already deduped -> nothing left?
case("j6_twice", j6)
