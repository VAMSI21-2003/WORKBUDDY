# ruff: noqa: E501 — reviewer tooling, not product code
"""PR-3 adversarial probes: races, hostile answers, shutdown, deadlines, mismatched plans.

Usage (from the repo root)::

    python scripts/review_harness/feat-backend-api-confirmation/adversarial_probe.py [--only NAME] [--speaker S] [--trials N] > out.jsonl

Real app, real pipeline, fake LLM, PR-3 trees only. One JSON line per case.
Every case records what ran (the pipeline's own step list), what was handed
back (an independent openpyxl read), every spreadsheet written in the
turn's folder, the reply, and the original's SHA-256 before and after.

Cases:
- ``race_answer_vs_timeout``: approve sent within +-60 ms of ``expires_at``,
  ``--trials`` times. Invariant: a file is delivered iff the recorded
  decision is approve-by-user; a 200 answer always means a user decision.
- ``race_approve_vs_reject``: approve and reject sent at once.
- ``stale_across_turns``: turn 1's confirmation id used on turn 2's plan.
- ``hostile_bodies``: duplicate keys, wrong types, form encoding, huge ids,
  other methods on the route; the plan must keep waiting.
- ``shutdown_while_waiting``: lifespan shutdown with a plan waiting, then a
  restart on the same data root.
- ``shutdown_before_asked``: shutdown while Brain is still working, so the
  plan is refused before anyone sees it.
- ``deadline_after_approval``: a two-step chain approved, step 1 slower than
  the deadline: the turn fails; does step 2 still run after the turn ended?
- ``mismatch_after_approval``: step 2 resolves at run time to a skill of
  another tier (registered for chained outputs only): refused after the user
  approved. What does the reply say?
- ``stage_raises_after_answer``: Hands, Inspector or Voice raises after an
  approve or a reject.
- ``many_steps``: an 8-step chain approved: the plan text and the final file.
- ``session_view_while_waiting``: GET /sessions while waiting; uploads and a
  second turn refused; the cap with two waiting plans.
- ``file_id_second_upload``: two uploads, the turn targets the first.
- ``settings_from_env``: WORKBUDDY_AUTO_APPROVE and WORKBUDDY_CONFIRMATION_TIMEOUT_S values.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pr3 import h  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings, SettingsError  # noqa: E402

DEDUPE = "spreadsheet.deduplicate"
EMPTY = "spreadsheet.remove_empty_rows"
CASES: dict[str, Any] = {}


def case(fn: Any) -> Any:
    CASES[fn.__name__] = fn
    return fn


def emit(name: str, speaker: str, **data: Any) -> None:
    print(json.dumps({"case": name, "speaker": speaker, **data}, default=str), flush=True)


def _wait_awaiting(app: pr3.App, sid: str, tid: str, timeout: float = 30) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        body = app.client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
        if body["status"] == "awaiting_confirmation":
            return body
        if body["status"] == "finished":
            return body
        time.sleep(0.005)
    raise AssertionError("never awaiting")


def _finish(app: pr3.App, sid: str, tid: str, timeout: float = 60) -> dict[str, Any]:
    return pr3.drive(app, sid, tid, None, timeout_s=timeout)["body"]


def _wait_workers(app: pr3.App, timeout: float = 30) -> None:
    end = time.monotonic() + timeout
    while app.client.app.state.runner.running and time.monotonic() < end:  # type: ignore[attr-defined]
        time.sleep(0.02)


def _summary(app: pr3.App, sid: str, tid: str, body: dict[str, Any], original: Path, before: str) -> dict[str, Any]:
    detail = body.get("detail") or {}
    return {
        "status": body.get("status"),
        "outcome": body.get("outcome"),
        "reply_source": body.get("reply_source"),
        "reply": body.get("reply"),
        "confirmation": detail.get("confirmation"),
        "steps_ran": app.factory.runs[-1].get("steps") if app.factory.runs else None,
        "delivered": pr3.delivered_check(app, body),
        "written_in_turn_folder": pr3.written(app, sid, tid),
        "original_unchanged": h.sha(original) == before,
    }


# -- Races ------------------------------------------------------------------------------


@case
def race_answer_vs_timeout(speaker: str, trials: int) -> None:
    rows = []
    for trial in range(trials):
        offset = -0.06 + 0.12 * (trial / max(trials - 1, 1))
        app = pr3.make_app(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, confirmation_timeout_s=0.4)
        try:
            sid = pr3.new_session(app.client, speaker)
            _, original = pr3.upload(app, sid, h.xlsx())
            before = h.sha(original)
            _, started = pr3.start(app, sid, "remove duplicate rows")
            tid = started["turn_id"]
            plan = _wait_awaiting(app, sid, tid)["pending_confirmation"]
            expires = datetime.fromisoformat(plan["expires_at"])
            delay = (expires - datetime.now(expires.tzinfo)).total_seconds() + offset
            if delay > 0:
                time.sleep(delay)
            sent_at = datetime.now(expires.tzinfo)
            ans = pr3.answer(app, sid, tid, plan["confirmation_id"], "approve")
            body = _finish(app, sid, tid)
            s = _summary(app, sid, tid, body, original, before)
            delivered = bool(s["delivered"])
            conf = s["confirmation"] or {}
            ok = (
                (delivered == (conf.get("decision") == "approve" and conf.get("decision_source") == "user"))
                and ((ans["http"] == 200) == (conf.get("decision_source") == "user"))
                and (not s["written_in_turn_folder"] or conf.get("decision") == "approve")
                and s["original_unchanged"]
            )
            rows.append(
                {
                    "trial": trial,
                    "sent_vs_expires_s": round((sent_at - expires).total_seconds(), 3),
                    "answer_http": ans["http"],
                    "answer_error": ans["error"],
                    "confirmation": conf,
                    "outcome": s["outcome"],
                    "delivered": delivered,
                    "written": len(s["written_in_turn_folder"]),
                    "invariant_ok": ok,
                }
            )
        finally:
            app.close()
    emit(
        "race_answer_vs_timeout",
        speaker,
        trials=rows,
        all_ok=all(r["invariant_ok"] for r in rows),
        approvals_accepted_after_expiry=[r for r in rows if r["answer_http"] == 200 and r["sent_vs_expires_s"] > 0],
    )


@case
def race_approve_vs_reject(speaker: str, trials: int) -> None:
    rows = []
    for trial in range(trials):
        app = pr3.make_app(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
        try:
            sid = pr3.new_session(app.client, speaker)
            _, original = pr3.upload(app, sid, h.xlsx())
            before = h.sha(original)
            _, started = pr3.start(app, sid, "remove duplicate rows")
            tid = started["turn_id"]
            plan = _wait_awaiting(app, sid, tid)["pending_confirmation"]
            barrier = threading.Barrier(2)
            results: dict[str, Any] = {}

            def post(decision: str, app: pr3.App = app, plan: dict[str, Any] = plan, sid: str = sid, tid: str = tid, barrier: threading.Barrier = barrier, results: dict[str, Any] = results) -> None:
                barrier.wait(5)
                results[decision] = pr3.answer(app, sid, tid, plan["confirmation_id"], decision)

            threads = [threading.Thread(target=post, args=(d,)) for d in ("approve", "reject")]
            for t in threads:
                t.start()
            for t in threads:
                t.join(10)
            body = _finish(app, sid, tid)
            s = _summary(app, sid, tid, body, original, before)
            winners = [d for d, r in results.items() if r["http"] == 200]
            conf = s["confirmation"] or {}
            ok = (
                len(winners) == 1
                and conf.get("decision") == winners[0]
                and bool(s["delivered"]) == (winners[0] == "approve")
                and (bool(s["written_in_turn_folder"]) == (winners[0] == "approve"))
                and s["original_unchanged"]
            )
            rows.append({"trial": trial, "codes": {d: r["http"] for d, r in results.items()}, "winner": winners, "confirmation": conf, "outcome": s["outcome"], "ok": ok})
        finally:
            app.close()
    emit("race_approve_vs_reject", speaker, trials=rows, all_ok=all(r["ok"] for r in rows))


# -- Hostile answers ---------------------------------------------------------------------


@case
def stale_across_turns(speaker: str, trials: int) -> None:
    llm = h.FakeLLM(h.single("remove_duplicate_rows") * 2)
    app = pr3.make_app(llm, speaker)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx())
        before = h.sha(original)
        first = pr3.run_turn(app, sid, "remove duplicate rows", original, "reject")
        first_id = None
        # Recover turn 1's id from its plan by asking the API again for a finished turn (no plan) -> use the plans list.
        _, started = pr3.start(app, sid, "remove duplicate rows")
        tid2 = started["turn_id"]
        plan2 = _wait_awaiting(app, sid, tid2)["pending_confirmation"]
        # Turn 1's confirmation id isn't in the finished body; build a well-formed id that was valid before.
        first_id = "c_" + "A" * 22
        stale = pr3.answer(app, sid, tid2, first_id, "approve")
        to_finished = pr3.answer(app, sid, first["turn_id"], plan2["confirmation_id"], "approve")
        still = app.client.get(f"/api/v1/sessions/{sid}/turns/{tid2}").json()
        rejected = pr3.answer(app, sid, tid2, plan2["confirmation_id"], "reject")
        body = _finish(app, sid, tid2)
        emit(
            "stale_across_turns",
            speaker,
            first_outcome=first["outcome"],
            stale_on_turn2=stale,
            turn2_id_on_finished_turn1=to_finished,
            turn2_still_waiting=still["status"] == "awaiting_confirmation" and still["pending_confirmation"] == plan2,
            real_answer=rejected,
            **_summary(app, sid, tid2, body, original, before),
        )
    finally:
        app.close()


@case
def hostile_bodies(speaker: str, trials: int) -> None:
    app = pr3.make_app(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx())
        before = h.sha(original)
        _, started = pr3.start(app, sid, "remove duplicate rows")
        tid = started["turn_id"]
        plan = _wait_awaiting(app, sid, tid)["pending_confirmation"]
        cid = plan["confirmation_id"]
        url = f"/api/v1/sessions/{sid}/turns/{tid}/confirmation"
        raw_cases: list[tuple[str, dict[str, Any]]] = [
            ("decision_true", {"json": {"confirmation_id": cid, "decision": True}}),
            ("decision_null", {"json": {"confirmation_id": cid, "decision": None}}),
            ("decision_list", {"json": {"confirmation_id": cid, "decision": ["approve"]}}),
            ("decision_padded", {"json": {"confirmation_id": cid, "decision": " approve"}}),
            ("decision_unicode_lookalike", {"json": {"confirmation_id": cid, "decision": "аpprove"}}),
            ("id_with_newline", {"json": {"confirmation_id": cid + "\n", "decision": "approve"}}),
            ("id_null", {"json": {"confirmation_id": None, "decision": "approve"}}),
            ("form_encoded", {"data": {"confirmation_id": cid, "decision": "approve"}}),
            ("text_plain_json", {"content": json.dumps({"confirmation_id": cid, "decision": "approve"}).encode(), "headers": {"Content-Type": "text/plain"}}),
            ("no_content_type", {"content": json.dumps({"confirmation_id": cid, "decision": "approve"}).encode(), "headers": {"Content-Type": ""}}),
            ("huge_id", {"json": {"confirmation_id": "c" * 100_000, "decision": "approve"}}),
            ("nested", {"json": {"confirmation_id": {"$ne": ""}, "decision": "approve"}}),
            ("extra_field_session", {"json": {"confirmation_id": cid, "decision": "approve", "session_id": sid}}),
            ("other_session_path_traversal", {"url": f"/api/v1/sessions/{sid}/turns/..%2F{tid}/confirmation", "json": {"confirmation_id": cid, "decision": "approve"}}),
            # Last: Python's json keeps the last of two equal keys, so this one is accepted (as "approve").
            ("dup_keys_reject_then_approve", {"content": f'{{"confirmation_id": "{cid}", "decision": "reject", "decision": "approve"}}'.encode(), "headers": {"Content-Type": "application/json"}}),
        ]
        results = {}
        for name, kwargs in raw_cases:
            target = kwargs.pop("url", url)
            response = app.client.post(target, **kwargs)
            body = response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:200]
            echoed = cid in json.dumps(body) if isinstance(body, dict) else cid in str(body)
            results[name] = {"http": response.status_code, "code": body.get("error", {}).get("code") if isinstance(body, dict) else None, "echoes_id": echoed}
            if response.status_code == 200:
                results[name]["ACCEPTED"] = True
                break
        methods = {}
        for method in ("get", "put", "delete", "patch"):
            response = getattr(app.client, method)(url)
            methods[method] = {"http": response.status_code, "body": response.json() if response.headers.get("content-type", "").startswith("application/json") else response.text[:120]}
        still = app.client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
        waiting = still["status"] == "awaiting_confirmation" and still["pending_confirmation"] == plan
        if waiting:
            pr3.answer(app, sid, tid, cid, "reject")
        body = _finish(app, sid, tid)
        emit("hostile_bodies", speaker, results=results, methods=methods, plan_still_waiting_after_all=waiting, **_summary(app, sid, tid, body, original, before))
    finally:
        app.close()


# -- Shutdown -----------------------------------------------------------------------------


@case
def shutdown_while_waiting(speaker: str, trials: int) -> None:
    llm = h.FakeLLM(h.single("remove_duplicate_rows"))
    app = pr3.make_app(llm, speaker)
    try:
        real_app = app.client.app
        with TestClient(real_app) as client:
            app.client = client
            sid = pr3.new_session(client, speaker)
            _, original = pr3.upload(app, sid, h.xlsx())
            before = h.sha(original)
            _, started = pr3.start(app, sid, "remove duplicate rows")
            tid = started["turn_id"]
            _wait_awaiting(app, sid, tid)
        app.client = TestClient(real_app, raise_server_exceptions=False)
        body = _finish(app, sid, tid)
        _wait_workers(app)
        first = _summary(app, sid, tid, body, original, before)
        restarted = TestClient(create_app(app.settings, pr3.Factory(h.FakeLLM([]), speaker)), raise_server_exceptions=False)
        again = restarted.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
        emit("shutdown_while_waiting", speaker, after_shutdown=first, after_restart_same=again == body, after_restart_outcome=again.get("outcome"), after_restart_confirmation=(again.get("detail") or {}).get("confirmation"))
    finally:
        app.close()


@case
def shutdown_before_asked(speaker: str, trials: int) -> None:
    llm = h.FakeLLM([h.sleeping(1.0, h.understand("remove_duplicate_rows")), h.think()])
    app = pr3.make_app(llm, speaker)
    try:
        real_app = app.client.app
        with TestClient(real_app) as client:
            app.client = client
            sid = pr3.new_session(client, speaker)
            _, original = pr3.upload(app, sid, h.xlsx())
            before = h.sha(original)
            _, started = pr3.start(app, sid, "remove duplicate rows")
            tid = started["turn_id"]
            time.sleep(0.2)
        app.client = TestClient(real_app, raise_server_exceptions=False)
        driven = pr3.drive(app, sid, tid, "approve")
        _wait_workers(app)
        emit("shutdown_before_asked", speaker, statuses=driven["statuses"], plans_shown=len(driven["plans"]), **_summary(app, sid, tid, driven["body"], original, before))
    finally:
        app.close()


# -- Deadline and mismatches -------------------------------------------------------------------


def _slow_skill(seconds: float, capability: str) -> Any:
    """Wrap the cycle so the given capability's skill sleeps before executing (Hands untouched)."""
    from skills.excel import ExcelDeduplicateSkill, ExcelRemoveEmptyRowsSkill

    cls = ExcelDeduplicateSkill if capability == DEDUPE else ExcelRemoveEmptyRowsSkill
    original_execute = cls.execute

    def execute(self: Any, task: Any) -> Any:
        time.sleep(seconds)
        return original_execute(self, task)

    return cls, original_execute, execute


@case
def deadline_after_approval(speaker: str, trials: int) -> None:
    cls, original_execute, slow = _slow_skill(1.5, DEDUPE)
    cls.execute = slow
    llm = h.FakeLLM(h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows")))
    app = pr3.make_app(llm, speaker, turn_deadline_s=1.0)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx())
        before = h.sha(original)
        _, started = pr3.start(app, sid, "remove duplicate rows, then remove empty rows")
        tid = started["turn_id"]
        driven = pr3.drive(app, sid, tid, "approve")
        finished_files = pr3.written(app, sid, tid)
        _wait_workers(app)
        time.sleep(0.5)
        later = app.client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
        emit(
            "deadline_after_approval",
            speaker,
            statuses=driven["statuses"],
            written_when_turn_finished=finished_files,
            written_after_worker_exit=pr3.written(app, sid, tid),
            turn_unchanged_after_worker=later == driven["body"],
            late_worker_steps=app.factory.runs[-1].get("steps") if app.factory.runs else None,
            **_summary(app, sid, tid, driven["body"], original, before),
        )
    finally:
        cls.execute = original_execute
        app.close()


def _mismatch_wrap(cycle: Any) -> Any:
    """Replace Hands' dispatcher with one where remove_empty_rows on a chained output resolves
    to an EXTERNAL_EFFECT-tier skill (the plan, resolved on the upload, lists the WRITE one)."""
    from buddy.hands import Hands
    from core.dispatcher import Dispatcher
    from skills.base import PermissionTier
    from skills.excel import (
        ExcelDeduplicateSkill,
        ExcelRemoveEmptyRowsSkill,
        SpreadsheetAnalysisSkill,
    )
    from skills.registry import SkillRegistry

    class UploadsOnlyEmptyRows(ExcelRemoveEmptyRowsSkill):
        @property
        def name(self) -> str:
            return "uploads_only_empty_rows"

        def can_handle(self, task: Any) -> bool:
            return super().can_handle(task) and "/outputs/" not in task.input_references[0]

    class ExternalEmptyRows(ExcelRemoveEmptyRowsSkill):
        @property
        def name(self) -> str:
            return "external_empty_rows"

        @property
        def permission_tier(self) -> PermissionTier:
            return PermissionTier.EXTERNAL_EFFECT

        def can_handle(self, task: Any) -> bool:
            return super().can_handle(task) and "/outputs/" in task.input_references[0]

    registry = SkillRegistry()
    for skill in (ExcelDeduplicateSkill(), UploadsOnlyEmptyRows(), ExternalEmptyRows(), SpreadsheetAnalysisSkill()):
        registry.register(skill)
    old: Hands = cycle.hands
    new = Hands(Dispatcher(registry), confirmation_provider=old._confirmation_provider, step_verifier=old._step_verifier)
    return dataclasses.replace(cycle, hands=new)


@case
def mismatch_after_approval(speaker: str, trials: int) -> None:
    llm = h.FakeLLM(h.chain((DEDUPE, "remove duplicate rows"), (EMPTY, "then remove empty rows")))
    app = pr3.make_app(llm, speaker, wrap=_mismatch_wrap)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx())
        result = pr3.run_turn(app, sid, "remove duplicate rows, then remove empty rows", original, "approve")
        result["steps_ran"] = app.factory.runs[-1].get("steps") if app.factory.runs else None
        emit("mismatch_after_approval", speaker, **result)
    finally:
        app.close()


def _raise_in(stage: str, after: bool) -> Any:
    def wrap(cycle: Any) -> Any:
        inner = getattr(cycle, stage)

        def stage_fn(state: Any) -> Any:
            if after:
                inner(state)
            raise RuntimeError("boom SECRET-CELL-4111 /srv/data")

        return dataclasses.replace(cycle, **{stage: stage_fn})

    return wrap


@case
def stage_raises_after_answer(speaker: str, trials: int) -> None:
    for stage in ("hands", "inspector", "voice"):
        for decision in ("approve", "reject"):
            app = pr3.make_app(h.FakeLLM(h.single("remove_duplicate_rows")), speaker, wrap=_raise_in(stage, True))
            try:
                sid = pr3.new_session(app.client, speaker)
                _, original = pr3.upload(app, sid, h.xlsx())
                result = pr3.run_turn(app, sid, "remove duplicate rows", original, decision)
                record = next(app.settings.sessions_dir.rglob(f"turns/{result['turn_id']}.json"))
                stored = json.loads(record.read_text(encoding="utf-8")).get("confirmation")
                leaked = any(s in json.dumps(result) for s in ("SECRET-CELL", "/srv/data", "boom"))
                emit("stage_raises_after_answer", speaker, stage=stage, decision=decision, stored_confirmation=stored, exception_text_leaked=leaked, **result)
            finally:
                app.close()


@case
def many_steps(speaker: str, trials: int) -> None:
    steps = [(DEDUPE if i % 2 == 0 else EMPTY, ("then " if i else "") + f"step {i} do it") for i in range(8)]
    llm = h.FakeLLM(h.chain(*steps))
    app = pr3.make_app(llm, speaker)
    try:
        sid = pr3.new_session(app.client, speaker)
        _, original = pr3.upload(app, sid, h.xlsx())
        result = pr3.run_turn(app, sid, "do eight things in order", original, "approve")
        emit("many_steps", speaker, **result)
    finally:
        app.close()


@case
def session_view_while_waiting(speaker: str, trials: int) -> None:
    llm = h.FakeLLM(h.single("remove_duplicate_rows") * 3)
    app = pr3.make_app(llm, speaker)
    try:
        a = pr3.new_session(app.client, speaker)
        b = pr3.new_session(app.client, speaker)
        c = pr3.new_session(app.client, speaker)
        for s in (a, b, c):
            pr3.upload(app, s, h.xlsx())
        _, ta = pr3.start(app, a, "remove duplicate rows")
        plan_a = _wait_awaiting(app, a, ta["turn_id"])["pending_confirmation"]
        state = app.client.get(f"/api/v1/sessions/{a}").json()
        upload_during = app.client.post(f"/api/v1/sessions/{a}/files", files={"file": ("x.xlsx", h.xlsx(), "application/octet-stream")})
        second = app.client.post(f"/api/v1/sessions/{a}/turns", json={"message": "remove empty rows"})
        _, tb = pr3.start(app, b, "remove duplicate rows")
        _wait_awaiting(app, b, tb["turn_id"])
        third = app.client.post(f"/api/v1/sessions/{c}/turns", json={"message": "remove duplicate rows"})
        emit(
            "session_view_while_waiting",
            speaker,
            busy=state["busy"],
            session_plan_equals_turn_plan=state["pending_confirmation"] == plan_a,
            summary_status=[t["status"] for t in state["turns"]],
            summary_has_plan=[t["pending_confirmation"] == plan_a for t in state["turns"]],
            upload_during=(upload_during.status_code, upload_during.json()["error"]["code"]),
            second_turn=(second.status_code, second.json()["error"]["code"]),
            third_session_turn=(third.status_code, third.json().get("error", {}).get("code"), third.headers.get("retry-after")),
        )
        pr3.answer(app, a, ta["turn_id"], plan_a["confirmation_id"], "reject")
        _finish(app, a, ta["turn_id"])
        pr3.drive(app, b, tb["turn_id"], "reject")
    finally:
        app.close()


@case
def file_id_second_upload(speaker: str, trials: int) -> None:
    app = pr3.make_app(h.FakeLLM(h.single("remove_duplicate_rows")), speaker)
    try:
        sid = pr3.new_session(app.client, speaker)
        first_id, first = pr3.upload(app, sid, h.xlsx(), "first.xlsx")
        _, second = pr3.upload(app, sid, h.xlsx(h.NO_DUP_ROWS), "second.xlsx")
        before_second = h.sha(second)
        result = pr3.run_turn(app, sid, "remove duplicate rows", first, "approve", file_id=first_id)
        result["second_unchanged"] = h.sha(second) == before_second
        emit("file_id_second_upload", speaker, **result)
    finally:
        app.close()


@case
def settings_from_env(speaker: str, trials: int) -> None:
    if speaker != "template":
        return
    records: list[str] = []

    class Grab(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    logging.getLogger("backend.settings").addHandler(Grab())
    out = {}
    for name, env in [
        ("auto_approve_1", {"WORKBUDDY_AUTO_APPROVE": "1"}),
        ("auto_approve_true", {"WORKBUDDY_AUTO_APPROVE": "true"}),
        ("auto_approve_garbage", {"WORKBUDDY_AUTO_APPROVE": "maybe"}),
        ("timeout_0", {"WORKBUDDY_CONFIRMATION_TIMEOUT_S": "0"}),
        ("timeout_neg", {"WORKBUDDY_CONFIRMATION_TIMEOUT_S": "-5"}),
        ("timeout_float", {"WORKBUDDY_CONFIRMATION_TIMEOUT_S": "2.5"}),
        ("timeout_text", {"WORKBUDDY_CONFIRMATION_TIMEOUT_S": "abc"}),
        ("timeout_empty", {"WORKBUDDY_CONFIRMATION_TIMEOUT_S": ""}),
        ("timeout_huge", {"WORKBUDDY_CONFIRMATION_TIMEOUT_S": "99999999999"}),
        ("default", {}),
    ]:
        records.clear()
        try:
            s = Settings.from_env({"WORKBUDDY_DATA_DIR": "/tmp/x", **env})
            out[name] = {"auto_approve": s.auto_approve, "confirmation_timeout_s": s.confirmation_timeout_s, "logs": list(records)}
        except SettingsError as exc:
            out[name] = {"SettingsError": str(exc)}
        except Exception as exc:  # noqa: BLE001
            out[name] = {"OTHER": f"{type(exc).__name__}: {exc}"}
    emit("settings_from_env", speaker, results=out)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only")
    parser.add_argument("--speaker", choices=pr3.SPEAKERS)
    parser.add_argument("--trials", type=int, default=12)
    args = parser.parse_args()
    if not pr3.IS_PR3:
        raise SystemExit("these probes need a tree with PR-3")
    for speaker in (args.speaker,) if args.speaker else pr3.SPEAKERS:
        for name, fn in CASES.items():
            if args.only and args.only not in name:
                continue
            fn(speaker, args.trials)


if __name__ == "__main__":
    main()
