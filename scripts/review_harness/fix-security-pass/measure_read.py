"""Peak memory and time of one pandas read of a workbook, after it passes check_workbook_limits.

Usage: python measure_read.py FILE.xlsx   (run from the repository root)
"""
import resource
import sys
import time

import pandas as pd

from skills.excel.workbook import check_workbook_limits

path = sys.argv[1]
check_workbook_limits(path)  # raises if refused
start = time.time()
df = pd.read_excel(path, dtype=object)
peak_mib = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"{path}: shape={df.shape} peak_rss={peak_mib:.0f} MiB seconds={time.time() - start:.1f}")
