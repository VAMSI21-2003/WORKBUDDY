"""Round 6: two users at once, in one server process, with the real child (R5-2 and R5-4's decision).

User A's file is refused as too costly, and since round 6 that verdict is not kept, so each of A's
checks takes up to SCAN_SECONDS of the one slot. A turn checks a refused file twice (probe_u). User
B checks an ordinary file one second after A starts. Prints what B got and how long B waited.

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_x_round6.py [--tree DIR] [--a S1_shared_formula_excel_len_50k] [--a-checks 2] [--gap 0.0]
"""
import argparse
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--a", default="S1_shared_formula_excel_len_50k")
    ap.add_argument("--a-checks", type=int, default=2)
    ap.add_argument("--gap", type=float, default=0.0, help="seconds of other work between A's checks")
    a = ap.parse_args()
    sys.path.insert(0, a.tree)
    from skills.excel import workbook as wb

    work = Path(tempfile.mkdtemp())
    a_file = work / "a.xlsx"
    b_file = work / "b.xlsx"
    shutil.copyfile(HERE / "out" / f"m_{a.a}.xlsx", a_file)
    shutil.copyfile(HERE / "out" / "m_P0_ordinary_control.xlsx", b_file)
    log: list[str] = []
    t0 = time.time()

    def check(who: str, path: Path) -> None:
        start = time.time()
        try:
            wb.check_workbook_limits(str(path))
            verdict = "PASS"
        except wb.WorkbookRefused as refused:
            verdict = f"REFUSED: {refused}"
        log.append(f"{who}: started {start - t0:5.1f}s, took {time.time() - start:5.1f}s: {verdict}")

    def user_a() -> None:
        for _ in range(a.a_checks):
            check("A", a_file)
            time.sleep(a.gap)

    ta = threading.Thread(target=user_a)
    ta.start()
    time.sleep(1)
    tb = threading.Thread(target=check, args=("B", b_file))
    tb.start()
    ta.join()
    tb.join()
    print(f"SCAN_SECONDS={wb.SCAN_SECONDS} SCAN_WAIT_SECONDS={wb.SCAN_WAIT_SECONDS} A={a.a} "
          f"a_checks={a.a_checks} gap={a.gap}")
    print("\n".join(log))
    shutil.rmtree(work)


if __name__ == "__main__":
    main()
