"""Why: the checker's problems for drafts on a scenario's real fact sheet (no rewrite, no template).

    python why.py s01-dedupe "message one || message two" ["another draft" ...]
"""
import sys, json
sys.path.insert(0, ".")
import nb
nb.setup("../../..")
from scenarios import S
from buddy import claims, writer
cap = {}
orig = writer.check_reply
def spy(messages, facts_used, sheet, user_message=""):
    cap["sheet"] = sheet; cap["um"] = user_message
    return orig(messages, facts_used, sheet, user_message=user_message)
writer.check_reply = spy
name = sys.argv[1]
nb.run(S[name], "friend", writer=nb.echo)
sheet = cap["sheet"]
for draft in sys.argv[2:]:
    msgs = draft.split(" || ")
    pr = claims.check_reply(msgs, [f.id for f in sheet.facts], sheet, user_message=cap["um"])
    print("PASS " if not pr else "REFUSE", msgs, [(p.code, p.detail) for p in pr])
