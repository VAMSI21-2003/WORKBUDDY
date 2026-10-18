"""Reviewer's round 2 prototype for R2-1 and R2-2 (not part of the branch), applied to a throwaway export.

Changes in skills/excel/workbook.py (head 9d17990):
 1. the grid's highest row is the <row r> as well as the cell's own reference (openpyxl read_only, which
    pandas uses, puts a row's cells at its <row r>);
 2. every part is streamed, whatever its name ends in, and a part the scan can't finish (not XML, bad
    deflate, unknown compression) is skipped on its own instead of ending the whole scan;
 3. a value in a spreadsheet element the scan can't read refuses the file instead of raising out of
    the handler (a raised exception used to end the whole scan with no verdict);
 4. a <c> counts only inside a <row> (calcChain.xml lists every formula cell as <c r=...> too).

    python scripts/review_harness/fix-security-pass/prototype_fix_r2.py DEST   # exports HEAD to DEST, patches it
"""
import io
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EDITS = [
    ("            for entry in entries:\n"
     "                if _XML_PART.search(entry.filename):\n"
     "                    problem = _scan_part(archive, entry, counts)\n"
     "                    if problem is not None:\n"
     "                        return problem\n",
     "            for entry in entries:\n"
     "                try:\n"
     "                    problem = _scan_part(archive, entry, counts)\n"
     "                except Exception:  # noqa: BLE001 - a reader can't get past this point either\n"
     "                    continue\n"
     "                if problem is not None:\n"
     "                    return problem\n"),
    ('    state = {"rows": 0, "row": 0, "column": 0, "top_row": 0, "top_column": 0}',
     '    state = {"rows": 0, "row": 0, "column": 0, "top_row": 0, "top_column": 0, "in_row": 0}'),
    ("    def on_start(name: str, attributes: dict[str, str]) -> None:\n"
     '        namespace, _, local = name.rpartition(" ")',
     "    def on_start(name: str, attributes: dict[str, str]) -> None:\n"
     "        try:\n"
     "            handle(name, attributes)\n"
     "        except _LimitPassed:\n"
     "            raise\n"
     "        except Exception:  # noqa: BLE001\n"
     "            refuse(_UNREADABLE)\n"
     "\n"
     "    def on_end(name: str) -> None:\n"
     '        if name.rpartition(" ")[2] == "row":\n'
     '            state["in_row"] = 0\n'
     "\n"
     "    def handle(name: str, attributes: dict[str, str]) -> None:\n"
     '        namespace, _, local = name.rpartition(" ")'),
    ('            state["column"] = 0\n            given = attributes.get("r")',
     '            state["column"] = 0\n            state["in_row"] = 1\n            given = attributes.get("r")'),
    ('        elif local == "c":\n            counts.cells += 1',
     '        elif local == "c" and state["in_row"]:\n            counts.cells += 1'),
    ('state["top_row"] = max(state["top_row"], row)',
     'state["top_row"] = max(state["top_row"], row, state["row"])'),
    ("    parser.StartElementHandler = on_start\n    parser.StartDoctypeDeclHandler = lambda *_: refuse(_NOT_ALLOWED)\n",
     "    parser.StartElementHandler = on_start\n    parser.EndElementHandler = on_end\n"
     "    parser.StartDoctypeDeclHandler = lambda *_: refuse(_NOT_ALLOWED)\n"),
]


def main() -> None:
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    tar = subprocess.run(["git", "archive", "HEAD"], cwd=REPO, capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(tar)).extractall(dest, filter="data")
    path = dest / "skills/excel/workbook.py"
    text = path.read_text()
    for old, new in EDITS:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    path.write_text(text)
    print("patched", path)


if __name__ == "__main__":
    main()
