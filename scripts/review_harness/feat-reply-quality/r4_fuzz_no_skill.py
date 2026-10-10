"""Round 4: a fast unit-level fuzz of buddy.outcome._no_skill_outcome (the only place that decides what the reply says about an
`unsupported` phrase that holds a report request).  Design D4: "A report is never also 'something I can't do'": for a phrase where
`strip_report_request` finds a report request, the result must be None or REPORT_ONLY, never NO_SKILL.

Phrases are generated: report head x tail, where tails are negations of a named action, extras, location phrases, fillers.
The ledger is what the engine would build: the Brain entry, plus the mention-detector entries for the message (the phrase).

    python r4_fuzz_no_skill.py [--tree DIR] [--show 40]
"""
import argparse
import itertools
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--show", type=int, default=40)
A = ap.parse_args()
sys.path.insert(0, str(Path(A.tree).resolve()))
from buddy import outcome as oc  # noqa: E402
from buddy.actions import (ACTIONS, LEDGER_FROM_BRAIN, LEDGER_FROM_MENTION, RequestedAction, mentioned_actions,  # noqa: E402
                           strip_report_request, mentions_report)

assert Path(oc.__file__).resolve().is_relative_to(Path(A.tree).resolve())

HEADS = ["give me a report", "analyze my file", "just analyze my file", "summarize my data", "give me a summary of the file",
         "I only want a report", "report on the file", "a quick summary please", "check my file for problems"]
TAILS = [
    "", " but don't remove the duplicates", ", keep the duplicates", " and leave the empty rows alone", " without removing duplicates",
    ", no need to delete the blank rows", " and don't touch the duplicate rows", ", not the empty rows", " but not the duplicates",
    " and never remove empty rows", " and keep the duplicates and the empty rows", " - leave the duplicate rows",
    " but do not drop the duplicates", " and please don't delete any duplicates",
    # extras and descriptors
    " in pdf", " and send it to my boss", " and sort it by amount", " and email it", " and the empty rows", " and the duplicates",
    " and remove the duplicates", " and delete the empty rows", " then remove duplicates", " but keep it short",
    " and don't forget the duplicates", " and ignore the empty rows", " and skip the duplicates",
]
FILLER_TAILS = [" please", " thanks", " and then", " also", ", just that", " only"]


def entry_for(phrase):
    return RequestedAction("unsupported", phrase, source=LEDGER_FROM_BRAIN, phrase_found=True)


def ledger_for(phrase):
    e = entry_for(phrase)
    mentions = [RequestedAction(a.name, text, source=LEDGER_FROM_MENTION, phrase_found=True) for a, text in mentioned_actions(phrase)]
    return (e, *mentions)


rows = []
for h, t in itertools.product(HEADS, TAILS + FILLER_TAILS):
    phrase = h + t
    if not mentions_report(phrase):
        continue
    for passed in (set(), {a.capability for a in ACTIONS}):
        led = ledger_for(phrase)
        out = oc._no_skill_outcome(led[0], led, passed, True)
        _, had = strip_report_request(phrase.casefold())
        rows.append((phrase, "passed-actions" if passed else "none-passed", None if out is None else out.reason, had))

c = Counter((r[1], r[2]) for r in rows)
print(len(rows), "cases;", dict(c))
bad = [r for r in rows if r[3] and r[2] == oc.NO_SKILL]
print("report request in the phrase AND reply says NO_SKILL (can't do):", len(bad))
seen = set()
shown = 0
for phrase, passed, reason, _ in bad:
    if (phrase) in seen:
        continue
    seen.add(phrase)
    if shown < A.show:
        print(f"   [{passed}] {phrase!r}")
        shown += 1
print("distinct phrases:", len(seen))
