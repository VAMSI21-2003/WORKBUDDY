"""Round 5: HEAD exported to DEST with the reviewer's prototype for R5-1 (workbook.py only).

    python scripts/review_harness/fix-security-pass/prototype_fix_r5.py DEST

The child also runs ``formula_cell_counts`` (the Inspector's ``data_only=False`` load, the only one
that parses and translates formulas and keeps every shared-formula master for the whole sheet), so
its time and memory count against the child's limits like the other reads.
"""
import io
import subprocess
import sys
import tarfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OLD = """        sheet_names(input_path)
        frames = (pd.read_excel(input_path), pd.read_excel(input_path, dtype=object))
        del frames
"""
NEW = """        sheet_names(input_path)
        frames = (pd.read_excel(input_path), pd.read_excel(input_path, dtype=object))
        del frames
        formula_cell_counts(input_path)  # the only data_only=False load: formulas parsed, masters kept
"""


def main() -> None:
    dest = Path(sys.argv[1])
    dest.mkdir(parents=True, exist_ok=True)
    data = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest)
    target = dest / "skills/excel/workbook.py"
    text = target.read_text()
    assert text.count(OLD) == 1, "HEAD changed: the prototype no longer applies"
    target.write_text(text.replace(OLD, NEW))
    print("prototype applied in", dest)


if __name__ == "__main__":
    main()
