"""Round 4: timing of the report-request patterns on hostile inputs (the round-4 head changed _REPORT_ABOUT, which now has a
repeated group with an optional prefix inside).  Every pattern entry point is timed: mentions_report, strip_report_request,
is_only_a_report_request.

    python r4_redos.py [--tree DIR]
"""
import argparse
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
A = ap.parse_args()
sys.path.insert(0, str(Path(A.tree).resolve()))
from buddy import actions  # noqa: E402

assert Path(actions.__file__).resolve().is_relative_to(Path(A.tree).resolve()), actions.__file__

N = 20000
CASES = {
    "the x N": "analyze " + "the " * N + "x",
    "the-and N": "analyze the file " + "and the " * N + "x",
    "file and file N": "report on " + "file and " * N + "zzz",
    "file, N": "report on the file " + "file " * N + "!",
    "first first N": "analyze " + "first " * N + "zzz",
    "missing N": "summary of " + "missing " * N + "zzz",
    "summary N": "give me a summary " * (N // 4),
    "spaces N": "analyze" + " " * (N * 5) + "x",
    "I will N": "i will " * N + "check the file",
    "myself N": "i'll check " + "a " * N + "myself",
    "don't N": "don't " * N + "give me a report",
    "check N": "check the file for " * (N // 4),
    "report and report N": "report and " * N,
    "unicode": "analyze my file ’“" * (N // 4),
    "newlines": "give me a report\n" * (N // 4),
    "the the the + report": "the " * N + "report",
    "the my your N file": "report of " + "the my your " * (N // 2) + "file and " * 3 + "zzz",
    "all any every": "summary of " + "all any every " * (N // 2) + "q",
}
worst = 0.0
for name, text in CASES.items():
    for fn in (actions.mentions_report, actions.strip_report_request, actions.is_only_a_report_request):
        t = time.perf_counter()
        fn(text)
        dt = time.perf_counter() - t
        worst = max(worst, dt)
        flag = "  <<<<<< SLOW" if dt > 1 else ""
        print(f"{name:24s} len={len(text):8d} {fn.__name__:26s} {dt*1000:9.1f} ms{flag}")
print("worst", round(worst, 3), "s")
