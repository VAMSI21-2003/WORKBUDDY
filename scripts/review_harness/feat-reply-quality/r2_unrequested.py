"""Round 2 probe U: messages that contain a word the widened report detector now matches but that do not ask for a report
("I'll check the file myself", "don't look at my data", "the Overview sheet"). Round 1's L2 for the new alternatives
(check / look at / review / inspect the file; what's wrong with ...; rundown; overview) and for their negation.
Run on head and base; compare whether an unrequested "Report on ..." block appears.

    python r2_unrequested.py [--tree DIR]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import brain  # noqa: E402

DD = ("remove_duplicate_rows", "remove the duplicate rows")
CASES = [
    ("remove the duplicate rows, I'll check the file myself", "remove_duplicate_rows", [DD]),
    ("remove the duplicate rows but don't look at my data", "remove_duplicate_rows", [("remove_duplicate_rows", "remove the duplicate rows")]),
    ("remove the duplicate rows. No need to review the file.", "remove_duplicate_rows", [("remove_duplicate_rows", "remove the duplicate rows")]),
    ("remove the duplicate rows and do not inspect the data", "remove_duplicate_rows", [("remove_duplicate_rows", "remove the duplicate rows")]),
    ("remove the duplicate rows from the overview sheet", "remove_duplicate_rows", [("remove_duplicate_rows", "remove the duplicate rows from the overview sheet")]),
    ("remove the duplicate rows, I will take a look at the file after", "remove_duplicate_rows", [("remove_duplicate_rows", "remove the duplicate rows")]),
    ("thanks, I'll review the data now", "conversation", []),
    ("thanks! I'll look at the file", "conversation", []),
    ("hi, can you check the file?", "conversation", []),
    ("remove the duplicate rows and don't give me a report", "remove_duplicate_rows", [("remove_duplicate_rows", "remove the duplicate rows")]),
]
for msg, intent, acts in CASES:
    spec = dict(file="plain", turns=[dict(msg=msg, b=brain(intent, *acts))])
    r = rq.run(spec, "template")["turns"][0]
    rep = r["reply"]
    print(f"\n{msg!r}\n   report_block={'Report on' in rep}  tackle_question={'tackle' in rep}  delivered={r['delivered']}\n   {rep}")
