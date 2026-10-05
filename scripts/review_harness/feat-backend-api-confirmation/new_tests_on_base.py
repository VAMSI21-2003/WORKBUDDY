# ruff: noqa: E501 — reviewer tooling, not product code
"""Do this branch's new and changed tests fail on the base code?

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/new_tests_on_base.py BASE_TREE

Copies BASE_TREE (for example a ``git worktree`` of ``origin/main``) to a
temporary folder, puts this checkout's ``tests/`` over it, and runs each
test file this branch added or changed there, one at a time. Prints, per
file, pytest's summary line (a collection error counts as a failure: the
module imports something the base doesn't have). The new Voice test is also
run on its own, since its module collects on the base.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

TARGETS = [
    "tests/backend/test_confirmation_api.py",
    "tests/backend/test_confirmation_provider.py",
    "tests/buddy/test_hands_confirmation_plan.py",
    "tests/buddy/test_voice.py::test_a_rejected_plan_isnt_called_a_problem_and_says_nothing_ran",
    "tests/backend/test_turns.py",
    "tests/backend/test_app_basics.py",
    "tests/backend/test_openapi_sync.py",
]


def main() -> None:
    base = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(base, copy, ignore=shutil.ignore_patterns(".git", "workspace", "scratch", "__pycache__", ".pytest_cache"))
        shutil.rmtree(copy / "tests")
        shutil.copytree(ROOT / "tests", copy / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        for target in TARGETS:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore", target],
                cwd=copy,
                capture_output=True,
                text=True,
                timeout=1200,
            )
            lines = [line for line in proc.stdout.splitlines() if line.strip()]
            summary = lines[-1] if lines else proc.stderr.strip().splitlines()[-1:]
            first_error = next((line for line in lines if line.startswith(("E ", "ERROR", "FAILED"))), "")
            print(f"{target}\n    exit {proc.returncode}: {summary}\n    first: {first_error[:200]}", flush=True)


if __name__ == "__main__":
    main()
