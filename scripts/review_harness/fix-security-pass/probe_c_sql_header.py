"""Does a crafted header reach DuckDB through Splink (marker file written)? Does near-dup still find pairs?

    python scripts/review_harness/fix-security-pass/probe_c_sql_header.py [TREE]
"""
import logging
import sys
import tempfile
from pathlib import Path

tree = str(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]).resolve())
sys.path.insert(0, tree)
logging.disable(logging.CRITICAL)
import pandas as pd  # noqa: E402

from skills.excel.analyze import NearDuplicateAnalyzer  # noqa: E402

d = Path(tempfile.mkdtemp())
sys.path.insert(0, str(Path(tree) / "tests" / "skills" / "excel"))
from test_analyze import _MESSY_NAME_PHONE_ROWS  # noqa: E402

names = [n for n, _ in _MESSY_NAME_PHONE_ROWS]
phones = [p for _, p in _MESSY_NAME_PHONE_ROWS]
cases = {
    "plain": "name",
    "spaces": "full name",
    "test-header (tests/buddy/test_file_text_in_replies.py)":
        "name') || (SELECT 1) || substr('x', 1, 3); COPY (SELECT 1) TO '{m}' --",
    "valid-expr-1": "name, 1, 3) || (SELECT 1 FROM (COPY (SELECT 1) TO '{m}')) || substr(name",
    "quoted-ident": 'name" , 1, 3), (COPY (SELECT 42) TO \'{m}\') --',
}
for label, h in cases.items():
    marker = d / f"{abs(hash(label))}.csv"
    header = h.format(m=marker)
    f = NearDuplicateAnalyzer()(pd.DataFrame({header: names, "phone": phones}))
    print(f"{label:58} findings={len(f)} marker_written={marker.exists()} first={f[:1]}")
