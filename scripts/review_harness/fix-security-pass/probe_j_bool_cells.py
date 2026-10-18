"""A TRUE cell in a column of numbers, through a whole dedupe turn: what does the delivered copy hold?
(Pre-existing on main; recorded for the owner.)

    python scripts/review_harness/fix-security-pass/probe_j_bool_cells.py [--tree BASE]
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
import openpyxl  # noqa: E402
from rq import brain  # noqa: E402

captured = {}


def make(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    for r in (("k", "paid"), ("a", 1), ("b", True), ("c", 0), ("d", False), ("a", 1)):
        ws.append(r)
    wb.save(path)


import buddy.engine_factory as ef  # noqa: E402

real = ef.build_engine


def spy(*a, **k):
    engine = real(*a, **k)
    run = engine.run

    def wrapped(state):
        out = run(state)
        er = state.execution_result
        if er and er.outputs:
            captured["cells"] = [c.value for c in openpyxl.load_workbook(er.outputs[0]).active["B"]]
        return out

    engine.run = wrapped
    return engine


rq.T["build_engine"] = spy
spec = dict(file=make, turns=[dict(msg="remove the duplicate rows",
                                   b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))])
rec = rq.run(spec, "template")["turns"][0]
print("original column B: ['paid', 1, True, 0, False, 1]")
print("delivered column B:", captured.get("cells"), "| delivered:", rec["delivered"], "| orig_ok:", rec["orig_unchanged"])
print("reply:", rec["reply"])
