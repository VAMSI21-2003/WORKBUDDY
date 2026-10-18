"""Reviewer's prototype for R1-H1 (not part of the branch): applied to a throwaway export of HEAD.

Changes in skills/excel/workbook.py:
 1. every XML part is scanned, not only xl/worksheets/*.xml (a workbook's rels can put a sheet anywhere);
    only SpreadsheetML <row>/<c> elements count, so other parts add nothing;
 2. a row's r and a cell's r are read the way openpyxl reads them (int, else float), and a value
    that isn't a whole number refuses the workbook;
 3. the grid a reader pads to (highest row x highest column, per sheet) counts against MAX_CELLS.

    python scripts/review_harness/fix-security-pass/prototype_fix.py DEST   # exports HEAD to DEST and patches it
"""
import io
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
EDITS = [
    ('            for entry in entries:\n                if _WORKSHEET_PART.match(entry.filename):\n'
     '                    problem = _scan_sheet(archive, entry, counts, budget)',
     '            for entry in entries:\n                if _XML_PART.search(entry.filename):\n'
     '                    problem = _scan_sheet(archive, entry, counts, budget)'),
    ('    state = {"rows": 0, "column": 0, "problem": None}',
     '    state = {"rows": 0, "column": 0, "problem": None, "row": 0, "top_row": 0, "top_col": 0}\n'
     '    main = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"\n'
     '\n'
     '    def number(text: str) -> int:\n'
     '        try:\n'
     '            return int(text)\n'
     '        except ValueError:\n'
     '            value = float(text)\n'
     '            if not value.is_integer():\n'
     '                raise\n'
     '            return int(value)\n'
     '\n'
     '    def grid(row: int, column: int) -> None:\n'
     '        state["top_row"] = max(state["top_row"], row)\n'
     '        state["top_col"] = max(state["top_col"], column)\n'
     '        if state["top_row"] * state["top_col"] > MAX_CELLS:\n'
     '            refuse(TOO_MANY_CELLS)'),
    ('        local = name.rpartition(" ")[2]\n        if local == "row":',
     '        space, _, local = name.rpartition(" ")\n        if space != main:\n            return\n        if local == "row":'),
    ('            number = attributes.get("r", "")\n'
     '            if state["rows"] > MAX_ROWS or (number.isdigit() and int(number) > MAX_ROWS):\n'
     '                refuse(TOO_MANY_ROWS)',
     '            given = attributes.get("r")\n'
     '            state["row"] = number(given) if given is not None else state["row"] + 1\n'
     '            if state["rows"] > MAX_ROWS or state["row"] > MAX_ROWS:\n'
     '                refuse(TOO_MANY_ROWS)'),
    ('            if match and int(match.group(2)) > MAX_ROWS:\n                refuse(TOO_MANY_ROWS)',
     '            if match and int(match.group(2)) > MAX_ROWS:\n                refuse(TOO_MANY_ROWS)\n'
     '            grid(state["row"], state["column"])'),
]


def apply(dest: Path) -> None:
    data = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest, filter="data")
    target = dest / "skills/excel/workbook.py"
    text = target.read_text()
    for old, new in EDITS:
        assert text.count(old) == 1, old[:60]
        text = text.replace(old, new)
    target.write_text(text)


if __name__ == "__main__":
    apply(Path(sys.argv[1]))
