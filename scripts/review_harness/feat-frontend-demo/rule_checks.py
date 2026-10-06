# ruff: noqa: E501 — reviewer tooling, not product code
"""Rule 1 and rule 2 over the page probes' outputs (ui_probe / adversarial / browser_probe JSONL).

    python scripts/review_harness/feat-frontend-demo/rule_checks.py out/ui_probe_head.jsonl [...]

For each row: the original's SHA-256 unchanged; the reply slot equal to the JSON reply; a download
link only for a delivered turn; for each downloaded file, the reply's "The copy has N row(s)" and
"N duplicate/fully-empty row(s) remaining" against an openpyxl read; for a turn with nothing
delivered, no completion words in the reply; no completion words in page text outside the reply.
"""

from __future__ import annotations

import json
import re
import sys

CLAIM_WORDS = re.compile(r"\b(Done|Removed|Saved as|is ready|cleaned copy)\b")


def check(row: dict) -> list[str]:
    p: list[str] = []
    if row.get("original_unchanged") is False:
        p.append("rule 2: original changed")
    if "page_equals_api" in row and row["page_equals_api"] is not True:
        p.append("page reply != API reply")
    reply = row.get("page_reply") or ""
    outcome = row.get("api_outcome")
    dls = row.get("downloads_turn") or row.get("downloads") or []
    if outcome != "delivered" and dls:
        p.append("links for a turn that delivered nothing")
    if outcome != "delivered" and outcome is not None and CLAIM_WORDS.search(reply):
        p.append(f"claim words with nothing delivered: {CLAIM_WORDS.findall(reply)}")
    for d in dls:
        ind = d.get("independent") or {}
        m = re.search(r"The copy has (\d+) row", reply)
        if m and int(m.group(1)) != ind.get("rows"):
            p.append(f"rows: reply {m.group(1)} vs file {ind.get('rows')}")
        m = re.search(r"(\d+) duplicate row\(s\) remaining", reply)
        if m and int(m.group(1)) != ind.get("duplicates"):
            p.append(f"dups remaining: reply {m.group(1)} vs file {ind.get('duplicates')}")
        m = re.search(r"(\d+) fully-empty row\(s\) remaining", reply)
        if m and int(m.group(1)) != ind.get("empty"):
            p.append(f"empty remaining: reply {m.group(1)} vs file {ind.get('empty')}")
        m = re.search(r"still has (\d+) fully-empty", reply)
        if m and int(m.group(1)) != ind.get("empty"):
            p.append(f"still-empty: reply {m.group(1)} vs file {ind.get('empty')}")
        if d.get("sha_matches_api") is False:
            p.append("download sha != API sha")
    outside = row.get("page_text_outside_reply") or ""
    if CLAIM_WORDS.search(outside.replace("Download ", "")):
        p.append(f"claim words outside the reply: {CLAIM_WORDS.findall(outside)}")
    return p


def main() -> None:
    total = problems = 0
    for path in sys.argv[1:]:
        for line in open(path, encoding="utf-8"):
            if not line.startswith("{"):
                continue
            row = json.loads(line)
            total += 1
            found = check(row)
            if found:
                problems += 1
                print(path, row.get("scenario") or row.get("case"), row.get("speaker"), found)
    print(f"# {total} rows, {problems} with problems")


if __name__ == "__main__":
    main()
