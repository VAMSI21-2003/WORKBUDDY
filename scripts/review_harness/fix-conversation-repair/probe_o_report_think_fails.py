# ruff: noqa: E501
"""Round 3. A report request while Think fails (twice on head, once on base): does the reply give the report next to "I couldn't work out what you asked"? Same on base?
python probe_o_report_think_fails.py [--tree DIR] 2>/dev/null"""
from cr import *  # noqa
rep = json.dumps({"intent": "report_on_file", "confidence": .9, "requested_actions": [{"action": RPT, "source_phrase": "give me a report"}]})
for sp in ("template", "friend", "focus"):
    s = mk(sp, [("messy-data.xlsx", LOOSE)])
    r = s.turn("give me a report", rep, think_="garbage")
    print(sp, "|", r["outcome"], "|", r["reply"][:500], "| unchanged", r["uploads_unchanged"]); s.close()
