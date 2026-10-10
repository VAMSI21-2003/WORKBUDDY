"""Round 4 (second reviewer): `buddy.actions.mentions_report` on 150+ messages I wrote, ordinary chat style
(no punctuation, typos, politeness, other work in the same message). want=True: the person asks Buddy for a report /
analysis of the file. want=False: they decline one, say they will do it, or ask for something else.

    python r4_detector.py [--tree DIR] [--tree2 DIR2] [--show-ok]
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

T, F = True, False
CASES = [
    # ---- plain requests, ordinary chat style ----
    ("can you give me a quick summary of this file", T),
    ("pls summarize my data", T),
    ("summary please", T),
    ("report please", T),
    ("a report would be nice", T),
    ("hey can you analyze this spreadsheet for me", T),
    ("could you check my data for any problems", T),
    ("what problems does my file have", F),   # not covered by the detector's wording; recorded as a paraphrase miss
    ("what is wrong with my spreadsheet", T),
    ("whats wrong with my file", T),
    ("tell me what is wrong with the data", T),
    ("give me an analysis", T),
    ("analysis of the file", T),
    ("do an analysis on my data", T),
    ("run a quality check", T),
    ("can I get a summary of the sheet", T),
    ("can i get a report", T),
    ("I'd like a report", T),
    ("I need an overview of my data", T),
    ("show me the problems in my file", F),   # paraphrase miss, recorded
    ("look over my file and tell me what you see", T),
    ("take a look at my spreadsheet", T),
    ("have a look at the data please", T),
    ("review the file", T),
    ("inspect the workbook", T),
    ("check my sheet for errors", T),
    ("check the file for blank rows", T),
    ("check my data for missing values", T),
    # ---- requests with a negation-ish or first-person word NEAR the noun, no punctuation ----
    ("no worries just give me a summary of the file", T),
    ("no rush give me a report on my data", T),
    ("not sure what is in here give me a summary", T),
    ("i dont know whats wrong give me a report", T),
    ("i don't have time give me a summary", T),
    ("don't delete it give me a summary", T),
    ("dont change anything just give me a report", T),
    ("without changing anything give me a summary", T),
    ("without touching it give me a report on the file", T),
    ("no changes please just a summary", T),
    ("no changes just a report of the file", T),
    ("not now but later give me a summary of this", T),
    ("not interested in cleaning just the summary", T),
    ("never mind the formatting give me a report", T),
    ("no need to fix anything give me a summary of the problems", T),
    ("no need to clean it just show me a report", T),
    ("i have no idea what is wrong can you analyze it", T),
    ("there is no way I can read all this please summarize it", T),
    ("nobody told me what is in this file can you summarize it", T),
    ("not a big file give me a summary", T),
    ("not too long a report on my data please", T),
    ("not in detail just a short summary", T),
    ("no fancy formatting needed a summary is fine", T),
    ("no thanks just a summary", T),
    ("no need to be fancy give me a report", T),
    ("I dont need it fixed I need a summary", T),
    ("dont fix anything report only", T),
    ("report only no changes", T),
    ("just a report no cleanup", T),
    ("a report but dont modify the file", T),
    ("I won't be around so give me a report now", T),
    ("I will not have time to read the file so summarize it", T),
    ("I can not open this file give me a summary", T),
    ("I can't tell what's wrong summarize my data", T),
    ("i cant see any problems myself can you analyze my file", T),
    ("tell me what is wrong with my data I cant find it myself", F),
    # ---- first-person plans (Buddy not asked) ----
    ("I will analyze it myself later", F),
    ("I'll look at the data myself", F),
    ("I'll check the file tomorrow", F),
    ("I will summarize it myself thanks", F),
    ("I am going to review the data myself", F),
    ("I'll review it myself", F),
    ("let me check the file myself", F),
    ("let me look at the data", F),
    ("I want to look at the file myself first", F),
    ("I'd rather check the data myself", F),
    ("I prefer to analyze it myself", F),
    ("I do the analysis myself", F),
    ("my team will analyze the data", F),
    ("my boss will review the file", F),
    # ---- declines ----
    ("don't give me a report", F),
    ("do not give me a summary", F),
    ("no summary", F),
    ("no summary please just remove duplicates", F),
    ("I do not want a report", F),
    ("I dont want a summary", F),
    ("I don't need an analysis", F),
    ("no need for an overview", F),
    ("skip the report", F),
    ("without the report", F),
    ("without a summary please", F),
    ("never send me a report", F),
    ("please don't summarize anything", F),
    ("don't analyze my data", F),
    ("don't check the file", F),
    ("don't look at the file", F),
    ("don't review my data", F),
    ("do not inspect the spreadsheet", F),
    # ---- other work with a report-ish noun in it ----
    ("remove duplicates from the Summary sheet", F),
    ("remove duplicates from the summary tab", F),
    ("clean the Report sheet", F),
    ("sort the summary column", F),
    ("delete the Overview sheet", F),
    ("update the quarterly report", F),
    ("send the report to Anna", F),
    ("email me the summary", F),
    ("attach the report to an email", F),
    ("share the analysis with my team", F),
    ("print the summary", F),
    ("save the report as pdf", F),
    ("export the summary to csv", F),
    ("add a summary row at the bottom", F),
    ("add a column called Summary", F),
    ("rename the report column", F),
    ("this is the report from last week remove the duplicates", F),
    ("here is my analysis file please remove the empty rows", F),
    ("thanks for the report", F),
    ("great summary thanks", F),
    ("the report looks good", F),
    ("where can I find the summary", F),
    ("what does the report mean", F),
    ("what did you mean in the summary", F),
    ("I need the duplicates removed for the report", F),
    ("clean the file before I write the report", F),
    ("this file is for my weekly summary", F),
    ("remove blanks so the analysis works", F),
    ("remove the empty rows then I can analyze it", F),
    ("remove duplicates then I will review it", F),
    ("remove duplicates and let me review it", F),
    ("remove duplicates, I'll check the result", F),
    ("remove duplicates then check the result", F),
    ("remove the duplicates and look at the result", F),
    # ---- questions about Buddy ----
    ("what can you do", F),
    ("what kind of analysis can you do", F),
    ("can you analyze spreadsheets", F),
    ("give me an overview of what you can do", F),
    ("do you make reports", F),
    ("can you write a summary", F),
    ("hi", F),
    ("thanks", F),
    # ---- compound requests ----
    ("remove duplicates and give me a summary", T),
    ("remove the empty rows and then a report please", T),
    ("first dedupe it then summarize the data", T),
    ("clean it up and tell me what was wrong with it", F),
    ("remove duplicates and tell me what you found", F),
    ("remove duplicates and show me what changed", F),
    ("remove duplicates, then check my data for errors", T),
    ("remove duplicates and check the file for problems afterwards", T),
    ("remove empty rows and report on the duplicates", T),
    ("remove duplicates; summary of the rest please", T),
    ("remove duplicates and no report", F),
    ("remove duplicates and dont give me a report", F),
    ("remove duplicates but I dont need a summary", F),
    ("give me a report but do not remove anything", T),
    ("give me a report and dont remove the duplicates", T),
    ("give me a summary and keep everything as is", T),
    ("analyze the file but dont touch it", T),
    ("analyze my file dont fix it", T),
    ("summarize my data but leave the rows alone", T),
    ("just look dont touch", F),
    ("just analyze it", T),
    ("analyze it", T),
    ("summarize", T),
    ("analyze", T),
    ("overview", T),
    ("Analyze.", T),
    ("Analysis?", T),
    ("report?", T),
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
print(" vetoed genuine requests (want=True got=False):", sum(1 for _, w, g in bad if w))
print(" unrequested reports (want=False got=True):   ", sum(1 for _, w, g in bad if not w))
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
