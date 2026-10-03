"""Turns through the API, on the real pipeline with a fake LLM (design Sections 3.3, 4, 5, 7.3).

Each test polls the turn until it's finished, so every turn runs on a real
worker thread, as in production.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.settings import Settings
from buddy.voice import _COMPLETION_CLAIM
from buddy.voice_catalog import (
    FALLBACK_INTEGRITY_FAILURE,
    FALLBACK_PIPELINE_ERROR,
    failure_reply,
)
from tests.backend.factories import (
    DEDUPE_THEN_EMPTY,
    SPEAKERS,
    BlockingFactory,
    PipelineFactory,
    QueuedProvider,
    chain,
    raise_in,
    single,
    touch_uploads_in,
)
from tests.backend.helpers import (
    NO_DUPLICATE_ROWS,
    new_session,
    start_turn,
    upload,
    wait_finished,
    xlsx_bytes,
)


def _app(settings: Settings, factory: Any) -> TestClient:
    return TestClient(create_app(settings, factory))


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _upload_path(settings: Settings, file_id: str) -> Path:
    (path,) = [p for p in settings.uploads_dir.rglob("*") if p.parent.name == file_id]
    return path


def _turn(
    client: TestClient, session_id: str, message: str, **extra: Any
) -> dict[str, Any]:
    response = start_turn(client, session_id, message, **extra)
    assert response.status_code == 202, response.text
    started = response.json()
    assert started["status"] == "running"
    assert started["reply"] is None and started["outcome"] is None
    return wait_finished(client, session_id, started["turn_id"])


def _session_with_upload(
    client: TestClient, mode: str = "friend", data: bytes | None = None
) -> tuple[str, str]:
    session_id = new_session(client, mode)
    response = upload(client, session_id, xlsx_bytes() if data is None else data)
    assert response.status_code == 201, response.text
    return session_id, str(response.json()["file_id"])


def _wait_idle(client: TestClient, session_id: str) -> None:
    deadline = time.monotonic() + 10
    while client.get(f"/api/v1/sessions/{session_id}").json()["busy"]:
        assert time.monotonic() < deadline
        time.sleep(0.02)


# -- The happy path ------------------------------------------------------------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_a_delivered_turn_hands_back_the_verified_file_and_the_pipelines_reply(
    settings: Settings, speaker: str
) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")), speaker=speaker)
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client, "focus" if speaker == "focus" else "friend")
    original = _upload_path(settings, file_id)
    before = _sha(original)

    turn = _turn(client, session_id, "  remove duplicate rows  ")

    (run,) = factory.runs
    assert run.user_message == "remove duplicate rows"  # trimmed
    assert turn["outcome"] == "delivered"
    assert turn["reply_source"] == "pipeline"
    assert turn["reply"] == run.reply  # byte for byte
    (delivered,) = turn["delivered_files"]
    assert delivered["file_id"] == f"d_{turn['turn_id']}_1"
    assert delivered["download_url"].endswith(f"/files/{delivered['file_id']}/download")
    on_disk = Path(run.outputs[0])
    assert on_disk.parent == settings.outputs_dir / on_disk.parent.parent.name / turn["turn_id"]
    assert delivered["name"] == on_disk.name
    assert delivered["sha256"] == _sha(on_disk)
    assert delivered["size"] == on_disk.stat().st_size
    written = pd.read_excel(on_disk)
    assert len(written) == 4 and int(written.duplicated().sum()) == 0
    assert _sha(original) == before


def test_the_detail_is_structured_data_without_skill_prose_or_paths(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    detail = turn["detail"]
    assert detail["intent"] == "remove_duplicate_rows"
    assert detail["clarification_required"] is False
    assert detail["stopped_at"] is None
    capabilities = [s["capability"] for s in detail["steps"]]
    assert "spreadsheet.deduplicate" in capabilities
    dedupe = next(s for s in detail["steps"] if s["capability"] == "spreadsheet.deduplicate")
    assert dedupe["verified"] is True and dedupe["permission_tier"] == "write"
    assert dedupe["input_name"] == "report.xlsx"
    text = json.dumps(detail)
    (run,) = factory.runs
    for prose in run.summaries + run.findings:
        if prose:
            assert prose not in text
    assert str(settings.data_dir) not in text and "/" not in "".join(
        name for s in detail["steps"] for name in s["output_names"]
    )


def test_the_session_summary_omits_detail(settings: Settings) -> None:
    client = _app(settings, PipelineFactory(QueuedProvider(single("remove_duplicate_rows"))))
    session_id, _ = _session_with_upload(client)
    _turn(client, session_id, "remove duplicate rows")

    (summary,) = client.get(f"/api/v1/sessions/{session_id}").json()["turns"]

    assert "detail" not in summary
    assert summary["status"] == "finished" and summary["outcome"] == "delivered"


# -- Not delivered -----------------------------------------------------------------------


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


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_an_unclear_request_delivers_nothing_and_claims_nothing(
    settings: Settings, speaker: str
) -> None:
    factory = PipelineFactory(QueuedProvider(_unclear()), speaker=speaker)
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client)
    before = _sha(_upload_path(settings, file_id))

    turn = _turn(client, session_id, "hi, what can you do?")

    assert turn["outcome"] == "not_delivered"
    assert turn["delivered_files"] == []
    assert turn["reply"] == factory.runs[0].reply
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None
    assert _sha(_upload_path(settings, file_id)) == before


def test_with_auto_approve_off_no_file_changing_step_runs(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", auto_approve=False)
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client)
    before = _sha(_upload_path(settings, file_id))

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "not_delivered"
    assert turn["delivered_files"] == []
    assert turn["reply"] == factory.runs[0].reply
    assert _COMPLETION_CLAIM.search(turn["reply"]) is None
    dedupe = next(
        s for s in turn["detail"]["steps"] if s["capability"] == "spreadsheet.deduplicate"
    )
    assert dedupe["status"] == "declined" and dedupe["output_names"] == []
    assert not list(settings.outputs_dir.rglob("*.xlsx"))
    assert _sha(_upload_path(settings, file_id)) == before


def test_the_auto_approve_setting_reaches_the_confirmation_provider(tmp_path: Path) -> None:
    for approve in (True, False):
        settings = Settings(data_dir=tmp_path / str(approve), auto_approve=approve)
        factory = PipelineFactory(QueuedProvider(_unclear()))
        client = _app(settings, factory)
        session_id, _ = _session_with_upload(client)
        _turn(client, session_id, "hello")
        (provider,) = factory.confirmation_providers
        assert provider.confirm(None, None) is approve  # type: ignore[arg-type]


# -- A chain, and F5 (per-turn folders) ----------------------------------------------------


def test_a_chain_hands_back_only_its_final_file(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(chain(*DEDUPE_THEN_EMPTY)))
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows, then remove empty rows")

    assert turn["outcome"] == "delivered"
    (delivered,) = turn["delivered_files"]
    turn_dir = Path(factory.runs[0].turn_output_dir or "")
    written = sorted(p.name for p in turn_dir.iterdir())
    assert len(written) == 2  # the intermediate file stays on disk, unlisted
    assert delivered["name"] == Path(factory.runs[0].outputs[0]).name
    final = pd.read_excel(turn_dir / delivered["name"])
    assert len(final) == 3


def test_two_turns_write_to_different_folders_and_the_first_file_survives(
    settings: Settings,
) -> None:
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    factory = PipelineFactory(provider)
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    first = _turn(client, session_id, "remove duplicate rows")
    first_path = Path(factory.runs[0].outputs[0])
    first_hash = _sha(first_path)
    second = _turn(client, session_id, "remove duplicate rows again")
    second_path = Path(factory.runs[1].outputs[0])

    assert first["outcome"] == second["outcome"] == "delivered"
    assert first_path != second_path
    assert first_path.parent != second_path.parent
    assert first_path.parent.name == first["turn_id"]
    assert second_path.parent.name == second["turn_id"]
    assert _sha(first_path) == first_hash == first["delivered_files"][0]["sha256"]


def test_a_chain_then_a_single_step_never_overwrite_each_other(settings: Settings) -> None:
    provider = QueuedProvider(chain(*DEDUPE_THEN_EMPTY) + single("remove_empty_rows"))
    factory = PipelineFactory(provider)
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    first = _turn(client, session_id, "remove duplicate rows, then remove empty rows")
    first_path = Path(factory.runs[0].outputs[0])
    first_hash = _sha(first_path)
    second = _turn(client, session_id, "now remove the empty rows too")

    assert first["outcome"] == second["outcome"] == "delivered"
    assert Path(factory.runs[1].outputs[0]).parent != first_path.parent
    assert _sha(first_path) == first_hash


# -- R6: a stage raises, Voice raises, the engine can't be built, the deadline -----------------


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_inspector_raising_after_a_real_output_delivers_nothing(
    settings: Settings, speaker: str
) -> None:
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows")),
        speaker=speaker,
        wrap=raise_in("inspector", after=True),
    )
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(client)
    before = _sha(_upload_path(settings, file_id))

    turn = _turn(client, session_id, "remove duplicate rows")

    (run,) = factory.runs
    assert run.failure is not None and run.outputs  # a real file exists...
    assert Path(run.outputs[0]).is_file()
    assert turn["outcome"] == "failed"  # ...and is not handed back
    assert turn["delivered_files"] == []
    assert turn["reply"] == failure_reply("inspector") == run.reply
    assert turn["reply_source"] == "pipeline"
    assert "4111" not in turn["reply"] and "/srv" not in turn["reply"]
    assert all(step["verified"] is False for step in turn["detail"]["steps"])
    assert _sha(_upload_path(settings, file_id)) == before


@pytest.mark.parametrize("stage", ["eyes", "brain", "think", "planner", "delegate", "hands"])
def test_any_stage_raising_ends_in_the_pipelines_failure_reply(
    settings: Settings, stage: str
) -> None:
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows")), wrap=raise_in(stage, after=False)
    )
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "failed"
    assert turn["reply"] == failure_reply(stage)
    assert turn["reply_source"] == "pipeline"
    assert turn["delivered_files"] == []


def _context_of(client: TestClient) -> Any:
    registry = client.app.state.registry  # type: ignore[attr-defined]
    (record,) = registry._sessions.values()
    return record


@pytest.mark.parametrize("speaker", SPEAKERS)
def test_voice_raising_discards_the_turns_state_and_uses_the_fallback(
    settings: Settings, speaker: str
) -> None:
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows")),
        speaker=speaker,
        wrap=raise_in("voice", after=True),
    )
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)
    record = _context_of(client)
    analyzed_before = record.state.context.analyzed_files

    turn = _turn(client, session_id, "remove duplicate rows")

    assert factory.runs == []  # engine.run raised; its state was never read
    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"
    assert turn["delivered_files"] == [] and turn["detail"] is None
    # The pre-turn state is kept: Inspector's mark_analyzed on the copy is gone.
    assert record.state.context.analyzed_files == analyzed_before
    conversation = [(t.role.value, t.message) for t in record.state.context.recent_conversation()]
    assert conversation == [("user", "remove duplicate rows"), ("buddy", FALLBACK_PIPELINE_ERROR)]


def test_an_engine_factory_that_raises_fails_the_turn(settings: Settings) -> None:
    def factory(confirmation_provider: Any) -> Any:
        raise RuntimeError("no provider: gsk_SHOULDNOTLEAK")

    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"


def test_an_unconfigured_llm_fails_the_turn_with_the_fallback(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    client = TestClient(create_app(settings))
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR


class _Abort(BaseException):
    pass


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_a_base_exception_in_a_stage_still_finishes_the_turn(settings: Settings) -> None:
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows")),
        wrap=raise_in("hands", after=True, exc=_Abort()),
    )
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["delivered_files"] == []
    _wait_idle(client, session_id)
    assert client.app.state.runner.running == 0  # type: ignore[attr-defined]


def test_the_deadline_fails_the_turn_and_a_late_worker_changes_nothing(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / "data", turn_deadline_s=0.3, auto_approve=True)
    factory = BlockingFactory(reply="Done — removed 2 duplicate row(s).")
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)
    record = _context_of(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "failed"
    assert turn["reply"] == FALLBACK_PIPELINE_ERROR
    assert turn["reply_source"] == "pipeline_fallback"
    assert client.get(f"/api/v1/sessions/{session_id}").json()["busy"] is False
    assert client.app.state.runner.running == 1  # type: ignore[attr-defined]
    factory.release.set()
    assert factory.finished.wait(5)
    deadline = time.monotonic() + 5
    while client.app.state.runner.running:  # type: ignore[attr-defined]
        assert time.monotonic() < deadline
        time.sleep(0.02)
    after = client.get(f"/api/v1/sessions/{session_id}/turns/{turn['turn_id']}").json()
    assert after == turn
    replies = record.state.context.recent_buddy_replies()
    assert replies == [FALLBACK_PIPELINE_ERROR]


def test_a_failed_turn_leaves_no_stale_failure_for_the_next_one(settings: Settings) -> None:
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_duplicate_rows"))
    calls = {"n": 0}

    def wrap(cycle: Any) -> Any:
        calls["n"] += 1
        return raise_in("planner", after=True)(cycle) if calls["n"] == 1 else cycle

    factory = PipelineFactory(provider, wrap=wrap)
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    first = _turn(client, session_id, "remove duplicate rows")
    second = _turn(client, session_id, "remove duplicate rows")

    assert first["outcome"] == "failed"
    assert second["outcome"] == "delivered"
    assert factory.runs[1].failure is None


# -- Rule 2 at runtime ------------------------------------------------------------------


@pytest.mark.parametrize("stage", ["hands", "inspector", "voice"])
def test_an_upload_that_changes_during_a_turn_is_an_integrity_failure(
    settings: Settings, stage: str
) -> None:
    factory = PipelineFactory(
        QueuedProvider(single("remove_duplicate_rows") + _unclear()),
        wrap=touch_uploads_in(stage),
    )
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)

    turn = _turn(client, session_id, "remove duplicate rows")

    assert turn["outcome"] == "integrity_failure"
    assert turn["reply"] == FALLBACK_INTEGRITY_FAILURE
    assert turn["reply_source"] == "pipeline_fallback"
    assert turn["delivered_files"] == [] and turn["detail"] is None
    # Fail closed: the original no longer matches, so no later turn is vouched for.
    factory.wrap = None
    later = _turn(client, session_id, "hello")
    assert later["outcome"] == "integrity_failure"


# -- Rule 1, property-style ----------------------------------------------------------------

_SCENARIOS: dict[str, tuple[list[str], dict[str, Any], str]] = {
    "delivered": (single("remove_duplicate_rows"), {}, "remove duplicate rows"),
    "nothing_to_remove": (
        single("remove_duplicate_rows"),
        {"rows": NO_DUPLICATE_ROWS},
        "remove duplicate rows",
    ),
    "chain": (chain(*DEDUPE_THEN_EMPTY), {}, "remove duplicate rows, then remove empty rows"),
    "declined": (single("remove_duplicate_rows"), {"auto_approve": False}, "remove duplicates"),
    "clarification": (_unclear(), {}, "do the thing"),
    "no_intent": (["not json", "not json either"], {}, "?"),
    "crash": (single("remove_duplicate_rows"), {"raise": "inspector"}, "remove duplicates"),
    "voice_crash": (single("remove_duplicate_rows"), {"raise": "voice"}, "remove duplicates"),
}


@pytest.mark.parametrize("speaker", SPEAKERS)
@pytest.mark.parametrize("scenario", sorted(_SCENARIOS))
def test_rule_1_holds_in_every_scenario(
    tmp_path: Path, scenario: str, speaker: str
) -> None:
    replies, options, message = _SCENARIOS[scenario]
    settings = Settings(
        data_dir=tmp_path / "data", auto_approve=options.get("auto_approve", True)
    )
    wrap = raise_in(options["raise"], after=True) if "raise" in options else None
    factory = PipelineFactory(QueuedProvider(replies), speaker=speaker, wrap=wrap)
    client = _app(settings, factory)
    session_id, file_id = _session_with_upload(
        client, "focus" if speaker == "focus" else "friend", xlsx_bytes(options.get("rows"))
    )
    before = _sha(_upload_path(settings, file_id))

    turn = _turn(client, session_id, message)

    pipeline_replies = [run.reply for run in factory.runs]
    assert turn["reply"] in pipeline_replies + [FALLBACK_PIPELINE_ERROR, FALLBACK_INTEGRITY_FAILURE]
    if turn["reply_source"] == "pipeline":
        assert turn["reply"] == pipeline_replies[-1]
    else:
        assert turn["reply"] in (FALLBACK_PIPELINE_ERROR, FALLBACK_INTEGRITY_FAILURE)
    assert (turn["outcome"] == "delivered") == bool(turn["delivered_files"])
    if turn["outcome"] != "delivered":
        assert _COMPLETION_CLAIM.search(turn["reply"]) is None, turn["reply"]
    text = json.dumps(turn["detail"])
    for run in factory.runs:
        for prose in run.summaries + run.findings:
            if prose:
                assert prose not in text
    assert _sha(_upload_path(settings, file_id)) == before


# -- Concurrency ---------------------------------------------------------------------------


def test_a_second_turn_or_an_upload_during_a_turn_is_409_and_creates_nothing(
    settings: Settings,
) -> None:
    factory = BlockingFactory()
    client = _app(settings, factory)
    session_id, _ = _session_with_upload(client)
    first = start_turn(client, session_id, "first").json()
    assert factory.started.wait(5)
    turn_dirs_before = sorted(settings.outputs_dir.rglob("t*"))

    second = start_turn(client, session_id, "second")
    upload_during = upload(client, session_id, xlsx_bytes())

    assert second.status_code == 409
    assert second.json()["error"]["code"] == "session_busy"
    assert upload_during.status_code == 409
    assert upload_during.json()["error"]["code"] == "session_busy"
    state = client.get(f"/api/v1/sessions/{session_id}").json()
    assert state["busy"] is True
    assert [t["turn_id"] for t in state["turns"]] == [first["turn_id"]]
    assert len(state["files"]) == 1
    assert sorted(settings.outputs_dir.rglob("t*")) == turn_dirs_before
    factory.release.set()
    wait_finished(client, session_id, first["turn_id"])


def test_the_global_cap_refuses_with_429_and_retry_after(tmp_path: Path) -> None:
    settings = Settings(
        data_dir=tmp_path / "data", max_concurrent_turns=2, busy_retry_after_s=7, auto_approve=True
    )
    factory = BlockingFactory()
    client = _app(settings, factory)
    sessions = [_session_with_upload(client)[0] for _ in range(3)]
    started = [start_turn(client, s, "go").json() for s in sessions[:2]]

    refused = start_turn(client, sessions[2], "go")

    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "server_busy"
    assert refused.headers["Retry-After"] == "7"
    assert client.get(f"/api/v1/sessions/{sessions[2]}").json()["turns"] == []
    factory.release.set()
    for session_id, turn in zip(sessions, started, strict=False):
        wait_finished(client, session_id, turn["turn_id"])
    _wait_idle(client, sessions[0])
    deadline = time.monotonic() + 5
    while client.app.state.runner.running:  # type: ignore[attr-defined]
        assert time.monotonic() < deadline
        time.sleep(0.02)
    assert start_turn(client, sessions[2], "go").status_code == 202


# -- Multi-turn context, mode and the active file --------------------------------------------


def test_the_second_turns_brain_prompt_carries_the_first_turn(settings: Settings) -> None:
    provider = QueuedProvider(single("remove_duplicate_rows") + single("remove_empty_rows"))
    client = _app(settings, PipelineFactory(provider))
    session_id, _ = _session_with_upload(client)

    first = _turn(client, session_id, "remove duplicate rows")
    calls_before = len(provider.calls)
    _turn(client, session_id, "now remove the empty rows too")

    brain_prompt = provider.calls[calls_before][1][-1].content
    assert "remove duplicate rows" in brain_prompt
    assert first["reply"] in brain_prompt
    assert "now remove the empty rows too" in brain_prompt
    # Backlog L2: replies are recorded, so PersonalityVoice's seed moves on.
    assert _context_of(client).state.context.buddy_reply_count() == 2


def test_the_mode_given_on_a_turn_becomes_the_sessions_mode(settings: Settings) -> None:
    client = _app(settings, PipelineFactory(QueuedProvider(_unclear())))
    session_id, _ = _session_with_upload(client, "friend")

    turn = _turn(client, session_id, "hello", mode="focus")

    assert turn["mode"] == "focus"
    assert client.get(f"/api/v1/sessions/{session_id}").json()["mode"] == "focus"


def test_a_file_id_on_a_turn_picks_that_upload(settings: Settings) -> None:
    factory = PipelineFactory(QueuedProvider(single("remove_duplicate_rows")))
    client = _app(settings, factory)
    session_id, first_id = _session_with_upload(client)
    upload(client, session_id, xlsx_bytes(NO_DUPLICATE_ROWS), name="other.xlsx")

    turn = _turn(client, session_id, "remove duplicate rows", file_id=first_id)

    dedupe = next(
        s for s in turn["detail"]["steps"] if s["capability"] == "spreadsheet.deduplicate"
    )
    assert dedupe["input_name"] == "report.xlsx"
    assert client.get(f"/api/v1/sessions/{session_id}").json()["active_file_id"] == first_id


# -- Validation and isolation ------------------------------------------------------------------


@pytest.fixture
def idle_client(settings: Settings) -> TestClient:
    return _app(settings, PipelineFactory(QueuedProvider(_unclear() * 3)))


@pytest.mark.parametrize(
    "body",
    [
        {"message": ""},
        {"message": "   \n\t "},
        {"message": "x" * 4001},
        {"message": "hi", "mode": "chatty"},
        {"message": "hi", "extra": True},
        {},
        {"message": 42},
    ],
)
def test_an_invalid_turn_request_is_422_and_creates_no_turn(
    idle_client: TestClient, body: dict[str, Any]
) -> None:
    session_id = new_session(idle_client)

    response = idle_client.post(f"/api/v1/sessions/{session_id}/turns", json=body)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
    assert idle_client.get(f"/api/v1/sessions/{session_id}").json()["turns"] == []


def test_a_message_of_exactly_the_limit_is_accepted(idle_client: TestClient) -> None:
    session_id = new_session(idle_client)

    response = start_turn(idle_client, session_id, "x" * 4000)

    assert response.status_code == 202
    wait_finished(idle_client, session_id, response.json()["turn_id"])


@pytest.mark.parametrize("file_id", ["f_unknown", "d_t0001_1", "../x", "a" * 65])
def test_an_unknown_file_id_is_404_and_creates_no_turn(
    idle_client: TestClient, file_id: str
) -> None:
    session_id, _ = _session_with_upload(idle_client)

    response = start_turn(idle_client, session_id, "hi", file_id=file_id)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "file_not_found"
    assert idle_client.get(f"/api/v1/sessions/{session_id}").json()["turns"] == []


def test_another_sessions_file_turn_and_session_are_404(idle_client: TestClient) -> None:
    a, a_file = _session_with_upload(idle_client)
    b, _ = _session_with_upload(idle_client)
    a_turn = _turn(idle_client, a, "hello")

    assert start_turn(idle_client, b, "hi", file_id=a_file).status_code == 404
    response = idle_client.get(f"/api/v1/sessions/{b}/turns/{a_turn['turn_id']}x")
    assert response.status_code == 404
    assert idle_client.get(f"/api/v1/sessions/{b}/turns/{a_turn['turn_id']}").status_code == 404
    assert idle_client.get(f"/api/v1/sessions/s_nope/turns/{a_turn['turn_id']}").status_code == 404


@pytest.mark.parametrize("turn_id", ["t9999", "..", "t0001%2F..", "x" * 65])
def test_an_unknown_turn_is_404(idle_client: TestClient, turn_id: str) -> None:
    session_id = new_session(idle_client)

    response = idle_client.get(f"/api/v1/sessions/{session_id}/turns/{turn_id}")

    if response.status_code == 200:  # the client normalised ".." to the session itself
        assert "turn_id" not in response.json()
    else:
        assert response.status_code == 404


def test_a_turn_without_any_upload_still_gets_a_pipeline_reply(idle_client: TestClient) -> None:
    session_id = new_session(idle_client)

    turn = _turn(idle_client, session_id, "hello")

    assert turn["outcome"] == "not_delivered"
    assert turn["reply_source"] == "pipeline"


def test_the_202_body_is_the_running_turn_even_if_it_already_finished(settings: Settings) -> None:
    gate = threading.Event()

    class _Instant:
        def __call__(self, cp: Any) -> Any:
            return self

        def run(self, state: Any) -> Any:
            state.reply = "Hello."
            gate.set()
            return state

    client = _app(settings, _Instant())
    session_id = new_session(client)

    response = start_turn(client, session_id, "hello")

    assert response.status_code == 202
    assert response.json()["status"] == "running"
    assert response.json()["reply"] is None
    wait_finished(client, session_id, response.json()["turn_id"])
