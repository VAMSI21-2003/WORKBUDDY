# ruff: noqa: E501 — reviewer tooling, not product code
"""Mutation checks: does a test fail when a guard is removed? Runs in throwaway copies.

Usage::

    python scripts/review_harness/mutations.py [--jobs 3] [--only NAME]

For each mutation it copies the repo (without .git and workspace/) to a
temporary folder, applies one textual replacement, runs
``tests/backend`` and ``tests/core/test_engine.py`` there, and reports
whether any test failed ("killed") or all passed ("SURVIVED": the guard
isn't tested). The working tree is never touched.
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

ROOT = Path(__file__).resolve().parents[2]

# name: (file, old, new), or a list of them
MUTATIONS: dict[str, Any] = {
    "delivery_no_failure_check": (
        "backend/delivery.py",
        "    if state.failure is not None:\n        return ()\n    execution",
        "    execution",
    ),
    "delivery_no_verified_check": (
        "backend/delivery.py",
        "    if verification is None or verification.passed is not True:\n        return ()\n",
        "",
    ),
    "delivery_no_reply_predicate": (
        "backend/delivery.py",
        "    if not is_verified_delivery(state):\n        return ()\n",
        "",
    ),
    "delivery_partial_on_bad_output": (
        "backend/delivery.py",
        '            logger.error("Turn %s: an output failed the delivery path check", turn_id)\n            return ()',
        '            continue',
    ),
    "check_file_no_parent_check": (
        "backend/delivery.py",
        "        if os.path.normpath(path.parent) != os.path.normpath(turn_dir):\n            return None\n",
        "",
    ),
    "check_file_allows_symlink": (
        "backend/delivery.py",
        "        if turn_dir.is_symlink() or path.is_symlink():\n            return None\n",
        "",
    ),
    "integrity_always_ok": (
        "backend/delivery.py",
        '    for file_id, (path, sha256) in expected.items():',
        '    return True\n    for file_id, (path, sha256) in expected.items():',
    ),
    "decide_no_failure_branch": (
        "backend/turns.py",
        "    if state.failure is not None:\n        return Finalization(FAILED, reply, PIPELINE, detail=turn_detail(state), commit=True)\n",
        "",
    ),
    "decide_no_mismatch_fallback": (
        "backend/turns.py",
        "    if is_verified_delivery(state):\n",
        "    if False:\n",
    ),
    "decide_integrity_ignored": (
        "backend/turns.py",
        "    if not integrity_ok:\n",
        "    if False:\n",
    ),
    "run_keeps_raised_state": (
        "backend/turns.py",
        "            logger.exception(\"Turn %s for session %s raised\", job.turn.turn_id, job.record.key)\n            final_state = None",
        "            logger.exception(\"Turn %s for session %s raised\", job.turn.turn_id, job.record.key)\n            final_state = job.work",
    ),
    "finalize_not_one_shot": (
        "backend/turns.py",
        "        with record.lock:\n            if turn.finished:\n                return\n            if result.commit",
        "        with record.lock:\n            if result.commit",
    ),
    "start_no_reset_transient": (
        "backend/turns.py",
        "                work.reset_transient()  # never let a stale failure skip this turn's stages\n",
        "",
    ),
    "start_no_deepcopy": (
        "backend/turns.py",
        "                work = copy.deepcopy(record.state)",
        "                work = record.state",
    ),
    "start_no_record_user_message": (
        "backend/turns.py",
        "                work.context.record_user_message(message)\n",
        "",
    ),
    "commit_no_record_reply": (
        "backend/turns.py",
        "                state.context.record_buddy_reply(result.reply)\n",
        "",
    ),
    "reply_trailing_space": (
        "backend/views.py",
        '        "reply": turn.reply,',
        '        "reply": (turn.reply + " ") if turn.reply else turn.reply,',
    ),
    "download_no_hash_check": (
        "backend/routes.py",
        "    if checked is None or hashlib.sha256(checked[1]).hexdigest() != delivered.sha256:",
        "    if checked is None:",
    ),
    "upload_two_sheets_ok": (
        "backend/uploads.py",
        "    if worksheets + other_sheets > 1:",
        "    if worksheets + other_sheets > 2:",
    ),
    "upload_no_size_cap_in_stream": (
        "backend/uploads.py",
        "                if size > settings.max_upload_bytes:",
        "                if False:",
    ),
    "upload_no_busy_check_at_register": (
        "backend/routes.py",
        "            if record.busy:  # a turn started while the file was being written\n",
        "            if False:\n",
    ),
    "redaction_no_session_pattern": (
        "backend/logging_filter.py",
        '    (re.compile(r"(/sessions/)[A-Za-z0-9_\\-%.]+"), r"\\1" + REDACTED),\n',
        "",
    ),
    "repair_leaves_running": (
        "backend/store.py",
        "            if turn.finished:\n                continue\n",
        "            continue\n",
    ),
    "restore_no_uploaded_files_check": (
        "backend/store.py",
        '    _check(uploaded == upload_paths, "context: uploaded_files disagree with the registry")\n',
        "",
    ),
    "engine_no_m1_hardening": (
        "core/engine.py",
        "                    if state.failure is None:\n                        state.failure = StageFailure(stage=name, error_type=type(exc).__name__)\n                    raise",
        "                    raise",
    ),
    "body_cap_not_counted": (
        "backend/middleware.py",
        "                if received > self.max_body_bytes:",
        "                if False:",
    ),
    "default_auto_approve_on": (
        "backend/settings.py",
        "    auto_approve: bool = False",
        "    auto_approve: bool = True",
    ),
    "slot_released_at_finalize_not_exit": (
        "backend/turns.py",
        "    def _expire(self, job: _Job) -> None:\n        \"\"\"The deadline passed: finalize as failed, unless the turn already finished.\"\"\"\n        self._finalize(job, None, timed_out=True)",
        "    def _expire(self, job: _Job) -> None:\n        \"\"\"The deadline passed: finalize as failed, unless the turn already finished.\"\"\"\n        self._finalize(job, None, timed_out=True)\n        self._release_slot()",
    ),
    # Several edits at once: a list of (file, old, new).
    "both_resets_removed": [
        (
            "backend/turns.py",
            "                work.reset_transient()  # never let a stale failure skip this turn's stages\n",
            "",
        ),
        ("backend/turns.py", "                state.reset_transient()\n", ""),
    ],
}


def run_one(name: str) -> tuple[str, str, str]:
    spec = MUTATIONS[name]
    edits = spec if isinstance(spec, list) else [spec]
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(
            ROOT,
            copy,
            ignore=shutil.ignore_patterns(".git", "workspace", "scratch", "__pycache__", ".pytest_cache"),
        )
        for file, old, new in edits:
            target = copy / file
            text = target.read_text()
            if text.count(old) != 1:
                return name, "NOT APPLIED", f"pattern found {text.count(old)} times"
            target.write_text(text.replace(old, new))
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x",
             "tests/backend", "tests/core/test_engine.py"],
            cwd=copy,
            capture_output=True,
            text=True,
            timeout=900,
        )
        last = [line for line in proc.stdout.splitlines() if line.strip()][-1:] or [""]
        failed = [line for line in proc.stdout.splitlines() if line.startswith("FAILED")]
        verdict = "killed" if proc.returncode != 0 else "SURVIVED"
        return name, verdict, (failed[0] if failed else last[0])[:200]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--only")
    args = parser.parse_args()
    names = [n for n in MUTATIONS if not args.only or args.only in n]
    with ThreadPoolExecutor(args.jobs) as pool:
        for name, verdict, info in pool.map(run_one, names):
            print(f"{verdict:12} {name:36} {info}", flush=True)


if __name__ == "__main__":
    main()
