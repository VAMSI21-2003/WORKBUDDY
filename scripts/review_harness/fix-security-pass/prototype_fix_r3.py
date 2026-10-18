"""Round 3: HEAD exported to DEST with the reviewer's prototype for R3-1..R3-3 applied (workbook.py only).

    python scripts/review_harness/fix-security-pass/prototype_fix_r3.py DEST

1. Every openpyxl load in workbook.py passes keep_links=False, as pandas does (R3-2; with it the walk and
   pandas agree again about a workbook with a broken or huge external link, R3-4).
2. The walk caps the rows of all sheets together at MAX_ROWS (R3-1: one part declared as thousands of
   sheets is walked once per sheet). Walking only book.worksheets[:1] (all pandas reads) also works, but
   test_the_grids_of_all_sheets_count_together pins the multi-sheet walk.
3. Before any openpyxl load, the parts openpyxl parses whole are capped by their declared size:
   the workbook part 1 MiB, styles 8 MiB (R3-3).
4. analyze.py: the near-duplicate check compares at most 100 characters of each field and skips when
   its two blocking rules would pair more than 250,000 rows (R3-5).
"""
import io
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]

EDITS = [
    ("            book = openpyxl.load_workbook(handle, read_only=True, data_only=True)\n            try:\n                grids = chars = 0\n                for sheet in book.worksheets:",
     "            book = openpyxl.load_workbook(handle, read_only=True, data_only=True, keep_links=False)\n            try:\n                grids = chars = 0\n                all_rows = 0  # rows of every sheet together: one part can be declared as many sheets\n                for sheet in book.worksheets:"),
    ("                        rows += 1\n                        widest = max(widest, len(row))\n                        if rows > MAX_ROWS:",
     "                        rows += 1\n                        all_rows += 1\n                        widest = max(widest, len(row))\n                        if all_rows > MAX_ROWS:"),
    ("    book = openpyxl.load_workbook(handle, read_only=True)\n",
     "    book = openpyxl.load_workbook(handle, read_only=True, keep_links=False)\n"),
    ("            book = openpyxl.load_workbook(handle, read_only=True, data_only=True)\n            try:\n                sheet = book[sheet_name]",
     "            book = openpyxl.load_workbook(handle, read_only=True, data_only=True, keep_links=False)\n            try:\n                sheet = book[sheet_name]"),
    ("shown_book = openpyxl.load_workbook(shown_handle, read_only=True, data_only=False)",
     "shown_book = openpyxl.load_workbook(shown_handle, read_only=True, data_only=False, keep_links=False)"),
    ("saved_book = openpyxl.load_workbook(saved_handle, read_only=True, data_only=True)",
     "saved_book = openpyxl.load_workbook(saved_handle, read_only=True, data_only=True, keep_links=False)"),
    ("            if sum(entry.file_size for entry in entries) > MAX_UNZIPPED_BYTES:\n                return TOO_BIG_UNPACKED\n",
     "            if sum(entry.file_size for entry in entries) > MAX_UNZIPPED_BYTES:\n                return TOO_BIG_UNPACKED\n"
     "            # Parts openpyxl parses whole into objects on every load, before any row is read.\n"
     "            for entry in entries:\n"
     "                name = entry.filename.lower()\n"
     "                if name.endswith('workbook.xml') and entry.file_size > (1 << 20):\n"
     "                    return _UNREADABLE\n"
     "                if name.endswith('styles.xml') and entry.file_size > (8 << 20):\n"
     "                    return _UNREADABLE\n"),
]


ANALYZE_EDITS = [
    ("                _NAME_FIELD: df[name_column].astype(str).to_numpy(),\n                _KEY_FIELD: df[discriminating_column].astype(str).to_numpy(),\n            }\n        )\n",
     "                _NAME_FIELD: df[name_column].astype(str).str.slice(0, 100).to_numpy(),\n                _KEY_FIELD: df[discriminating_column].astype(str).str.slice(0, 100).to_numpy(),\n            }\n        )\n"
     "        # Splink materialises every pair its blocking rules make: bound them before it starts.\n"
     "        pairs = sum(\n"
     "            int(n) * (int(n) - 1) // 2\n"
     "            for column in (working[_NAME_FIELD].str.slice(0, 3), working[_KEY_FIELD])\n"
     "            for n in column.value_counts()\n"
     "        )\n"
     "        if pairs > 250_000:\n"
     "            return ()\n"),
]


def main() -> None:
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest, filter="data")
    target = dest / "skills/excel/workbook.py"
    text = target.read_text()
    for old, new in EDITS:
        assert text.count(old) == 1, old[:80]
        text = text.replace(old, new)
    target.write_text(text)
    target = dest / "skills/excel/analyze.py"
    text = target.read_text()
    for old, new in ANALYZE_EDITS:
        assert text.count(old) == 1, old[:80]
        text = text.replace(old, new)
    target.write_text(text)
    print("prototype written to", dest)


if __name__ == "__main__":
    main()
