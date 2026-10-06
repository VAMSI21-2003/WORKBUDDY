# ruff: noqa: E501 — reviewer tooling, not product code
"""``python -m backend`` with each environment the startup checks cover: refused, or where it binds.

    python scripts/review_harness/feat-backend-retention/startup_probe.py > out/startup_probe.jsonl

Each case runs the real entry point in a subprocess for up to 6 s on a free
port with a temporary data root, then stops it. It records the exit code,
the last stderr line, the address uvicorn reports, and whether the
placeholder password appears in any output.
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

ROOT = Path(__file__).resolve().parents[3]
PW = "placeholder-startup-password"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


CASES = [
    ("nothing_set", {}, []),
    ("dev_no_password", {"WORKBUDDY_ENV": "dev"}, []),
    ("dev_host_0000", {"WORKBUDDY_ENV": "dev"}, ["--host", "0.0.0.0"]),
    ("dev_host_ipv6_any", {"WORKBUDDY_ENV": "dev"}, ["--host", "::"]),
    ("unset_env_with_password", {"WORKBUDDY_DEMO_PASSWORD": PW}, []),
    ("production_no_origin", {"WORKBUDDY_ENV": "production", "WORKBUDDY_DEMO_PASSWORD": PW}, []),
    ("production_http_origin", {"WORKBUDDY_ENV": "production", "WORKBUDDY_DEMO_PASSWORD": PW, "WORKBUDDY_PUBLIC_ORIGIN": "http://demo.example"}, []),
    ("production_origin_with_path", {"WORKBUDDY_ENV": "production", "WORKBUDDY_DEMO_PASSWORD": PW, "WORKBUDDY_PUBLIC_ORIGIN": "https://demo.example/app"}, []),
    ("production_ok", {"WORKBUDDY_ENV": "production", "WORKBUDDY_DEMO_PASSWORD": PW, "WORKBUDDY_PUBLIC_ORIGIN": "https://demo.example"}, []),
    ("Production_capitalised", {"WORKBUDDY_ENV": "Production", "WORKBUDDY_DEMO_PASSWORD": PW, "WORKBUDDY_PUBLIC_ORIGIN": "https://demo.example"}, []),
    ("empty_password_env", {"WORKBUDDY_ENV": "production", "WORKBUDDY_DEMO_PASSWORD": "", "WORKBUDDY_PUBLIC_ORIGIN": "https://demo.example"}, []),
    ("cors_wildcard", {"WORKBUDDY_ENV": "dev", "WORKBUDDY_CORS_ORIGINS": "*"}, []),
    ("deadline_3601", {"WORKBUDDY_ENV": "dev", "WORKBUDDY_TURN_DEADLINE_S": "3601"}, []),
    ("sweep_interval_1e10", {"WORKBUDDY_ENV": "dev", "WORKBUDDY_SWEEP_INTERVAL_S": "1e10"}, []),
    ("poll_refill_below_cap", {"WORKBUDDY_ENV": "dev", "WORKBUDDY_RATE_POLL_PER_HOUR": "3600", "WORKBUDDY_MAX_CONCURRENT_TURNS": "2"}, []),
]


def run(name: str, extra: dict[str, str], args: list[str]) -> dict[str, object]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("WORKBUDDY_")}
    env.update(extra)
    env["WORKBUDDY_DATA_DIR"] = tempfile.mkdtemp()
    port = free_port()
    proc = subprocess.Popen([sys.executable, "-m", "backend", "--port", str(port), *args], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    end = time.monotonic() + 6
    while time.monotonic() < end and proc.poll() is None:
        time.sleep(0.2)
    running = proc.poll() is None
    if running:
        proc.terminate()
    output = proc.communicate(timeout=20)[0]
    lines = [line for line in output.splitlines() if line.strip()]
    bound = next((line.split("Uvicorn running on ")[1] for line in lines if "Uvicorn running on" in line), None)
    refusal = next((line for line in lines if "can't start" in line), None)
    return {"case": name, "started": running, "exit": None if running else proc.returncode, "bound": bound, "refusal": refusal,
            "password_in_output": PW in output, "traceback": "Traceback" in output}


def main() -> None:
    for name, extra, args in CASES:
        print(json.dumps(run(name, extra, args)), flush=True)


if __name__ == "__main__":
    main()
