"""Round 3: the analysis skill (with its Splink near-duplicate check) on out/m_M6_*.xlsx, no address cap.

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_o_long_names.py FILE [--tree DIR]

Prints the findings count, wall time and peak RSS of one SpreadsheetAnalysisSkill run.
"""
import argparse
import resource
import sys
import time

ap = argparse.ArgumentParser()
ap.add_argument("file")
ap.add_argument("--tree", default=".")
a = ap.parse_args()
sys.path.insert(0, a.tree)
import pandas as pd  # noqa: E402
from skills.excel.analyze import NearDuplicateAnalyzer  # noqa: E402

df = pd.read_excel(a.file)
t = time.time()
found = NearDuplicateAnalyzer()(df)
print(f"rows={len(df)} name_len={df['name'].str.len().max()} near-duplicate findings={len(found)} "
      f"{time.time() - t:.1f}s peak={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:.0f}MiB")
