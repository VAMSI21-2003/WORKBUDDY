# ruff: noqa: E501 — reviewer tooling, not product code
"""Compare the page's replies (ui_probe.py on this branch) with the JSON API's replies on another tree.

    python scripts/review_harness/feat-frontend-demo/compare_page_vs_base.py out/ui_probe_head.jsonl out/api_probe_base.jsonl

Rewrites the page rows into the API probe's shape (reply = the reply slot's text, outcome, the
statuses, the plans' step texts and note) and runs ``../feat-backend-api-confirmation/r2_compare.py``
on reply, outcome and the plan text, stripping the reviewed catalog's openers and closers.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPARE = HERE.parent / "feat-backend-api-confirmation" / "r2_compare.py"


def main() -> None:
    page, other = sys.argv[1], sys.argv[2]
    with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as a, tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as b:
        for line in open(page, encoding="utf-8"):
            r = json.loads(line)
            a.write(json.dumps({
                "scenario": r["scenario"], "speaker": r["speaker"], "reply": r.get("page_reply"), "outcome": r.get("api_outcome"),
                "plan_text": [[s for s in p["steps"]] + [p["note"]] for p in r.get("plans", [])],
                "delivered_names": r.get("api_delivered"),
            }) + "\n")
        for line in open(other, encoding="utf-8"):
            r = json.loads(line)
            b.write(json.dumps({
                "scenario": r["scenario"], "speaker": r["speaker"], "reply": r.get("reply"), "outcome": r.get("outcome"),
                "plan_text": [[s[1] for s in p["steps"]] + [p["plan_note"]] for p in r.get("plans", [])],
                "delivered_names": [d["name"] for d in r.get("delivered") or []],
            }) + "\n")
    subprocess.run([sys.executable, str(COMPARE), a.name, b.name, "--key", "scenario", "--strip-catalog", "--fields", "reply,outcome,plan_text,delivered_names"], check=True)


if __name__ == "__main__":
    main()
