"""NearDuplicateAnalyzer on ordinary and hostile headers (head vs --tree base).

    python scripts/review_harness/fix-security-pass/probe_b_neardup.py [TREE]
"""
import logging
import sys
from pathlib import Path

tree = str(Path(sys.argv[1] if len(sys.argv) > 1 else Path(__file__).resolve().parents[3]).resolve())
sys.path.insert(0, tree)
logging.disable(logging.CRITICAL)
import pandas as pd  # noqa: E402

import skills.excel.analyze as an  # noqa: E402

assert an.__file__.startswith(tree), an.__file__
rows = {"names": ["Jonathan Smith", "Jonathon Smith", "Maria Garcia", "Bob Lee", "Jon Smith"],
        "mail": ["jon@x.com", "jon@x.com", "maria@y.org", "bob@z.net", "jon@x.com"]}
HEADERS = [
    ("name", "email"),
    ("Name", "Email"),
    ("full name", "email address"),           # spaces: needed quoting in SQL before
    ("name-1", "e-mail"),                     # hyphen
    ("select name", "email"),                 # SQL keyword
    ("name') OR length(read_text('/etc/passwd'))>0 --", "email"),
    ('name" , (SELECT 1) AS "x', "email"),
]
for name_h, mail_h in HEADERS:
    df = pd.DataFrame({name_h: rows["names"], mail_h: rows["mail"]})
    try:
        f = an.NearDuplicateAnalyzer()(df)
    except Exception as e:  # noqa: BLE001
        f = ("RAISED " + type(e).__name__,)
    print(f"{name_h!r:55} {mail_h!r:18} -> {len(f)} findings  {f[:1]}")
