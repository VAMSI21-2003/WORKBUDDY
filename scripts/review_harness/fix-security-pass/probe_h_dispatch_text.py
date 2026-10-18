"""Does the dispatcher's message (it names '<session key>-task-N') reach a reply? Plans with steps no
skill runs, alone and around a real one, in three speakers. Run from the repo root:

    python scripts/review_harness/fix-security-pass/probe_h_dispatch_text.py [--tree BASE]
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
import r5lib  # noqa: E402

A = r5lib.args()
rq = r5lib.start(A)
from rq import DD, brain, plan  # noqa: E402

KEY = "SESSIONKEY0123456789"
S = {
    "only-sort": ("sort by amount", brain("unsupported", ("unsupported", "sort by amount")),
                  plan(("spreadsheet.sort", "sort by amount"))),
    "dedupe-then-sort": ("remove duplicates and sort by amount",
                         brain("multi_step_request", ("remove_duplicate_rows", "remove duplicates"), ("unsupported", "sort by amount")),
                         plan((DD, "remove duplicates"), ("spreadsheet.sort", "sort by amount"))),
    "unknown-capability-named-like-dedupe": ("remove the duplicate rows",
                         brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows")),
                         plan(("spreadsheet.deduplicate_v2", "remove the duplicate rows"))),
}
for name, (msg, b, p) in S.items():
    for sp in ("template", "friend", "focus"):
        rec = rq.run(dict(file="plain", turns=[dict(msg=msg, b=b, p=p)]), sp, session=KEY)["turns"][0]
        reply = rec.get("reply") or rec.get("error") or ""
        print(f"== {name} [{sp}] key_in_reply={KEY in reply} task_id_in_reply={'task-' in reply} "
              f"delivered={rec.get('delivered')} orig_ok={rec.get('orig_unchanged')}")
        if sp == "template":
            print("   " + reply[:400])
