"""A stand-in for api.groq.com, for the deploy-stack probes (stdlib only, no key, no network).

Runs as a container on the compose network; the app reaches it through GROQ_BASE_URL
(the groq SDK reads it). It answers the intent classifier and the reasoning stage the way
``tests/buddy/test_engine_factory._single_intent_replies`` does, so a "remove duplicate rows"
turn goes through the real pipeline, skills and verifiers inside the image.

    FAKE_INTENT   the intent the classifier returns (default remove_duplicate_rows)
    FAKE_MODE     ok (default) | error500 (every call fails, like an outage or a bad key)

    python fake_groq.py PORT
"""

from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply_for(system: str) -> str:
    intent = os.environ.get("FAKE_INTENT", "remove_duplicate_rows")
    if "intent classifier" in system:
        return json.dumps(
            {"intent": intent, "confidence": 0.95, "missing_information": [], "notes": ""}
        )
    if "reasoning stage" in system:
        return json.dumps(
            {
                "objective": "Clean up the spreadsheet.",
                "summary": "Clear enough to proceed.",
                "clarification_required": False,
                "confidence": 0.9,
            }
        )
    return json.dumps({"note": "fake"})


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", "0"))) or b"{}")
        if os.environ.get("FAKE_MODE") == "error500":
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":{"message":"fake outage"}}')
            return
        system = " ".join(
            str(m.get("content", "")) for m in body.get("messages", []) if m.get("role") == "system"
        )
        content = reply_for(system)
        out = {
            "id": "chatcmpl-fake",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": body.get("model", "fake"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
        print("fake_groq:", "classifier" if "intent classifier" in system else
              "reasoning" if "reasoning stage" in system else "other", flush=True)


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), Handler).serve_forever()  # noqa: S104
