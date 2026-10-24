"""P7 (round 2): how long the real writer chain can hold a turn, with the real providers.

``build_providers_from_env`` with placeholder keys; Gemini's URL and Groq's base URL point at a
local server that answers slowly or never. The writer in the pipeline is ``providers.writer``
(Gemini, then Groq, each with ``WRITER_TIMEOUT_S`` and no SDK retries). No network, no real key.

    python p7_writer_budget.py
"""
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

os.environ.update(GROQ_API_KEY="placeholder-groq", GEMINI_API_KEY="placeholder-gemini",
                  NO_PROXY="127.0.0.1,localhost", no_proxy="127.0.0.1,localhost")

import nb  # noqa: E402

nb.setup("../../..")
from scenarios import S  # noqa: E402

from buddy import llm  # noqa: E402

PLAN = {"gemini": [], "groq": []}  # per call: seconds to wait, then the answer (None: hang 60 s)
CALLS = []
REFUSED = {"messages": ["removed 9 duplicate rows from report.xlsx."], "facts_used": []}


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_POST(self):
        self.rfile.read(int(self.headers.get("content-length", 0)))
        who = "gemini" if self.path.startswith("/gemini") else "groq"
        CALLS.append((who, round(time.monotonic() - T0, 1)))
        delay, answer = PLAN[who].pop(0) if PLAN[who] else (60, None)
        if delay == 500:  # a fast server error
            self.send_response(500)
            self.send_header("content-length", "0")
            self.end_headers()
            return
        time.sleep(delay)
        text = json.dumps(answer or REFUSED)
        if who == "gemini":
            body = {"candidates": [{"content": {"parts": [{"text": text}]}, "finishReason": "STOP"}]}
        else:
            body = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                    "choices": [{"index": 0, "finish_reason": "stop",
                                 "message": {"role": "assistant", "content": text}}]}
        raw = json.dumps(body).encode()
        try:
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except OSError:
            pass


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
port = srv.server_address[1]
os.environ["GROQ_BASE_URL"] = f"http://127.0.0.1:{port}/groq"
llm._GEMINI_URL = f"http://127.0.0.1:{port}/gemini/{{model}}"
providers = llm.build_providers_from_env()


def real_writer(prompt, attempt, conv):
    return providers.writer.complete(system=nb.T["writer"].system_prompt(), messages=tuple(conv),
                                     max_tokens=700)


for label, plan in [
    ("both hang", {"gemini": [], "groq": []}),
    ("gemini 10 s refused draft, then both hang", {"gemini": [(10, None)], "groq": []}),
    ("gemini 500 fast, groq 11 s refused draft, then both hang", {"gemini": [(500, None)], "groq": [(11, None)]}),
]:
    PLAN.update({k: list(v) for k, v in plan.items()})
    CALLS.clear()
    T0 = time.monotonic()
    rec = nb.run(S["s01-dedupe"], "friend", writer=real_writer)
    took = time.monotonic() - T0
    tr = rec["turns"][-1]
    print(f"{label:55} turn {took:5.1f} s | writer shown={tr.get('writer_shown')} "
          f"fallback={tr.get('fallback')} delivered={tr.get('delivered')} | server calls {CALLS}")
