"""Round 6: whole turns on files earlier rounds wrote (out/m_*.xlsx), counting the checking children.

probe_n_turns.py, plus: every call of ``_scan_in_child`` in the turn is recorded with its time and
whether it passed, so a refused file's cost to the one check slot can be read off (the R5-4
decision keeps no verdict for a timeout or a memory refusal).

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_u_round6.py P0_ordinary_control S4_masters_2000_x_4000_refs \
        [--tree DIR] [--speakers template,friend,focus] [--kinds dedupe,report] [--no-facts]
"""
from pathlib import Path

HERE = Path(__file__).resolve().parent

COUNT = (
    "import os\n"
    "import skills.excel.workbook as _wb\n"
    "_runs = []\n"
    "_real = _wb._scan_in_child\n"
    "def _counting(p):\n"
    "    _s = time.time()\n"
    "    _v = _real(p)\n"
    "    _runs.append(f\"{time.time() - _s:.1f}s:{'pass' if _v is None else 'refuse'}\")\n"
    "    return _v\n"
    "_wb._scan_in_child = _counting\n"
)

source = (HERE / "probe_n_turns.py").read_text()
for old, new in (
    ("import os\nif os.environ", COUNT + "if os.environ"),
    ('print("   reply:"', 'print("   children:", len(_runs), " ".join(_runs))\nprint("   reply:"'),
    ('line.startswith(("   reply", ))', 'line.startswith(("   reply", "   children"))'),
    ('"_n_child.py"', '"_u_child.py"'),
):
    assert old in source, old
    source = source.replace(old, new, 1)

if __name__ == "__main__":
    exec(compile(source, str(HERE / "probe_n_turns.py"), "exec"), {"__name__": "__main__", "__file__": str(HERE / "probe_n_turns.py")})  # noqa: S102
