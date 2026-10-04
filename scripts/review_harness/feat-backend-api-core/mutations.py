# ruff: noqa: E501 — reviewer tooling, not product code
"""Mutation checks: does a test fail when a guard is removed? Runs in throwaway copies.

Usage::

    python scripts/review_harness/feat-backend-api-core/mutations.py [--jobs 3] [--only NAME]

For each mutation it copies the repo (without .git and workspace/) to a
temporary folder, applies one textual replacement, runs ``TEST_PATHS``
(``tests/backend``, ``tests/core/test_engine.py`` and the buddy tests for
Voice and state) there, and reports
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

ROOT = Path(__file__).resolve().parents[3]

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
        "        with record.lock:\n            if turn.finished:\n                return\n            # The finished record",
        "        with record.lock:\n            # The finished record",
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


# Round 2: the round-1 fixes (M1, M2, M3, L1, L8, L11).
ROUND2: dict[str, Any] = {
    "m1_no_nothing_ran_branch": (
        "buddy/voice.py",
        "                return NOTHING_RAN_REPLY\n",
        "                pass\n",
    ),
    "m1_ignores_errors": (
        "buddy/voice.py",
        "execution_result.steps or execution_result.outputs or execution_result.errors",
        "execution_result.steps or execution_result.outputs",
    ),
    "m1_ignores_steps": (
        "buddy/voice.py",
        "execution_result.steps or execution_result.outputs or execution_result.errors",
        "execution_result.outputs or execution_result.errors",
    ),
    "m2_not_set_at_start": (
        "backend/turns.py",
        "                work.earlier_deliveries = len(record.delivered())\n",
        "",
    ),
    "m2_counts_every_turn": (
        "backend/turns.py",
        "                work.earlier_deliveries = len(record.delivered())\n",
        "                work.earlier_deliveries = len(record.turns)\n",
    ),
    "m2_sentence_always": (
        "buddy/voice.py",
        "    if request.earlier_deliveries > 0:\n",
        "    if True:\n",
    ),
    "m2_not_passed_to_speaker": (
        "buddy/voice.py",
        "            earlier_deliveries=state.earlier_deliveries,\n",
        "",
    ),
    "m2_reset_keeps_it": (
        "buddy/state.py",
        "        self.earlier_deliveries = 0\n",
        "",
    ),
    "m3_save_failure_ignored": (
        "backend/turns.py",
        "            if not self._save_finished(record, turn, result, now):\n",
        "            if self._save_finished(record, turn, result, now) and False:\n",
    ),
    "m3_no_fallback_save": (
        "backend/turns.py",
        "                self._save_finished(record, turn, result, now)\n            if result.commit",
        "            if result.commit",
    ),
    "m3_save_after_publish": [
        (
            "backend/turns.py",
            "            if not self._save_finished(record, turn, result, now):\n                result = Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)\n                self._save_finished(record, turn, result, now)\n",
            "",
        ),
        (
            "backend/turns.py",
            "                self._registry.save_session(record)\n            except Exception:",
            "                self._registry.save_turn(record, turn)\n                self._registry.save_session(record)\n            except Exception:",
        ),
    ],
    "l1_always_collapse": (
        "backend/logging_filter.py",
        "    if redact(message) != message:\n",
        "    if True:\n",
    ),
    "l1_never_collapse": (
        "backend/logging_filter.py",
        "    if redact(message) != message:\n",
        "    if False:\n",
    ),
    "l8_no_encodable_check": (
        "backend/routes.py",
        " or not _encodable(message):",
        ":",
    ),
    "l8_no_allow_header": (
        "backend/errors.py",
        'headers={"Allow": allow} if allow else None',
        "headers=None",
    ),
    "l11_lax_detail": (
        "backend/store.py",
        '_check(data["detail"] is None or _valid_detail(data["detail"]), "turn: detail")',
        '_check(data["detail"] is None or isinstance(data["detail"], dict), "turn: detail")',
    ),
}
MUTATIONS.update(ROUND2)


# Round 3: the round-2 fixes (R2-M1 per-upload count, R2-L1 redaction, R2-L2 BaseException).
# Some round-2 mutations above target code that changed ("NOT APPLIED"); these retarget them.
_R3_COUNT = (
    "                work.earlier_deliveries = sum(\n"
    "                    len(t.delivered_files)\n"
    "                    for t in record.turns\n"
    "                    if t.finished\n"
    "                    and t.outcome == DELIVERED\n"
    "                    and target_id is not None\n"
    "                    and t.target_file_id == target_id\n"
    "                )\n"
)
_R3_TARGET = "                target_id = file_id if file_id is not None else record.active_file_id\n"
ROUND3: dict[str, Any] = {
    "r3_m2_not_set": ("backend/turns.py", _R3_COUNT, ""),
    "r3_m2_counts_every_turn": (
        "backend/turns.py",
        _R3_COUNT,
        "                work.earlier_deliveries = len(record.turns)\n",
    ),
    "r3_m2_whole_session": (  # round 2's rule: any delivery in the session
        "backend/turns.py",
        "                    and t.target_file_id == target_id\n",
        "",
    ),
    "r3_m2_target_ignores_file_id": (
        "backend/turns.py",
        _R3_TARGET,
        "                target_id = record.active_file_id\n",
    ),
    "r3_m2_target_only_file_id": (
        "backend/turns.py",
        _R3_TARGET,
        "                target_id = file_id\n",
    ),
    "r3_m2_no_outcome_filter": (  # expected equivalent: only delivered turns list files
        "backend/turns.py",
        "                    and t.outcome == DELIVERED\n",
        "",
    ),
    "r3_l1_redact_msg_first": (
        "backend/logging_filter.py",
        "    try:\n        original = record.getMessage()\n",
        "    if isinstance(record.msg, str):\n        record.msg = redact(record.msg)\n    try:\n        original = record.getMessage()\n",
    ),
    "r3_l1_never_collapse": (
        "backend/logging_filter.py",
        "            if reformatted != redacted:\n",
        "            if False:\n",
    ),
    "r3_l1_always_collapse": (
        "backend/logging_filter.py",
        "            if reformatted != redacted:\n",
        "            if True:\n",
    ),
    "r3_l1_unformattable_unredacted": (
        "backend/logging_filter.py",
        "        if isinstance(record.msg, str):\n            record.msg = redact(record.msg)\n        record.args = _redacted_args(record.args)\n    else:",
        "        pass\n    else:",
    ),
    "r3_l2_exception_only": (
        "backend/turns.py",
        "        except BaseException:\n            # Any failure, even a BaseException",
        "        except Exception:\n            # Any failure, even a BaseException",
    ),
}
MUTATIONS.update(ROUND3)

TEST_PATHS = [
    "tests/backend",
    "tests/core/test_engine.py",
    "tests/buddy/test_nothing_ran_and_from_original.py",
    "tests/buddy/test_state.py",
    "tests/buddy/test_voice.py",
    "tests/buddy/test_voice_catalog.py",
]


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
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", *TEST_PATHS],
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
