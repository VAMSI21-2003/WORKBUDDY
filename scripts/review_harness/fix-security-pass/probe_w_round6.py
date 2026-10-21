"""Round 6: each workbook read the server makes, measured alone, against what the check's child does.

For each file (out/m_<name>.xlsx from earlier rounds, or out/r6/<name>.xlsx), each read runs in its
own process with no memory cap, and prints its wall time and peak RSS (the process baseline, about
the imports, is in the "baseline" row). The child's own figures come from running
``_scan_in_this_process`` the same way. Use files the check passes: a refused file never reaches
the server's reads.

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_w_round6.py P9_huge_unused_string S0_shared_formula_short [...] [--tree DIR]
"""
import argparse
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

READS = {
    "baseline": "import pandas, openpyxl",
    "child (_scan_in_this_process)": "print(wb._scan_in_this_process(p, '{}'), end=' ')",
    "walk (_reader_verdict, child only)": "wb._reader_verdict(p)",
    "_zip_sheet_names (sheet_names)": "wb._zip_sheet_names(open(p, 'rb'))",
    "_sheet_problem (layout, not in child)": "wb._sheet_problem(p, wb._zip_sheet_names(open(p, 'rb'))[0])",
    "formula_cell_counts": "wb._in_scan_child = True; wb.formula_cell_counts(p)",
    "read_excel dtype=object (skills, _read_cells)": "pd.read_excel(p, dtype=object)",
    "read_excel default + object at once (analysis)": "a = pd.read_excel(p); b = pd.read_excel(p, dtype=object)",
}

CHILD = r"""
import resource, sys, time
sys.path.insert(0, sys.argv[2])
import pandas as pd
import skills.excel.workbook as wb
p = sys.argv[1]
s = time.time()
exec(sys.argv[3])
print(f"{time.time() - s:6.1f}s {resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024:7.0f} MiB")
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="+")
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    a = ap.parse_args()
    for name in a.names:
        path = HERE / "out" / f"m_{name}.xlsx"
        if not path.exists():
            path = HERE / "out" / "r6" / f"{name}.xlsx"
        print(f"== {name} ({path.stat().st_size // 1024} KB)", flush=True)
        for label, code in READS.items():
            r = subprocess.run([sys.executable, "-c", CHILD, str(path), a.tree, code],
                               capture_output=True, text=True, cwd=a.tree, timeout=1800)
            out = r.stdout.strip() or ("ERROR " + r.stderr.strip().splitlines()[-1][:120])
            print(f"   {label:48} {out}", flush=True)


if __name__ == "__main__":
    main()
