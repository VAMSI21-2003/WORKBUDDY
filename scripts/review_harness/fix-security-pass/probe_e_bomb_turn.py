"""A whole turn on a 1.5 KB workbook whose two far cells sit inside every limit (real pipeline,
fake LLM, template speaker): peak memory, wall time, reply. Run from the repo root:

    python scripts/review_harness/fix-security-pass/probe_e_bomb_turn.py ROWS COLS [--tree BASE]
"""
import resource
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROWS, COLS = int(sys.argv[1]), int(sys.argv[2])
sys.argv = [sys.argv[0], *sys.argv[3:]]
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
sys.path.insert(0, str(HERE))
import r5lib  # noqa: E402
from make_sparse_bomb import build  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import brain  # noqa: E402

spec = dict(file=lambda p: build(str(p), ROWS, COLS, full_header=True),
            turns=[dict(msg="remove the duplicate rows",
                        b=brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))])
start = time.time()
rec = rq.run(spec, "template")["turns"][0]
peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
print(f"rows={ROWS} cols={COLS} seconds={time.time() - start:.0f} peak_rss={peak:.0f} MiB "
      f"delivered={rec.get('delivered')} orig_ok={rec.get('orig_unchanged')} steps={rec.get('steps')}")
print("reply:", rec.get("reply") or rec.get("error"))
