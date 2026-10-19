"""Round 5: whole turns where the workbook check's child answers busy, crash, timeout or can't start,
on the upload or only on a file the turn wrote. Real build_engine, real skills and verifiers, the
real child unless a case replaces it; fake LLM only (../feat-reply-quality/rq.py, imported).

    python scripts/review_harness/fix-security-pass/probe_t_round5.py [--tree DIR] [--speakers ...] [--only T1,T2] [--out F.json]
"""
import contextlib
import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import brain  # noqa: E402

from skills.excel import workbook  # noqa: E402

RPT, RD = "report_on_file", "remove_duplicate_rows"
DED = "remove the duplicate rows"
S = {}


def sc(name, msg, b, patch=None, file="plain"):
    S[name] = dict(file=file, turns=[dict(msg=msg, b=b)], patch=patch, secrets=("task-0", "session-1"))


@contextlib.contextmanager
def fresh():
    workbook._scanned.clear()
    yield
    workbook._scanned.clear()


def slot_busy_after(n):
    """The first ``n`` checks get the slot; every later one waits and is told busy (no 10 s wait)."""
    @contextlib.contextmanager
    def cm():
        calls = {"n": 0}
        real = workbook._scan_slot

        class Slot:
            def acquire(self, timeout=None):
                calls["n"] += 1
                return real.acquire(timeout=0) if calls["n"] <= n else False

            def release(self):
                real.release()

        with fresh(), mock.patch.object(workbook, "_scan_slot", Slot()):
            yield
    return cm


def child_answer_on_outputs(answer):
    """The real child for the upload; ``answer`` (a verdict, or an exception to raise) for any
    file under workspace/outputs, i.e. the copies the turn wrote."""
    @contextlib.contextmanager
    def cm():
        real = workbook._scan_in_child

        def fake(path):
            if "outputs" in str(path):
                if isinstance(answer, BaseException):
                    raise answer
                return answer
            return real(path)

        with fresh(), mock.patch.object(workbook, "_scan_in_child", fake):
            yield
    return cm


def child_answer_everywhere(answer):
    @contextlib.contextmanager
    def cm():
        with fresh(), mock.patch.object(workbook, "_scan_in_child", lambda _p: answer):
            yield
    return cm


@contextlib.contextmanager
def no_python():
    with fresh(), mock.patch.object(workbook.sys, "executable", "/nonexistent/python"):
        yield


D = brain(RD, (RD, DED))
R = brain(RPT, (RPT, "give me a report on this file"))
sc("T0-control-real-child-dedupe", DED, D, fresh)
sc("T0r-control-real-child-report", "give me a report on this file", R, fresh)
sc("T1-busy-at-first-check", DED, D, slot_busy_after(0))
sc("T2-busy-on-the-written-copy", DED, D, slot_busy_after(1))
sc("T2r-busy-after-first-check-report", "give me a report on this file", R, slot_busy_after(1))
sc("T3-crash-on-the-written-copy", DED, D, child_answer_on_outputs(workbook.CHECK_STOPPED))
sc("T4-timeout-on-the-written-copy", DED, D, child_answer_on_outputs(workbook.TOO_COSTLY))
sc("T5-cannot-start-child", DED, D, no_python)
sc("T5r-cannot-start-child-report", "give me a report on this file", R, no_python)
sc("T6-timeout-on-upload", DED, D, child_answer_everywhere(workbook.TOO_COSTLY))
sc("T7-cannot-start-on-copy-only", DED, D,
   child_answer_on_outputs(workbook.WorkbookRefused(workbook.CHECK_STOPPED)))

if __name__ == "__main__":
    only = tuple(x for x in A.only.split(",") if x)
    res = rq.run_all(S, only, tuple(A.speakers.split(",")))
    out = Path(A.out) if A.out else HERE / "out" / "probe_t.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    for r in res:
        if "error" in r:
            print(f"== {r['name']} [{r['speaker']}] ERROR {r['error']}")
            continue
        print(f"== {r['name']} [{r['speaker']}] delivered={r['delivered']} steps={r['steps']} "
              f"orig_ok={r['orig_unchanged']} flags={r['flags']}")
        print(f"   orig={r['orig_facts']} final={r['final_facts']}")
        print("   " + (r["reply"] or "").replace("\n", "\n   "))
