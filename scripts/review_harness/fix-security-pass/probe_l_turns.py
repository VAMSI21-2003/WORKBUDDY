"""Round 2: whole turns (real pipeline, fake LLM) on the out/k_*.xlsx files probe_k_round2.py wrote.

Per file and speaker: delivered or not, the reply, the original's SHA-256 before and after, peak RSS
and wall time of the process so far. Run from the repo root, after probe_k_round2.py:

    OPENPYXL_LXML=False python scripts/review_harness/fix-security-pass/probe_l_turns.py K1_row_r_mismatch_20k [...] [--tree DIR] [--speakers template,friend,focus]
"""
import resource
import shutil
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
NAMES = [a for a in sys.argv[1:] if a[:1] == "K" and a[1:2].isdigit()]
sys.argv = [sys.argv[0], *[a for a in sys.argv[1:] if a not in NAMES]]
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import brain  # noqa: E402

TURNS = {
    "dedupe": dict(msg="remove the duplicate rows",
                   b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))),
    "report": dict(msg="give me a report on this file",
                   b=brain("report_on_file", ("report_on_file", "give me a report on this file"))),
}

for name in NAMES:
    src = HERE / "out" / f"k_{name}.xlsx"
    for speaker in A.speakers.split(","):
        for kind, turn in TURNS.items():
            spec = dict(file=lambda p, s=src: shutil.copyfile(s, p), turns=[turn])
            start = time.time()
            rec = rq.run(spec, speaker)["turns"][0]
            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            print(f"== {name} {speaker} {kind}: {time.time() - start:.0f}s peak_rss={peak:.0f}MiB "
                  f"delivered={rec.get('delivered')} orig_unchanged={rec.get('orig_unchanged')} "
                  f"steps={rec.get('steps')}", flush=True)
            print("   reply:", (rec.get("reply") or rec.get("error") or "").replace("\n", " | "), flush=True)
