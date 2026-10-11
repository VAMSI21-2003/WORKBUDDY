"""Round 4: random hostile-string fuzz of the new predicates and `_no_skill_outcome` (12,000 strings built from report words, actions,
negations, apostrophe variants, newlines, zero-width and non-breaking spaces, emoji, special-casing letters, format markers).
Checks: no exception; a REPORT_ONLY outcome never has a source, a quoted phrase or a capability, and never appears when the reply
does not state the report (`report_stated=False`).

    python r4_fuzz_random.py [--tree DIR] [--n 12000] [--seed 4]
"""
import argparse
import random
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--n", type=int, default=12000)
ap.add_argument("--seed", type=int, default=4)
A = ap.parse_args()
sys.path.insert(0, str(Path(A.tree).resolve()))
from buddy import actions as a  # noqa: E402
from buddy import outcome as oc  # noqa: E402
from buddy.actions import LEDGER_FROM_BRAIN, LEDGER_FROM_MENTION, RequestedAction, mentioned_actions  # noqa: E402

assert Path(a.__file__).resolve().is_relative_to(Path(A.tree).resolve()), a.__file__
random.seed(A.seed)
TOKS = ["give", "me", "a", "report", "summary", "analyze", "analyse", "summarize", "check", "the", "file", "data", "sheet", "duplicates",
        "empty", "rows", "remove", "delete", "don't", "dont", "don’t", "not", "no", "never", "keep", "leave", "but", "and", "then", "in",
        "pdf", "email", "send", "to", "boss", "myself", "I'll", "I", "can", "please", "thanks", "everything", "first", "tab", "\n", "\t",
        "—", "…", "\U0001f600", "İ", "ß", "ǅ", "report.", "(summary)", "​", " ", "'", '"', "`", "<b>",
        "%s", "{x}", "\\"]
n = bad = 0
t0 = time.time()
for _ in range(A.n):
    s = " ".join(random.choice(TOKS) for _ in range(random.randint(1, 14)))
    if random.random() < 0.2:
        s = s.upper()
    try:
        a.mentions_report(s)
        a.is_only_a_report_request(s)
        a.strip_report_request(s)
        e = RequestedAction("unsupported", s, source=LEDGER_FROM_BRAIN, phrase_found=random.random() < 0.5)
        ledger = (e, *[RequestedAction(x.name, t, source=LEDGER_FROM_MENTION, phrase_found=True) for x, t in mentioned_actions(s)])
        for passed in (set(), {"spreadsheet.deduplicate"}, {"spreadsheet.deduplicate", "spreadsheet.remove_empty_rows"}):
            for stated in (True, False):
                o = oc._no_skill_outcome(e, ledger, passed, stated)
                if o is not None and o.reason == oc.REPORT_ONLY and (o.source or o.user_phrase or o.capability or not stated):
                    bad += 1
        n += 1
    except Exception as ex:  # noqa: BLE001
        print("EXCEPTION", repr(s), repr(ex))
        bad += 1
        break
print(n, "strings,", bad, "problems,", round(time.time() - t0, 1), "s")
