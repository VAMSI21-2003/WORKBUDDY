# ruff: noqa: E501 — reviewer tooling, not product code
"""Run the real server (``python -m backend``'s ``main``) with a fake LLM, and probe it over HTTP.

Usage::

    python scripts/review_harness/feat-backend-api-core/server_probe.py

It starts ``backend.__main__.main`` in a subprocess, with ``create_app``
patched to use the real pipeline on a fake LLM (no key, no network), a
temporary data root, and ``TMPDIR`` pointed at an empty folder. Then it
checks, over real HTTP:

- the server's own log output (uvicorn's access log, with the API's log
  redaction installed): does it format, and does a session id appear?
- multipart spooling: after oversized and refused uploads, is anything
  left in ``TMPDIR``?
- a full turn end to end (upload, turn, poll, download).
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent

CHILD = r"""
import sys
sys.path.insert(0, {root!r}); sys.path.insert(0, {here!r})
from pathlib import Path
import harness as h
import backend.__main__ as entry
from backend import main as backend_main
from backend.settings import Settings
from api_probe import Factory

llm = h.FakeLLM(h.single("remove_duplicate_rows") * 20)
real_create_app = backend_main.create_app
def create_app():
    return real_create_app(Settings(data_dir=Path({data!r}), auto_approve=True), Factory(llm, "focus"))
entry.create_app = create_app
sys.argv = ["backend", "--port", "{port}"]
entry.main()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    result: dict[str, object] = {}
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(tmp) / "data"
        spool = Path(tmp) / "spool"
        spool.mkdir()
        port = free_port()
        env = {**os.environ, "TMPDIR": str(spool)}
        code = CHILD.format(root=str(ROOT), here=str(HERE), data=str(data), port=port)
        log = open(Path(tmp) / "server.log", "w+")
        proc = subprocess.Popen([sys.executable, "-c", code], stdout=log, stderr=subprocess.STDOUT, env=env)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(200):
                try:
                    httpx.get(base + "/healthz", timeout=1)
                    break
                except httpx.HTTPError:
                    time.sleep(0.1)
            c = httpx.Client(base_url=base, timeout=30)
            sid = c.post("/api/v1/sessions", json={"mode": "focus"}).json()["session_id"]
            sys.path.insert(0, str(HERE))
            import harness as h

            up = c.post(f"/api/v1/sessions/{sid}/files", files={"file": ("report.xlsx", h.xlsx())})
            result["upload"] = up.status_code
            turn = c.post(f"/api/v1/sessions/{sid}/turns", json={"message": "remove duplicate rows"}).json()
            for _ in range(600):
                body = c.get(f"/api/v1/sessions/{sid}/turns/{turn['turn_id']}").json()
                if body["status"] == "finished":
                    break
                time.sleep(0.05)
            result["turn"] = (body["outcome"], body["reply"])
            if body["delivered_files"]:
                d = c.get(body["delivered_files"][0]["download_url"])
                result["download"] = (d.status_code, d.headers.get("content-disposition"))
            # Oversized bodies, with and without Content-Length, and a refused type.
            big = b"\0" * (6 * 1024 * 1024)
            r1 = c.post(f"/api/v1/sessions/{sid}/files", files={"file": ("big.xlsx", big)})

            def gen():
                yield b'--b\r\nContent-Disposition: form-data; name="file"; filename="x.xlsx"\r\n\r\n'
                for _ in range(70):
                    yield b"\0" * 100_000
                yield b"\r\n--b--\r\n"

            r2 = c.post(
                f"/api/v1/sessions/{sid}/files",
                content=gen(),
                headers={"Content-Type": "multipart/form-data; boundary=b"},
            )
            r3 = c.post(
                f"/api/v1/sessions/{sid}/files",
                files={"file": ("big.csv", b"a" * (4 * 1024 * 1024))},
            )
            r4 = c.post(
                f"/api/v1/sessions/{sid}/files",
                files={"file": ("bad.xlsx", b"PK\x03\x04" + b"\0" * (4 * 1024 * 1024))},
            )
            result["oversized"] = [r1.status_code, r2.status_code, r3.status_code, r4.status_code]
            # Round 3: query strings any client can send, which make the whole access
            # line match a pattern that the path argument alone doesn't.
            result["odd_queries"] = [
                c.get(q).status_code
                for q in ("/healthz?api_key=", "/healthz?x=Bearer", f"/api/v1/sessions/{sid}?api_key=")
            ]
            time.sleep(0.5)
            result["tmpdir_after"] = sorted(p.name for p in spool.rglob("*"))
        finally:
            proc.terminate()
            proc.wait(10)
        log.seek(0)
        text = log.read()
        result["log_has_logging_error"] = "--- Logging error ---" in text
        result["log_logging_error_count"] = text.count("--- Logging error ---")
        result["session_id_in_log"] = sid in text
        result["log_excerpt"] = [line for line in text.splitlines() if "api/v1" in line or "Error" in line][:12]
    print(json.dumps(result, indent=1, default=str))


if __name__ == "__main__":
    main()
