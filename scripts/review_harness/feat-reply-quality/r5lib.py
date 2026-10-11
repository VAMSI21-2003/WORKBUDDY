"""Round 5 helpers: argument parsing, fault patches and a classifier for replies (shared by the r5_*.py probes).

Real ``build_engine`` (real skills, planner, Inspector, Voice) with a fake LLM only, through ``rq.py``.
Nothing here reads the head's removed functions (``mentions_report`` and friends).
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))


def args(extra=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--tree", default=str(HERE.parents[2]))
    ap.add_argument("--speakers", default="template,friend,focus")
    ap.add_argument("--out", default="")
    ap.add_argument("--only", default="")
    ap.add_argument("--quiet", action="store_true")
    if extra:
        extra(ap)
    return ap.parse_args()


def start(a):
    import rq

    rq.setup(a.tree)
    return rq


# Fixed-text probes for what a reply may say -------------------------------------------------------
CANT = re.compile(r"can't do|can’t do|something I can't|didn't read that as something I can do", re.I)
REPORT = re.compile(r"Report on (the copy|your original file)")
NOT_PRODUCED = re.compile(r"I (couldn't|didn't) produce the report")
LEAD = re.compile(r"^(?:[^—]{0,60}?)?(Done|Partly done|Nothing)", re.M)


def classify(rep: str) -> list[str]:
    """Automatic tags on a reply; a human reads every reply too."""
    tags = []
    if not rep:
        return ["empty"]
    if REPORT.search(rep) and CANT.search(rep):
        tags.append("REPORT+CANT")
    if CANT.search(rep) and ("Here's what I noticed" in rep or "I also noticed" in rep):
        tags.append("CANT+FINDINGS")
    if "Your message mentions" in rep:
        tags.append("MENTIONS")
    if re.search(r"\bDone\b", rep):
        tags.append("Done")
    if "Partly done" in rep:
        tags.append("Partly")
    if REPORT.search(rep):
        tags.append("REPORT")
    if NOT_PRODUCED.search(rep):
        tags.append("NOT_PRODUCED")
    return tags


# Fault patches (the analysis skill / the verifiers) -------------------------------------------------
def _calls(on_calls):
    n = {"c": 0}

    def hit():
        n["c"] += 1
        return on_calls == "all" or n["c"] in on_calls

    return hit


def analyzer_raises(on_calls="all"):
    @contextlib.contextmanager
    def cm():
        import skills.excel.analyze as an

        orig = an.ExactDuplicateRowAnalyzer.__call__
        hit = _calls(on_calls)

        def fake(self, df):
            if hit():
                raise RuntimeError("analyzer boom /secret/path TOPSECRET")
            return orig(self, df)

        with mock.patch.object(an.ExactDuplicateRowAnalyzer, "__call__", fake):
            yield

    return cm


def analysis_verifier_fails(on_calls="all"):
    @contextlib.contextmanager
    def cm():
        import buddy.inspector as insp

        orig = insp.SpreadsheetAnalysisVerifier.__call__
        hit = _calls(on_calls)

        def fake(self, request):
            if hit():
                return insp.VerificationResult(
                    passed=False, confidence=1.0, findings=("TOPSECRET check says no",)
                )
            return orig(self, request)

        with mock.patch.object(insp.SpreadsheetAnalysisVerifier, "__call__", fake):
            yield

    return cm


def dedupe_verifier_fails():
    @contextlib.contextmanager
    def cm():
        import buddy.inspector as insp

        def fake(self, request):
            return insp.VerificationResult(
                passed=False, confidence=1.0, findings=("TOPSECRET dedupe check says no",)
            )

        with mock.patch.object(insp.SpreadsheetDedupeVerifier, "__call__", fake):
            yield

    return cm


def read_excel_raises(on_calls="all"):
    @contextlib.contextmanager
    def cm():
        import pandas as pd

        orig = pd.read_excel
        hit = _calls(on_calls)

        def fake(*a, **k):
            if hit():
                raise RuntimeError("read boom /secret/path TOPSECRET")
            return orig(*a, **k)

        with mock.patch.object(pd, "read_excel", fake):
            yield

    return cm


def planner_raises():
    @contextlib.contextmanager
    def cm():
        import buddy.planner as pl

        with mock.patch.object(pl.Planner, "_resolve_analysis_target", side_effect=RuntimeError("planner boom TOPSECRET")):
            yield

    return cm


def hands_raises():
    @contextlib.contextmanager
    def cm():
        import buddy.hands as hands

        with mock.patch.object(hands.Hands, "__call__", side_effect=RuntimeError("hands boom TOPSECRET")):
            yield

    return cm


def dump(res, path):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(res, indent=1, ensure_ascii=False))
