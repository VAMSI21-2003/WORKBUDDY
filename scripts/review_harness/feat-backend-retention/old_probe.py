# ruff: noqa: E501 — reviewer tooling, not product code
"""Run one of PR-3's probes (``../feat-backend-api-confirmation/<script>``) unchanged on a tree with PR-4.

PR-4 gives ``Settings`` real per-IP buckets by default, and the PR-3 probes
poll every 10 ms on the real clock, so on a PR-4 tree this wrapper relaxes
the default buckets (as ``tests/backend/conftest.py`` does) before the probe
builds an app. On a tree without PR-4 it changes nothing.

    python scripts/review_harness/feat-backend-retention/old_probe.py api_probe.py [--tree DIR] [probe args] > out.jsonl
"""

from __future__ import annotations

import runpy
import sys
from pathlib import Path

CONF = Path(__file__).resolve().parents[1] / "feat-backend-api-confirmation"
script = CONF / sys.argv[1]
sys.argv = [str(script), *sys.argv[2:]]
sys.path.insert(0, str(CONF))

import pr3  # noqa: E402,F401  (consumes --tree, puts that tree first on sys.path)

import backend.settings as settings_module  # noqa: E402

if hasattr(settings_module, "DEFAULT_RATE_LIMITS"):
    relaxed = settings_module.BucketSpec(burst=1_000_000, per_hour=3_600_000_000)
    for name in list(settings_module.DEFAULT_RATE_LIMITS):
        settings_module.DEFAULT_RATE_LIMITS[name] = relaxed

runpy.run_path(str(script), run_name="__main__")
