# ruff: noqa: E501
"""Round 4: LibreOffice Calc exports of an overflowing note with a double space (two spaces after a
full stop, a common typing habit) and one bold or red word (so several text objects), next cell
empty; column width swept in half characters. A rule in the double space leaves 0.56 em between
the halves, above `RUN_EM` (0.5).

    python r4_lo_double.py         # output: out/r4/lo_double.json; prints each wrong delivery and the counts
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import r3_layout  # noqa: E402
from r3_lo_rich import calc_pdf  # noqa: E402

NOTES = [("Paid.  ", "Thanks", " again"), ("Ships  ", "next", "  week"), ("Note:  ", "call", "  first"), ("Done.  ", "Sent", "  today")]
WIDTHS = [6 + 0.5 * k for k in range(25)]


def main():
    if shutil.which("soffice") is None:
        print("no soffice")
        return
    recs, counts = [], {}
    for note in NOTES:
        for bold in (True, False):
            for w in WIDTHS:
                rows = [["Name", "Note", "Qty"], ["Alice", note, ""], ["Bob", "short", "3"]]
                text = " ".join("".join(note).split())  # a cell's normal form folds runs of spaces
                want = [["Name", "Note", "Qty"], ["Alice", text, ""], ["Bob", "short", "3"]]
                r = r3_layout.judge(calc_pdf(rows, w, bold), want)
                key = r["result"].split("(")[0] if r["result"].startswith("REFUSED") else r["result"]
                counts[key] = counts.get(key, 0) + 1
                recs.append({"note": "".join(note), "bold": bold, "width": w, **r})
                if key == "DELIVER-WRONG":
                    print("".join(note), "bold" if bold else "red", w, "->", r.get("got", [None, None])[1], flush=True)
    print(counts)
    out = HERE / "out" / "r4"
    out.mkdir(parents=True, exist_ok=True)
    (out / "lo_double.json").write_text(json.dumps(recs, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
