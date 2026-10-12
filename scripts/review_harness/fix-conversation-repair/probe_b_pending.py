# ruff: noqa: E501
"""Pending request ("which file?" asked, then answered): resume, misread, restart, fail. python probe_b_pending.py [--tree DIR] [--out F] 2>/dev/null"""
from cr import *  # noqa
import glob
OUT = outpath("b-head.json")
res = {}
Q = "remove duplicate rows in budget.xlsx"

def sj(s):
    ps = glob.glob(str(s.app.settings.sessions_dir / "*" / "session.json"))
    return json.loads(open(ps[0]).read()).get("pending_request", "ABSENT") if ps else "NOFILE"

def run(name, speaker, fn, uploads=None):
    s = mk(speaker, uploads or [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.log = []
    try:
        fn(s)
    except Exception:
        import traceback; s.log.append({"EXC": traceback.format_exc()})
    res[f"{name}/{speaker}"] = s.log
    s.close()

def T(s, msg, b, th=None, **kw):
    r = s.turn(msg, b, think_=th, **kw)
    r["pending_after"] = sj(s)
    r["brain_prompt_pending"] = ("waiting for the user" in s.llm.brain_prompts[-1]) if s.llm.brain_prompts else None
    return r

def b1(s):  # question asked, answered, Brain says it is only the answer
    T(s, Q, bj(phrase="remove duplicate rows"), tj())          # unclear name -> ask
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b2(s):  # Brain says the answer is a request of its own (false) -> message read as usual
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "messy-data.xlsx", bj("unclear", answers=False), tj())
def b3(s):  # Brain misreads: new different request, says answers_pending true
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "actually just remove the empty rows in messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b4(s):  # next message names no file -> pending not offered, cleared
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "hello", bj("greeting"), tj())
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b5(s):  # answers_pending true but nothing was pending
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b6(s):  # answers_pending as string / 1 -> ignored
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "messy-data.xlsx", bj(phrase="remove duplicate rows", extra={"answers_pending": "true"}), tj())
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "messy-data.xlsx", bj(phrase="remove duplicate rows", extra={"answers_pending": 1}), tj())
def b7(s):  # answer turn fails (Think non-JSON twice) -> pending kept? then retry succeeds
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), "garbage")
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b8(s):  # pending request is a chain; answer resumes
    T(s, "remove duplicates and then remove the empty rows in budget.xlsx",
      bj("multi_step_request", extra={"requested_actions": [{"action": RD, "source_phrase": "remove duplicates"}, {"action": RE, "source_phrase": "remove the empty rows"}]}), tj())
    T(s, "messy-data.xlsx", bj("multi_step_request", answers=True, extra={"requested_actions": [{"action": RD, "source_phrase": "remove duplicates"}, {"action": RE, "source_phrase": "remove the empty rows"}]}), tj(),
      p=plan((DD, "remove duplicates"), (ER, "remove the empty rows")))
def b9(s):  # answer names a different upload than the pending's "intended" one; still resumes on it
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "other.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b10(s):  # Brain says answers true and a phrase NOT in the pending request -> dropped?
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "messy-data.xlsx", bj("remove_empty_rows", answers=True, phrase="remove the empty rows"), tj())
def b11(s):  # asked twice in a row (still unclear), then answered
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    T(s, "budget2.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())
def b12(s):  # restart between question and answer
    T(s, Q, bj(phrase="remove duplicate rows"), tj())
    s.app.client.close()
    # new app on the same data dir
    from backend.main import create_app
    s.app.client = pr3.TestClient(create_app(s.app.settings, s.app.factory), raise_server_exceptions=False)
    T(s, "messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), tj())

for sp in ("template", "friend", "focus"):
    for n, f in (("b1", b1), ("b2", b2), ("b3", b3), ("b4", b4), ("b5", b5), ("b6", b6), ("b7", b7), ("b8", b8), ("b9", b9), ("b10", b10), ("b11", b11), ("b12", b12)):
        run(n, sp, f)

dump(OUT, res)
for k, log in res.items():
    if not k.endswith("/friend") and "--all" not in sys.argv: continue
    print("==", k)
    for r in log:
        if "EXC" in r: print(r["EXC"][-800:]); continue
        print(" ", json.dumps({x: r.get(x) for x in ("label", "outcome", "reply", "delivered_names", "final_describe", "pending_after", "brain_prompt_pending", "uploads_unchanged")})[:1300])
