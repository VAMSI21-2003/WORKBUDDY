"""A report request (analysis only) on the odd files probe_f writes to out/ (run probe_f first).

    python scripts/review_harness/fix-security-pass/probe_g_report_odd.py [--tree BASE]
"""
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import brain  # noqa: E402

logging.basicConfig(level=logging.CRITICAL)
for name in ("odd_corrupt_deflate", "odd_method99", "odd_dense_300k_elsewhere"):
    src = HERE / "out" / f"{name}.xlsx"
    for msg, b in (("give me a report on this file", brain("report_on_file", ("report_on_file", "give me a report"))),
                   ("remove the duplicate rows", brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")))):
        spec = dict(file=lambda dst, s=src: dst.write_bytes(s.read_bytes()), turns=[dict(msg=msg, b=b)])
        rec = rq.run(spec, "template")["turns"][0]
        print(f"== {name} | {msg} | delivered={rec.get('delivered')} orig_ok={rec.get('orig_unchanged')} "
              f"steps={rec.get('steps')} failure={rec.get('failure')}")
        print("   reply:", (rec.get("reply") or rec.get("error") or "")[:400])
