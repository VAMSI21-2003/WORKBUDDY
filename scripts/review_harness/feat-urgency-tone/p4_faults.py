"""Round-1 probe 4: a stage fails halfway while Brain reads the person as urgent/frustrated.

    python p4_faults.py [--tree DIR] [--out FILE]

Faults are injected by patching the tree's classes at run time. For each fault: calm/urgent/frustrated
x template/friend/focus; urgent/frustrated must equal the calm *focus* reply (calm template for template),
and no reply may carry the planted secret text or a completion claim when nothing was delivered.
"""
import argparse, contextlib, json, sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "feat-reply-quality"))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--out", default=str(HERE / "out" / "p4.json"))
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import plan, think, DD, ER  # noqa: E402
import buddy.hands as hands  # noqa: E402
import buddy.inspector as insp  # noqa: E402
import buddy.planner as pl  # noqa: E402
import skills.excel.analyze as an  # noqa: E402


def b(state, acts=(("remove_duplicate_rows", "remove the duplicate rows"),), intent="remove_duplicate_rows"):
    return json.dumps({"intent": intent, "confidence": 0.9, "user_state": state,
                       "requested_actions": [{"action": a, "source_phrase": p} for a, p in acts]})


def cm_patch(target, name, fn):
    @contextlib.contextmanager
    def cm():
        with mock.patch.object(target, name, fn):
            yield
    return cm


def verifier_fails(self, request):
    return insp.VerificationResult(passed=False, confidence=1.0, findings=("TOPSECRET verifier says no",))


def hands_boom(self, *a, **k):
    raise RuntimeError("hands boom TOPSECRET /secret/path")


def planner_boom(self, *a, **k):
    raise RuntimeError("planner boom TOPSECRET")


def inspector_boom(self, *a, **k):
    raise RuntimeError("inspector boom TOPSECRET")


def read_boom(*a, **k):
    raise RuntimeError("read boom TOPSECRET /secret/path")


import pandas as pd  # noqa: E402

FAULTS = {
    "none": contextlib.nullcontext,
    "dedupe-verifier-fails": cm_patch(insp.SpreadsheetDedupeVerifier, "__call__", verifier_fails),
    "analysis-verifier-fails": cm_patch(insp.SpreadsheetAnalysisVerifier, "__call__", verifier_fails),
    "hands-raises": cm_patch(hands.Hands, "__call__", hands_boom),
    "planner-raises": cm_patch(pl.Planner, "_resolve_analysis_target", planner_boom),
    "read-excel-raises": cm_patch(pd, "read_excel", read_boom),
}
BOTH = (("remove_duplicate_rows", "remove duplicates"), ("remove_empty_rows", "remove empty rows"))
MODELS = {  # name -> (acts, intent, plan)
    "single": ((("remove_duplicate_rows", "remove the duplicate rows"),), "remove_duplicate_rows", None, "remove the duplicate rows"),
    "chain": (BOTH, "multi_step_request", plan((DD, "remove duplicates"), (ER, "remove empty rows")), "remove duplicates then remove empty rows"),
}
res, bad = [], 0
for fname, fcm in FAULTS.items():
    for mname, (acts, intent, pl_, msg) in MODELS.items():
        got = {}
        for state in ("calm", "urgent", "frustrated"):
            for sp in rq.SPEAKERS:
                spec = {"file": "plain", "patch": fcm, "secrets": ("TOPSECRET", "boom", "/secret/"),
                        "turns": [{"msg": msg, "b": b(state, acts, intent), "t": think(), "p": pl_}]}
                t = rq.run(spec, sp)["turns"][0]
                t.update(fault=fname, model=mname, state=state)
                got[(state, sp)] = t
                res.append(t)
        for state in ("urgent", "frustrated"):
            for sp in rq.SPEAKERS:
                ref = got[("calm", "focus" if sp != "template" else "template")]
                t = got[(state, sp)]
                if t.get("reply") != ref.get("reply") or t.get("delivered") != ref.get("delivered"):
                    bad += 1
                    print("DIFF", fname, mname, state, sp, "\n  ", t.get("reply") or t.get("error"), "\n  ", ref.get("reply") or ref.get("error"))
        for k, t in got.items():
            if t.get("flags") or t.get("orig_unchanged") is False or "error" in t:
                print("FLAG", fname, mname, k, t.get("flags"), t.get("orig_unchanged"), t.get("error"))
Path(A.out).write_text(json.dumps(res, indent=1, default=str))
print(len(res), "turns; urgent/frustrated != calm-focus:", bad)
