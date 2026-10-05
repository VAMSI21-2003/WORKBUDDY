# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: compare the earlier harnesses' outputs (PR-B ``harness.py``, ``round5.py``), head vs base.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_older_compare.py > out/r2/older_harnesses_head_vs_base.txt

Reads ``out/r2/prb_harness_{head,base}.json.gz`` and ``out/r2/round5_{head,base}.json.gz``
(regenerate them with ``harness.py --out`` and ``round5.py --out``, on head and
with ``--tree`` on a base worktree, then gzip). Turns are matched on (name,
speaker, session id); for each scenario that differs it prints the fields
that differ, with one turn's values from each tree.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

OUT = Path(__file__).resolve().parent / "out" / "r2"


def load(name: str) -> dict[tuple[str, str, str], dict]:
    rows = json.loads(gzip.decompress((OUT / name).read_bytes()))
    return {(d["name"], d["speaker"], d.get("sid")): d for d in rows}


def main() -> None:
    for label, a_f, b_f in (
        ("PR-B harness.py", "prb_harness_base.json.gz", "prb_harness_head.json.gz"),
        ("round5.py", "round5_base.json.gz", "round5_head.json.gz"),
    ):
        a, b = load(a_f), load(b_f)
        keys = sorted(set(a) | set(b))
        diff = [k for k in keys if a.get(k) != b.get(k)]
        print(f"## {label}: {len(b)} turns on head, {len(a)} on base; {len(keys) - len(diff)} identical, {len(diff)} differ")
        print(f"   voice calls head/base: {sum(d.get('voice_calls', 0) for d in b.values())}/{sum(d.get('voice_calls', 0) for d in a.values())}; originals unchanged head/base: {all(d.get('orig_unchanged') for d in b.values())}/{all(d.get('orig_unchanged') for d in a.values())}")
        for n in sorted({k[0] for k in diff}):
            ks = [k for k in diff if k[0] == n]
            k = ks[0]
            fields = sorted(f for f in set(a[k]) | set(b[k]) if a[k].get(f) != b[k].get(f))
            print(f"   {n}: {len(ks)} turn(s) differ in {fields}")
            for f in fields:
                print(f"      base {f}: {json.dumps(a[k].get(f))[:600]}")
                print(f"      head {f}: {json.dumps(b[k].get(f))[:600]}")


if __name__ == "__main__":
    main()
