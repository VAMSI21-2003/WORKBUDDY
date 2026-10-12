# ruff: noqa: E501
"""Round 3. The resumed sentence after the F5 narrowing, and the F6 file-name exemption, attacked.
 n1 answer is a delivered COPY's name containing an action word (not in file_names) after an empty-rows request
 n2 resumed report + Think fails twice / Brain unclear / Brain failed (sentence must be absent when no report given)
 n3 resumed dedupe + an unsupported extra ("and translate it") -> Partly done: sentence?
 n4 resumed 'dedupe then give me a report' -> sentence once
 n5 resumed, plan confirmation declined, file refused (all-empty upload)
 n6 three-turn leak: resumed, then plain request, then question
 n7 hostile file name containing the sentence / action words / regex chars; name == whole request phrase
 n8 a message that contains the file name AND a real request word, name is a substring of the request
python probe_n_round3.py 2>/dev/null"""
from cr import *  # noqa
import re
SENT = "went on with your earlier request"
EMPTYALL = {"Name": [None, None], "Email": [None, None], "Amount": [None, None]}

def show(sp, tag, r):
    rep = r["reply"] or ""
    print(f"{sp} | {tag} | {r['outcome']} {r['delivered_names']} | sentence={rep.count(SENT)} | unchanged={r['uploads_unchanged']}\n      {rep[:700]}")

def rpt(ans, extra=()):
    return json.dumps({"intent": "report_on_file", "confidence": .9, "requested_actions": [{"action": RPT, "source_phrase": "give me a report"}, *extra], **({"answers_pending": True} if ans else {})})

for sp in ("template", "friend", "focus"):
    # n1: copy name as the answer
    s = mk(sp, [("report.xlsx", MESSY), ("other.xlsx", OTHER)])
    s.turn("remove duplicate rows in report.xlsx", bj(phrase="remove duplicate rows"), think_=tj())
    s.turn("remove the empty rows in budget.xlsx", bj("remove_empty_rows", phrase="remove the empty rows"), think_=tj())
    show(sp, "n1 answer = copy name 'report_deduplicated.xlsx'", s.turn("report_deduplicated.xlsx", bj("remove_empty_rows", answers=True, phrase="remove the empty rows"), think_=tj())); s.close()
    # n2a: resumed report, Think fails twice
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.turn("give me a report on budget.xlsx", rpt(False), think_=tj())
    show(sp, "n2a report, think fails twice", s.turn("messy-data.xlsx", rpt(True), think_="garbage")); s.close()
    # n2b: resumed report, Brain fails twice
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.turn("give me a report on budget.xlsx", rpt(False), think_=tj())
    show(sp, "n2b report, brain fails twice", s.turn("messy-data.xlsx", "junk", think_=tj())); s.close()
    # n3: resumed dedupe with an unsupported extra
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    two = json.dumps({"intent": "remove_duplicate_rows", "confidence": .9, "requested_actions": [{"action": RD, "source_phrase": "remove duplicate rows"}, {"action": "unsupported", "source_phrase": "translate it to french"}]})
    s.turn("remove duplicate rows in budget.xlsx and translate it to french", two, think_=tj())
    show(sp, "n3 resumed + unsupported extra", s.turn("messy-data.xlsx", json.dumps({**json.loads(two), "answers_pending": True}), think_=tj())); s.close()
    # n4: resumed dedupe + report
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    both = lambda a: json.dumps({"intent": "remove_duplicate_rows", "confidence": .9, "requested_actions": [{"action": RD, "source_phrase": "remove the duplicate rows"}, {"action": RPT, "source_phrase": "give me a report"}], **({"answers_pending": True} if a else {})})
    s.turn("remove the duplicate rows in budget.xlsx and then give me a report", both(False), think_=tj())
    show(sp, "n4 resumed dedupe+report", s.turn("messy-data.xlsx", both(True), think_=tj())); s.close()
    # n5: plan declined / file refused
    s = mk(sp, [("empty.xlsx", EMPTYALL), ("other.xlsx", OTHER)])
    s.turn("remove duplicate rows in budget.xlsx", bj(phrase="remove duplicate rows"), think_=tj())
    show(sp, "n5 answer names an all-empty file", s.turn("empty.xlsx", bj(answers=True, phrase="remove duplicate rows"), think_=tj())); s.close()
    # n6: leak
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.turn("remove duplicate rows in budget.xlsx", bj(phrase="remove duplicate rows"), think_=tj())
    s.turn("messy-data.xlsx", bj(answers=True, phrase="remove duplicate rows"), think_=tj())
    show(sp, "n6a next plain", s.turn("what can you do?", json.dumps({"intent": "capabilities", "confidence": .9}), think_=tj()))
    s.close()
    # n7: name equal to the request phrase
    s = mk(sp, [("remove duplicate rows.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.turn("remove duplicate rows in budget.xlsx", bj(phrase="remove duplicate rows"), think_=tj())
    show(sp, "n7 file named like the request", s.turn("remove duplicate rows.xlsx", bj(answers=True, phrase="remove duplicate rows"), think_=tj())); s.close()
    # n8: real request mentions an action that is also in the file name, Brain misses it
    s = mk(sp, [("empty rows jan.xlsx", MESSY), ("other.xlsx", OTHER)])
    show(sp, "n8 request wants empty rows, file named 'empty rows jan', Brain lists only dedupe", s.turn("remove duplicate rows and the empty rows in empty rows jan.xlsx", bj(phrase="remove duplicate rows"), think_=tj())); s.close()
