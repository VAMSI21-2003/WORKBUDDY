# ruff: noqa: E501 — reviewer tooling, not product code
"""Probe the API end to end: real app, real pipeline, fake LLM, all three speakers.

Usage::

    python scripts/review_harness/api_probe.py [--only NAME] [--speaker template|friend|focus]

For every scenario and speaker it prints one JSON object: what happened
(status, outcome, reply_source), what was handed back (names, sizes, an
independent openpyxl read of each delivered file), the reply, the reply
the pipeline itself produced (to check the API passes it through
byte for byte), and the original upload's SHA-256 before and after.

A scenario is ``(name, auto_approve, fixture, message, replies, wrap,
extra)``. ``wrap`` replaces stages of the real cycle (to make one raise,
or touch the upload).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import harness as h  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402
from buddy.engine_factory import build_engine  # noqa: E402
from buddy.voice import PersonalityVoice, TemplateSpeaker  # noqa: E402
from core.engine import Engine  # noqa: E402

SPEAKERS = ("template", "friend", "focus")


class Factory:
    """The real pipeline on a fake LLM; records the reply each run produced."""

    def __init__(self, llm: h.FakeLLM, speaker: str, wrap: Any = None) -> None:
        self.llm = llm
        self.speaker = speaker
        self.wrap = wrap
        self.pipeline_replies: list[str | None] = []
        self.final_states: list[Any] = []

    def __call__(self, confirmation_provider: Any) -> Any:
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
        except BaseException:
            self._factory.pipeline_replies.append(state.reply)
            raise
        self._factory.pipeline_replies.append(result.reply)
        self._factory.final_states.append(
            {
                "failure": dataclasses.asdict(result.failure) if result.failure else None,
                "outputs": list(result.execution_result.outputs)
                if result.execution_result
                else None,
                "verified": result.verification_result.passed
                if result.verification_result
                else None,
            }
        )
        return result


def wait(client: TestClient, sid: str, tid: str, timeout: float = 60.0) -> dict[str, Any]:
    end = time.monotonic() + timeout
    while True:
        body = client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
        if body.get("status") == "finished":
            return body
        if time.monotonic() > end:
            return {"status": "TIMEOUT", **body}
        time.sleep(0.02)


def run_turn(
    client: TestClient, sid: str, message: str, upload_path: Path | None, **extra: Any
) -> dict[str, Any]:
    before = h.sha(upload_path) if upload_path else None
    started = client.post(f"/api/v1/sessions/{sid}/turns", json={"message": message, **extra})
    if started.status_code != 202:
        return {"http": started.status_code, "body": started.json()}
    tid = started.json()["turn_id"]
    body = wait(client, sid, tid)
    after = h.sha(upload_path) if upload_path else None
    delivered = []
    for d in body.get("delivered_files", []):
        r = client.get(d["download_url"])
        entry: dict[str, Any] = {"name": d["name"], "http": r.status_code}
        if r.status_code == 200:
            entry["sha_matches_listing"] = h.sha(r.content) == d["sha256"]
            entry["independent"] = h.describe(h.read_rows(r.content))
        delivered.append(entry)
    return {
        "turn_id": tid,
        "status": body.get("status"),
        "outcome": body.get("outcome"),
        "reply_source": body.get("reply_source"),
        "reply": body.get("reply"),
        "detail": body.get("detail"),
        "delivered": delivered,
        "original_sha_before": before,
        "original_sha_after": after,
        "original_unchanged": before == after,
    }


def upload(client: TestClient, sid: str, data: bytes, name: str = "report.xlsx") -> Any:
    return client.post(
        f"/api/v1/sessions/{sid}/files", files={"file": (name, data, "application/octet-stream")}
    )


def scenario(
    name: str,
    speaker: str,
    *,
    auto_approve: bool = True,
    fixture: bytes | None = None,
    message: str = "remove duplicate rows",
    replies: list[Any] | None = None,
    wrap: Any = None,
    deadline: float = 60.0,
    filename: str = "report.xlsx",
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        llm = h.FakeLLM(replies if replies is not None else h.single("remove_duplicate_rows"))
        factory = Factory(llm, speaker, wrap)
        settings = Settings(
            data_dir=Path(tmp) / "data", auto_approve=auto_approve, turn_deadline_s=deadline
        )
        client = TestClient(create_app(settings, factory), raise_server_exceptions=False)
        mode = "friend" if speaker == "template" else speaker
        sid = client.post("/api/v1/sessions", json={"mode": mode}).json()["session_id"]
        path = None
        if fixture is not None:
            up = upload(client, sid, fixture, filename)
            if up.status_code != 201:
                return {"scenario": name, "speaker": speaker, "upload": up.json()}
            fid = up.json()["file_id"]
            path = next((settings.uploads_dir).rglob(up.json()["name"]))
            assert fid in str(path)
        result = run_turn(client, sid, message, path)
        result["pipeline_reply_equal"] = (
            factory.pipeline_replies[-1] == result.get("reply")
            if factory.pipeline_replies
            else None
        )
        result["pipeline_state"] = factory.final_states[-1] if factory.final_states else None
        result["llm_calls"] = len(llm.calls)
        return {"scenario": name, "speaker": speaker, **result}


def scenarios(speaker: str) -> list[dict[str, Any]]:
    from dataclasses import replace as dc_replace

    def raise_in(stage: str, after: bool) -> Any:
        def wrap(cycle: Any) -> Any:
            inner = getattr(cycle, stage)

            def stage_fn(state: Any) -> Any:
                if after:
                    inner(state)
                raise RuntimeError("boom with cell text SECRET-CELL and /srv/path")

            return dc_replace(cycle, **{stage: stage_fn})

        return wrap

    dedupe_then_empty = (
        ("spreadsheet.deduplicate", "remove duplicate rows"),
        ("spreadsheet.remove_empty_rows", "then remove empty rows"),
    )
    return [
        scenario("1_dedupe_approved", speaker, fixture=h.xlsx()),
        scenario("2_dedupe_default_config_declined", speaker, auto_approve=False, fixture=h.xlsx()),
        scenario(
            "3_chain_approved",
            speaker,
            fixture=h.xlsx(),
            message="remove duplicate rows, then remove empty rows",
            replies=h.chain(*dedupe_then_empty),
        ),
        scenario(
            "4_chain_declined",
            speaker,
            auto_approve=False,
            fixture=h.xlsx(),
            message="remove duplicate rows, then remove empty rows",
            replies=h.chain(*dedupe_then_empty),
        ),
        scenario("5_no_duplicates", speaker, fixture=h.xlsx(h.NO_DUP_ROWS)),
        scenario(
            "6_greeting_unclear",
            speaker,
            fixture=h.xlsx(),
            message="hi, what can you do?",
            replies=h.single("unclear"),
        ),
        scenario("7_no_file", speaker, fixture=None),
        scenario("8_inspector_raises_after", speaker, fixture=h.xlsx(), wrap=raise_in("inspector", True)),
        scenario("9_voice_raises", speaker, fixture=h.xlsx(), wrap=raise_in("voice", False)),
        scenario("10_hands_raises_after", speaker, fixture=h.xlsx(), wrap=raise_in("hands", True)),
        scenario(
            "11_llm_raises_everywhere",
            speaker,
            fixture=h.xlsx(),
            replies=[h.raising(RuntimeError("provider down gsk_LEAKLEAK"))] * 10,
        ),
        scenario(
            "12_empty_rows_approved",
            speaker,
            fixture=h.xlsx(),
            message="remove empty rows",
            replies=h.single("remove_empty_rows"),
        ),
        scenario(
            "13_clarification",
            speaker,
            fixture=h.xlsx(),
            message="clean it",
            replies=[h.understand("remove_duplicate_rows"), h.think(clarify=True)],
        ),
    ]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--speaker", choices=SPEAKERS)
    parser.add_argument("--only")
    args = parser.parse_args()
    for speaker in (args.speaker,) if args.speaker else SPEAKERS:
        for result in scenarios(speaker):
            if args.only and args.only not in result["scenario"]:
                continue
            print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
