# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: compare two JSONL outputs of the same probe, scenario by scenario.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_compare.py A.jsonl B.jsonl [--key scenario|case] [--fields f1,f2]

Rows are matched on (key, speaker). For each pair it prints SAME, or each
field that differs with both values. Fields that change on every run
(turn ids, timestamps, confirmation ids, hashes of fresh fixtures) aren't
compared unless named with ``--fields``. ``--strip-catalog`` removes the
reviewed openers and closers (chosen by a hash of the random session id)
from replies before comparing; ``--ignore statuses`` skips the polled
status sequence (it depends on poll timing).
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

DEFAULT_FIELDS = [
    "start_http", "statuses", "plans", "answers", "outcome", "reply_source", "reply", "confirmation",
    "detail_steps", "delivered", "written_in_turn_folder", "original_unchanged", "pipeline_reply_equal",
    "TIMEOUT",
]

_VOLATILE = re.compile(r"(c_[A-Za-z0-9_-]{20,}|t\d{4}|[0-9a-f]{32}-task-\d+|_[0-9a-f]{8,}(?=[_.]))")


_CATALOG: list[str] = []


def _strip_catalog(text: str) -> str:
    """Drop the catalog's openers and closers (seeded by the random session id)."""
    for phrase in _CATALOG:
        text = text.replace(phrase + " ", "").replace(" " + phrase, "")
    return text


def _norm(value: Any) -> Any:
    text = json.dumps(value, sort_keys=True, default=str)
    value = json.loads(_VOLATILE.sub("<id>", text))
    if _CATALOG and isinstance(value, str):
        value = _strip_catalog(value)
    return value


def load(path: str, key: str) -> dict[tuple[str, str], dict[str, Any]]:
    rows = {}
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line.startswith("{"):
                continue
            row = json.loads(line)
            rows[(str(row.get(key)), str(row.get("speaker")))] = row
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("a")
    parser.add_argument("b")
    parser.add_argument("--key", default="scenario")
    parser.add_argument("--fields")
    parser.add_argument("--strip-catalog", action="store_true", help="ignore catalog openers/closers in replies")
    parser.add_argument("--ignore", default="", help="comma-separated fields not to compare")
    args = parser.parse_args()
    if args.strip_catalog:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
        from buddy.voice_catalog import CATALOG

        _CATALOG.extend(sorted((p.text for p in CATALOG if p.text), key=len, reverse=True))
    fields = args.fields.split(",") if args.fields else DEFAULT_FIELDS
    fields = [f for f in fields if f not in args.ignore.split(",")]
    a, b = load(args.a, args.key), load(args.b, args.key)
    same = differ = 0
    for k in sorted(set(a) | set(b)):
        if k not in a or k not in b:
            print(f"{k[1]:9} {k[0]:42} ONLY IN {'A' if k in a else 'B'}")
            continue
        diffs = []
        for field in fields:
            va, vb = _norm(a[k].get(field)), _norm(b[k].get(field))
            if va != vb:
                diffs.append((field, va, vb))
        if not diffs:
            same += 1
            print(f"{k[1]:9} {k[0]:42} SAME")
            continue
        differ += 1
        print(f"{k[1]:9} {k[0]:42} DIFFERS")
        for field, va, vb in diffs:
            print(f"    {field}:\n      A: {json.dumps(va, default=str)[:900]}\n      B: {json.dumps(vb, default=str)[:900]}")
    print(f"# {same} same, {differ} differ")


if __name__ == "__main__":
    main()
