# ruff: noqa: E501
"""Round 2 (F3 fix): MentionCrossCheck now also reads the answering message. A correct resume whose message is only a file name that contains an action phrase ('duplicate rows jan.xlsx').
python probe_k_name_keywords.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
for sp in ("template", "friend", "focus"):
    for up, pending_msg, phrase, intent in (("duplicate rows jan.xlsx", "remove the empty rows in budget.xlsx", "remove the empty rows", "remove_empty_rows"),
                                            ("empty rows jan.xlsx", "remove duplicate rows in budget.xlsx", "remove duplicate rows", "remove_duplicate_rows")):
        s = mk(sp, [(up, MESSY), ("other.xlsx", OTHER)])
        s.turn(pending_msg, bj(intent, phrase=phrase), think_=tj())
        r = s.turn(up, bj(intent, answers=True, phrase=phrase), think_=tj())
        print(sp, "|", up, "->", r["outcome"], r["delivered_names"], "|", r["reply"][:520], "| unchanged", r["uploads_unchanged"])
        s.close()
