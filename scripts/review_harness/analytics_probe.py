"""P-2 probe: does an analysed sheet cause a call to packages.ydata.ai? (requests.get is counted, not sent.)
Usage: YDATA_PROFILING_NO_ANALYTICS= python analytics_probe.py [TREE]   (also try with the variable unset)"""
import os, sys, tempfile
tree = sys.argv[1] if len(sys.argv) > 1 else os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, tree)
import pandas as pd
import requests
calls = []
requests.get = lambda url, *a, **k: calls.append(url)
from skills.excel.analyze import MissingValueAndDistributionAnalyzer
import skills.excel.analyze as A
assert A.__file__.startswith(tree), A.__file__
MissingValueAndDistributionAnalyzer()(pd.DataFrame({"a": [1, None, 3], "b": ["x", "y", None]}))
print("tree", tree.split("/")[-1], "| env now:", repr(os.environ.get("YDATA_PROFILING_NO_ANALYTICS")), "| calls to vendor:", [c.split("?")[0] for c in calls])
