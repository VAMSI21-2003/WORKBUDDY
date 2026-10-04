"""Mutation checks for fix/values-only-copy: does the test suite notice when a guard is removed?

For each mutation: export HEAD (git archive) to a scratch dir, apply one text replacement, run the
listed test files there, and report whether any test failed ("caught") or all passed ("SURVIVED").
Nothing in the real tree is touched.

Usage (repo root):  python scripts/review_harness/mutations.py [NAME ...]
"""
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
TESTS = [
    "tests/buddy/test_values_only_copy.py",
    "tests/skills/excel/test_plain_table.py",
    "tests/skills/excel/test_analyze.py",
    "tests/buddy/test_voice.py",
    "tests/buddy/test_outcome.py",
    "tests/buddy/test_multi_sheet.py",
    "tests/buddy/test_planner.py",
    "tests/buddy/test_inspector.py",
]

# name: (file, old, new)
M = {
    "merged-scan-off": ("skills/excel/workbook.py", "reason = _MERGED_CELLS if _has_merged_cells(sheet) else _layout_problem(sheet)", "reason = _layout_problem(sheet)"),
    "header-not-first-off": ("skills/excel/workbook.py", "return _HEADER_NOT_FIRST  # nothing on row 1, something below it", "pass"),
    "gap-check-off": ("skills/excel/workbook.py", "if len(headers) != last_header or any(column > last_header for column in data_columns):", "if any(column > last_header for column in data_columns):"),
    "right-of-header-off": ("skills/excel/workbook.py", "if len(headers) != last_header or any(column > last_header for column in data_columns):", "if len(headers) != last_header:"),
    "dup-header-off": ("skills/excel/workbook.py", "if len(set(headers.values())) != len(headers):", "if False:"),
    "skill-dedupe-no-refuse": ("skills/excel/dedupe.py", "sheet_name = plain_table_sheet_name(input_path)", "sheet_name = __import__('skills.excel.workbook', fromlist=['x']).single_sheet_name(input_path)"),
    "skill-empty-no-refuse": ("skills/excel/remove_empty_rows.py", "sheet_name = plain_table_sheet_name(input_path)", "sheet_name = __import__('skills.excel.workbook', fromlist=['x']).single_sheet_name(input_path)"),
    "inspector-check-off": ("buddy/inspector.py", "sheet_problem = _sheet_list_problem(step) or _plain_table_finding(step)", "sheet_problem = _sheet_list_problem(step)"),
    "inspector-only-first-sheet": ("buddy/inspector.py", "problem = plain_table_problem(step.input_reference)", "problem = None"),
    "measurer-unwired": ("buddy/engine_factory.py", "verifier=OriginalMeasurer(ResidualMeasurer(verifier))", "verifier=ResidualMeasurer(verifier)"),
    "measurer-no-facts": ("buddy/inspector.py", "        return replace(\n            result,\n            original_facts=OriginalFacts(\n                formula_cells=counts.formulas", "        return result\n        return replace(\n            result,\n            original_facts=OriginalFacts(\n                formula_cells=counts.formulas"),
    "measurer-picks-last-step": ("buddy/inspector.py", "(step.input_reference for step in execution_result.steps if _reads_a_workbook(step))", "(step.input_reference for step in reversed(execution_result.steps) if _reads_a_workbook(step))"),
    "measurer-failure-silent": ("buddy/inspector.py", "return replace(result, original_facts=OriginalFacts(formula_cells=None))", "return result"),
    "reads-workbook-ignores-recheck": ("buddy/inspector.py", "        or step.recheck\n        or not step.outputs\n        or not step.input_reference.lower()", "        or not step.outputs\n        or not step.input_reference.lower()"),
    "values-only-sentence-gone": ("buddy/outcome.py", "    sentences = [VALUES_ONLY_SENTENCE]\n", "    sentences = []\n"),
    "formulas-sentence-gone": ("buddy/outcome.py", "    elif formulas:\n", "    elif False:\n"),
    "unsaved-sentence-gone": ("buddy/outcome.py", "        if unsaved:\n", "        if False:\n"),
    "unchecked-sentence-gone": ("buddy/outcome.py", "        sentences.append(FORMULAS_UNCHECKED_SENTENCE)", "        pass"),
    "unsaved-count-all": ("skills/excel/workbook.py", "without_value += _is_empty(saved.value)", "without_value += 1"),
    "formula-count-data-only": ("skills/excel/workbook.py", "shown_book = openpyxl.load_workbook(shown_handle, read_only=True, data_only=False)", "shown_book = openpyxl.load_workbook(shown_handle, read_only=True, data_only=True)"),
    "planner-check-off": ("buddy/planner.py", "return plain_table_problem(file_reference) is not None", "return False"),
    "analytics-setdefault": ("skills/excel/analyze.py", 'os.environ[_NO_ANALYTICS_ENV] = "1"', 'os.environ.setdefault(_NO_ANALYTICS_ENV, "1")'),
    "analytics-off": ("skills/excel/analyze.py", 'os.environ[_NO_ANALYTICS_ENV] = "1"', "pass"),
    # round 2 (head 8222c7f)
    "r2-reset-dim-sheet-problem-off": ("skills/excel/workbook.py", "                sheet.reset_dimensions()\n                reason =", "                reason ="),
    "r2-reset-dim-count-shown-off": ("skills/excel/workbook.py", "                    shown_sheet.reset_dimensions()\n", ""),
    "r2-reset-dim-count-saved-off": ("skills/excel/workbook.py", "                    saved_sheet.reset_dimensions()\n", ""),
    "r2-error-type-check-off": ("skills/excel/workbook.py", 'if cell.data_type == "e" or (', "if False or ("),
    "r2-na-text-check-off": ("skills/excel/workbook.py", "cell.row != 1 and isinstance(cell.value, str) and cell.value in _PANDAS_NA_TEXT", "False"),
    "r2-na-in-header-refused": ("skills/excel/workbook.py", "cell.row != 1 and isinstance(cell.value, str)", "isinstance(cell.value, str)"),
    "r2-na-set-drops-null": ("skills/excel/workbook.py", '"NULL",', ""),
    "r2-reads-as-empty-gets-suffix": ("skills/excel/workbook.py", "if reason is None or reason == _READS_AS_EMPTY:", "if reason is None:"),
    "r2-na-check-after-first-row-logic-only-row1": ("skills/excel/workbook.py", "cell.row != 1 and isinstance(cell.value, str) and cell.value in _PANDAS_NA_TEXT", "cell.row == 2 and isinstance(cell.value, str) and cell.value in _PANDAS_NA_TEXT"),
    "is-empty-ignores-empty-string": ("skills/excel/workbook.py", 'return value is None or value == ""', "return value is None"),
}


def run(name):
    path, old, new = M[name]
    with tempfile.TemporaryDirectory() as tmp:
        subprocess.run(f"git -C {ROOT} archive HEAD | tar -x -C {tmp}", shell=True, check=True)
        target = Path(tmp) / path
        text = target.read_text()
        assert text.count(old) == 1, f"{name}: pattern found {text.count(old)} times"
        target.write_text(text.replace(old, new))
        proc = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", *TESTS],
                              cwd=tmp, capture_output=True, text=True)
        tail = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else proc.stderr[-200:]
        first_fail = next((ln for ln in proc.stdout.splitlines() if ln.startswith("FAILED")), "")
        print(f"{'caught  ' if proc.returncode else 'SURVIVED'} {name:34} {first_fail[:110] or tail}", flush=True)


if __name__ == "__main__":
    for n in (sys.argv[1:] or M):
        run(n)
