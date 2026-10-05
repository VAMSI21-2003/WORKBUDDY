# ruff: noqa: E501 — reviewer tooling, not product code
"""PR-3 mutation checks: does a test fail when one guard of the new code is removed?

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/mutations.py [--jobs 3] [--only NAME] [--root DIR]

For each mutation it copies the checkout (without .git, workspace/, scratch/)
to a temporary folder, applies one textual replacement (it must match
exactly once, else "NOT APPLIED"), runs ``TEST_PATHS`` there with ``-x``,
and prints "killed" (a test failed, with the first failure), "SURVIVED" (every
test passed: the guard isn't tested) or "killed (timeout)". The working tree
is never touched.
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

C = "backend/confirmation.py"
T = "backend/turns.py"
H = "buddy/hands.py"
V = "buddy/voice.py"

# name: (file, old, new) or a list of them
MUTATIONS: dict[str, Any] = {
    # -- WebConfirmationProvider -------------------------------------------------------
    "next_step_no_task_id": (C, "            and task.task_id == expected.task_id\n", ""),
    "next_step_no_tier_vs_plan": (C, "            and tier is expected.tier\n", ""),
    "next_step_no_task_permission": (C, "            and task.permission is expected.tier\n", ""),
    "next_step_no_confirmed_tiers": (C, "            tier in _CONFIRMED_TIERS\n            and ", "            "),
    "next_step_ignores_refusing": (C, "        if self._refusing or plan is None or self._next >= len(plan):", "        if plan is None or self._next >= len(plan):"),
    "next_step_past_end_allowed": (C, "        if self._refusing or plan is None or self._next >= len(plan):\n            return False\n        expected = plan[self._next]", "        if self._refusing or plan is None:\n            return False\n        expected = plan[min(self._next, len(plan) - 1)]"),
    "no_plan_means_approve": (C, "        if self._refusing or plan is None or self._next >= len(plan):\n            return False\n", "        if plan is None:\n            return True\n        if self._refusing or self._next >= len(plan):\n            return False\n"),
    "timeout_is_approve": (C, "                self._settle(ConfirmationOutcome(REJECT, BY_TIMEOUT))", "                self._settle(ConfirmationOutcome(APPROVE, BY_TIMEOUT))"),
    "timeout_approves_via_return": (C, "            assert self._outcome is not None\n            return self._outcome.decision == APPROVE", "            assert self._outcome is not None\n            return True"),
    "later_steps_always_true": (C, "            if self._outcome is not None:\n                return self._outcome.decision == APPROVE", "            if self._outcome is not None:\n                return True"),
    "closed_before_ask_publishes": (C, "            if self._closed_by is not None:\n                self._settle(ConfirmationOutcome(REJECT, self._closed_by))\n                return False\n", ""),
    "close_doesnt_settle": (C, "                if self._pending is not None:\n                    self._settle(ConfirmationOutcome(REJECT, source))\n", "                pass\n"),
    "close_doesnt_wake": (C, "            try:\n                if self._pending is not None:\n                    self._settle(ConfirmationOutcome(REJECT, source))\n            finally:\n                self._answered.set()", "            if self._pending is not None:\n                self._settle(ConfirmationOutcome(REJECT, source))"),
    "close_records_no_source": (C, "            if self._closed_by is None:\n                self._closed_by = source\n", ""),
    "publish_even_if_finishing": (C, "            if not self._on_publish(plan):\n                self._settle(ConfirmationOutcome(REJECT, BY_TURN_END))\n                return False\n", "            self._on_publish(plan)\n"),
    "stale_id_accepted": (C, "            if confirmation_id != pending.confirmation_id:\n                raise ApiError(ErrorCode.STALE_CONFIRMATION)\n", ""),
    "answer_without_pending_ok": (C, "            if pending is None:\n                raise ApiError(ErrorCode.NO_PENDING_CONFIRMATION)\n            if confirmation_id", "            if pending is not None and confirmation_id"),
    "answer_doesnt_wake": (C, "            try:\n                self._settle(ConfirmationOutcome(decision, BY_USER))\n            finally:\n                self._answered.set()", "            self._settle(ConfirmationOutcome(decision, BY_USER))"),
    "second_plan_accepted": (C, "            if self._plan is not None:\n                # Hands announces once per run; a second plan means the\n                # first can't be trusted to be what runs.\n                logger.error(\"A second plan was announced for one turn; refusing every step\")\n                self._refusing = True\n                return\n", ""),
    "settle_overwrites": (C, "        if self._outcome is not None:\n            return\n        self._outcome = outcome", "        self._outcome = outcome"),
    "next_not_advanced": (C, "            self._next += 1\n", ""),
    "empty_plan_built": (C, "    if not steps:\n        raise ValueError(\"a plan needs at least one step\")\n", "    if not steps:\n        return PendingPlan(confirmation_id, (), None, expires_at)\n"),
    "later_template_names_upload": (C, "    return later.format(capability=capability, previous=position - 1)", "    return first.format(capability=capability, input_name=input_name)"),
    "plan_note_always": (C, "    return PLAN_NOTE_CHAIN if step_count >= 2 else None", "    return PLAN_NOTE_CHAIN"),
    "plan_note_never": (C, "    return PLAN_NOTE_CHAIN if step_count >= 2 else None", "    return None"),
    "plan_input_name_is_path": (C, "    input_name = Path(steps[0].input_reference).name", "    input_name = steps[0].input_reference"),
    # -- Hands -----------------------------------------------------------------------------
    "hands_never_announces": (H, "        if isinstance(provider, PlanAwareConfirmationProvider):\n            provider.expect_plan(self.confirmation_plan(state, requests))\n", ""),
    "plan_continues_past_unroutable": (H, "            except DispatchError:\n                break\n", "            except DispatchError:\n                continue\n"),
    "plan_includes_read_steps": (H, "            if skill.permission_tier in _CONFIRMATION_REQUIRED_TIERS:\n                steps.append(", "            if True:\n                steps.append("),
    "plan_task_id_off_by_one": (H, "            probe = self._build_task(state, request, index, request.target)\n            try:\n                skill = self._dispatcher.select(probe, request.capability)", "            probe = self._build_task(state, request, index + 1, request.target)\n            try:\n                skill = self._dispatcher.select(probe, request.capability)"),
    # -- Voice -------------------------------------------------------------------------------
    "voice_problem_always_dropped": (V, "            return step.status == DECLINED_STATUS\n    return False", "            return True\n    return True"),
    "voice_problem_never_dropped": (V, "        if not _declined_before_any_change(execution_result):\n", "        if True:\n"),
    "voice_decline_any_step": (V, "        if _is_action_step(step):\n            return step.status == DECLINED_STATUS\n    return False", "        if _is_action_step(step) and step.status == DECLINED_STATUS:\n            return True\n    return False"),
    # -- TurnRunner ----------------------------------------------------------------------------
    "publish_keeps_deadline": (T, "        turn.status = AWAITING_CONFIRMATION\n        self._disarm_deadline(job)\n", "        turn.status = AWAITING_CONFIRMATION\n"),
    "settle_doesnt_rearm": (T, "            if not job.finishing:\n                self._arm_deadline(job)\n", ""),
    "disarm_forgets_time_used": (T, "            job.deadline_left -= time.monotonic() - job.armed_at\n", ""),
    "expire_ignores_generation": (T, "            if generation != job.generation or job.turn.finished or job.finishing:", "            if job.turn.finished or job.finishing:"),
    "expire_doesnt_mark_finishing": (T, "                return  # disarmed, paused or finished since this timer was armed\n            job.finishing = True\n", "                return  # disarmed, paused or finished since this timer was armed\n"),
    "on_publish_ignores_finishing": (T, "        if turn.finished or job.finishing:\n            return False\n        turn.pending_confirmation = plan", "        if turn.finished:\n            return False\n        turn.pending_confirmation = plan"),
    "on_settle_no_record": (T, "        turn.confirmation = outcome\n", ""),
    "on_settle_keeps_plan_visible": (T, "        turn.confirmation = outcome\n        turn.pending_confirmation = None\n", "        turn.confirmation = outcome\n"),
    "on_settle_keeps_awaiting": (T, "        if turn.status == AWAITING_CONFIRMATION:\n            turn.status = RUNNING\n", "        if turn.status == AWAITING_CONFIRMATION:\n"),
    "finalize_doesnt_close_gate": (T, "                if job.gate is not None:\n                    job.gate.close(BY_TURN_END)\n", ""),
    "finalize_close_unguarded": (T, "            try:\n                self._disarm_deadline(job)\n                if job.gate is not None:\n                    job.gate.close(BY_TURN_END)\n            except BaseException:\n                logger.exception(\"Turn %s: closing its plan failed\", turn.turn_id)\n", "            self._disarm_deadline(job)\n            if job.gate is not None:\n                job.gate.close(BY_TURN_END)\n"),
    "finalize_record_unguarded": (T, "            try:\n                self._record_in_session(job, state, result)\n            except BaseException:\n                # The turn still finishes as decided; only the session's\n                # conversation misses it.\n                logger.exception(\"Turn %s: the session couldn't record it\", turn.turn_id)\n", "            self._record_in_session(job, state, result)\n"),
    "finalize_exception_only": (T, "        except BaseException:\n            logger.exception(\"Turn %s: finalization failed; using the fallback\", turn.turn_id)", "        except Exception:\n            logger.exception(\"Turn %s: finalization failed; using the fallback\", turn.turn_id)"),
    "finalize_read_only_unguarded": (T, "        try:\n            make_read_only(result.delivered)\n        except BaseException:\n            logger.exception(\n                \"Turn %s: marking files read-only failed; using the fallback\", turn.turn_id\n            )\n            result = Finalization(FAILED, FALLBACK_PIPELINE_ERROR, PIPELINE_FALLBACK)\n", "        make_read_only(result.delivered)\n"),
    "shutdown_noop": (T, "        for job in jobs:\n            if job.gate is not None:\n                job.gate.close(BY_SHUTDOWN)\n", ""),
    "answer_skips_pending_check": (T, "            if job is None or job.gate is None or turn.pending_confirmation is None:", "            if job is None or job.gate is None:"),
    "jobs_never_removed": (T, "            with self._jobs_lock:\n                self._jobs.pop((record.key, turn.turn_id), None)\n", ""),
    "auto_approve_always_web": (T, "        if self._settings.auto_approve:\n            return StaticConfirmationProvider(approve=True)\n", ""),
    "start_failure_not_finalized": (T, "            try:\n                with record.lock:\n                    self._disarm_deadline(job)\n                self._finalize(job, None, timed_out=False)\n            finally:\n                self._release_slot()\n            raise", "            self._release_slot()\n            raise"),
    # -- Views, store, settings, app --------------------------------------------------------
    "view_never_awaiting": ("backend/views.py", "    if turn.finished or turn.status != AWAITING_CONFIRMATION:\n        return None", "    return None"),
    "session_plan_hidden": ("backend/views.py", "        pending_confirmation=_waiting_plan(running) if running is not None else None,", "        pending_confirmation=None,"),
    "detail_without_confirmation": ("backend/views.py", "    return TurnDetail.model_validate({**turn.detail, \"confirmation\": confirmation})", "    return TurnDetail.model_validate({**turn.detail, \"confirmation\": None})"),
    "store_writes_live_status": ("backend/store.py", "            \"status\": FINISHED if turn.finished else RUNNING,", "            \"status\": turn.status,"),
    "store_drops_decision": ("backend/store.py", "            \"confirmation\": (\n                {\"decision\": confirmation.decision, \"decision_source\": confirmation.source}\n                if confirmation is not None\n                else None\n            ),", "            \"confirmation\": None,"),
    "store_unchecked_decision": ("backend/store.py", "    _check(data[\"decision\"] in DECISIONS, \"turn: confirmation: decision\")\n    _check(data[\"decision_source\"] in DECISION_SOURCES, \"turn: confirmation: decision_source\")\n", ""),
    "store_running_with_decision_ok": ("backend/store.py", "            _check(confirmation is None, \"turn: confirmation on a running turn\")\n", ""),
    "settings_reads_auto_approve": ("backend/settings.py", "            confirmation_timeout_s=float(_int(env, \"WORKBUDDY_CONFIRMATION_TIMEOUT_S\", 120)),\n", "            confirmation_timeout_s=float(_int(env, \"WORKBUDDY_CONFIRMATION_TIMEOUT_S\", 120)),\n            auto_approve=bool(env.get(\"WORKBUDDY_AUTO_APPROVE\")),\n"),
    "settings_timeout_unchecked": ("backend/settings.py", "        for name in (\"turn_deadline_s\", \"confirmation_timeout_s\"):", "        for name in (\"turn_deadline_s\",):"),
    "lifespan_no_shutdown": ("backend/main.py", "        app.state.runner.shutdown()\n", "        pass\n"),
    "schema_steps_may_be_empty": ("backend/schemas.py", "    steps: list[PlanStep] = Field(min_length=1)", "    steps: list[PlanStep]"),
}

TEST_PATHS = [
    "tests/backend",
    "tests/buddy/test_hands_confirmation_plan.py",
    "tests/buddy/test_hands.py",
    "tests/buddy/test_voice.py",
    "tests/core/test_engine.py",
]


def run_one(name: str, root: Path) -> tuple[str, str, str]:
    spec = MUTATIONS[name]
    edits = spec if isinstance(spec, list) else [spec]
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "repo"
        shutil.copytree(
            root,
            copy,
            ignore=shutil.ignore_patterns(".git", "workspace", "scratch", "__pycache__", ".pytest_cache"),
        )
        for file, old, new in edits:
            target = copy / file
            text = target.read_text()
            if text.count(old) != 1:
                return name, "NOT APPLIED", f"pattern found {text.count(old)} times in {file}"
            target.write_text(text.replace(old, new))
        paths = [p for p in TEST_PATHS if (copy / p).exists()]
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-x", "-W", "ignore", *paths],
                cwd=copy,
                capture_output=True,
                text=True,
                timeout=900,
            )
        except subprocess.TimeoutExpired:
            return name, "killed (timeout)", "the suite didn't finish in 900 s"
        last = [line for line in proc.stdout.splitlines() if line.strip()][-1:] or [""]
        failed = [line for line in proc.stdout.splitlines() if line.startswith(("FAILED tests/", "ERROR tests/"))]
        verdict = "killed" if proc.returncode != 0 else "SURVIVED"
        return name, verdict, (failed[0] if failed else last[0])[:220]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=3)
    parser.add_argument("--only")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    names = [n for n in MUTATIONS if not args.only or args.only in n]
    with ThreadPoolExecutor(args.jobs) as pool:
        for name, verdict, info in pool.map(lambda n: run_one(n, args.root), names):
            print(f"{verdict:16} {name:34} {info}", flush=True)


if __name__ == "__main__":
    main()
