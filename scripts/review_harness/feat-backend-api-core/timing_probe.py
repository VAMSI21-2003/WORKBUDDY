# ruff: noqa: E501 — reviewer tooling, not product code
"""Wall time of a turn on a file just under the default upload cap, through the API.

Usage::

    python scripts/review_harness/feat-backend-api-core/timing_probe.py [--root CHECKOUT] [--cap BYTES]

Imports ``backend`` and ``buddy`` from ``--root`` (default: this checkout), so
the same script times an older commit's worktree. Real app and pipeline, a
fake LLM (no network), ``auto_approve`` on, the turn deadline raised to 900 s
so the full time is measured. The fixture is ``scripts/live_api_runs.py``'s
measured shape (14 columns, 5% duplicates, a few empty rows), built as large
as fits under ``--cap`` (default 2 MiB). Prints one JSON object per turn:
wall time, outcome, whether it would have passed the 180 s default deadline,
the reply, and the original's SHA-256 before and after.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=str(HERE.parents[3]))
    parser.add_argument("--cap", type=int, default=2 * 1024 * 1024)
    args = parser.parse_args()
    root = Path(args.root).resolve()
    sys.path.insert(0, str(root))
    sys.path.insert(1, str(HERE.parents[3] / "scripts"))

    import live_api_runs as lr  # the fixture builder (this checkout's copy)
    from fastapi.testclient import TestClient

    from backend.main import create_app
    from backend.settings import Settings
    from buddy.engine_factory import build_engine
    from buddy.voice import PersonalityVoice

    rows = 20_000
    data = b""
    for _ in range(5):
        data = lr._xlsx(lr._wide_frame(rows))
        if 0.95 * args.cap <= len(data) <= args.cap:
            break
        rows = int(rows * args.cap / len(data) * 0.98)

    class LLM:
        def __init__(self, replies: list[str]) -> None:
            self.replies = replies
            self.lock = threading.Lock()

        def complete(self, *, system: str, messages: Any, max_tokens: int = 1024) -> str:
            with self.lock:
                return self.replies.pop(0)

    def understand(intent: str) -> str:
        return json.dumps({"intent": intent, "confidence": 0.95, "missing_information": [], "notes": ""})

    think = json.dumps({"objective": "x", "summary": "x", "clarification_required": False, "confidence": 0.9})
    plan = json.dumps(
        {
            "steps": [
                {"description": "d", "capability": "spreadsheet.deduplicate", "depends_on_step_numbers": [], "source_phrase": "remove duplicate rows"},
                {"description": "e", "capability": "spreadsheet.remove_empty_rows", "depends_on_step_numbers": [], "source_phrase": "remove empty rows"},
            ]
        }
    )
    cases = (
        ("remove duplicate rows", [understand("remove_duplicate_rows"), think]),
        ("remove duplicate rows, then remove empty rows", [understand("multi_step_request"), think, plan]),
    )
    for message, replies in cases:
        with tempfile.TemporaryDirectory() as tmp:
            llm = LLM(list(replies))

            def factory(confirmation_provider: Any, llm: Any = llm) -> Any:
                return build_engine(llm_provider=llm, speaker=PersonalityVoice(llm), confirmation_provider=confirmation_provider)

            settings = Settings(data_dir=Path(tmp) / "d", auto_approve=True, turn_deadline_s=900, max_upload_bytes=args.cap)
            client = TestClient(create_app(settings, factory), raise_server_exceptions=False)
            sid = client.post("/api/v1/sessions", json={"mode": "focus"}).json()["session_id"]
            up = client.post(f"/api/v1/sessions/{sid}/files", files={"file": ("big.xlsx", io.BytesIO(data), "application/octet-stream")})
            body = up.json()
            stored = next(p for p in settings.uploads_dir.rglob("big.xlsx"))
            before = hashlib.sha256(stored.read_bytes()).hexdigest()
            started = time.monotonic()
            tid = client.post(f"/api/v1/sessions/{sid}/turns", json={"message": message}).json()["turn_id"]
            while True:
                turn = client.get(f"/api/v1/sessions/{sid}/turns/{tid}").json()
                if turn["status"] == "finished":
                    break
                time.sleep(0.5)
            wall = round(time.monotonic() - started, 1)
            after = hashlib.sha256(stored.read_bytes()).hexdigest()
            print(
                json.dumps(
                    {
                        "root": str(root),
                        "fixture_bytes": len(data),
                        "fixture_rows": rows,
                        "upload_status": up.status_code,
                        "upload_sha_matches": body.get("sha256") == before,
                        "message": message,
                        "wall_s": wall,
                        "within_180s": wall <= 180,
                        "outcome": turn.get("outcome"),
                        "reply": turn.get("reply"),
                        "original_unchanged": before == after,
                    }
                )
            )
            sys.stdout.flush()


if __name__ == "__main__":
    main()
