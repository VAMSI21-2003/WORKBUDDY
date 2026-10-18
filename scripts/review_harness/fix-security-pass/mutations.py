"""One-place mutations of the branch's new code; each must make at least one test fail.

Exports HEAD (git archive) into a fresh temp dir per mutation, applies one exact replacement, and
runs the named tests there. The working copy is never touched. Run from the repo root:

    python scripts/review_harness/fix-security-pass/mutations.py [NAME ...]

Output: one line per mutation, "KILLED" (a test failed) or "SURVIVED" (all passed).
"""
import io
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
HW = "tests/skills/excel/test_hostile_workbooks.py"
FT = "tests/buddy/test_file_text_in_replies.py"
RD = "tests/buddy/test_residual_done_paths.py"
RT = "tests/buddy/test_retry.py"
TC = "tests/buddy/test_text_cells.py"
WEB = "tests/web/test_file_text_in_page.py"

M = {
    # hostile workbooks
    "rows-count-off": ("skills/excel/workbook.py", 'if state["rows"] > MAX_ROWS or (', 'if False and ('),
    "row-attr-off": ("skills/excel/workbook.py", '(number.isdigit() and int(number) > MAX_ROWS)', 'False'),
    "cells-off": ("skills/excel/workbook.py", "if counts.cells > MAX_CELLS:", "if False:"),
    "columns-off": ("skills/excel/workbook.py", 'if state["column"] > MAX_COLUMNS:', "if False:"),
    "cellref-row-off": ("skills/excel/workbook.py", "if match and int(match.group(2)) > MAX_ROWS:", "if False:"),
    "doctype-any-part-off": ("skills/excel/workbook.py", "if _XML_PART.search(entry.filename) and _has_doctype(archive, entry):", "if False:"),
    "doctype-sheet-off": ("skills/excel/workbook.py", "parser.StartDoctypeDeclHandler = lambda *_: refuse(_NOT_ALLOWED)", "pass"),
    "parts-off": ("skills/excel/workbook.py", "if len(entries) > MAX_PARTS:", "if False:"),
    "declared-size-off": ("skills/excel/workbook.py", "if sum(entry.file_size for entry in entries) > MAX_UNZIPPED_BYTES:", "if False:"),
    "bytes-read-off": ("skills/excel/workbook.py", "if counts.bytes_read > budget:", "if False:"),
    "limits-not-in-sheet_names": ("skills/excel/workbook.py", "        check_workbook_limits(input_path)\n        with open(input_path, \"rb\") as handle:\n            magic", "        with open(input_path, \"rb\") as handle:\n            magic"),
    "limits-not-in-analysis-skill": ("skills/excel/analyze.py", "            check_workbook_limits(input_path)\n", ""),
    "limits-not-in-inspector-read": ("buddy/inspector.py", "    check_workbook_limits(path)\n    return pd.read_excel(path, dtype=object)", "    return pd.read_excel(path, dtype=object)"),
    "cache-ignores-mtime": ("skills/excel/workbook.py", "key = (input_path, stat.st_size, stat.st_mtime_ns, stat.st_ino)", "key = (input_path, 0, 0, 0)"),
    "upload-cap-200": ("backend/settings.py", "max_zip_uncompressed_bytes: int = 64 * MIB", "max_zip_uncompressed_bytes: int = 200 * MIB"),
    # file text
    "shown-no-quote-swap": ("skills/excel/shown_text.py", "elif char == '\"':\n            pieces.append(\"'\")", "elif False:\n            pass"),
    "shown-keeps-format-chars": ("skills/excel/shown_text.py", 'elif category[0] == "C":\n            continue', 'elif False:\n            continue'),
    "shown-no-cap": ("skills/excel/shown_text.py", "if len(text) > limit:", "if False:"),
    "shown-no-quotes": ("skills/excel/shown_text.py", "return f'\"{text}\"'", "return text"),
    "analysis-missing-str-alert": ("skills/excel/analyze.py", 'f"column {shown(alert.column_name)}: {missing} {noun} ({percentage:.1f}%)"', 'str(alert)'),
    "inspector-missing-raw-header": ("buddy/inspector.py", 'f"column {shown(column)}: {count_noun(missing, \'missing value\')} ({percentage:.1f}%)"', 'f"{column}: {count_noun(missing, \'missing value\')} ({percentage:.1f}%)"'),
    "inspector-exc-text-back": ("buddy/inspector.py", "                findings=(COULD_NOT_READ_OUTPUT,),\n            )\n\n        duplicate_count", "                findings=(COULD_NOT_READ_OUTPUT + str(output_path),),\n            )\n\n        duplicate_count"),
    "sheet-names-back": ("buddy/inspector.py", 'return "the output\'s sheets don\'t match its input\'s, so a sheet was lost or renamed"', 'return f"the output\'s sheets ({\', \'.join(after)}) don\'t match its input\'s, so a sheet was lost or renamed"'),
    "hands-dispatch-msg-back": ("buddy/hands.py", "errors.append(no_skill)", "errors.append(str(exc))"),
    "splink-gets-header": ("skills/excel/analyze.py", "_NAME_FIELD: df[name_column].astype(str).to_numpy(),", "name_column: df[name_column].astype(str).to_numpy(),\n                _NAME_FIELD: df[name_column].astype(str).to_numpy(),"),
    "splink-revert-to-headers": ("skills/excel/analyze.py", [
        ('_NAME_FIELD: df[name_column].astype(str).to_numpy(),', 'name_column: df[name_column].astype(str).to_numpy(),'),
        ('_KEY_FIELD: df[discriminating_column].astype(str).to_numpy(),', 'discriminating_column: df[discriminating_column].astype(str).to_numpy(),'),
        ('comparisons=[cl.NameComparison(_NAME_FIELD), cl.ExactMatch(_KEY_FIELD)],', 'comparisons=[cl.NameComparison(name_column), cl.ExactMatch(discriminating_column)],'),
        ('block_on(f"substr({_NAME_FIELD}, 1, 3)")', 'block_on(f"substr({name_column}, 1, 3)")'),
        ('block_on(_KEY_FIELD)', 'block_on(discriminating_column)'),
        ('name_left, name_right = f"{_NAME_FIELD}_l", f"{_NAME_FIELD}_r"', 'name_left, name_right = f"{name_column}_l", f"{name_column}_r"'),
    ]),
    # residual paths
    "z31-whole-new-block-off": ("buddy/outcome.py", "        if other_action.mention is None or not other_action.mention.search(said):\n            names_action = unabsorbed = True\n            continue\n", ""),
    "z31-unabsorbed-off": ("buddy/outcome.py", "        unabsorbed\n        or any(", "        False\n        or any("),
    "z18-negation-back": ("buddy/outcome.py", "        or named_silent\n    ):", "        or (named_silent and not negated)\n    ):"),
    # retries
    "transient-always": ("buddy/llm.py", "    if isinstance(exc, _TIMEOUT_ERRORS):\n        return True", "    return True"),
    "transient-no-5xx": ("buddy/llm.py", "(status == 429 or 500 <= status <= 599)", "(status == 429)"),
    "brain-no-sleep": ("buddy/brain.py", "                self._sleep(retry_pause_seconds())", "                pass"),
    "think-no-sleep": ("buddy/think.py", "                self._sleep(retry_pause_seconds())", "                pass"),
    "brain-retry-any": ("buddy/brain.py", "            if not worth_retrying:\n                break", "            pass"),
    "jitter-off": ("buddy/llm.py", "return RETRY_PAUSE_S * (1 + RETRY_JITTER * (2 * draw - 1))", "return RETRY_PAUSE_S"),
    # text cells
    "dedupe-no-object": ("skills/excel/dedupe.py", "input_path, sheet_name=sheet_name, dtype=object", "input_path, sheet_name=sheet_name"),
    "empty-no-object": ("skills/excel/remove_empty_rows.py", "input_path, sheet_name=sheet_name, dtype=object", "input_path, sheet_name=sheet_name"),
    "inspector-no-object": ("buddy/inspector.py", "    return pd.read_excel(path, dtype=object)", "    return pd.read_excel(path)"),
    "analysis-cells-converted": ("skills/excel/analyze.py", "cells = pd.read_excel(input_path, dtype=object)", "cells = pd.read_excel(input_path)"),
}
TESTS = {
    "rows-count-off": [HW], "row-attr-off": [HW], "cells-off": [HW], "columns-off": [HW],
    "cellref-row-off": [HW], "doctype-any-part-off": [HW], "doctype-sheet-off": [HW],
    "parts-off": [HW], "declared-size-off": [HW], "bytes-read-off": [HW],
    "limits-not-in-sheet_names": [HW], "limits-not-in-analysis-skill": [HW],
    "limits-not-in-inspector-read": [HW], "cache-ignores-mtime": [HW],
    "upload-cap-200": [HW, "tests/backend/test_uploads.py"],
    "shown-no-quote-swap": [FT], "shown-keeps-format-chars": [FT], "shown-no-cap": [FT],
    "shown-no-quotes": [FT], "analysis-missing-str-alert": [FT, "tests/skills/excel/test_analyze.py"],
    "inspector-missing-raw-header": [FT], "inspector-exc-text-back": [FT],
    "sheet-names-back": [FT, "tests/buddy/test_inspector_sheets.py"],
    "hands-dispatch-msg-back": [FT, "tests/buddy/test_hands.py"],
    "splink-gets-header": [FT, "tests/skills/excel/test_analyze.py"],
    "z31-unabsorbed-off": [RD], "z18-negation-back": [RD], "z31-whole-new-block-off": [RD],
    "splink-revert-to-headers": [FT, "tests/skills/excel/test_analyze.py"],
    "transient-always": [RT], "transient-no-5xx": [RT], "brain-no-sleep": [RT], "think-no-sleep": [RT],
    "brain-retry-any": [RT], "jitter-off": [RT],
    "dedupe-no-object": [TC], "empty-no-object": [TC], "inspector-no-object": [TC],
    "analysis-cells-converted": [TC],
}


def export_head(dest: Path) -> None:
    data = subprocess.run(["git", "-C", str(REPO), "archive", "HEAD"], capture_output=True, check=True).stdout
    tarfile.open(fileobj=io.BytesIO(data)).extractall(dest, filter="data")


def run(name: str) -> str:
    path, *rest = M[name]
    pairs = rest[0] if len(rest) == 1 else [tuple(rest)]  # one (old, new), or a list of them (replace all)
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        export_head(root)
        target = root / path
        text = target.read_text()
        for old, new in pairs:
            if len(rest) != 1 and text.count(old) != 1 or old not in text:
                return f"{name:32} BAD-MUTATION (found {text.count(old)} times)"
            text = text.replace(old, new)
        target.write_text(text)
        r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS[name]],
                           cwd=root, capture_output=True, text=True, timeout=1500)
        last = (r.stdout.strip().splitlines() or ["?"])[-1]
        return f"{name:32} {'KILLED' if r.returncode else 'SURVIVED'}  ({last[:90]})"


if __name__ == "__main__":
    for n in sys.argv[1:] or list(M):
        print(run(n), flush=True)
