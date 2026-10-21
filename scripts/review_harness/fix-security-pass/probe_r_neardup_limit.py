"""Round 4: NearDuplicateAnalyzer alone on probe_q's contact lists (blank phones, near the row limit).

    python scripts/review_harness/fix-security-pass/probe_r_neardup_limit.py [TREE]

Prints, per frame, the pair count the new limit computes and the findings the analyzer returns.
"""
import sys
import time
from pathlib import Path

tree = sys.argv[1] if len(sys.argv) > 1 else "."
sys.path.insert(0, tree)
sys.path.insert(0, str(Path(__file__).resolve().parent))
import pandas as pd  # noqa: E402
from skills.excel.analyze import NearDuplicateAnalyzer  # noqa: E402

import importlib.util  # noqa: E402
spec = importlib.util.spec_from_file_location("q", Path(__file__).resolve().parent / "probe_q_round4.py")
src = spec.loader.get_source("q")
ns: dict = {"random": __import__("random")}
exec(src[src.index("def contacts"):src.index("def f_contacts")], ns)  # only the data builder
contacts = ns["contacts"]


def pairs(df):
    total = 0
    for col in (df["name"].astype(str).str.slice(0, 100).str.slice(0, 3), df["phone"].astype(str).str.slice(0, 100)):
        total += sum(int(n) * (int(n) - 1) // 2 for n in col.value_counts())
    return total


for label, rows, blank in (("all phones, 800", 800, 0), ("710 blank phones of 800", 800, 710),
                           ("760 blank phones of 800", 800, 760), ("9,000 rows, all phones", 9000, 0),
                           ("4,000 rows, 750 blank", 4000, 750)):
    names, phones = contacts(rows, blank)
    df = pd.DataFrame({"name": names, "phone": phones})
    t = time.time()
    found = NearDuplicateAnalyzer()(df)
    print(f"{label:28s} pairs={pairs(df):>9,} findings={len(found)} {time.time() - t:.1f}s "
          f"{[f[:80] for f in found[:2]]}", flush=True)
