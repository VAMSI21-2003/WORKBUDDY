# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2: do the tests added or changed since round 1 fail on the round-1 code?

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_new_tests_on_old.py OLD_TREE > out.txt

OLD_TREE is a checkout of the last reviewed commit (for example
``git worktree add --detach ../r1head 3ef871c``). The script copies it to a
temporary folder, puts this checkout's ``tests/`` over it, runs every test
function listed in ``SELECTED`` there (all their parameter cases), and
prints one line per test case: PASSED or FAILED on the old code. A test
that guards behaviour round 2 changed must fail there; one that only adds
coverage to code round 1 already had may pass.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

# file -> test functions added or changed by 74f1a1e/b1059d3 (git diff 3ef871c..b1059d3 -- tests/)
SELECTED: dict[str, list[str]] = {
    "tests/backend/test_app_basics.py": [
        "test_an_invalid_setting_refuses_to_start",
        "test_the_confirmation_timeout_comes_from_its_variable",
    ],
    "tests/backend/test_confirmation_provider.py": [
        "test_once_closed_no_later_step_runs_even_after_an_approval",
        "test_a_tier_asked_that_isnt_the_plans_is_refused_even_if_the_task_agrees",
        "test_a_read_step_is_never_approved_even_if_a_plan_lists_it",
    ],
    "tests/backend/test_confirmation_api.py": [
        "test_a_chain_that_cant_finish_is_never_put_to_the_user_and_writes_nothing",
        "test_no_step_starts_after_the_deadline_ended_the_turn_even_if_approved",
        "test_a_plan_is_never_shown_once_the_deadline_has_claimed_the_turn",
        "test_a_worker_that_cant_start_finishes_its_turn_as_failed",
        "test_a_finished_turn_leaves_no_job_behind",
        "test_a_stored_running_turn_with_a_decision_is_not_restored",
    ],
    "tests/buddy/test_engine_factory.py": [
        "test_full_slice_never_says_done_when_the_last_step_cant_be_run",
        "test_full_slice_personality_voice_never_describes_a_three_step_chain_stopped_midway",
        "test_dedupe_and_sort_as_a_chain_hands_nothing_back_and_claims_nothing",
    ],
    "tests/buddy/test_hands.py": [
        "test_hands_leaves_a_next_step_with_no_skill_to_stop_the_chain_itself",
        "test_hands_records_the_failing_last_step_when_the_chain_cant_finish",
        "test_a_chain_stopped_after_an_earlier_step_passed_hands_back_no_file",
    ],
    "tests/buddy/test_hands_confirmation_plan.py": [
        "test_a_chain_that_cant_finish_asks_nothing_and_runs_no_chain_step",
        "test_a_step_the_dispatcher_cant_route_before_any_chain_step_stops_as_before",
        "test_a_chain_that_cant_finish_doesnt_start_without_a_plan_aware_provider",
    ],
}


def main() -> None:
    old = Path(sys.argv[1]).resolve()
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(old, copy, ignore=shutil.ignore_patterns(".git", "workspace", "scratch", "__pycache__", ".pytest_cache"))
        shutil.rmtree(copy / "tests")
        shutil.copytree(ROOT / "tests", copy / "tests", ignore=shutil.ignore_patterns("__pycache__"))
        totals = {"passed": 0, "failed": 0}
        for path, names in SELECTED.items():
            report = Path(tmp) / "junit.xml"
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore",
                 f"--junitxml={report}", path, "-k", " or ".join(names)],
                cwd=copy, capture_output=True, text=True, timeout=1800,
            )
            if not report.exists():
                print(f"{path}: no report (exit {proc.returncode}): {proc.stdout[-300:]}")
                continue
            seen = set()
            for tc in ET.parse(report).getroot().iter("testcase"):
                name = tc.get("name", "")
                base = name.split("[")[0]
                if base not in names:
                    continue  # -k matched a longer name
                seen.add(base)
                failed = any(child.tag in ("failure", "error") for child in tc)
                totals["failed" if failed else "passed"] += 1
                first = ""
                for child in tc:
                    if child.tag in ("failure", "error"):
                        first = (child.get("message") or "").splitlines()[0][:150] if child.get("message") else ""
                print(f"{'FAILED' if failed else 'PASSED':7} {path}::{name}  {first}")
            for missing in set(names) - seen:
                print(f"MISSING {path}::{missing} (not collected)")
        print(f"TOTAL on old code: {totals['failed']} failed, {totals['passed']} passed")


if __name__ == "__main__":
    main()
