"""Round 5: calls buddy.outcome._no_skill_outcome directly over a generated corpus of Brain 'unsupported'
phrases, with the dedupe action passed (so a dropped entry means 'said nothing'). Prints the phrases that
DROP the entry (None) although they hold a word that widens the scope beyond one sheet.
Usage: python scope_diff.py [TREE]   (default: this checkout)"""
import itertools, sys, json
from pathlib import Path
tree = sys.argv[1] if len(sys.argv) > 1 else str(Path(__file__).resolve().parents[2])
sys.path.insert(0, tree)
from buddy.outcome import _no_skill_outcome
from buddy.models import RequestedAction

DD = "spreadsheet.deduplicate"
ledger0 = (RequestedAction("remove_duplicate_rows", "remove duplicates", "brain", True),)
verbs = ["remove duplicates", "remove the duplicate rows", "delete the duplicates", "dedupe", "get rid of the repeated entries",
         "remove all duplicates", "remove duplicate rows"]
preps = ["in", "on", "from", "across", "within", "throughout", "inside", "for", "over", "through", "among", "between"]
dets = ["", "the ", "this ", "my ", "our ", "your ", "that ", "all ", "every ", "each ", "both ", "all the ", "the whole ", "the entire ", "the other ", "the second ", "the remaining "]
nouns = ["sheet", "sheets", "file", "files", "spreadsheet", "spreadsheets", "workbook", "workbooks", "table", "tables", "data",
         "tab", "tabs", "worksheet", "worksheets", "document", "book", "excel file", "xlsx", "sheet 2", "sheet2", "q2", "pages",
         "all sheets", "every sheet", "each tab", "other sheets", "second sheet", "the rest"]
WIDE = ["all", "every", "each", "both", "other", "second", "remaining", "workbook", "across", "sheets", "tabs", "files",
        "worksheet", "whole", "entire", "rest", "sheet 2", "sheet2", "q2", "multiple", "several", "two", "3", "2"]
bad = []
n = 0
for v, p, d, nn in itertools.product(verbs, preps, dets, nouns):
    phrase = f"{v} {p} {d}{nn}"
    n += 1
    out = _no_skill_outcome(RequestedAction("unsupported", phrase, "brain", True), ledger0 + (RequestedAction("unsupported", phrase, "brain", True),), {DD})
    tail = f"{p} {d}{nn}"
    if out is None and any(w in tail.split() or w in tail for w in WIDE):
        bad.append(phrase)
print(n, "phrases;", len(bad), "dropped although they hold a scope word")
for b in bad[:60]:
    print("  DROPPED:", b)
