"""Round 3 probe: the report detector (`buddy.actions.mentions_report`) and the mention detector (`mentioned_actions`) on ordinary
messages that contain a token the round-3 guards look at ("myself", "I can", "don't", "no need to", "overview", "summary" ...).
Expected values are what a person asking would mean; a mismatch is listed. No LLM involved.

    python r3_detect.py [--tree DIR]
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
A = ap.parse_args()
sys.path.insert(0, str(Path(A.tree).resolve()))
from buddy.actions import mentioned_actions, mentions_report  # noqa: E402

# (message, is it a request for a report on the file?)
CASES = [
    # asks for a report, with a token a guard reads
    ("give me a summary so I can review it", True),
    ("give me a report I can share with my team", True),
    ("analyze my file, I can't do it myself", True),
    ("can you analyze the data? I don't know how to check it myself", True),
    ("I can't check the file myself, give me a report", True),
    ("I don't have time to look at it, give me a summary", True),
    ("please don't forget to send me a summary", True),
    ("don't forget to give me a report", True),
    ("no hurry — a summary please", True),
    ("I can't tell what's wrong, can you analyze my data", True),
    ("I'm not sure what is in it. Give me an overview of the file", True),
    ("give me a report so I can decide what to do", True),
    ("I will need a report on this file", True),
    ("I can use a summary of the data", True),
    ("I'll need a summary of the file first", True),
    ("summarize the file so I can look at the problems", True),
    ("analyze my file; I can fix the rest myself", True),
    ("I'm going to need a report on this spreadsheet", True),
    ("could you check my file for issues before I send it on", True),
    ("check the file for problems please", True),
    ("take a look at my data", True),
    ("review the data and tell me what is wrong", True),
    ("what is wrong with my data", True),
    ("what are wrong with the sheet", True),
    ("give me a report on the empty rows", True),
    ("give me a report on blank rows", True),
    # not a report request
    ("remove the duplicate rows but don't look at my data", False),
    ("I'll check the file myself", False),
    ("thanks, I'll review the data now", False),
    ("remove the duplicate rows from the overview sheet", False),
    ("remove duplicates by the Analysis column", False),
    ("give me an overview of what you can do", False),
    ("can you give me a summary of what you do?", False),
    ("no need to check my file", False),
    ("no report please", False),
    ("email me the report", False),
    ("where can I find the report?", False),
    ("remove the duplicates in the Summary tab", False),
    ("thanks for the summary", False),
    ("sort by summary", False),
    ("clean up the rows under Health Check", False),
    ("I did a quality check myself", False),
]
bad = []
for msg, want in CASES:
    got = mentions_report(msg)
    if got != want:
        bad.append((msg, want, got))
print(f"{len(CASES)} messages; {len(bad)} where the detector disagrees with what the person means")
for msg, want, got in bad:
    print(f"   want={want!s:5} got={got!s:5} {msg!r}")

# the mention detector: what a report is about must not be read as a request to do it
print("\nmentioned_actions (action names):")
for msg in ["give me a report on the empty rows", "report on duplicate and empty rows", "check my file for blank rows",
            "summary of the blank rows", "report on the fully-empty rows", "give me a report on the duplicates",
            "check my file for duplicates and remove them", "give me a summary of the duplicate rows and remove them",
            "remove the duplicate rows and give me a report on the empty rows", "analyze the file, then delete the empty rows",
            "show me the empty rows", "how many blank rows are there?", "count the duplicate rows", "list the duplicates"]:
    print(f"   {[a.name for a, _ in mentioned_actions(msg)]!s:55} {msg!r}")

# time: a long message must not hang the patterns
for label, msg in [("give me x" * 3000, "give me x" * 3000), ("no " * 3000 + "report", "no " * 3000 + "report"),
                   ("the " * 4000 + "summary", "the " * 4000 + "summary"), ("I can " * 3000 + "check", "I can " * 3000 + "check"),
                   ("don't " + "a " * 3000, "don't " + "a " * 3000)]:
    t = time.perf_counter()
    mentions_report(msg)
    mentioned_actions(msg)
    print(f"   {time.perf_counter() - t:6.3f}s  {len(msg)} chars  {label[:30]!r}")
