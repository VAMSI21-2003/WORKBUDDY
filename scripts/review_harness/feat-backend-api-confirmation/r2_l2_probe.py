# ruff: noqa: E501 — reviewer tooling, not product code
"""Round 2 probe: what the L2 fix (a closed provider refuses every later step) leaves and changes.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/r2_l2_probe.py [--only NAME] [--trials N] [--speaker S] > out.jsonl

Cases:
- ``unit_close_between_answer_and_wake``: the real ``WebConfirmationProvider``.
  The worker waits on step 1; the user approves and, before the worker
  wakes, the provider is closed (the deadline or a shutdown). Does step 1
  still get ``True``? (Deterministic: the test holds the provider's lock
  across ``answer`` and ``close``.)
- ``api_answer_at_the_deadline_edge``: real app and pipeline. The plan is
  asked with no working time left (``deadline_left`` forced to 0 when the
  plan is published, as if the pipeline used the whole deadline before
  asking), then approved at once. Per trial: did step 1 start, and did it
  start after the turn had finished? What was handed back?
- ``api_shutdown_after_approval``: real app and pipeline, a two-step chain,
  approved; the server shuts down (lifespan exit) while step 1 runs. What
  does the finished turn say, and what does ``detail.confirmation`` say?
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pr3 import h  # noqa: E402

import backend.turns as turns_module  # noqa: E402
from backend.confirmation import BY_TURN_END, WebConfirmationProvider  # noqa: E402
from buddy.hands import ConfirmationStep  # noqa: E402
from skills.base import PermissionTier, SkillTask  # noqa: E402

DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
WRITE = PermissionTier.WRITE


def emit(name: str, **data: Any) -> None:
    print(json.dumps({"case": name, **data}, default=str), flush=True)


def unit_close_between_answer_and_wake(trials: int, speaker: str) -> None:
    lock = threading.RLock()
    published: list[Any] = []
    settled: list[Any] = []
    publishing = threading.Event()

    def on_publish(plan: Any) -> bool:
        published.append(plan)
        publishing.set()
        return True

    provider = WebConfirmationProvider(lock=lock, timeout_s=10, clock=lambda: datetime.now(UTC), on_publish=on_publish, on_settle=settled.append)
    steps = tuple(ConfirmationStep(task_id=f"k-task-{i}", capability=c, tier=WRITE, input_reference="/u/report.xlsx") for i, c in ((1, DEDUPE), (2, EMPTY)))
    tasks = [SkillTask(task_id=s.task_id, session_id="k", workspace_id="k", description="", input_references=["/u/report.xlsx"], permission=WRITE) for s in steps]
    provider.expect_plan(steps)
    result: dict[str, Any] = {}
    worker = threading.Thread(target=lambda: result.setdefault("step1", provider.confirm(tasks[0], WRITE)))
    worker.start()
    assert publishing.wait(5)
    with lock:  # the answer and the close land before the worker re-takes the lock
        provider.answer(published[0].confirmation_id, "approve")
        provider.close(BY_TURN_END)
    worker.join(5)
    step2 = provider.confirm(tasks[1], WRITE)
    emit("unit_close_between_answer_and_wake", step1_allowed_after_close=result.get("step1"), step2_allowed_after_close=step2, settled=[(o.decision, o.source) for o in settled])


def api_answer_at_the_deadline_edge(trials: int, speaker: str) -> None:
    from skills.excel import ExcelDeduplicateSkill

    rows = []
    real_execute = ExcelDeduplicateSkill.execute
    real_on_publish = turns_module.TurnRunner._on_publish
    real_finalize = turns_module.TurnRunner._finalize
    events: dict[str, float] = {}

    def execute(self: Any, task: Any) -> Any:
        events.setdefault("step1_started", time.monotonic())
        return real_execute(self, task)

    def on_publish(self: Any, job: Any, plan: Any) -> bool:
        ok = real_on_publish(self, job, plan)
        job.deadline_left = 0.0  # no working time left when the plan is asked
        return ok

    def finalize(self: Any, job: Any, state: Any, *, timed_out: bool) -> None:
        real_finalize(self, job, state, timed_out=timed_out)
        events.setdefault("turn_finished", time.monotonic())

    ExcelDeduplicateSkill.execute = execute
    turns_module.TurnRunner._on_publish = on_publish
    turns_module.TurnRunner._finalize = finalize
    try:
        for trial in range(trials):
            events.clear()
            app = pr3.make_app(h.FakeLLM(h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows"))), speaker, turn_deadline_s=30.0)
            try:
                sid = pr3.new_session(app.client, speaker)
                _, original = pr3.upload(app, sid, h.xlsx())
                before = h.sha(original)
                _, started = pr3.start(app, sid, "remove duplicate rows, then remove empty rows")
                tid = started["turn_id"]
                driven = pr3.drive(app, sid, tid, "approve")
                end = time.monotonic() + 20
                while app.client.app.state.runner.running and time.monotonic() < end:  # type: ignore[attr-defined]
                    time.sleep(0.01)
                body = driven["body"]
                started_at, finished_at = events.get("step1_started"), events.get("turn_finished")
                rows.append({
                    "trial": trial,
                    "answer_http": [a.get("http") for a in driven["answers"]],
                    "outcome": body.get("outcome"),
                    "reply_source": body.get("reply_source"),
                    "delivered": [d["name"] for d in body.get("delivered_files") or []],
                    "step1_started": started_at is not None,
                    "step1_started_after_turn_finished": bool(started_at and finished_at and started_at > finished_at),
                    "late_steps": [(s["capability"], s["status"]) for s in (app.factory.runs[-1].get("steps") or [])] if app.factory.runs else None,
                    "written_in_turn_folder": pr3.written(app, sid, tid),
                    "original_unchanged": h.sha(original) == before,
                })
            finally:
                app.close()
    finally:
        ExcelDeduplicateSkill.execute = real_execute
        turns_module.TurnRunner._on_publish = real_on_publish
        turns_module.TurnRunner._finalize = real_finalize
    emit(
        "api_answer_at_the_deadline_edge",
        speaker=speaker,
        trials=rows,
        step1_started=sum(r["step1_started"] for r in rows),
        step1_started_after_turn_finished=sum(r["step1_started_after_turn_finished"] for r in rows),
        delivered=sum(bool(r["delivered"]) for r in rows),
        originals_unchanged=all(r["original_unchanged"] for r in rows),
    )


def api_shutdown_after_approval(trials: int, speaker: str) -> None:
    from skills.excel import ExcelDeduplicateSkill

    real_execute = ExcelDeduplicateSkill.execute

    def slow(self: Any, task: Any) -> Any:
        time.sleep(1.0)
        return real_execute(self, task)

    ExcelDeduplicateSkill.execute = slow
    app = pr3.make_app(h.FakeLLM(h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows"))), speaker)
    try:
        real_app = app.client.app
        with TestClient(real_app) as client:
            app.client = client
            sid = pr3.new_session(client, speaker)
            _, original = pr3.upload(app, sid, h.xlsx())
            before = h.sha(original)
            _, started = pr3.start(app, sid, "remove duplicate rows, then remove empty rows")
            tid = started["turn_id"]
            end = time.monotonic() + 30
            while time.monotonic() < end:
                body = client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
                if body["status"] == "awaiting_confirmation":
                    pr3.answer(app, sid, tid, body["pending_confirmation"]["confirmation_id"], "approve")
                    break
                time.sleep(0.01)
            time.sleep(0.3)  # step 1 is running; leaving the block shuts the server down
        app.client = TestClient(real_app, raise_server_exceptions=False)
        body = pr3.drive(app, sid, tid, None)["body"]
        end = time.monotonic() + 20
        while app.client.app.state.runner.running and time.monotonic() < end:  # type: ignore[attr-defined]
            time.sleep(0.02)
        detail = body.get("detail") or {}
        emit(
            "api_shutdown_after_approval",
            speaker=speaker,
            outcome=body.get("outcome"),
            reply_source=body.get("reply_source"),
            reply=body.get("reply"),
            confirmation=detail.get("confirmation"),
            detail_steps=[(s["capability"], s["status"]) for s in detail.get("steps", [])],
            delivered=[d["name"] for d in body.get("delivered_files") or []],
            written_in_turn_folder=pr3.written(app, sid, tid),
            original_unchanged=h.sha(original) == before,
        )
    finally:
        ExcelDeduplicateSkill.execute = real_execute
        app.close()


CASES = {f.__name__: f for f in (unit_close_between_answer_and_wake, api_answer_at_the_deadline_edge, api_shutdown_after_approval)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--speaker", choices=pr3.SPEAKERS)
    args = parser.parse_args()
    for speaker in (args.speaker,) if args.speaker else pr3.SPEAKERS:
        for name, fn in CASES.items():
            if args.only and args.only not in name:
                continue
            if name.startswith("unit_") and speaker != "template":
                continue  # no speaker involved
            fn(args.trials, speaker)


if __name__ == "__main__":
    main()
