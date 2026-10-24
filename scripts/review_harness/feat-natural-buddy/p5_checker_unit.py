"""P5: the claim checker on its own, with a fact sheet taken from a real delivered turn.

    python p5_checker_unit.py
Timing on long hostile drafts (ReDoS), and a few one-line drafts (prints passes/refusals).
"""
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
import nb  # noqa: E402

nb.setup(str(Path(__file__).resolve().parents[3]))
from scenarios import S  # noqa: E402

from buddy.claims import check_reply  # noqa: E402

rec = nb.run(S["s01-dedupe"], "friend")
sheet_holder = {}
W = nb.T["writer"]
orig = W.build_fact_sheet
W.build_fact_sheet = lambda r: sheet_holder.setdefault("s", orig(r))
nb.run(S["s01-dedupe"], "friend")
W.build_fact_sheet = orig
sheet = sheet_holder["s"]
ids = [f.id for f in sheet.facts]

worst = 0.0
random.seed(1)
alphabet = ["no ", "not ", "removed ", "2 ", "duplicate ", "rows ", "\"", "'", ".", ",", "-", " and ", "report.xlsx ",
            "a" * 50, "(", ")", "⟦", "values only ", "kept ", "formatting "]
for _ in range(300):
    msg = "".join(random.choice(alphabet) for _ in range(200))[:600]
    t0 = time.perf_counter()
    check_reply([msg, msg, msg], ids, sheet, user_message=msg * 3)
    worst = max(worst, time.perf_counter() - t0)
print(f"worst check time over 300 random 3x600-char drafts: {worst*1000:.1f} ms")

LINES = [
    "removed 2 duplicate rows from report.xlsx. heads up, the copy keeps values only.",
    "removed 2 duplicate rows from report.xlsx and fixed the typos. values only.",
    "removed 2 duplicate rows from report.xlsx and fixed the missing values. values only.",
    "removed 2 duplicate rows from report.xlsx and backed it up. values only.",
    "removed 2 duplicate rows from report.xlsx, a couple of empty rows too. values only.",
    "removed 2 duplicate rows from report.xlsx, twenty-one rows left. values only.",
    "removed 2 duplicate rows from report.xlsx, there are 2 duplicate rows in it. values only.",
    "removed 2 duplicate rows from report.xlsx, it has no problems left at all. values only.",
    "removed 2 duplicate rows from report.xlsx and it's clean now. values only.",
]
for line in LINES:
    p = check_reply([line], ids, sheet)
    print("PASS   " if not p else "REFUSE ", line, "" if not p else [x.code for x in p])
