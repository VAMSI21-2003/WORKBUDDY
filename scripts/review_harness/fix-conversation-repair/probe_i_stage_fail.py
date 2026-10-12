# ruff: noqa: E501
"""A stage raises on the turn that answers 'which file?' (and on the asking turn): is the waiting request kept / lost, what is the reply?  python probe_i_stage_fail.py [--tree DIR] 2>/dev/null"""
import dataclasses, glob
from cr import *  # noqa
FAIL = {"stage": None}
class Flaky:
    def __init__(self, inner, name): self.inner, self.name = inner, name
    def __call__(self, state):
        if FAIL["stage"] == self.name:
            raise RuntimeError("secret cell 4111 /srv/data gsk_FAKE")
        return self.inner(state)
def wrap(cycle):
    return dataclasses.replace(cycle, **{n: Flaky(getattr(cycle, n), n) for n in ("brain", "think", "planner", "hands", "inspector", "voice")})
def pend(s):
    ps = glob.glob(str(s.app.settings.sessions_dir / "*" / "session.json"))
    return json.loads(open(ps[0]).read()).get("pending_request", "ABSENT")
Q = "remove duplicate rows in budget.xlsx"
for sp in ("template", "friend", "focus"):
    print("=====", sp)
    for stage in ("brain", "think", "planner", "hands", "inspector", "voice"):
        s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)], wrap=wrap)
        FAIL["stage"] = None
        s.turn(Q, bj(phrase="remove duplicate rows"), think_=tj())
        FAIL["stage"] = stage
        r = s.turn("messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), think_=tj(), label=f"fail@{stage}")
        pa = pend(s); FAIL["stage"] = None
        r2 = s.turn("messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), think_=tj(), label="retry")
        print(f"  {stage:9} fail-turn: {r['outcome']} pending_after={'KEPT' if pa and pa!='ABSENT' else pa} | {r['reply'][:110]!r}\n            retry: {r2['outcome']} delivered={r2['delivered_names']} | {r2['reply'][:90]!r} secret_in_reply={'gsk_' in (r['reply'] or '') or '4111' in (r['reply'] or '')}")
        s.close()

print("\n##### same, but Brain is realistic on the retry: a bare file name with nothing waiting is not a request")
import re
for sp in ("friend",):
    for stage in ("think", "hands"):
        s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)], wrap=wrap)
        FAIL["stage"] = None
        s.turn(Q, bj(phrase="remove duplicate rows"), think_=tj())
        FAIL["stage"] = stage
        s.turn("messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), think_=tj(), label=f"fail@{stage}")
        FAIL["stage"] = None
        def b():
            p = s.llm.brain_prompts[-1]
            return bj(answers=True, phrase="remove duplicate rows") if "waiting for the user" in p else bj("unclear")
        r2 = s.turn("messy-data.xlsx", b, think_=tj(), label="retry")
        print(f"  {sp}/{stage}: retry -> {r2['outcome']} delivered={r2['delivered_names']} | {r2['reply'][:260]!r}")
        s.close()
