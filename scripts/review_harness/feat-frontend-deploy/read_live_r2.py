# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: read the files a live run delivered with openpyxl (independent of the run script's
pandas check). ``python scripts/review_harness/feat-frontend-deploy/read_live_r2.py out/live_runs_container_r2.json``"""

import glob
import json
import sys

import openpyxl

for r in json.load(open(sys.argv[1])):
    print("RUN", r["run"], repr(r["message"]), "| outcome", r["outcome"], "| sha before/after equal:", r["original_sha256_stored_before"] == r["original_sha256_stored_after"])
    print("  reply:", r["api_reply"])
    for name in r["api_delivered"]:
        for p in glob.glob("scratch/live-frontend/**/" + name, recursive=True):
            ws = openpyxl.load_workbook(p, read_only=True).worksheets[0]
            rows = list(ws.iter_rows(values_only=True))
            data = rows[1:]
            empty = sum(1 for x in data if all(v is None or str(v).strip() == "" for v in x))
            print("  openpyxl:", "rows", len(data), "duplicates", len(data) - len(set(data)), "empty", empty, "|", data)
