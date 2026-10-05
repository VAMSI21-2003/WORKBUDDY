"""The plan approval through the API, on the real pipeline with a fake LLM.

Design Sections 1.5, 4.3 and 7.3 item 5 ("approve delivers a file; reject
delivers none, the reply says the step wasn't done, and the original is
unchanged. Also timeout, stale id, wrong session"), "Decisions
(2026-09-30)" item 2, part 2 asks 1 and 7, and "Decisions (2026-10-01)"
items 4 and 5. Every turn runs on a real worker thread, as in production.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from backend import turns as turns_module
from backend.confirmation import PLAN_NOTE_CHAIN, WebConfirmationProvider
from backend.main import create_app
from backend.settings import Settings
from buddy.context import Context
from buddy.hands import ConfirmationStep
from buddy.models import DECLINED_STATUS, SKIPPED_STATUS
from buddy.state import BuddyState
from buddy.voice import _COMPLETION_CLAIM
from buddy.voice_catalog import FALLBACK_PIPELINE_ERROR
from skills.base import PermissionTier, SkillTask
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    SPEAKERS,
    BlockingFactory,
    PipelineFactory,
    QueuedProvider,
    chain,
    single,
)
from tests.backend.helpers import (
    new_session,
    start_turn,
    upload,
    wait_finished,
    xlsx_bytes,
)

DEDUPE_PLAN = [
    {"capability": "spreadsheet.deduplicate", "prompt": "Remove duplicate rows from report.xlsx."}
]
CHAIN_MESSAGE = "remove duplicate rows, then remove empty rows"


def _app(settings: Settings, factory: Any) -> TestClient:
    return TestClient(create_app(settings, factory))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _upload_path(settings: Settings, file_id: str) -> Path:
    (path,) = [p for p in settings.uploads_dir.rglob("*") if p.parent.name == file_id]
    return path


def _mode(speaker: str) -> str:
    return "focus" if speaker == "focus" else "friend"


def _session_with_upload(client: TestClient, mode: str = "friend") -> tuple[str, str]:
    session_id = new_session(client, mode)
    response = upload(client, session_id, xlsx_bytes())
    assert response.status_code == 201, response.text
    return session_id, str(response.json()["file_id"])


def _start(client: TestClient, session_id: str, message: str) -> str:
    response = start_turn(client, session_id, message)
    assert response.status_code == 202, response.text
    return str(response.json()["turn_id"])


def _poll(client: TestClient, session_id: str, turn_id: str) -> dict[str, Any]:
    response = client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


def _wait_awaiting(
    client: TestClient, session_id: str, turn_id: str, timeout_s: float = 30
) -> dict[str, Any]:
    """Poll until the turn's plan is waiting for an answer; fail if it finishes first."""
    deadline = time.monotonic() + timeout_s
    while True:
        body = _poll(client, session_id, turn_id)
        if body["status"] == "awaiting_confirmation":
            return body
        assert body["status"] == "running", body
        assert time.monotonic() < deadline, "the plan was never shown"
        time.sleep(0.02)


def _answer(
    client: TestClient, session_id: str, turn_id: str, body: Any, *, raw: bytes | None = None
) -> Any:
    url = f"/api/v1/sessions/{session_id}/turns/{turn_id}/confirmation"
    if raw is not None:
        return client.post(url, content=raw, headers={"Content-Type": "application/json"})
    return client.post(url, json=body)


def _written(settings: Settings) -> list[Path]:
    """Every spreadsheet any step wrote, under the outputs root."""
    return sorted(settings.outputs_dir.rglob("*.xlsx"))


def _turn_record(settings: Settings, turn_id: str) -> dict[str, Any]:
    (path,) = settings.sessions_dir.rglob(f"turns/{turn_id}.json")
    return dict(json.loads(path.read_text(encoding="utf-8")))


def _wait_idle(client: TestClient, session_id: str) -> None:
    deadline = time.monotonic() + 10
    while client.get(f"/api/v1/sessions/{session_id}").json()["busy"]:
        assert time.monotonic() < deadline
        time.sleep(0.02)


def _wait_workers_gone(client: TestClient) -> None:
    deadline = time.monotonic() + 10
    while client.app.state.runner.running:  # type: ignore[attr-defined]
        assert time.monotonic() < deadline
        time.sleep(0.02)


# -- Approve -----------------------------------------------------------------------------------


def test_the_plan_is_shown_before_any_file_changes_and_approving_it_delivers(
    settings: Settings,
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client)
    original = _upload_path(settings, file_id)
    before = _sha(original)
    turn_id = _start(client, session_id, "remove duplicate rows")

    waiting = _wait_awaiting(client, session_id, turn_id)

    plan = waiting["pending_confirmation"]
    assert plan["steps"] == DEDUPE_PLAN
    assert plan["plan_note"] is None
    expires = datetime.fromisoformat(plan["expires_at"])
    now = datetime.now(UTC)
    assert now + timedelta(seconds=100) < expires <= now + timedelta(seconds=120)
    assert waiting["reply"] is None and waiting["outcome"] is None
    assert waiting["reply_source"] is None and waiting["delivered_files"] == []
    assert waiting["detail"] is None
    assert _written(settings) == []  # no file-changing step has run
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["busy"] is True
    assert state["pending_confirmation"] == plan
    (summary,) = state["turns"]
    assert summary["status"] == "awaiting_confirmation"
    assert summary["pending_confirmation"] == plan
    on_disk = _turn_record(settings, turn_id)  # a waiting plan is never written down
    assert on_disk["status"] == "running" and on_disk["confirmation"] is None

    answer = _answer(
        client,
        session_id,
        turn_id,
        {"confirmation_id": plan["confirmation_id"], "decision": "approve"},
    )

    assert answer.status_code == 200, answer.text
    assert answer.json()["status"] == "running"
    assert answer.json()["pending_confirmation"] is None
    assert answer.json()["reply"] is None
    assert _poll(client, session_id, turn_id)["pending_confirmation"] is None
    assert client.get(f"/api/v1/sessions/{session_id}").json()["pending_confirmation"] is None
    turn = wait_finished(client, session_id, turn_id, decision=None)
    assert turn["outcome"] == "delivered"
    assert turn["reply"] == factory.runs[0].reply
    assert turn["detail"]["confirmation"] == {"decision": "approve", "decision_source": "user"}
    assert _turn_record(settings, turn_id)["confirmation"] == {
        "decision": "approve",
        "decision_source": "user",
    }
    assert _sha(original) == before


def test_a_two_step_chain_asks_once_and_the_plan_is_what_runs(settings: Settings) -> None:
    """Part 2 ask 1, and "Decisions (2026-10-01)" item 4: the wording matches the wiring."""
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client)
    plans: list[dict[str, Any]] = []

    turn = wait_finished(
        client, session_id, _start(client, session_id, CHAIN_MESSAGE), plans=plans
    )

    (plan,) = plans  # asked exactly once for both steps
    assert plan["steps"] == [
        *DEDUPE_PLAN,
        {
            "capability": "spreadsheet.remove_empty_rows",
            "prompt": "Then remove empty rows from the result of step 1.",
        },
    ]
    assert "report.xlsx" not in plan["steps"][1]["prompt"]
    assert plan["plan_note"] == PLAN_NOTE_CHAIN
    (run,) = factory.runs
    ran = [s for s in run.steps if s.permission_tier in ("write", "external_effect")]
    assert [s.capability for s in ran] == [step["capability"] for step in plan["steps"]]
    assert [s.status for s in ran] == ["success", "success"]
    # Step 1 reads the upload; step 2 reads step 1's result, as the plan says.
    assert ran[0].input_reference == str(_upload_path(settings, file_id))
    assert ran[1].input_reference == ran[0].outputs[0]
    assert turn["outcome"] == "delivered" and len(turn["delivered_files"]) == 1
    assert turn["detail"]["confirmation"] == {"decision": "approve", "decision_source": "user"}
    detail_writes = [
        s["capability"] for s in turn["detail"]["steps"] if s["permission_tier"] == "write"
    ]
    assert detail_writes == [step["capability"] for step in plan["steps"]]


# -- Reject and timeout ---------------------------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_rejected_plan_runs_no_step_and_the_pipelines_reply_says_so(
    settings: Settings, speaker: str
) -> None:
    """Design 1.5: the decline path, through the API, in every speaker."""
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker=speaker)
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client, _mode(speaker))
    before = _sha(_upload_path(settings, file_id))
    plans: list[dict[str, Any]] = []

    turn = wait_finished(
        client,
        session_id,
        _start(client, session_id, "remove duplicate rows"),
        decision="reject",
        plans=plans,
    )

    assert [p["steps"] for p in plans] == [DEDUPE_PLAN]
    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert turn["reply_source"] == "pipeline"
    assert turn["reply"] == factory.runs[0].reply
    reply = turn["reply"]
    assert "The deduplicate step: wasn't confirmed, so it didn't run." in reply
    assert "no file was handed back" in reply
    assert _COMPLETION_CLAIM.search(reply) is None, reply
    assert "problem" not in reply  # the user's "no" isn't a failure
    assert _written(settings) == []  # no step wrote anything
    (declined,) = [s for s in factory.runs[0].steps if s.permission_tier == "write"]
    assert declined.status == DECLINED_STATUS and declined.outputs == ()
    assert turn["detail"]["confirmation"] == {"decision": "reject", "decision_source": "user"}
    assert _sha(_upload_path(settings, file_id)) == before


def test_a_rejected_chain_runs_neither_step(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client)
    before = _sha(_upload_path(settings, file_id))

    turn = wait_finished(
        client, session_id, _start(client, session_id, CHAIN_MESSAGE), decision="reject"
    )

    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    statuses = [s.status for s in factory.runs[0].steps if s.capability != "spreadsheet.analyze"]
    assert statuses == [DECLINED_STATUS, SKIPPED_STATUS]
    assert "remove empty rows never ran" in turn["reply"]
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None
    assert _written(settings) == []
    assert _sha(_upload_path(settings, file_id)) == before


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_no_answer_before_the_plan_expires_is_a_rejection(tmp_path: Path, speaker: str) -> None:
    settings = Settings(data_dir=tmp_path / "data", confirmation_timeout_s=0.5)
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker=speaker)
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client, _mode(speaker))
    before = _sha(_upload_path(settings, file_id))
    turn_id = _start(client, session_id, "remove duplicate rows")
    plans: list[dict[str, Any]] = []

    turn = wait_finished(client, session_id, turn_id, decision=None, plans=plans)

    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert turn["reply"] == factory.runs[0].reply
    assert "wasn't confirmed, so it didn't run" in turn["reply"]
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None
    assert turn["detail"]["confirmation"] == {"decision": "reject", "decision_source": "timeout"}
    assert _written(settings) == []
    late = _answer(
        client,
        session_id,
        turn_id,
        {"confirmation_id": plans[0]["confirmation_id"], "decision": "approve"},
    )
    assert late.status_code == 409
    assert late.json()["error"]["code"] == "no_pending_confirmation"
    assert _sha(_upload_path(settings, file_id)) == before


def _unclear() -> list[str]:
    understand = json.dumps({"intent": "unclear", "confidence": 0.2})
    think = json.dumps(
        {
            "objective": "",
            "summary": "Too vague.",
            "clarification_required": True,
            "confidence": 0.2,
        }
    )
    return [understand, think]


def test_a_turn_without_a_file_changing_step_never_waits_for_an_answer(
    settings: Settings,
) -> None:
    """Only the read-only analysis runs: no plan, no ``awaiting_confirmation``."""
    factory = PipelineFactory(QueuedProvider(_unclear()))
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "hi, what can you do?")

    statuses = set()
    deadline = time.monotonic() + 30
    while (body := _poll(client, session_id, turn_id))["status"] != "finished":
        statuses.add(body["status"])
        assert time.monotonic() < deadline
        time.sleep(0.01)

    assert statuses <= {"running"}
    assert body["outcome"] == "not_delivered"
    assert body["detail"]["confirmation"] is None
    (provider,) = factory.confirmation_providers
    assert provider.outcome is None and provider.pending is None  # type: ignore[attr-defined]
    analyses = [s for s in factory.runs[0].steps if s.capability == "spreadsheet.analyze"]
    assert analyses  # the analysis ran, without asking


# -- Refused answers -------------------------------------------------------------------------------


@pytest.fixture
def waiting(settings: Settings) -> tuple[TestClient, str, str, dict[str, Any], Settings]:
    """A session whose first turn's plan is waiting for an answer."""
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    plan = _wait_awaiting(client, session_id, turn_id)["pending_confirmation"]
    return client, session_id, turn_id, plan, settings


def test_an_unknown_session_or_turn_is_404(waiting: Any) -> None:
    client, session_id, turn_id, plan, _ = waiting
    body = {"confirmation_id": plan["confirmation_id"], "decision": "approve"}

    no_session = _answer(client, "s_doesnotexist", turn_id, body)
    assert no_session.status_code == 404
    assert no_session.json()["error"]["code"] == "session_not_found"
    for bad_turn in ("t9999", "x" * 65, "t0001.."):
        no_turn = _answer(client, session_id, bad_turn, body)
        assert no_turn.status_code == 404, bad_turn
        assert no_turn.json()["error"]["code"] in ("turn_not_found", "not_found")
    assert _poll(client, session_id, turn_id)["pending_confirmation"] == plan


def test_a_stale_id_is_409_and_the_plan_keeps_waiting(waiting: Any) -> None:
    client, session_id, turn_id, plan, _ = waiting

    for stale in ("c_notthisone", plan["confirmation_id"] + "x"):
        refused = _answer(
            client, session_id, turn_id, {"confirmation_id": stale, "decision": "approve"}
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "stale_confirmation"
        assert refused.json()["error"]["message"] == (
            "That answer is for a plan that isn't the one waiting."
        )
    assert _poll(client, session_id, turn_id)["pending_confirmation"] == plan
    turn = wait_finished(client, session_id, turn_id)  # the real id still works
    assert turn["outcome"] == "delivered"


@pytest.mark.parametrize(
    "body",
    [
        {"decision": "approve"},
        {"confirmation_id": "ID"},
        {"confirmation_id": "ID", "decision": "yes"},
        {"confirmation_id": "ID", "decision": "APPROVE"},
        {"confirmation_id": "ID", "decision": "approve", "steps": []},
        {"confirmation_id": "../x", "decision": "approve"},
        {"confirmation_id": "", "decision": "approve"},
        {"confirmation_id": "c" * 65, "decision": "approve"},
        {"confirmation_id": 5, "decision": "approve"},
        ["ID", "approve"],
    ],
)
def test_an_invalid_answer_is_422_and_the_plan_keeps_waiting(waiting: Any, body: Any) -> None:
    client, session_id, turn_id, plan, _ = waiting
    text = json.dumps(body).replace('"ID"', json.dumps(plan["confirmation_id"]))

    refused = _answer(client, session_id, turn_id, None, raw=text.encode())

    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "invalid_request"
    assert _poll(client, session_id, turn_id)["pending_confirmation"] == plan


def test_a_body_that_isnt_json_is_422(waiting: Any) -> None:
    client, session_id, turn_id, plan, _ = waiting

    refused = _answer(client, session_id, turn_id, None, raw=b"approve please")

    assert refused.status_code == 422
    assert _poll(client, session_id, turn_id)["pending_confirmation"] == plan


def test_a_second_answer_is_409_and_the_first_stands(waiting: Any) -> None:
    client, session_id, turn_id, plan, settings = waiting
    first = _answer(
        client,
        session_id,
        turn_id,
        {"confirmation_id": plan["confirmation_id"], "decision": "reject"},
    )
    assert first.status_code == 200

    again = _answer(
        client,
        session_id,
        turn_id,
        {"confirmation_id": plan["confirmation_id"], "decision": "approve"},
    )

    assert again.status_code == 409
    assert again.json()["error"]["code"] == "no_pending_confirmation"
    turn = wait_finished(client, session_id, turn_id, decision=None)
    assert turn["outcome"] == "not_delivered"
    assert turn["detail"]["confirmation"] == {"decision": "reject", "decision_source": "user"}
    assert _written(settings) == []
    after = _answer(
        client,
        session_id,
        turn_id,
        {"confirmation_id": plan["confirmation_id"], "decision": "approve"},
    )
    assert after.status_code == 409  # nor once the turn has finished
    assert after.json()["error"]["code"] == "no_pending_confirmation"


def test_a_turn_with_no_plan_waiting_is_409(settings: Settings) -> None:
    factory = BlockingFactory()
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    assert factory.started.wait(5)

    refused = _answer(
        client, session_id, turn_id, {"confirmation_id": "c_anything", "decision": "approve"}
    )

    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "no_pending_confirmation"
    assert refused.json()["error"]["message"] == "This turn has no plan waiting for an answer."
    factory.release.set()
    wait_finished(client, session_id, turn_id)


def test_another_sessions_answer_never_decides_this_plan(settings: Settings) -> None:
    """Design 7.3 item 5, "wrong session": both sessions' first turns are ``t0001``."""
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    client = _app(settings, PipelineFactory(provider))
    session_a, _ = _session_with_upload(client)
    session_b, _ = _session_with_upload(client)
    turn_a = _start(client, session_a, "remove duplicate rows")
    plan_a = _wait_awaiting(client, session_a, turn_a)["pending_confirmation"]
    turn_b = _start(client, session_b, "remove duplicate rows")
    plan_b = _wait_awaiting(client, session_b, turn_b)["pending_confirmation"]
    assert turn_a == turn_b == "t0001"

    crossed = _answer(
        client,
        session_b,
        turn_b,
        {"confirmation_id": plan_a["confirmation_id"], "decision": "approve"},
    )

    assert crossed.status_code == 409
    assert crossed.json()["error"]["code"] == "stale_confirmation"
    assert _poll(client, session_a, turn_a)["pending_confirmation"] == plan_a
    assert _poll(client, session_b, turn_b)["pending_confirmation"] == plan_b
    finished_b = wait_finished(client, session_b, turn_b, decision="reject")
    finished_a = wait_finished(client, session_a, turn_a, decision="approve")
    assert finished_a["outcome"] == "delivered"
    assert finished_b["outcome"] == "not_delivered"


def test_two_answers_at_once_record_exactly_one(waiting: Any) -> None:
    client, session_id, turn_id, plan, _ = waiting
    barrier = threading.Barrier(2)
    results: dict[str, int] = {}

    def post(decision: str) -> None:
        barrier.wait(5)
        response = _answer(
            client,
            session_id,
            turn_id,
            {"confirmation_id": plan["confirmation_id"], "decision": decision},
        )
        results[decision] = response.status_code

    threads = [threading.Thread(target=post, args=(d,)) for d in ("approve", "reject")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert sorted(results.values()) == [200, 409]
    winner = next(d for d, status in results.items() if status == 200)
    turn = wait_finished(client, session_id, turn_id, decision=None)
    assert turn["detail"]["confirmation"] == {"decision": winner, "decision_source": "user"}
    assert turn["outcome"] == ("delivered" if winner == "approve" else "not_delivered")


def test_a_waiting_turn_keeps_its_session_busy_and_its_slot(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", max_concurrent_turns=1)
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    client = _app(settings, PipelineFactory(provider))
    session_a, _ = _session_with_upload(client)
    session_b, _ = _session_with_upload(client)
    turn_a = _start(client, session_a, "remove duplicate rows")
    _wait_awaiting(client, session_a, turn_a)

    again = start_turn(client, session_a, "remove empty rows")
    upload_during = upload(client, session_a, xlsx_bytes())
    elsewhere = start_turn(client, session_b, "remove duplicate rows")

    assert again.status_code == 409 and again.json()["error"]["code"] == "session_busy"
    assert upload_during.status_code == 409
    assert elsewhere.status_code == 429 and elsewhere.json()["error"]["code"] == "server_busy"
    wait_finished(client, session_a, turn_a)
    _wait_workers_gone(client)
    turn_b = _start(client, session_b, "remove duplicate rows")
    assert wait_finished(client, session_b, turn_b)["outcome"] == "delivered"


# -- The deadline counts working time only (design Section 4.3) ------------------------------------


class _ConfirmingEngine:
    """Asks for a one-step plan the way Hands does, then replies with the answer it got.

    It writes no file, so a finished turn is ``not_delivered`` with this
    reply when the worker finishes it, and ``failed`` with the fallback when
    the deadline does.
    """

    def __init__(self, *, before_s: float = 0.0, after_s: float = 0.0) -> None:
        self.before_s = before_s
        self.after_s = after_s
        self.provider: Any = None
        self.approved: bool | None = None
        self.done = threading.Event()

    def __call__(self, confirmation_provider: Any) -> Any:
        self.provider = confirmation_provider
        return self

    def run(self, state: BuddyState) -> BuddyState:
        time.sleep(self.before_s)
        step = ConfirmationStep(
            task_id=f"{state.session_id}-task-0",
            capability="spreadsheet.deduplicate",
            tier=PermissionTier.WRITE,
            input_reference=state.context.active_file or "report.xlsx",
        )
        self.provider.expect_plan((step,))
        task = SkillTask(
            task_id=step.task_id,
            session_id=state.session_id,
            workspace_id=state.workspace_id,
            description="",
            permission=PermissionTier.WRITE,
        )
        self.approved = self.provider.confirm(task, PermissionTier.WRITE)
        time.sleep(self.after_s)
        state.reply = "Answered: approve." if self.approved else "Answered: no approval."
        self.done.set()
        return state


def _deadline_app(tmp_path: Path, deadline_s: float, engine: _ConfirmingEngine) -> TestClient:
    return _app(Settings(data_dir=tmp_path / "data", turn_deadline_s=deadline_s), engine)


def test_the_deadline_doesnt_count_the_wait_for_an_answer(tmp_path: Path) -> None:
    engine = _ConfirmingEngine()
    client = _deadline_app(tmp_path, 1.0, engine)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    _wait_awaiting(client, session_id, turn_id)

    time.sleep(1.5)  # longer than the deadline, waiting for the user
    assert _poll(client, session_id, turn_id)["status"] == "awaiting_confirmation"
    turn = wait_finished(client, session_id, turn_id)

    assert turn["reply"] == "Answered: approve."
    assert turn["reply_source"] == "pipeline"


def test_the_deadline_counts_the_work_after_the_answer(tmp_path: Path) -> None:
    engine = _ConfirmingEngine(after_s=1.5)
    client = _deadline_app(tmp_path, 0.5, engine)
    session_id, _ = _session_with_upload(client)

    turn = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))

    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"


def test_the_working_time_before_and_after_the_wait_adds_up(tmp_path: Path) -> None:
    """The deadline resumes with what was left; it doesn't start again."""
    engine = _ConfirmingEngine(before_s=0.6, after_s=0.8)  # each under the deadline, not both
    client = _deadline_app(tmp_path, 1.2, engine)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    _wait_awaiting(client, session_id, turn_id)
    time.sleep(1.5)

    turn = wait_finished(client, session_id, turn_id)

    assert turn["outcome"] == "failed" and turn["reply"] == FALLBACK_PIPELINE_ERROR
    # The late worker was approved, and its answer counts for nothing.
    assert engine.done.wait(5) and engine.approved is True


def test_working_time_under_the_deadline_finishes_however_long_the_wait(tmp_path: Path) -> None:
    engine = _ConfirmingEngine(before_s=0.2, after_s=0.2)
    client = _deadline_app(tmp_path, 1.5, engine)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    _wait_awaiting(client, session_id, turn_id)
    time.sleep(2.0)

    turn = wait_finished(client, session_id, turn_id)

    assert turn["reply"] == "Answered: approve." and turn["reply_source"] == "pipeline"


def test_a_plan_asked_after_the_deadline_is_never_shown(tmp_path: Path) -> None:
    engine = _ConfirmingEngine(before_s=1.0)
    client = _deadline_app(tmp_path, 0.3, engine)
    session_id, _ = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    plans: list[dict[str, Any]] = []

    turn = wait_finished(client, session_id, turn_id, decision=None, plans=plans)
    assert engine.done.wait(5)
    _wait_workers_gone(client)

    assert turn["outcome"] == "failed" and turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert plans == []
    assert engine.approved is False  # refused without asking anyone
    assert _poll(client, session_id, turn_id) == turn  # a finished turn never changes


# -- Shutdown and restart ----------------------------------------------------------------------


def test_a_server_shutdown_rejects_the_waiting_plan(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    app = create_app(settings, factory)
    with TestClient(app) as client:
        session_id, file_id = _session_with_upload(client)
        before = _sha(_upload_path(settings, file_id))
        turn_id = _start(client, session_id, "remove duplicate rows")
        _wait_awaiting(client, session_id, turn_id)
    # Leaving the block ran the app's shutdown.

    turn = wait_finished(TestClient(app), session_id, turn_id, decision=None)

    assert turn["outcome"] == "not_delivered" and turn["delivered_files"] == []
    assert turn["detail"]["confirmation"] == {"decision": "reject", "decision_source": "shutdown"}
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None
    assert _written(settings) == []
    assert _sha(_upload_path(settings, file_id)) == before


def test_a_restart_while_the_plan_waits_reports_the_turn_failed(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    first_app = create_app(settings, factory)
    client = TestClient(first_app)
    session_id, file_id = _session_with_upload(client)
    turn_id = _start(client, session_id, "remove duplicate rows")
    _wait_awaiting(client, session_id, turn_id)

    restarted = TestClient(create_app(settings, PipelineFactory()))
    turn = _poll(restarted, session_id, turn_id)
    state = restarted.get(f"/api/v1/sessions/{session_id}").json()

    assert turn["status"] == "finished" and turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"
    assert turn["delivered_files"] == [] and turn["detail"] is None
    assert state["busy"] is False and state["pending_confirmation"] is None
    first_app.state.runner.shutdown()  # let the old process's worker finish
    _wait_workers_gone(client)
    assert _written(settings) == []


def test_the_decision_is_kept_with_the_turn_across_a_restart(settings: Settings) -> None:
    client = _app(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    session_id, _ = _session_with_upload(client)
    turn = wait_finished(
        client, session_id, _start(client, session_id, "remove duplicate rows"), decision="reject"
    )

    restarted = TestClient(create_app(settings, PipelineFactory()))
    again = _poll(restarted, session_id, turn["turn_id"])

    assert again == turn
    assert again["detail"]["confirmation"] == {"decision": "reject", "decision_source": "user"}


def test_a_turn_record_written_before_pr3_still_restores(settings: Settings) -> None:
    client = _app(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    session_id, _ = _session_with_upload(client)
    turn = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))
    (path,) = settings.sessions_dir.rglob(f"turns/{turn['turn_id']}.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    del record["confirmation"]  # PR-2's shape
    path.write_text(json.dumps(record), encoding="utf-8")

    restarted = TestClient(create_app(settings, PipelineFactory()))
    again = _poll(restarted, session_id, turn["turn_id"])

    assert again["outcome"] == "delivered"
    assert again["detail"]["confirmation"] is None


@pytest.mark.parametrize(
    "confirmation",
    [
        {"decision": "maybe", "decision_source": "user"},
        {"decision": "approve", "decision_source": "the model"},
        {"decision": "approve"},
        "approve",
    ],
)
def test_a_malformed_stored_decision_is_not_restored(settings: Settings, confirmation: Any) -> None:
    client = _app(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    session_id, _ = _session_with_upload(client)
    turn = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))
    (path,) = settings.sessions_dir.rglob(f"turns/{turn['turn_id']}.json")
    record = json.loads(path.read_text(encoding="utf-8"))
    record["confirmation"] = confirmation
    path.write_text(json.dumps(record), encoding="utf-8")

    restarted = TestClient(create_app(settings, PipelineFactory()))

    assert restarted.get(f"/api/v1/sessions/{session_id}").status_code == 404


# -- R3-L4: a raise inside finalization never leaves a turn running ------------------------------


class _Halt(BaseException):
    pass


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.parametrize("helper", ["uploads_unchanged", "decide", "make_read_only"])
@pytest.mark.parametrize("error", [RuntimeError, _Halt], ids=["exception", "base_exception"])
def test_a_raise_inside_finalization_still_finishes_the_turn(
    settings: Settings,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
    error: type[BaseException],
) -> None:
    """Review round 3, R3-L4: finished as failed, nothing handed back, the session free."""
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    client = _app(settings, PipelineFactory(provider))
    session_id, _ = _session_with_upload(client)

    def raising(*args: Any, **kwargs: Any) -> Any:
        raise error("secret cell 4111")

    monkeypatch.setattr(turns_module, helper, raising)
    turn = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))
    monkeypatch.undo()

    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"
    assert turn["delivered_files"] == []
    files = client.get(f"/api/v1/sessions/{session_id}/files").json()["files"]
    assert [f["kind"] for f in files] == ["upload"]
    _wait_idle(client, session_id)
    _wait_workers_gone(client)
    after = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))
    assert after["outcome"] == "delivered"


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
@pytest.mark.parametrize("target", ["close_the_plan", "record_the_reply"])
def test_a_raise_while_finishing_still_finishes_the_turn_as_decided(
    settings: Settings, monkeypatch: pytest.MonkeyPatch, target: str
) -> None:
    """The finishing block's bookkeeping can't leave the turn running either."""
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    client = _app(settings, PipelineFactory(provider))
    session_id, _ = _session_with_upload(client)

    def raising(*args: Any, **kwargs: Any) -> Any:
        raise _Halt

    if target == "close_the_plan":
        monkeypatch.setattr(WebConfirmationProvider, "close", raising)
    else:
        monkeypatch.setattr(Context, "record_buddy_reply", raising)
    turn = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))
    monkeypatch.undo()

    assert turn["outcome"] == "delivered"  # what was decided and written stands
    assert turn["reply_source"] == "pipeline"
    _wait_idle(client, session_id)
    _wait_workers_gone(client)
    after = wait_finished(client, session_id, _start(client, session_id, "remove duplicate rows"))
    assert after["outcome"] == "delivered"
