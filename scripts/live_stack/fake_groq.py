"""A stand-in for api.groq.com, for a live stack that cannot reach the real model.

    python fake_groq.py PORT        # serves POST /openai/v1/chat/completions

NOT the real model: a run that uses it must say so. It answers the three stages the pipeline asks
(intent, reasoning, planning) from the user's message, with the same JSON shapes the tests' canned
replies use. The pipeline after that (skills, Inspector, Hands, the reply) is the real one.
"""

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def reply_for(system: str, user: str) -> str:
    match = re.search(r'User message: "(.*)"', user)
    message = (match.group(1) if match else user).lower()
    dedupe, empty = "duplicate" in message, "empty" in message
    if "intent classifier" in system:
        intent = "multi_step_request" if (dedupe and empty) else (
            "remove_duplicate_rows" if dedupe else "remove_empty_rows" if empty else "unclear")
        return json.dumps({"intent": intent, "confidence": 0.95, "missing_information": [],
                           "ambiguity_flags": [], "notes": ""})
    if "reasoning stage" in system:
        return json.dumps({"objective": "Clean up the spreadsheet.", "summary": "Clear enough to proceed.",
                           "clarification_required": False, "confidence": 0.9})
    if "planning stage" in system:
        steps = []
        if dedupe:
            steps.append(("spreadsheet.deduplicate", "remove duplicate rows"))
        if empty:
            steps.append(("spreadsheet.remove_empty_rows", ("then " if dedupe else "") + "remove empty rows"))
        return json.dumps({"steps": [{"description": f"do {c}", "capability": c,
                                      "depends_on_step_numbers": [], "source_phrase": p} for c, p in steps]})
    return "{}"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        if self.path.endswith("/chat/completions"):
            msgs = body.get("messages", [])
            system = next((m["content"] for m in msgs if m["role"] == "system"), "")
            user = next((m["content"] for m in reversed(msgs) if m["role"] == "user"), "")
            return self._json(200, {"id": "x", "object": "chat.completion", "model": "fake",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant", "content": reply_for(system, user)}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}})
        self._json(404, {})


ThreadingHTTPServer(("0.0.0.0", int(sys.argv[1])), H).serve_forever()
