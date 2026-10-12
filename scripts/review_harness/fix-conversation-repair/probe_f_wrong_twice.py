# ruff: noqa: E501
"""The user names an unknown file twice, then the right one.  Brain here is 'realistic': it only resumes if the waiting request it was shown is an actual request.
python probe_f_wrong_twice.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
import re
out = {}
for sp in ("template", "friend", "focus"):
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    def realistic_brain():
        p = s.llm.brain_prompts[-1]
        m = re.search(r'waiting for the user to say which file it is about: "(.*)"', p)
        if m and "remove duplicate" in m.group(1):
            return bj(answers=True, phrase="remove duplicate rows")
        if m:  # waiting "request" is only a file name: nothing to carry out
            return bj("unclear", answers=True)
        if "remove duplicate" in p.split("User message:")[-1][:200] if "User message:" in p else False:
            return bj(phrase="remove duplicate rows")
        return bj(phrase="remove duplicate rows") if "dedupe" in p else bj("unclear")
    log = []
    for msg in ("remove duplicate rows in sales.xlsx", "sales_2024.xlsx", "messy-data.xlsx"):
        r = s.turn(msg, (lambda msg=msg: bj(phrase="remove duplicate rows") if "remove duplicate" in msg else realistic_brain()), think_=tj())
        pr = s.llm.brain_prompts[-1]
        log.append({"msg": msg, "reply": r["reply"], "delivered": r["delivered_names"], "prompt_pending_line": next((l for l in pr.splitlines() if "waiting for the user" in l), None)})
    out[sp] = log; s.close()
for sp, log in out.items():
    print("==", sp)
    for l in log: print(" ", json.dumps(l)[:700])
