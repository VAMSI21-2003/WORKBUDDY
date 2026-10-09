"""Round-1 probe C: a stage fails halfway, with and without a requested report.

    python probe_c_faults.py [--tree DIR] [--out FILE] [ids...]

Faults are injected by patching the tree's own classes at run time (nothing on disk changes).
"""
import argparse
import contextlib
import json
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
ap = argparse.ArgumentParser()
ap.add_argument("--tree", default=str(HERE.parents[2]))
ap.add_argument("--out", default=str(HERE / "out" / "c.json"))
ap.add_argument("--speakers", default="template,friend,focus")
ap.add_argument("--show", action="store_true")
ap.add_argument("only", nargs="*")
A = ap.parse_args()
import rq  # noqa: E402

rq.setup(A.tree)
from rq import brain, plan, think, DD, AN  # noqa: E402

import skills.excel.analyze as an  # noqa: E402
import buddy.inspector as insp  # noqa: E402
import buddy.planner as pl  # noqa: E402
import buddy.hands as hands  # noqa: E402
import buddy.voice as vc  # noqa: E402

RP = "give me a report"
DUP = brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"))
DUPRP = brain("remove_duplicate_rows", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", RP))
UNS = brain("unclear", ("unsupported", RP))


def analyzer_raises(on_calls):
    """Patch the exact-duplicate analyzer to raise on the given 1-based call numbers."""
    @contextlib.contextmanager
    def cm():
        orig = an.ExactDuplicateRowAnalyzer.__call__ if hasattr(an, "ExactDuplicateRowAnalyzer") else None
        n = {"c": 0}

        def fake(self, df):
            n["c"] += 1
            if on_calls == "all" or n["c"] in on_calls:
                raise RuntimeError("analyzer boom /secret/path TOPSECRET")
            return orig(self, df)
        target = an.ExactDuplicateRowAnalyzer if orig else an.IQROutlierAnalyzer
        if not orig:
            orig = an.IQROutlierAnalyzer.__call__
        with mock.patch.object(target, "__call__", fake):
            yield
    return cm


def verifier_fails(on_calls):
    @contextlib.contextmanager
    def cm():
        orig = insp.SpreadsheetAnalysisVerifier.__call__
        n = {"c": 0}

        def fake(self, request):
            n["c"] += 1
            if on_calls == "all" or n["c"] in on_calls:
                return insp.VerificationResult(passed=False, confidence=1.0, findings=("TOPSECRET verifier says no",))
            return orig(self, request)
        with mock.patch.object(insp.SpreadsheetAnalysisVerifier, "__call__", fake):
            yield
    return cm


def dedupe_verifier_fails():
    @contextlib.contextmanager
    def cm():
        orig = insp.SpreadsheetDedupeVerifier.__call__

        def fake(self, request):
            return insp.VerificationResult(passed=False, confidence=1.0, findings=("TOPSECRET dedupe verifier no",))
        with mock.patch.object(insp.SpreadsheetDedupeVerifier, "__call__", fake):
            yield
    return cm


def read_excel_raises(on_calls):
    @contextlib.contextmanager
    def cm():
        import pandas as pd
        orig = pd.read_excel
        n = {"c": 0}

        def fake(*a, **k):
            n["c"] += 1
            if on_calls == "all" or n["c"] in on_calls:
                raise RuntimeError("read boom /secret/path TOPSECRET")
            return orig(*a, **k)
        with mock.patch.object(pd, "read_excel", fake):
            yield
    return cm


def planner_raises():
    @contextlib.contextmanager
    def cm():
        with mock.patch.object(pl.Planner, "_resolve_analysis_target", side_effect=RuntimeError("planner boom TOPSECRET")):
            yield
    return cm


def hands_raises():
    @contextlib.contextmanager
    def cm():
        with mock.patch.object(hands.Hands, "__call__", side_effect=RuntimeError("hands boom TOPSECRET")):
            yield
    return cm


S = {}


def sc(name, patch, *turns, file="plain"):
    S[name] = dict(file=file, turns=list(turns), patch=patch, secrets=("TOPSECRET", "boom", "/secret/"))


def tn(msg, b, t=None, p=None):
    d = dict(msg=msg, b=b)
    if t is not None:
        d["t"] = t
    if p is not None:
        d["p"] = p
    return d


sc("f01-report-only-analyzer-raises", analyzer_raises("all"), tn(RP, UNS))
sc("f02-report-only-verifier-fails", verifier_fails("all"), tn(RP, UNS))
sc("f03-dedupe-report-recheck-analyzer-raises", analyzer_raises({2}), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f04-dedupe-report-first-analyzer-raises", analyzer_raises({1}), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f05-dedupe-report-recheck-verifier-fails", verifier_fails({2}), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f06-dedupe-report-first-verifier-fails", verifier_fails({1}), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f07-dedupe-report-dedupe-verifier-fails", dedupe_verifier_fails(), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f08-dedupe-only-dedupe-verifier-fails", dedupe_verifier_fails(), tn("remove the duplicate rows", DUP))
sc("f09-report-planner-raises", planner_raises(), tn(RP, UNS))
sc("f10-report-hands-raises", hands_raises(), tn(RP, UNS))
sc("f11-unclear-analyzer-raises", analyzer_raises("all"), tn("make it nicer", brain("unclear")))
sc("f12-unclear-verifier-fails", verifier_fails("all"), tn("make it nicer", brain("unclear")))
sc("f13-hi-verifier-fails", verifier_fails("all"), tn("hi", brain("conversation")))
sc("f14-dedupe-first-verifier-fails", verifier_fails({1}), tn("remove the duplicate rows", DUP))
sc("f15-dedupe-recheck-verifier-fails", verifier_fails({2}), tn("remove the duplicate rows", DUP))
sc("f16-dedupe-report-both-fail", verifier_fails("all"), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f17-report-plan-report-chain-analyzer-raises", analyzer_raises({2}), tn("remove the duplicate rows and then give me a report",
   brain("multi_step_request", ("remove_duplicate_rows", "remove the duplicate rows"), ("unsupported", RP)), p=plan((DD, "remove the duplicate rows"), ("spreadsheet.report", RP))))
sc("f18-think-raises-report", contextlib.nullcontext, tn(RP, UNS, t=RuntimeError("think boom TOPSECRET")))
sc("f19-report-refused-plus-failure", analyzer_raises("all"), tn(RP, UNS), file="multi")

sc("f20-report-only-skill-read-fails", read_excel_raises({1}), tn(RP, UNS))
sc("f21-unclear-skill-read-fails", read_excel_raises({1}), tn("make it nicer", brain("unclear")))
sc("f22-dedupe-report-skill-read-fails-first", read_excel_raises({1}), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f23-dedupe-report-recheck-skill-read-fails", read_excel_raises({4, 5}), tn("remove the duplicate rows and give me a report", DUPRP))
sc("f24-report-all-reads-fail", read_excel_raises("all"), tn(RP, UNS))
sc("f25-dedupe-reads-fail", read_excel_raises("all"), tn("remove the duplicate rows", DUP))

if __name__ == "__main__":
    res = rq.run_all(S, A.only, tuple(A.speakers.split(",")))
    Path(A.out).parent.mkdir(parents=True, exist_ok=True)
    Path(A.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))
    print(len(res), "turns ->", A.out)
    rq.show(res) if A.show else None
