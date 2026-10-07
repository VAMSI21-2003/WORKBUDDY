# ruff: noqa: E501 — reviewer tooling, not product code
"""Compare two ``../feat-frontend-demo/ui_probe.py`` outputs (head and base) row by row.

    python scripts/review_harness/feat-frontend-deploy/compare_ui.py HEAD.jsonl BASE.jsonl

Rows match on (scenario, speaker). Session ids, confirmation ids and file-name hashes are
masked, and the friend speaker's catalog opener and closer are stripped (they're seeded from the
random session id). What's compared: the outcome, the delivered names, the reply, the plan's
steps and note, the turn's statuses, whether the links were offered and fetched correctly, and
whether the original is unchanged.
"""

from __future__ import annotations

import json
import re
import sys

MASKS = [
    (re.compile(r"\bs_[A-Za-z0-9_-]{22}"), "s_X"),
    (re.compile(r"\bc_[A-Za-z0-9_-]{22}"), "c_X"),
    (re.compile(r"[0-9a-f]{10,}"), "HEX"),
]
sys.path.insert(0, ".")
from buddy.voice_catalog import CATALOG  # noqa: E402

PHRASES = sorted({p.text for p in CATALOG}, key=len, reverse=True)


def mask(value):
    text = json.dumps(value, sort_keys=True, default=str)
    for pattern, sub in MASKS:
        text = pattern.sub(sub, text)
    return text


def core(reply: str | None) -> str | None:
    """The reply from "Done —" (or its first sentence that isn't a catalog line) on, closer cut."""
    if reply is None:
        return None
    body = reply
    for phrase in PHRASES:  # the catalog's openers and closers, wherever they are
        body = body.replace(phrase, "")
    return re.sub(r"\s+", " ", body).strip()


def key_fields(row: dict) -> dict:
    return {
        "outcome": row.get("api_outcome"),
        "delivered": row.get("api_delivered"),
        "reply": core(row.get("api_reply")),
        "page_equals_api": row.get("page_equals_api"),
        "full_page_equals_api": row.get("full_page_equals_api"),
        "links_only_if_delivered": row.get("links_only_if_delivered"),
        "downloads_ok": [d.get("ok") if isinstance(d, dict) else d for d in (row.get("downloads_turn") or [])],
        "plans": [(p.get("steps"), p.get("note"), p.get("buttons")) for p in row.get("plans") or []],
        "statuses": row.get("statuses"),
        "original_unchanged": row.get("original_unchanged"),
        "upload_http": row.get("upload_http"),
    }


def main() -> None:
    head, base = ({(r["scenario"], r["speaker"]): r for r in map(json.loads, open(p))} for p in sys.argv[1:3])
    differ = 0
    for key in sorted(set(head) | set(base)):
        a, b = head.get(key), base.get(key)
        if a is None or b is None:
            print("MISSING", key, "head" if a is None else "base")
            differ += 1
            continue
        fa, fb = key_fields(a), key_fields(b)
        for field in fa:
            if mask(fa[field]) != mask(fb[field]):
                differ += 1
                print("DIFF", key, field, "\n  head:", mask(fa[field])[:400], "\n  base:", mask(fb[field])[:400])
    unchanged = sum(1 for r in head.values() if r.get("original_unchanged"))
    print(f"# {len(head)} head rows, {len(base)} base rows, {differ} differing fields; original unchanged in {unchanged}/{len(head)} head rows")


if __name__ == "__main__":
    main()
