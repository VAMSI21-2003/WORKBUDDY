# ruff: noqa: E501
"""Round 2: Brain returns non-JSON twice on the answering turn; is the waiting request kept, does the retry deliver?  (mutation n04 survives the suite)
python probe_m_brain_fails_twice.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
import glob
for sp in ("template", "friend", "focus"):
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.turn("remove duplicate rows in budget.xlsx", bj(phrase="remove duplicate rows"), think_=tj())
    r1 = s.turn("messy-data.xlsx", "not json at all", think_=tj())
    ps = glob.glob(str(s.app.settings.sessions_dir / "*" / "session.json"))
    pend = json.loads(open(ps[0]).read()).get("pending_request")
    real = lambda: bj(answers=True, phrase="remove duplicate rows") if "waiting for the user" in s.llm.brain_prompts[-1] else bj("unclear")  # a bare name with nothing waiting is not a request
    r2 = s.turn("messy-data.xlsx", real, think_=tj())
    print(sp, "| fail turn:", r1["outcome"], r1["reply"][:90], "| pending kept:", pend is not None, "| retry:", r2["outcome"], r2["delivered_names"])
    s.close()
