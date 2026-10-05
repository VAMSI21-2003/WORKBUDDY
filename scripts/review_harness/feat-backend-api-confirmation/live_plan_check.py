# ruff: noqa: E501 — reviewer tooling, not product code
"""One live check (real LLM from the environment) of the plan shown for a request with an unsupported step.

Usage (from the repo root, ``GROQ_API_KEY`` or ``ANTHROPIC_API_KEY`` set; never printed)::

    python scripts/review_harness/feat-backend-api-confirmation/live_plan_check.py [--decision approve|reject] [--message TEXT]

Runs the app in-process (``create_app`` with its default, real engine
factory) on a temporary data root, uploads the 6-row fixture, starts one
turn, records every plan shown, answers it, and prints the intent, the
steps that ran, the plan, the answer, the outcome, the reply, an
independent read of any delivered file, and the original's SHA-256 before
and after. Stops on a 429 from the provider (it shows up as a failed turn
and a "429" in the server log, which this prints the count of).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import pr3  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pr3 import h  # noqa: E402

from backend.main import create_app  # noqa: E402
from backend.settings import Settings  # noqa: E402


class _Count(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.rate_limited = 0

    def emit(self, record: logging.LogRecord) -> None:
        if "429" in record.getMessage():
            self.rate_limited += 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--decision", default="reject", choices=("approve", "reject"))
    parser.add_argument("--message", default="remove duplicates and sort by amount")
    args = parser.parse_args()
    counter = _Count()
    logging.getLogger().addHandler(counter)
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        settings = Settings(data_dir=Path(tmp) / "data")
        client = TestClient(create_app(settings), raise_server_exceptions=False)
        app = pr3.App(client, settings, None, None)  # type: ignore[arg-type]
        sid = pr3.new_session(client, "focus")
        _, original = pr3.upload(app, sid, h.xlsx(), "manual_report.xlsx")
        before = h.sha(original)
        code, started = pr3.start(app, sid, args.message)
        if code != 202:
            print(json.dumps({"start_http": code, "body": started}))
            return
        tid = started["turn_id"]
        driven = pr3.drive(app, sid, tid, args.decision, timeout_s=240)
        body = driven["body"]
        detail = body.get("detail") or {}
        print(
            json.dumps(
                {
                    "message": args.message,
                    "decision": args.decision,
                    "intent": detail.get("intent"),
                    "clarification_required": detail.get("clarification_required"),
                    "statuses": driven["statuses"],
                    "plans": [{"steps": [(s["capability"], s["prompt"]) for s in p["steps"]], "plan_note": p["plan_note"]} for p in driven["plans"]],
                    "answers": driven["answers"],
                    "steps": [f"{s['capability']}[{s['status']}]" for s in detail.get("steps", [])],
                    "confirmation": detail.get("confirmation"),
                    "outcome": body.get("outcome"),
                    "reply_source": body.get("reply_source"),
                    "reply": body.get("reply"),
                    "delivered": pr3.delivered_check(app, body),
                    "written_in_turn_folder": pr3.written(app, sid, tid),
                    "original_sha_before": before,
                    "original_sha_after": h.sha(original),
                    "provider_429_log_lines": counter.rate_limited,
                },
                indent=1,
            )
        )


if __name__ == "__main__":
    main()
