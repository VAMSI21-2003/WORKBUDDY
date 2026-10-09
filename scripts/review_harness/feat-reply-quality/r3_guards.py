"""Round 3 probe: the new report-detector guards (efaf27d: `_REPORT_BY_THE_PERSON`, the verb alternative of `_REPORT_NEGATED`).

A guard that vetoes a genuine report request turns "Report on ..." into "I can't do that yet: <the phrase>" next to the same
findings. So: ordinary messages that ASK for a report/analysis and also contain "I can ...", "myself", "I'll ...", "don't forget",
"no need to", "won't" etc. in a non-negating sense (want=True), and messages that really decline one (want=False).
No LLM involved. The same list is run against another tree (--tree) to see what changed.

    python r3_guards.py [--tree DIR] [--show-ok]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--show-ok", action="store_true")
A = ap.parse_args()
sys.path.insert(0, str(Path(A.tree).resolve()))
from buddy.actions import mentions_report  # noqa: E402

CASES = [
    # genuine requests that contain a guard token
    ("give me a summary so I can review it", True),
    ("give me a report so I can check it", True),
    ("analyze my file so I can look at the problems", True),
    ("summarize the data so I can check the numbers", True),
    ("give me a report on this file, then I can decide what to do", True),
    ("I can't tell what's wrong with my file, can you analyze it", True),
    ("I can't read this spreadsheet, give me a summary", True),
    ("I can't find the problem myself, check my file", True),
    ("I built this file myself, can you analyze it", True),
    ("I made this spreadsheet myself. Give me a report on it", True),
    ("I'm new to this myself, give me a summary of the file", True),
    ("I'll upload a new version later; for now give me a report", True),
    ("I'll be away tomorrow, so please analyze my data now", True),
    ("I'll need a full report on the file", True),
    ("I will review it later, give me a summary now", True),
    ("I can send it to my boss after you analyze my file", True),
    ("please don't forget to include a summary of the problems", True),
    ("don't forget to give me a report on the file", True),
    ("don't worry about the formatting, just analyze my file", True),
    ("don't hesitate to give me a report on everything you find", True),
    ("never mind the duplicates, give me a summary of the file", True),
    ("no need to be fancy, just give me a report on the data", True),
    ("no need to wait, analyze my spreadsheet", True),
    ("won't take long: give me a summary of this file", True),
    ("never used this before, can you analyze my data", True),
    ("I can't wait to see the report on my file", True),
    ("can you check my data? I don't have time to do it myself", True),
    ("I can use a summary of the data", True),
    ("don't take too long, a summary of the file please", True),
    ("check my data and tell me what you find, I'm too busy to look myself", True),
    # genuine declines (must stay vetoed)
    ("don't give me a report", False),
    ("no report please", False),
    ("don't analyze my file", False),
    ("I don't want a summary", False),
    ("no need to check my file", False),
    ("no need to give me a report", False),
    ("I'll check the file myself", False),
    ("I will review the data myself", False),
    ("I can look at the file myself", False),
    ("don't send me a summary, just remove the duplicates", False),
    ("skip the report", False),
    ("without a report", False),
    ("remove the duplicate rows but don't look at my data", False),
    ("email me the report", False),
]
bad = []
for msg, want in CASES:
    got = mentions_report(msg)
    if got != want:
        bad.append((msg, want, got))
    elif A.show_ok:
        print("   ok  ", want, msg)
print(f"{len(CASES)} messages; {len(bad)} disagree with what the person means")
for msg, want, got in bad:
    print(f"   want={want!s:5} got={got!s:5} {msg!r}")
