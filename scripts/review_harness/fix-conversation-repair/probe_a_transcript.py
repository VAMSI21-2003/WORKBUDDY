# ruff: noqa: E501
"""The owner's transcript, replayed through the real app x template/friend/focus.  python probe_a_transcript.py [--tree DIR] [--out F]"""
from cr import *  # noqa
OUT = outpath("a-head.json")
res = {}

def run(speaker):
    s = mk(speaker, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    log = []
    def t(msg, b, th=None, **kw):
        r = s.turn(msg, b, think_=th, **kw); log.append(short(r)); return r
    # T1: Think fails (non-JSON twice) -> old honest reply
    t("remove duplicate rows", bj(phrase="remove duplicate rows"), th="not json at all")
    # T2: Think asks which_file though active upload exists
    t("remove duplicate rows", bj(phrase="remove duplicate rows"), th=tj("which_file"))
    # T3: names a typo'd file -> expect the question + pending
    r = t("remove duplicate rows in mesy-dta.xlsx", bj(phrase="remove duplicate rows"), th=tj())
    # T4: answer with correct (typo) name
    s.llm.brain_prompts.clear()
    r = t("messy-data.xslx", bj(answers=True, phrase="remove duplicate rows"), th=tj())
    log[-1]["brain_prompt_has_pending"] = "waiting for the user" in (s.llm.brain_prompts[-1] if s.llm.brain_prompts else "")
    # T5: empty rows in a typo'd name
    t("now remove the empty rows too in mess-data.xlsx", bj("remove_empty_rows", "remove the empty rows"), th=tj())
    # T6: start over
    t("start over from my original file and remove the empty rows", bj("remove_empty_rows", "remove the empty rows", file="original"), th=tj())
    log.append({"state_files": s.state().get("next_request_file")})
    s.close()
    return log

for sp in ("template", "friend", "focus"):
    try:
        res[sp] = run(sp)
    except Exception as e:
        import traceback; res[sp] = {"EXC": traceback.format_exc()}
dump(OUT, res)
for sp, log in res.items():
    print("==", sp)
    if isinstance(log, dict): print(log); continue
    for r in log:
        print(json.dumps({k: v for k, v in r.items() if k in ("label", "outcome", "reply", "delivered_names", "final_describe", "uploads_unchanged", "voice_calls", "brain_prompt_has_pending", "next_request_file")}, indent=None)[:1500])
