# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 mutation checks: the round-1 fixes' guards, and round 1's surviving mutations again.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_mutations.py [--jobs 4] [--only NAME] [--root DIR]

Same method as ``mutations.py`` (round 1): copy the checkout to a temporary
folder, apply one textual replacement (it must match exactly once, else
"NOT APPLIED"), run the tests with ``-x``, and print "killed" with the
first failing test, or "SURVIVED". Hands mutations also run
``tests/buddy/test_engine_factory.py``, where the M1 engine tests are.
Round 1's survivors are imported from ``mutations.py`` by name; where the
code they patched changed, an updated pattern is given here.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import mutations as round1  # noqa: E402

ROOT = HERE.parents[2]
C = "backend/confirmation.py"
H = "buddy/hands.py"
S = "backend/settings.py"

BASE_TESTS = list(round1.TEST_PATHS)
HANDS_TESTS = [*BASE_TESTS, "tests/buddy/test_engine_factory.py"]

NEW: dict[str, Any] = {
    # -- M1: a chain that can't finish doesn't start -----------------------------------
    "m1_precheck_off": (H, "        blocked_at = _known_stop(routes)\n", "        blocked_at = None\n"),
    "m1_plan_ignores_known_stop": (H, "        if _known_stop(routes) is not None:\n            return ()\n", ""),
    "m1_skip_read_steps_too": (H, "            if blocked_at is not None and index < blocked_at and _changes_files(routes[index]):", "            if blocked_at is not None and index < blocked_at:"),
    "m1_skip_the_blocked_step_too": (H, "            if blocked_at is not None and index < blocked_at and _changes_files(routes[index]):", "            if blocked_at is not None and index <= blocked_at and _changes_files(routes[index]):"),
    "m1_changes_files_any_routed": (H, "    return skill is not None and skill.permission_tier is not PermissionTier.READ", "    return skill is not None"),
    "m1_known_stop_never": (H, "    if routes and routes[-1] is None:\n        return len(routes) - 1\n    return None", "    return None"),
    "m1_routes_dont_stop_at_unroutable": (H, "                routes.append(None)\n                break\n", "                routes.append(None)\n"),
    "m1_not_started_uncounted": (H, "                not_started += 1\n", ""),
    "m1_plan_task_id_off_by_one": (H, "                        task_id=self._build_task(state, request, index, request.target).task_id,", "                        task_id=self._build_task(state, request, index + 1, request.target).task_id,"),
    "m1_hands_never_announces": (H, "        if isinstance(provider, PlanAwareConfirmationProvider):\n            provider.expect_plan(self._plan(state, requests, routes))\n", ""),
    "m1_plan_includes_read_steps": (H, "            if skill is not None and skill.permission_tier in _CONFIRMATION_REQUIRED_TIERS:", "            if skill is not None:"),
    # -- L2: a closed provider refuses every later step ------------------------------------
    "l2_old_order_outcome_first": (
        C,
        "            if self._closed_by is not None:\n                # The turn finished or the server is stopping: no step starts\n                # now, even one the user approved (review round 1, L2).\n                self._settle(ConfirmationOutcome(REJECT, self._closed_by))\n                return False\n            if self._outcome is not None:\n                return self._outcome.decision == APPROVE\n",
        "            if self._outcome is not None:\n                return self._outcome.decision == APPROVE\n            if self._closed_by is not None:\n                self._settle(ConfirmationOutcome(REJECT, self._closed_by))\n                return False\n",
    ),
    "l2_closed_returns_the_decision": (
        C,
        "                self._settle(ConfirmationOutcome(REJECT, self._closed_by))\n                return False\n",
        "                self._settle(ConfirmationOutcome(REJECT, self._closed_by))\n                return self._outcome is not None and self._outcome.decision == APPROVE\n",
    ),
    "l2_closed_block_removed": (
        C,
        "            if self._closed_by is not None:\n                # The turn finished or the server is stopping: no step starts\n                # now, even one the user approved (review round 1, L2).\n                self._settle(ConfirmationOutcome(REJECT, self._closed_by))\n                return False\n",
        "",
    ),
    # -- L7: the confirmation timeout's cap -----------------------------------------------
    "l7_cap_removed": (S, "        if self.confirmation_timeout_s > MAX_CONFIRMATION_TIMEOUT_S:\n", "        if False:\n"),
    "l7_cap_inclusive": (S, "        if self.confirmation_timeout_s > MAX_CONFIRMATION_TIMEOUT_S:\n", "        if self.confirmation_timeout_s >= MAX_CONFIRMATION_TIMEOUT_S:\n"),
    "l7_cap_ten_hours": (S, "MAX_CONFIRMATION_TIMEOUT_S = 3600\n", "MAX_CONFIRMATION_TIMEOUT_S = 36000\n"),
}

# Round 1's survivors (out/mutations.txt), re-run against the round-2 tests.
ROUND1_SURVIVORS = [
    "next_step_no_tier_vs_plan",
    "next_step_no_confirmed_tiers",
    "settle_overwrites",
    "expire_ignores_generation",
    "expire_doesnt_mark_finishing",
    "on_publish_ignores_finishing",
    "on_settle_keeps_plan_visible",
    "on_settle_keeps_awaiting",
    "finalize_doesnt_close_gate",
    "answer_skips_pending_check",
    "jobs_never_removed",
    "start_failure_not_finalized",
    "store_writes_live_status",
    "store_running_with_decision_ok",
]

MUTATIONS: dict[str, tuple[Any, list[str]]] = {
    **{name: (spec, HANDS_TESTS if spec[0] == H else BASE_TESTS) for name, spec in NEW.items()},
    **{f"r1:{name}": (round1.MUTATIONS[name], BASE_TESTS) for name in ROUND1_SURVIVORS},
}


def run_one(name: str, root: Path) -> tuple[str, str, str]:
    spec, test_paths = MUTATIONS[name]
    edits = spec if isinstance(spec, list) else [spec]
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(root, copy, ignore=shutil.ignore_patterns(".git", "workspace", "scratch", "__pycache__", ".pytest_cache"))
        for file, old, new in edits:
            target = copy / file
            text = target.read_text()
            if text.count(old) != 1:
                return name, "NOT APPLIED", f"pattern found {text.count(old)} times in {file}"
            target.write_text(text.replace(old, new))
        paths = [p for p in test_paths if (copy / p).exists()]
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "-W", "ignore", *paths],
                cwd=copy, capture_output=True, text=True, timeout=1500,
            )
        except subprocess.TimeoutExpired:
            return name, "killed (timeout)", "the tests didn't finish in 1500 s"
        last = [line for line in proc.stdout.splitlines() if line.strip()][-1:] or [""]
        failed = [line for line in proc.stdout.splitlines() if line.startswith(("FAILED tests/", "ERROR tests/"))]
        verdict = "killed" if proc.returncode != 0 else "SURVIVED"
        return name, verdict, (failed[0] if failed else last[0])[:220]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--only")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    names = [n for n in MUTATIONS if not args.only or args.only in n]
    with ThreadPoolExecutor(args.jobs) as pool:
        for name, verdict, info in pool.map(lambda n: run_one(n, args.root), names):
            print(f"{verdict:16} {name:38} {info}", flush=True)


if __name__ == "__main__":
    main()
