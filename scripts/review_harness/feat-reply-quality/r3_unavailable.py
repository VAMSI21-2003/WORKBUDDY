"""Round 3 probe: a report request (with an extra the closed list does not know) when the report cannot be made:
unreadable file, password file, no file, analysis fails, chain stopped. The early return in `_no_skill_outcome` must not
turn "no report" into silence. Brain returns `unsupported` with the whole phrase.

    python r3_unavailable.py [--tree DIR] [--speakers template,friend,focus]
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--speakers", default="template")
A = ap.parse_args()
import rq  # noqa: E402
from rq import DD, brain, plan  # noqa: E402

rq.setup(A.tree)
PH = "give me a report in pdf"
for kind in [None, "garbage", "password", "xls", "empty_only", "multi", "merged"]:
    for dedupe in (False, True):
        for sp in A.speakers.split(","):
            if dedupe:
                msg = f"remove the duplicate rows and {PH}"
                b = brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", PH))
            else:
                msg, b = PH, brain("unclear", ("unsupported", PH))
            r = rq.run(dict(file=kind, turns=[dict(msg=msg, b=b)]), sp)["turns"][0]
            if "error" in r:
                print(f"[{sp}] {kind} dedupe={dedupe} ERROR {r['error']}")
                continue
            print(f"[{sp}] file={kind} dedupe={dedupe} delivered={r['delivered']} orig_ok={r['orig_unchanged']} flags={r['flags']}\n    {r['reply'][:420]}\n")
