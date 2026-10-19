"""Round 5: reads the app makes outside the check's child, on files the child passes.

Builds out/m_S*.xlsx (so probe_n_turns.py runs them as turns), then for each file, in a fresh process:
the upload route's check, check_workbook_limits with the real child (verdict, seconds), and
formula_cell_counts (the Inspector's read of the original on every delivered turn; it loads with
data_only=False, the only openpyxl path that parses and translates formulas) with its seconds.

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_s_round5.py [--tree DIR] [--only S1,S2] [--timeout 1200]
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
sys.path.insert(0, str(HERE))
import probe_m_round3 as m  # noqa: E402

NS = m.NS


def shared_formula_sheet(rows: int, refs: int) -> str:
    """Row 1: headers. Rows 2..rows+1: column A is one shared formula of ``refs`` references
    (master in A2, every other row a 40-byte dependent), column B an id, so every row is distinct."""
    formula = "+".join(f"B{i}" for i in range(1, refs + 1))
    out = [f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>',
           '<row r="1"><c r="A1" t="inlineStr"><is><t>total</t></is></c>'
           '<c r="B1" t="inlineStr"><is><t>id</t></is></c></row>',
           f'<row r="2"><c r="A2"><f t="shared" ref="A2:A{rows + 1}" si="0">{formula}</f><v>1</v></c>'
           '<c r="B2"><v>2</v></c></row>']
    for r in range(3, rows + 2):
        out.append(f'<row r="{r}"><c r="A{r}"><f t="shared" si="0"/><v>1</v></c><c r="B{r}"><v>{r}</v></c></row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


def many_masters_sheet(rows: int, refs: int) -> str:
    """Every row's column A is its own shared-formula master (its own ``si``), so openpyxl keeps a
    tokenized copy of each one for the whole sheet when it parses formulas."""
    formula = "+".join(f"B{i}" for i in range(1, refs + 1))
    out = [f'<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="{NS}"><sheetData>',
           '<row r="1"><c r="A1" t="inlineStr"><is><t>total</t></is></c>'
           '<c r="B1" t="inlineStr"><is><t>id</t></is></c></row>']
    for r in range(2, rows + 2):
        out.append(f'<row r="{r}"><c r="A{r}"><f t="shared" ref="A{r}:A{r}" si="{r}">{formula}</f><v>1</v></c>'
                   f'<c r="B{r}"><v>{r}</v></c></row>')
    out.append("</sheetData></worksheet>")
    return "".join(out)


CASES = {
    # control: a short shared formula, as a real sheet has
    "S0_shared_formula_short": dict(rows=20_000, refs=2),
    # Excel-legal length (under 8,192 characters)
    "S1_shared_formula_excel_len_50k": dict(rows=50_000, refs=1_500),
    # longer than Excel writes, still inside every limit
    "S2_shared_formula_4000_refs_20k": dict(rows=20_000, refs=4_000),
    "S3_shared_formula_4000_refs_100k": dict(rows=100_000, refs=4_000),
    # many masters: memory held for the whole sheet
    "S4_masters_2000_x_4000_refs": dict(rows=2_000, refs=4_000, masters=True),
    "S5_masters_4500_excel_len": dict(rows=4_500, refs=1_500, masters=True),
}

CHILD = r"""
import resource, sys, time
sys.path.insert(0, sys.argv[2])
from skills.excel import workbook
p = sys.argv[1]
t = time.time()
try:
    v = workbook.check_workbook_limits(p); v = "PASS"
except workbook.WorkbookRefused as e:
    v = "REFUSED: " + str(e)
print(f"check: {v} ({time.time() - t:.1f}s)", flush=True)
if v == "PASS":
    t = time.time()
    c = workbook.formula_cell_counts(p)
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
    print(f"formula_cell_counts: {c.formulas} formulas ({time.time() - t:.1f}s, server peak {peak} MiB)", flush=True)
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--only", default="")
    ap.add_argument("--timeout", type=int, default=1200)
    a = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    child = OUT / "_s_child.py"
    child.write_text(CHILD)
    env = dict(os.environ, OPENPYXL_LXML="False")
    for name, spec in CASES.items():
        if a.only and name.split("_")[0] not in a.only.split(","):
            continue
        path = OUT / f"m_{name}.xlsx"
        if not path.exists():
            spec = dict(spec)
            build = many_masters_sheet if spec.pop("masters", False) else shared_formula_sheet
            m.package(path, build(**spec))
        print(f"== {name}: {path.stat().st_size // 1024} KB", flush=True)
        try:
            r = subprocess.run([sys.executable, str(child), str(path), a.tree], capture_output=True,
                               text=True, env=env, timeout=a.timeout, cwd=a.tree)
            print(r.stdout.strip() or ("CHILD FAILED: " + r.stderr.strip()[-400:]), flush=True)
        except subprocess.TimeoutExpired as e:
            got = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
            print(got.strip(), f"\nstill running at {a.timeout}s (killed)", flush=True)


if __name__ == "__main__":
    main()
