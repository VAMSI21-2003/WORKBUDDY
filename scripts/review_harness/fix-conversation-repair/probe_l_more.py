# ruff: noqa: E501
"""Round 2: resumed report; resumed unsupported; one-upload unknown name with Brain's unsupported entry for the name; stale pending after a stage failure on an unrelated message.
python probe_l_more.py 2>/dev/null"""
from cr import *  # noqa
import dataclasses
def show(sp, tag, r):
    print(sp, "|", tag, "|", r["outcome"], r["delivered_names"], "|", r["reply"][:600], "| unchanged", r["uploads_unchanged"])
for sp in ("template", "friend", "focus"):
    # l1: resumed report
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    rep = lambda ans: json.dumps({"intent": "report_on_file", "confidence": .9, "requested_actions": [{"action": RPT, "source_phrase": "give me a report"}], **({"answers_pending": True} if ans else {})})
    s.turn("give me a report on budget.xlsx", rep(False), think_=tj())
    show(sp, "l1 resumed report", s.turn("messy-data.xlsx", rep(True), think_=tj())); s.close()
    # l2: resumed, Brain reads the waiting request as unsupported
    s = mk(sp, [("messy-data.xlsx", LOOSE), ("other.xlsx", OTHER)])
    s.turn("remove duplicate rows in budget.xlsx", bj(phrase="remove duplicate rows"), think_=tj())
    show(sp, "l2 resumed unsupported", s.turn("messy-data.xlsx", json.dumps({"intent": "unsupported", "confidence": .9, "answers_pending": True, "requested_actions": [{"action": "unsupported", "source_phrase": "remove duplicate rows"}]}), think_=tj())); s.close()
    # l3: one upload, a request naming a file that is not there, Brain lists the name request as unsupported
    s = mk(sp, [("messy-data.xlsx", LOOSE)])
    show(sp, "l3 one upload, save as", s.turn("remove duplicate rows and save it as cleaned.xlsx", json.dumps({"intent": "remove_duplicate_rows", "confidence": .9, "requested_actions": [{"action": RD, "source_phrase": "remove duplicate rows"}, {"action": "unsupported", "source_phrase": "save it as cleaned.xlsx"}]}), think_=tj())); s.close()
    # l4: one upload, a different name than the upload
    s = mk(sp, [("messy-data.xlsx", LOOSE)])
    show(sp, "l4 one upload, other name", s.turn("remove duplicate rows in sales_2024.xlsx", bj(phrase="remove duplicate rows"), think_=tj())); s.close()
