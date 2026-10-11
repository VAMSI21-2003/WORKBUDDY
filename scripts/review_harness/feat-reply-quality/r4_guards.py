"""Round 4: the detector (`buddy.actions.mentions_report`) after the round-3 fix, on 120 messages I wrote that are NOT in r3_guards.py.
want=True: the person asks for a report/analysis of the file.  want=False: they decline one, will do it themselves, or ask for something else.
Prints the disagreements, and (with --tree2) the cases where two trees differ.

    python r4_guards.py [--tree DIR] [--tree2 DIR2] [--show-ok]
"""
import argparse
import importlib
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--tree2", default=None)
ap.add_argument("--show-ok", action="store_true")
A = ap.parse_args()

CASES = [
    # --- genuine requests with guard-ish tokens ---
    ("give me a summary so I can decide what to fix", True),
    ("give me a report, I'll fix the rest myself", True),
    ("I can never find the errors myself, can you analyze it?", True),
    ("I can't check this myself, please summarize the file", True),
    ("I'd like a summary of the file; I'll review the details later", True),
    ("let me know what you find: analyze my file", True),
    ("let me see a report on this file", True),
    ("let me have a summary of the data", True),
    ("I want to see a summary of my data", True),
    ("I want a full report on the file, I will read it tonight", True),
    ("I'm going to need a report on this spreadsheet", True),
    ("I will need an analysis of the file before the meeting", True),
    ("I can use an overview of the data", True),
    ("summarize the file; I can't do it myself", True),
    ("could you analyze my file? I'm not good at spreadsheets myself", True),
    ("I tried to analyze it myself but gave up. Please analyze my file", True),
    ("I tried to check the file myself but could not. Can you check the file for problems?", True),
    ("I looked at the data myself and it seems odd. What's wrong with my data?", True),
    ("don't forget a summary of the data", True),
    ("don't hesitate to analyze my file thoroughly", True),
    ("never hesitate to give me a report", True),
    ("no need to hurry, just give me a report on the file", True),
    ("don't worry about the empty rows, just give me a summary", True),
    ("no problem if it takes a while: analyze my data", True),
    ("without delay, give me a summary of the file", True),
    ("without fuss, a report on the duplicates please", True),
    ("not urgent, but give me a report on my file", True),
    ("not sure what's in it - summarize my data", True),
    ("please skip the formatting and give me a summary of the file", True),
    ("skip to the point: give me a report on the file", True),
    ("no formatting needed, just analyze my file", True),
    ("don't remove anything, just give me a summary of the file", True),
    ("give me a summary of the file, don't change anything", True),
    ("give me a report on the file but don't touch the formatting", True),
    ("check my file for problems, don't fix them", True),
    ("check my file for problems, I'll fix them myself", True),
    ("review my data for mistakes, I will correct them myself", True),
    ("look at my data and tell me what's wrong with it", True),
    ("take a look at my spreadsheet for errors", True),
    ("what's wrong with my file? I can't tell", True),
    ("what is wrong with this sheet", True),
    ("can you inspect my workbook?", True),
    ("Could you run a quality check on the file?", True),
    ("do a sanity check on my data", True),
    ("give me a health check of the file", True),
    ("i'd love a rundown of the data", True),
    ("an analysis of my file would be great", True),
    ("A SUMMARY OF MY DATA PLEASE", True),
    ("summarise my file", True),
    ("analyse the data", True),
    ("Show me a summary.", True),
    # --- declines and other asks that must NOT be read as a report request ---
    ("I want to analyze it myself", False),
    ("I'd rather analyze the data myself", False),
    ("I'll do the analysis myself", False),
    ("I'm going to summarize it myself, you just remove the duplicates", False),
    ("let me analyze the file myself first", False),
    ("let me review the data myself", False),
    ("I should check the file myself", False),
    ("I need to look at the data myself", False),
    ("don't summarize my data", False),
    ("do not analyze the file", False),
    ("never give me a report", False),
    ("without a summary, please", False),
    ("no analysis needed", False),
    ("no need for a report", False),
    ("skip the summary, just remove the duplicates", False),
    ("please don't check my file", False),
    ("I don't need a report", False),
    ("I don't want you to look at my data", False),
    ("don't look over the spreadsheet", False),
    ("I won't need a summary", False),
    ("send the summary to my boss", False),
    ("e-mail the report to me", False),
    ("print the analysis", False),
    ("upload the report to the drive", False),
    ("export a summary as csv", False),
    ("share the report with my team", False),
    ("remove the duplicate rows from the Summary sheet", False),
    ("clean the Analysis tab", False),
    ("delete the summary column", False),
    ("remove duplicates from the Overview sheet", False),
    ("thanks for the summary!", False),
    ("where is the report?", False),
    ("what can you do?", False),
    ("give me an overview of what you can do", False),
    ("a summary of what you offer", False),
    ("hello", False),
    ("remove the duplicate rows", False),
    ("remove empty rows and then sort by amount", False),
    ("sort the file", False),
    ("check the formatting of cell B2", False),
    ("I like the new layout", False),
    ("have a look at it and remove the duplicates", False),
    # --- ambiguous bare nouns (the bare noun is read as a request; recorded, not scored as a defect unless want=False) ---
    ("remove the duplicate rows from my sales report", False),
    ("update the summary", False),
    ("finalize the report", False),
    ("approve the analysis", False),
    ("proofread my summary", False),
    ("the report is due tomorrow, remove the duplicates", False),
    ("my analysis shows duplicates, remove them", False),
    ("I need the file cleaned for the report", False),
    ("clean the data for my report", False),
    ("this is for the quarterly summary, remove the duplicates", False),
    # --- the fix's own tokens in other roles ---
    ("I'll upload it again, please give me a summary", True),
    ("I can send you the other sheet later; analyze this one now", True),
    ("I can attach the other file if you give me a report on this one", True),
    ("let me upload a new one after you summarize this", True),
    ("I'll be quick: summary of the file please", True),
    ("I will wait for the summary of the file", True),
    ("I can wait. Analyze my data when you can", True),
    ("I can't wait for the analysis of my file", True),
    ("I'm going to bed, summarize my file for tomorrow", True),
    ("myself and my team need a summary of the file", True),
    ("my boss and myself need a report on the data", True),
    ("I built it myself and want a summary of it", True),
    ("please check the data for errors, I did it myself and expect mistakes", True),
    ("I typed it all myself; what's wrong with my data?", True),
    ("I prepared the file myself. Take a look at my data.", True),
]


def load(tree):
    for m in [m for m in sys.modules if m == "buddy" or m.startswith("buddy.")]:
        del sys.modules[m]
    sys.path.insert(0, str(Path(tree).resolve()))
    try:
        mod = importlib.import_module("buddy.actions")
        assert Path(mod.__file__).resolve().is_relative_to(Path(tree).resolve()), mod.__file__
        return {m: mod.mentions_report(m) for m, _ in CASES}
    finally:
        sys.path.pop(0)


head = load(A.tree)
bad = [(m, w, head[m]) for m, w in CASES if head[m] != w]
print(f"{len(CASES)} messages; {len(bad)} disagree with what the person means (tree={A.tree})")
for m, w, g in bad:
    print(f"   want={w!s:5} got={g!s:5} {m!r}")
if A.show_ok:
    for m, w in CASES:
        if head[m] == w:
            print("   ok  ", w, m)
if A.tree2:
    other = load(A.tree2)
    diff = [(m, other[m], head[m]) for m, _ in CASES if other[m] != head[m]]
    print(f"\n{len(diff)} messages differ between {A.tree2} and {A.tree}:")
    for m, o, h in diff:
        want = dict(CASES)[m]
        print(f"   want={want!s:5} tree2={o!s:5} tree={h!s:5} {m!r}")
