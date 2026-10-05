# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: rule 1 and rule 2 over every run in the round-2 outputs.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_rule_checks.py [FILE ...]

With no arguments it reads every ``out/r2/*.jsonl``, ``out/r2/live_*.json`` and the
older harnesses' ``out/r2/prb_harness_*.json.gz`` and ``out/r2/round5_*.json.gz``.
For each run it reports:

- rule 2: the original's SHA-256 before and after (``original_unchanged``,
  or the two hashes);
- rule 1, delivered: every delivered file's independent openpyxl read
  against the reply's "The copy has N row(s)", its "remaining" counts, and
  the rows the reply says were removed (original rows minus the copy's);
- rule 1, nothing delivered: no "Done", "Removed", "removed", "Saved as",
  "is ready" or "cleaned copy" in the reply.
"""

from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CLAIMS = ("Done", "Removed", "removed", "Saved as", "is ready", "cleaned copy")
ORIGINAL_ROWS = {"dup": 6, "nodup": 4, "header": 0}


def _fixture(row: dict[str, Any]) -> str:
    name = str(row.get("scenario") or row.get("case") or "")
    if "no_duplicates" in name:
        return "nodup"
    if "header_only" in name:
        return "header"
    return "dup"


def _runs(row: dict[str, Any]) -> list[dict[str, Any]]:
    """Each turn in a row, with its reply, delivered files and the row's hashes."""
    if "orig_unchanged" in row:  # the older harnesses (PR-B harness.py, round5.py)
        final = row.get("final")
        delivered = [{"independent": {"rows": final[0], "duplicates": final[1], "empty": final[2]}}] if row.get("delivered") and final else ([True] if row.get("delivered") else [])
        # Without the original's counts ("orig"), the removed-rows check is skipped.
        return [{"reply": row.get("reply"), "delivered": delivered, "original_unchanged": row["orig_unchanged"], "original_rows": (row.get("orig") or [None])[0], "skip_removed": not row.get("orig")}]
    if "turns" in row and isinstance(row["turns"], list):  # engine part of r2_probe
        return [{**t, "original_unchanged": row.get("original_unchanged")} for t in row["turns"]]
    if "trials" in row and isinstance(row["trials"], list) and row["trials"] and isinstance(row["trials"][0], dict):
        return [{"reply": None, "delivered": t.get("delivered") or [], "original_unchanged": t.get("original_unchanged", t.get("invariant_ok"))} for t in row["trials"]]
    if "after_shutdown" in row:
        return [row["after_shutdown"]]
    return [row]


def check(row: dict[str, Any], problems: list[str], counts: dict[str, int]) -> None:
    label = f"{row.get('scenario') or row.get('case') or row.get('name')}/{row.get('speaker')}"
    for run in _runs(row):
        unchanged = run.get("original_unchanged")
        if unchanged is None and run.get("original_sha_before"):
            unchanged = run["original_sha_before"] == run.get("original_sha_after")
        if unchanged is not None:
            counts["rule2_checked"] += 1
            if not unchanged:
                problems.append(f"RULE 2: {label}: the original changed")
        reply = run.get("reply")
        raw = run.get("delivered")
        items = raw if isinstance(raw, list) else []
        delivered = [d for d in items if isinstance(d, dict) and d.get("independent")]
        if not reply:
            continue
        counts["replies"] += 1
        if not raw:
            hits = [c for c in CLAIMS if c in reply]
            if hits:
                problems.append(f"RULE 1 (no delivery): {label}: {hits}: {reply[:200]}")
            continue
        for d in delivered:
            counts["delivered_checked"] += 1
            ind = d["independent"]
            m = re.search(r"The copy has (\d+) row\(s\)", reply)
            if m and int(m.group(1)) != ind["rows"]:
                problems.append(f"RULE 1 (rows): {label}: reply {m.group(1)} vs file {ind['rows']}")
            tail = reply[m.end():] if m else ""
            dup = re.search(r"(\d+) duplicate row\(s\)(?: and| remaining|,)", tail)
            if dup and int(dup.group(1)) != ind["duplicates"]:
                problems.append(f"RULE 1 (duplicates left): {label}: reply {dup.group(1)} vs file {ind['duplicates']}")
            emp = re.search(r"(\d+) fully-empty row\(s\) remaining", tail)
            if emp and int(emp.group(1)) != ind["empty"]:
                problems.append(f"RULE 1 (empty left): {label}: reply {emp.group(1)} vs file {ind['empty']}")
            if run.get("skip_removed"):
                continue
            removed = sum(int(n) for n in re.findall(r"[Rr]emoved (\d+) ", reply))
            original = run.get("original_rows")
            if original is None:
                original = ORIGINAL_ROWS[_fixture(row)]
            if removed != original - ind["rows"]:
                problems.append(f"RULE 1 (removed): {label}: reply removed {removed}, rows {original} -> {ind['rows']}")


def main() -> None:
    out = HERE / "out" / "r2"
    files = [Path(a) for a in sys.argv[1:]] or (
        sorted(out.glob("*.jsonl")) + sorted(out.glob("live_*.json")) + sorted(out.glob("prb_harness_*.json.gz")) + sorted(out.glob("round5_*.json.gz"))
    )
    counts = {"rows": 0, "rule2_checked": 0, "replies": 0, "delivered_checked": 0}
    problems: list[str] = []
    for path in files:
        text = gzip.decompress(path.read_bytes()).decode("utf-8") if path.suffix == ".gz" else path.read_text(encoding="utf-8")
        if path.suffix in (".json", ".gz"):
            data = json.loads(text)
            rows = data if isinstance(data, list) else [data]
        else:
            rows = [json.loads(line) for line in text.splitlines() if line.startswith("{")]
        for row in rows:
            counts["rows"] += 1
            check(row, problems, counts)
    print(json.dumps(counts))
    print(f"{len(problems)} problem(s)")
    for p in problems:
        print(" ", p)


if __name__ == "__main__":
    main()
