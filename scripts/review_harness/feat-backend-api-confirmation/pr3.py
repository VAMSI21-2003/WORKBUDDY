# ruff: noqa: E501 — reviewer tooling, not product code
"""Shared pieces for the PR-3 (plan confirmation) review probes. Not part of the test suite.

Everything runs the real app (``backend.main.create_app``) and the real
pipeline (``buddy.engine_factory.build_engine``: real skills, Hands,
Inspector, verifiers, Voice) with only the LLM faked. The fake LLM, the
fixtures and the independent openpyxl check are imported from
``scripts/review_harness/feat-backend-api-core/harness.py`` (not edited).

``--tree DIR`` (handled by ``tree_root()`` before anything imports the
product code) runs the same probe against another checkout, for example a
``git worktree`` of ``origin/main``. On a tree without PR-3 there is no
confirmation endpoint: "approve" maps to ``auto_approve=True`` and
"reject"/"timeout" to ``auto_approve=False`` (PR-2's decline).
"""

from __future__ import annotations

import dataclasses
import hashlib
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "feat-backend-api-core"


def tree_root() -> Path:
    """The checkout to probe: ``--tree DIR`` from argv, else this repo. Call before product imports."""
    if "--tree" in sys.argv:
        index = sys.argv.index("--tree")
        root = Path(sys.argv[index + 1]).resolve()
        del sys.argv[index : index + 2]
    else:
        root = HERE.parents[2]
    for path in (str(CORE), str(HERE), str(root)):
        if path in sys.path:
            sys.path.remove(path)
        sys.path.insert(0, path)
    return root


ROOT = tree_root()

import harness as h  # noqa: E402  (feat-backend-api-core's shared fake LLM and file check)
from fastapi.testclient import TestClient  # noqa: E402

import backend.confirmation as confirmation_module  # noqa: E402
from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402
from buddy.engine_factory import build_engine  # noqa: E402
from buddy.voice import PersonalityVoice, TemplateSpeaker  # noqa: E402
from core.engine import Engine  # noqa: E402

IS_PR3 = hasattr(confirmation_module, "WebConfirmationProvider")
SPEAKERS = ("template", "friend", "focus")


class Factory:
    """``engine_factory``: the real pipeline on a fake LLM; records each run and each provider."""

    def __init__(self, llm: h.FakeLLM, speaker: str, wrap: Callable[[Any], Any] | None = None) -> None:
        self.llm = llm
        self.speaker = speaker
        self.wrap = wrap
        self.providers: list[Any] = []
        self.runs: list[dict[str, Any]] = []

    def __call__(self, confirmation_provider: Any) -> Any:
        self.providers.append(confirmation_provider)
        speaker = TemplateSpeaker() if self.speaker == "template" else PersonalityVoice(self.llm)
        engine = build_engine(
            llm_provider=self.llm, speaker=speaker, confirmation_provider=confirmation_provider
        )
        if self.wrap is not None:
            engine = Engine(self.wrap(engine._cycle))
        return _Spy(engine, self)


class _Spy:
    def __init__(self, engine: Engine, factory: Factory) -> None:
        self._engine = engine
        self._factory = factory

    def run(self, state: Any) -> Any:
        try:
            result = self._engine.run(state)
        except BaseException as exc:
            self._factory.runs.append({"raised": type(exc).__name__, "reply": state.reply})
            raise
        execution = result.execution_result
        self._factory.runs.append(
            {
                "reply": result.reply,
                "failure": dataclasses.asdict(result.failure) if result.failure else None,
                "outputs": [Path(o).name for o in execution.outputs] if execution else None,
                "steps": [
                    {
                        "capability": s.capability,
                        "status": s.status,
                        "tier": s.permission_tier,
                        "input": Path(s.input_reference).name if s.input_reference else None,
                        "outputs": [Path(o).name for o in s.outputs],
                        "recheck": s.recheck,
                    }
                    for s in (execution.steps if execution else ())
                ],
                "verified": result.verification_result.passed if result.verification_result else None,
            }
        )
        return result


@dataclasses.dataclass
class App:
    """One app on a temporary data root."""

    client: TestClient
    settings: Settings
    factory: Factory
    tmp: tempfile.TemporaryDirectory[str]

    def close(self) -> None:
        self.tmp.cleanup()


def make_app(
    llm: h.FakeLLM,
    speaker: str,
    *,
    wrap: Callable[[Any], Any] | None = None,
    base_auto_approve: bool = True,
    **settings: Any,
) -> App:
    tmp = tempfile.TemporaryDirectory()
    factory = Factory(llm, speaker, wrap)
    if not IS_PR3:
        settings.pop("confirmation_timeout_s", None)
        settings.setdefault("auto_approve", base_auto_approve)
    s = Settings(data_dir=Path(tmp.name) / "data", **settings)
    client = TestClient(create_app(s, factory), raise_server_exceptions=False)
    return App(client, s, factory, tmp)


def new_session(client: TestClient, speaker: str) -> str:
    mode = "focus" if speaker == "focus" else "friend"
    response = client.post("/api/v1/sessions", json={"mode": mode})
    assert response.status_code == 201, response.text
    return str(response.json()["session_id"])


def upload(app: App, sid: str, data: bytes, name: str = "report.xlsx") -> tuple[str, Path]:
    response = app.client.post(
        f"/api/v1/sessions/{sid}/files", files={"file": (name, data, "application/octet-stream")}
    )
    assert response.status_code == 201, response.text
    body = response.json()
    path = next(p for p in app.settings.uploads_dir.rglob("*") if p.is_file() and p.parent.name == body["file_id"])
    return str(body["file_id"]), path


Decision = str | None | Callable[[App, str, str, dict[str, Any]], Any]


def drive(
    app: App,
    sid: str,
    tid: str,
    decision: Decision = "approve",
    *,
    answer_after_s: float = 0.0,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Poll a turn to the end, answering each plan it shows with ``decision``.

    ``decision`` is "approve", "reject", ``None`` (never answer) or a callable
    ``(app, sid, tid, plan) -> anything`` that answers (or not) itself.
    Returns the statuses seen (in order, de-duplicated), every plan shown,
    every answer sent with its HTTP status and body, and the finished body.
    """
    end = time.monotonic() + timeout_s
    statuses: list[str] = []
    plans: list[dict[str, Any]] = []
    answers: list[dict[str, Any]] = []
    while True:
        response = app.client.get(f"/api/v1/sessions/{sid}/turns/{tid}")
        body = response.json()
        status = body.get("status", f"HTTP {response.status_code}")
        if not statuses or statuses[-1] != status:
            statuses.append(status)
        if status == "finished":
            return {"statuses": statuses, "plans": plans, "answers": answers, "body": body}
        if status == "awaiting_confirmation":
            plan = body["pending_confirmation"]
            if all(p["confirmation_id"] != plan["confirmation_id"] for p in plans):
                plans.append(plan)
                if answer_after_s:
                    time.sleep(answer_after_s)
                if callable(decision):
                    answers.append({"custom": decision(app, sid, tid, plan)})
                elif decision is not None:
                    answers.append(answer(app, sid, tid, plan["confirmation_id"], decision))
        if time.monotonic() > end:
            return {"statuses": statuses, "plans": plans, "answers": answers, "body": body, "TIMEOUT": True}
        time.sleep(0.01)


def answer(app: App, sid: str, tid: str, confirmation_id: str, decision: str) -> dict[str, Any]:
    response = app.client.post(
        f"/api/v1/sessions/{sid}/turns/{tid}/confirmation",
        json={"confirmation_id": confirmation_id, "decision": decision},
    )
    body = response.json()
    return {
        "decision": decision,
        "http": response.status_code,
        "status_after": body.get("status"),
        "pending_after": body.get("pending_confirmation"),
        "error": (body.get("error") or {}).get("code") if isinstance(body, dict) else None,
    }


def session_key(sid: str) -> str:
    return hashlib.sha256(sid.encode("utf-8")).hexdigest()[:32]


def written(app: App, sid: str, tid: str) -> list[str]:
    """Every spreadsheet any step wrote into the turn's folder (whether handed back or not)."""
    folder = app.settings.outputs_dir / session_key(sid) / tid
    return sorted(p.name for p in folder.glob("*.xlsx")) if folder.is_dir() else []


def delivered_check(app: App, body: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for d in body.get("delivered_files") or []:
        r = app.client.get(d["download_url"])
        entry: dict[str, Any] = {"name": d["name"], "http": r.status_code}
        if r.status_code == 200:
            entry["sha_matches_listing"] = h.sha(r.content) == d["sha256"]
            entry["independent"] = h.describe(h.read_rows(r.content))
        out.append(entry)
    return out


def start(app: App, sid: str, message: str, **extra: Any) -> tuple[int, dict[str, Any]]:
    response = app.client.post(f"/api/v1/sessions/{sid}/turns", json={"message": message, **extra})
    return response.status_code, response.json()


def run_turn(
    app: App,
    sid: str,
    message: str,
    original: Path | None,
    decision: Decision = "approve",
    *,
    answer_after_s: float = 0.0,
    timeout_s: float = 60.0,
    **extra: Any,
) -> dict[str, Any]:
    """Start a turn, drive it, and report everything the review needs about it."""
    before = h.sha(original) if original else None
    code, started = start(app, sid, message, **extra)
    if code != 202:
        return {"start_http": code, "start_body": started}
    tid = started["turn_id"]
    driven = drive(app, sid, tid, decision, answer_after_s=answer_after_s, timeout_s=timeout_s)
    body = driven["body"]
    after = h.sha(original) if original else None
    detail = body.get("detail") or {}
    return {
        "turn_id": tid,
        "statuses": driven["statuses"],
        "plans": [
            {"steps": [(s["capability"], s["prompt"]) for s in p["steps"]], "plan_note": p["plan_note"]}
            for p in driven["plans"]
        ],
        "answers": driven["answers"],
        "outcome": body.get("outcome"),
        "reply_source": body.get("reply_source"),
        "reply": body.get("reply"),
        "confirmation": detail.get("confirmation"),
        "detail_steps": [
            f"{s['capability']}[{s['status']}{',recheck' if s.get('recheck') else ''}] {s['input_name']}->{s['output_names']}"
            for s in detail.get("steps", [])
        ],
        "delivered": delivered_check(app, body),
        "written_in_turn_folder": written(app, sid, tid),
        "original_sha_before": before,
        "original_sha_after": after,
        "original_unchanged": before == after,
        "pipeline_reply_equal": (app.factory.runs[-1].get("reply") == body.get("reply")) if app.factory.runs else None,
        "TIMEOUT": driven.get("TIMEOUT", False),
    }
